from __future__ import annotations
import os, json, re, sys
from typing import List, Dict, Any
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
import yaml

from . import notify, hooks

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
def _log(site_id: str | None, message: str) -> None:
    prefix = site_id if site_id else "runner"
    print(f"[{prefix}] {message}", flush=True)



def normalize_text(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.strip())

def title_match(title: str, keywords: List[str]) -> bool:
    t = normalize_text(title).lower()
    for kw in keywords or []:
        if kw and kw.lower() in t:
            return True
    return False

def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def load_state(path: str) -> tuple[set[str], str]:
    if not os.path.exists(path):
        return set(), ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            ids = set(data.get("ids", []) or data.get("gcodes", []))
            head_id = str(data.get("head_id", "") or "").strip()
            return ids, head_id
    except Exception:
        pass
    return set(), ""


def _item_id(item: Dict[str, Any]) -> str:
    return str(item.get("id") or item.get("gcode") or "").strip()


def _take_until_head(items: List[Dict[str, Any]], prev_head_id: str) -> List[Dict[str, Any]]:
    if not prev_head_id:
        return items
    out: List[Dict[str, Any]] = []
    for it in items:
        if _item_id(it) == prev_head_id:
            break
        out.append(it)
    return out


def save_state(path: str, ids: set, head_id: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ids": sorted(ids), "head_id": head_id}, f, ensure_ascii=False, indent=2)



def fetch_html(url: str) -> str:
    s = requests.Session()
    # warm-up: hit top to set cookies
    try:
        s.get("https://www.amiami.jp/", headers=HEADERS, timeout=30)
    except Exception:
        pass

    h2 = dict(HEADERS)

    # Special handling for AmiAmi product detail pages to reduce 403
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        path = parsed.path or ''
    except Exception:
        host, path = '', ''

    if host.endswith('amiami.jp') and '/top/detail/detail' in path:
        # Simulate navigation from slist (same-site) and include modern fetch/client-hints headers
        slist_referer = "https://slist.amiami.jp/top/search/list?s_sortkey=regtimed&pagemax=60"
        try:
            s.get("https://slist.amiami.jp/top/search/list?s_sortkey=regtimed&pagemax=60", headers=HEADERS, timeout=30)
        except Exception:
            pass
        h2.update({
            "Referer": slist_referer,
            # Typical modern Chromium client hints / fetch headers for top-level navigation
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-User": "?1",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", ";Not=A?Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
        })
    else:
        # Default referer behavior (previous behavior)
        h2["Referer"] = "https://www.amiami.jp/"

    resp = s.get(url, headers=h2, timeout=30, allow_redirects=True)
    if resp.status_code in (403, 503):
        # retry once with minor header tweaks
        h2["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        h2["Cache-Control"] = "no-cache"
        h2.setdefault("Pragma", "no-cache")
        try:
            resp = s.get(url, headers=h2, timeout=30, allow_redirects=True)
        except Exception:
            pass

    # Fallback: try slist domain for detail if AmiAmi still returns 403
    if resp.status_code == 403 and host.endswith('amiami.jp') and '/top/detail/detail' in path:
        try:
            slist_url = url.replace('://www.amiami.jp', '://slist.amiami.jp')
            h_slist = dict(h2)
            # When hitting slist directly, keep slist referer
            h_slist["Referer"] = "https://slist.amiami.jp/"
            alt = s.get(slist_url, headers=h_slist, timeout=30, allow_redirects=True)
            if alt.ok:
                resp = alt
        except Exception:
            pass
    # If still blocked, try curl_cffi (Chrome TLS/HTTP2 impersonation) if available
    # Broaden condition: any amiami.jp 403 should attempt impersonation
    if resp.status_code == 403 and host.endswith('amiami.jp'):
        try:
            from curl_cffi import requests as curl_requests  # type: ignore
            cr_headers = dict(h2)
            for imp in ("chrome124", "chrome120"):
                try:
                    alt = curl_requests.get(url, headers=cr_headers, impersonate=imp, timeout=30)
                    if alt.status_code == 200 and (alt.text or "").strip():
                        return alt.text
                except Exception:
                    continue
        except Exception:
            pass

    resp.raise_for_status()
    return resp.text

def extract_amiami(html: str, base_url: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict[str, str]] = []
    for a in soup.select('a[href*="detail?gcode="]'):
        href = a.get("href", "")
        full_url = urljoin(base_url, href)
        q = parse_qs(urlparse(full_url).query)
        gcode = (q.get("gcode", [""])[0] or "").strip()
        if not gcode:
            continue
        name_el = a.select_one(".product_name_inner")
        title = normalize_text(name_el.get_text()) if name_el else ""
        price_el = a.select_one(".product_price")
        price = normalize_text(price_el.get_text()) if price_el else ""
        items.append({"id": gcode, "gcode": gcode, "title": title, "url": full_url, "price": price})
    return items

def extract_generic(html: str, base_url: str, selectors: Dict[str, Any]) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict[str, str]] = []
    item_sel = selectors.get("item")
    link_sel = selectors.get("link")
    title_sel = selectors.get("title")
    price_sel = selectors.get("price")
    id_conf = selectors.get("id", {})

    for node in soup.select(item_sel):
        link_node = node.select_one(link_sel) if link_sel else node
        href = link_node.get("href") if link_node else None
        if not href:
            continue
        full_url = urljoin(base_url, href)
        title = ""
        if title_sel:
            tnode = node.select_one(title_sel)
            title = normalize_text(tnode.get_text()) if tnode else ""
        price = ""
        if price_sel:
            pnode = node.select_one(price_sel)
            price = normalize_text(pnode.get_text()) if pnode else ""
        pid = None
        mtype = id_conf.get("type", "query_param")
        if mtype == "query_param":
            param = id_conf.get("param")
            if param:
                q = parse_qs(urlparse(full_url).query)
                pid = (q.get(param, [""])[0] or "").strip()
        elif mtype == "regex":
            pat = id_conf.get("pattern")
            if pat:
                m = re.search(pat, full_url)
                if m:
                    pid = m.group(1) if m.groups() else m.group(0)
        if not pid:
            continue
        items.append({"id": pid, "title": title, "url": full_url, "price": price})
    return items


def extract_bandai_candy(html: str, base_url: str) -> List[Dict[str, str]]:
    """Extract latest product links from Bandai Candy top page.
    The page contains various sections (slider, blocks) linking to
    /candy/products/YYYY/ID.html. We collect unique product URLs and
    derive IDs from the numeric part before .html.
    Title/price are omitted here (detail fetch will populate them).
    """
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict[str, str]] = []
    seen: set[str] = set()
    for a in soup.select('a[href*="/candy/products/"]'):
        href = a.get("href") or ""
        if not href:
            continue
        full_url = urljoin(base_url, href)
        # ID = last digits chunk in filename
        m = re.search(r"/candy/products/\d{4}/(\d+)\.html", full_url)
        if not m:
            continue
        pid = m.group(1)
        if pid in seen:
            continue
        seen.add(pid)
        title = ""
        try:
            # Prefer visible text near the link if present
            title = normalize_text(a.get_text())
            if not title:
                img = a.select_one('img[alt]')
                if img and img.get('alt'):
                    title = normalize_text(img.get('alt'))
        except Exception:
            pass
        items.append({"id": pid, "title": title, "url": full_url, "price": ""})
    return items


def _normalize_shopify_price(value: str) -> str:
    stripped = str(value or '').strip().replace(',', '').replace('，', '')
    if '.' in stripped:
        whole, frac = stripped.split('.', 1)
        if frac.strip('0') == '':
            stripped = whole
    digits = re.sub(r'[^0-9]', '', stripped)
    return digits or stripped


def extract_shopify_products(raw: str, source_url: str, options: Dict[str, Any]) -> List[Dict[str, str]]:
    try:
        data = json.loads(raw)
    except Exception:
        return []
    products = data.get('products') or []
    base_override = options.get('base_url')
    if base_override:
        store_base = base_override.rstrip('/') + '/'
    else:
        parsed = urlparse(source_url)
        store_base = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else ''
    try:
        products = sorted(
            products,
            key=lambda prod: prod.get('published_at') or prod.get('created_at') or '',
            reverse=True,
        )
    except Exception:
        pass
    items: List[Dict[str, str]] = []
    for prod in products:
        handle = prod.get('handle') or ''
        title = normalize_text(prod.get('title') or '')
        if not title:
            continue
        product_url = prod.get('online_store_url') or ''
        if not product_url and store_base and handle:
            product_url = urljoin(store_base, f'products/{handle}')
        if not product_url:
            continue
        pid = str(prod.get('id') or handle or product_url).strip()
        price = ''
        variants = prod.get('variants') or []
        for variant in variants:
            pv = variant.get('price')
            if pv:
                price = _normalize_shopify_price(pv)
                break
        if not price and variants:
            pv = variants[0].get('price')
            if pv:
                price = _normalize_shopify_price(pv)
        items.append({'id': pid, 'title': title, 'url': product_url, 'price': price})
    return items


def run_site(site: Dict[str, Any], discord_env_url: str | None) -> None:
    site_id = str(site.get("id", "site") or "site")
    url = site.get("monitor_url")
    parser = site.get("parser", "amiami")
    top_n = int(site.get("top_n", 40))
    keywords = site.get("keywords", [])
    state_file = site.get("state_file", f"state/{site.get('id','site')}.json")
    webhook = site.get("discord_webhook") or discord_env_url

    if not url:
        _log(site_id, "monitor_url not configured; skipping site")
        return

    _log(site_id, f"Fetching monitor URL: {url}")
    try:
        html = fetch_html(url)
    except Exception as e:
        _log(site_id, f"[ERROR] fetch_html failed for {url}: {e}")
        return
    _log(site_id, "Fetch succeeded")

    if parser == "amiami":
        items = extract_amiami(html, url)
    elif parser == "generic":
        items = extract_generic(html, url, site.get("selectors", {}))
    elif parser == "shopify":
        items = extract_shopify_products(html, url, site.get("parser_options", {}))
    elif parser == "bandai_candy":
        items = extract_bandai_candy(html, url)
    else:
        _log(site_id, f"Unknown parser '{parser}'; defaulting to no items")
        items = []

    _log(site_id, f"Parser '{parser}' produced {len(items)} item(s)")
    items = items[:top_n]
    _log(site_id, f"Trimmed to {len(items)} item(s) (top_n={top_n})")

    if keywords:
        filtered = [it for it in items if title_match(it.get("title", ""), keywords)]
        _log(site_id, f"Keyword filter applied ({len(keywords)} keyword(s)): {len(filtered)} item(s) remain")
    else:
        filtered = items
        _log(site_id, f"No keywords configured; using {len(filtered)} item(s)")

    prev, prev_head_id = load_state(state_file)
    _log(site_id, f"Loaded {len(prev)} previous id(s) from state (head_id={prev_head_id or '-'})")

    window = _take_until_head(filtered, prev_head_id)
    if prev_head_id:
        _log(site_id, f"Head cutoff applied: {len(window)} item(s) before previous head")

    current = {_item_id(it) for it in filtered}
    current = {x for x in current if x}
    new_ids = current - prev
    new_items = [it for it in window if _item_id(it) in new_ids]
    _log(site_id, f"Current set size: {len(current)}; new items detected: {len(new_items)}")
    new_head_id = _item_id(filtered[0]) if filtered else prev_head_id

    if new_items and webhook:
        _log(site_id, f"Sending Discord summary for {len(new_items)} new item(s)")
        try:
            notify.send_discord_summary(webhook, site, new_items)
        except Exception as exc:
            _log(site_id, f"[ERROR] Discord summary send failed: {exc}")
    elif new_items:
        _log(site_id, "New items found, but no webhook configured; skipping Discord summary")
    else:
        _log(site_id, "No new items detected; skipping Discord summary")

    if new_items:
        _log(site_id, f"Running hooks.on_change for {len(new_items)} item(s)")
        try:
            payloads, errors, wrote = hooks.on_change(site, new_items)
            _log(site_id, f"hooks.on_change finished (wrote={wrote}, errors={len(errors)})")
            if errors:
                for err in errors[:3]:
                    _log(site_id, f"[HOOK ERROR] {err}")
                if len(errors) > 3:
                    _log(site_id, f"[HOOK ERROR] ... ({len(errors) - 3} more)")
                if webhook:
                    try:
                        notify.send_discord_error(webhook, site, errors)
                    except Exception as exc:
                        _log(site_id, f"[ERROR] Discord error notification failed: {exc}")
        except Exception as exc:
            _log(site_id, f"[ERROR] hooks.on_change raised: {exc}")
    else:
        _log(site_id, "Skipping hooks.on_change (no new items)")

    try:
        save_state(state_file, current, new_head_id)
        _log(site_id, f"State saved ({len(current)} id(s)) to {state_file}")
    except Exception as exc:
        _log(site_id, f"[ERROR] Failed to save state: {exc}")
        raise

def _cli_manual_url(argv: List[str]) -> str:
    if not argv:
        return ''
    for i, arg in enumerate(argv):
        if arg in ('--url', '-u') and i + 1 < len(argv):
            return str(argv[i + 1] or '').strip()
    first = str(argv[0] or '').strip()
    if first.startswith('http://') or first.startswith('https://'):
        return first
    return ''


def run_manual_url(url: str) -> None:
    manual_url = str(url or '').strip()
    if not manual_url:
        _log('manual', 'MANUAL_ITEM_URL is empty; skipping')
        return

    _log('manual', f'Processing manual URL: {manual_url}')
    try:
        _, errors, wrote = hooks.on_change({'id': 'manual'}, [{'id': 'manual', 'url': manual_url, 'title': '', 'price': ''}])
        _log('manual', f'Manual processing finished (wrote={wrote}, errors={len(errors)})')
        if errors:
            for err in errors[:3]:
                _log('manual', f'[HOOK ERROR] {err}')
            if len(errors) > 3:
                _log('manual', f'[HOOK ERROR] ... ({len(errors) - 3} more)')
    except Exception as exc:
        _log('manual', f'[ERROR] Manual URL processing failed: {exc}')


def main() -> None:
    cfg_path = os.environ.get("SITES_YAML", os.path.join(os.path.dirname(__file__), "sites.yaml"))
    cfg = (load_yaml(cfg_path) if os.path.exists(cfg_path) else {"sites": []})
    sites = cfg.get("sites", []) or []
    _log(None, f"Loaded {len(sites)} site(s) from {cfg_path}")

    cli_url = _cli_manual_url(sys.argv[1:])
    if cli_url:
        _log(None, "CLI manual URL is set; running manual single-item mode")
        run_manual_url(cli_url)
        return

    manual_url = os.environ.get("MANUAL_ITEM_URL", "").strip()
    if manual_url:
        _log(None, "MANUAL_ITEM_URL is set; running manual single-item mode")
        run_manual_url(manual_url)
        return

    discord_env_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if discord_env_url:
        _log(None, "Using Discord webhook from environment/secret")
    else:
        _log(None, "No global Discord webhook configured")

    total = len(sites)
    for index, site in enumerate(sites, 1):
        site_id = str(site.get("id", "")) or f"site_{index}"
        _log(None, f"Processing site {index}/{total}: {site_id}")
        run_site(site, discord_env_url)

    if total == 0:
        _log(None, "No sites configured; nothing to do")
    else:
        _log(None, "All sites processed")


if __name__ == "__main__":
    main()




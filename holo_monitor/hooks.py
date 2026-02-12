from __future__ import annotations
from typing import List, Dict, Tuple, Any
import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs
import mimetypes
import requests
from .sheets import append_payloads
from .scrape_utils import g, title_to_key
def _is_holoshop_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ''
    except Exception:
        return False
    return host.lower().endswith('shop.hololivepro.com')
def _normalize_price_digits(value: str) -> str:
    if not value:
        return ''
    cleaned = str(value).strip()
    cleaned = re.sub('[ ,\uFF0C\u3001\s\u00A0]', '', cleaned)
    if '.' in cleaned:
        whole, frac = cleaned.split('.', 1)
        if frac.strip('0') == '':
            cleaned = whole
    digits = re.sub(r'[^0-9]', '', cleaned)
    return digits or cleaned
def _normalize_holoshop_price_value(value: str) -> str:
    if not value:
        return ''
    segments = [seg.strip() for seg in re.split('[,\uFF0C\u3001]+', str(value)) if seg.strip()]
    normalized: List[str] = []
    for seg in segments:
        parts = re.split('[:\uFF1A]', seg, 1)
        if len(parts) == 2:
            title, amount_raw = parts[0].strip(), parts[1].strip()
            digits = re.sub(r'[^0-9]', '', amount_raw) or amount_raw
            normalized.append(f"{title}:{digits}")
        else:
            normalized.append(seg)
    return ', '.join(normalized)
def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
def _now_jp() -> str:
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.now(ZoneInfo('Asia/Tokyo'))
    except Exception:
        # Fallback: UTC+9
        dt = datetime.utcnow() + timedelta(hours=9)
    # Match sheet expectation: YYYY/MM/DD HH:MM:SS
    return dt.strftime('%Y/%m/%d %H:%M:%S')
def _hash(s: str) -> str:
    return hashlib.md5(s.encode('utf-8')).hexdigest()
def _sig_value(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, (list, tuple)):
        parts = [_sig_value(v) for v in value if v is not None]
        return '|'.join([p for p in parts if p])
    return g(str(value))
PROMPT_FIELDS = (
    'Title',
    'Body',
    'PriceValue',
    'PriceCurrency',
    'PreorderStart',
    'PreorderEnd',
    'ReleaseDate',
    'ShippingDate',
)
def _build_prompt_payload(detail: Dict[str, Any]) -> Dict[str, str]:
    payload: Dict[str, str] = {}
    for key in PROMPT_FIELDS:
        value = detail.get(key) if key != 'Body' else detail.get('Body')
        if value is None:
            continue
        text_value = str(value).strip()
        if not text_value:
            continue
        payload[key] = text_value
    return payload
def _generate_body_with_gemini(detail: Dict[str, Any]) -> str | None:
    """Gemini generation moved to run_gemini.js (Google Apps Script)."""
    return None
# ======================== WP image mirror (upload) ========================
_WP_UPLOAD_CACHE: Dict[str, str] = {}
def _env(name: str) -> str:
    return str(os.getenv(name, '')).strip()
def _guess_filename(url: str, content_type: str | None) -> str:
    try:
        parsed = urlparse(url)
        path = parsed.path or 'image'
    except Exception:
        path = 'image'
    name = (path.rsplit('/', 1)[-1] or 'image').split('?', 1)[0].split('#', 1)[0]
    # animate等のリサイズPHP形式 (?image=filename.jpg) から元のファイル名を復元
    try:
        if (not name or name.endswith('.php')) and parsed and parsed.query:
            q = parse_qs(parsed.query)
            img_param = q.get('image', [])
            if img_param:
                candidate = str(img_param[0]).split('/')[-1]
                if candidate:
                    name = candidate
    except Exception:
        pass
    if not re.search(r"\.[A-Za-z0-9]{3,4}$", name or '') and content_type:
        mapping = {
            'image/jpeg': '.jpg',
            'image/jpg': '.jpg',
            'image/png': '.png',
            'image/webp': '.webp',
            'image/gif': '.gif',
        }
        ext = mapping.get(content_type.lower())
        if ext and not name.endswith(ext):
            name = (name or 'image') + ext
    return name or 'image.jpg'
def _wp_upload_image(session: requests.Session, site: str, auth: Tuple[str, str], image_url: str, referer: str) -> str | None:
    # 既にWP配下のURLならそのまま返す
    if image_url.startswith(site.rstrip('/') + '/'):
        return image_url
    if image_url in _WP_UPLOAD_CACHE:
        return _WP_UPLOAD_CACHE.get(image_url)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
        'Referer': referer or (site.rstrip('/') + '/'),
    }
    try:
        # Warm-up referer to get cookies if needed
        if referer:
            try:
                session.get(referer, headers={'User-Agent': headers['User-Agent'], 'Accept': 'text/html,*/*;q=0.8'}, timeout=12, allow_redirects=True)
            except Exception:
                pass
        r = session.get(image_url, headers=headers, timeout=20, allow_redirects=True)
        if not (200 <= r.status_code < 300):
            return None
        ct = (r.headers.get('Content-Type') or '').split(';')[0].strip()
        filename = _guess_filename(image_url, ct)
        # Content-Type が無い/汎用の場合は拡張子から推測
        if not ct or ct.lower() == 'application/octet-stream':
            guessed, _ = mimetypes.guess_type(filename)
            ct = guessed or 'image/jpeg'
        files = {'file': (filename, r.content, ct)}
        media_url = site.rstrip('/') + '/wp-json/wp/v2/media'
        up = session.post(media_url, headers={'User-Agent': headers['User-Agent']}, files=files, auth=auth, timeout=30)
        if 200 <= up.status_code < 300:
            try:
                data = up.json()
            except Exception:
                return None
            src = data.get('source_url') or (data.get('guid') or {}).get('rendered')
            if src:
                _WP_UPLOAD_CACHE[image_url] = str(src)
                return str(src)
    except Exception:
        return None
    return None
def _mirror_images_to_wp(urls: List[str], referer: str) -> List[str]:
    site = _env('WP_SITE_URL')
    user = _env('WP_USER')
    app = _env('WP_APP_PASSWORD')
    if not (site and user and app):
        return urls
    s = requests.Session()
    out: List[str] = []
    for u in urls:
        wp = _wp_upload_image(s, site, (user, app), u, referer)
        out.append(wp or u)
    return out
def build_payload(url: str, detail: Dict[str, Any] | None = None) -> Dict[str, Any]:
    from .detail_scrapers import scrape_detail
    detail_data = detail if detail is not None else scrape_detail(url)
    imgs = detail_data.get('Images') or []
    imgs = [str(im).strip() for im in imgs if im]
    # Mirror images to WordPress and use returned URLs when possible
    if imgs:
        try:
            mirrored = _mirror_images_to_wp(imgs, url)
            if mirrored and any(mirrored):
                imgs = mirrored
        except Exception:
            pass
    image_field = ',\n'.join(imgs) if len(imgs) > 1 else (imgs[0] if imgs else '')
    title = detail_data.get('Title') or ''
    if not title:
        fallback_title = detail_data.get('Character')
        if fallback_title:
            title = str(fallback_title)
    body = detail_data.get('Body') or ''
    overview = detail_data.get('overview', '')
    bonus = detail_data.get('Bonus', '')
    # Manual category: use provided value as-is (names/paths). No auto-matching.
    category_value = detail_data.get('category') or detail_data.get('Category') or ''
    prompt_payload = _build_prompt_payload(detail_data)
    if 'Title' not in prompt_payload:
        prompt_payload['Title'] = title
    prompt_payload['Body'] = body
    body_source = json.dumps(prompt_payload, ensure_ascii=False)
    gemini_body = _generate_body_with_gemini(detail_data)
    final_body = gemini_body if gemini_body else body
    body_norm = _sig_value(body)[:2000]
    signature_parts = [
        _sig_value(title),
        body_norm,
        _sig_value(overview),
        _sig_value(bonus),
        _sig_value(detail_data.get('PriceValue')),
        _sig_value(detail_data.get('PriceTaxIncluded')),
        _sig_value(detail_data.get('PriceCurrency')),
        _sig_value(detail_data.get('PreorderStart')),
        _sig_value(detail_data.get('PreorderEnd')),
        _sig_value(detail_data.get('ReleaseDate')),
        _sig_value(detail_data.get('ShippingDate')),
        _sig_value(detail_data.get('Maker')),
        _sig_value(detail_data.get('Materials')),
        _sig_value(detail_data.get('Modeler')),
        _sig_value(detail_data.get('Character')),
        _sig_value(detail_data.get('Series')),
        _sig_value(detail_data.get('Tags')),
        _sig_value(detail_data.get('Copyright')),
        '|'.join(imgs),
    ]
    source_hash = _hash('|'.join(signature_parts))
    title_key = title_to_key(title)
    payload = {
        'Date': _now_jp(),
        'Title': title,
        # Use separate SourceTitle for link anchor text in WP body
        # Default to same as Title at ingestion time
        'SourceTitle': title,
        'slug': '',
        'BodySource': body_source,
        'Body': final_body,
        'Tags': detail_data.get('Tags', ''),
        'category': category_value,
        'Keyword': '',
        'AffiliateLink': '',
        'ImageURL': image_field,
        'PriceValue': detail_data.get('PriceValue', ''),
        'PriceTaxIncluded': detail_data.get('PriceTaxIncluded', ''),
        'PriceCurrency': detail_data.get('PriceCurrency', ''),
        'JAN': detail_data.get('JAN', ''),
        'TitleKey': title_key,
        'PreorderStart': detail_data.get('PreorderStart', ''),
        'PreorderEnd': detail_data.get('PreorderEnd', ''),
        'ReleaseDate': detail_data.get('ReleaseDate', ''),
        'ShippingDate': detail_data.get('ShippingDate', ''),
        'Maker': detail_data.get('Maker', ''),
        'Materials': detail_data.get('Materials', ''),
        'AgeRating': '',
        'Copyright': detail_data.get('Copyright', ''),
        'Series': detail_data.get('Series', ''),
        'Modeler': detail_data.get('Modeler', ''),
        'Character': detail_data.get('Character', ''),
        'SourceURL': url,
        'Bonus': bonus,
        'overview': overview,
        'UpdatedAt': _iso_now(),
        # WP投稿ID/URLは初期は空。ID/URLは後続の処理で設定する想定。
        # WP投稿のURLを格納（旧: WPPostID）
        'SourceHash': source_hash,
        'NeedsReview': False,
        'status': '',
    }
    return payload
def on_change(site: Dict, change_items: List[Dict]) -> Tuple[List[Dict], List[str], int]:
    site_id = site.get('id') or 'site'
    total = len(change_items)
    print(f"[HOOK] {site_id} start change processing ({total} item(s))", flush=True)
    payloads: List[Dict] = []
    errors: List[str] = []
    for index, item in enumerate(change_items, 1):
        payload = item.get('payload')
        url = item.get('url') or item.get('SourceURL') or ''
        detail_data = item.get('detail_data')
        display_target = (url or str(item.get('id') or item.get('gcode') or ''))[:160]
        print(f"[HOOK] {site_id} building payload {index}/{total}: {display_target}", flush=True)
        if payload is None:
            try:
                payload = build_payload(url, detail_data)
            except Exception as exc:
                err_msg = f"{display_target}: {exc}"
                errors.append(err_msg)
                print(f"[HOOK] {site_id} payload error: {err_msg}", flush=True)
                continue
        payloads.append(payload)
    wrote = 0
    if payloads:
        print(f"[HOOK] {site_id} appending {len(payloads)} payload(s) to sheet", flush=True)
        try:
            wrote = append_payloads(payloads)
        except Exception as exc:
            err_msg = f"sheets: {exc}"
            errors.append(err_msg)
            print(f"[HOOK] {site_id} sheet error: {err_msg}", flush=True)
    print(f"[HOOK] {site_id} processed {len(payloads)} change(s), wrote {wrote}, errors={len(errors)}", flush=True)
    return payloads, errors, wrote

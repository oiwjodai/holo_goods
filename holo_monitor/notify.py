import requests
from typing import List, Dict, Any

MAX_ITEM_LINES = 5


def _pick_str(source: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return ''


def _resolve_title_url(item: Dict[str, Any]) -> tuple[str, str]:
    payload = item.get('payload') if isinstance(item.get('payload'), dict) else None
    title = _pick_str(item, 'title', 'Title', 'name', 'Name')
    url = _pick_str(item, 'url', 'SourceURL', 'link', 'Link')
    if payload:
        title = title or _pick_str(payload, 'title', 'Title', 'name', 'Name')
        url = url or _pick_str(payload, 'url', 'SourceURL', 'link', 'Link')
    return title, url


def _infer_change_type(item: Dict[str, Any]) -> str:
    payload = item.get('payload') if isinstance(item.get('payload'), dict) else None
    candidates: List[str] = []
    for source in (item, payload):
        if not isinstance(source, dict):
            continue
        value = source.get('ChangeType') or source.get('change_type')
        if isinstance(value, str):
            candidates.append(value.lower())
    for value in candidates:
        if value in ('updated', 'update', 'modified', 'changed'):
            return 'updated'
        if value in ('new', 'added', 'created'):
            return 'new'
    for source in (item, payload):
        if isinstance(source, dict) and source.get('is_updated'):
            return 'updated'
    return 'new'


def send_discord_summary(webhook_url: str, site: Dict, payloads: List[Dict]) -> None:
    if not webhook_url or not payloads:
        return
    site_id = _pick_str(site, 'id', 'name') or 'site'
    new_count = 0
    updated_count = 0
    for item in payloads:
        if _infer_change_type(item) == 'updated':
            updated_count += 1
        else:
            new_count += 1
    lines: List[str] = []
    for item in payloads[:MAX_ITEM_LINES]:
        title, url = _resolve_title_url(item)
        if title and url:
            lines.append(f"- {title} | {url}")
        elif title:
            lines.append(f"- {title}")
        elif url:
            lines.append(f"- {url}")
    headline = f"**{site_id} updates**"
    summary = f"New: {new_count} / Updated: {updated_count}"
    content_lines = [headline, summary]
    if len(payloads) > MAX_ITEM_LINES:
        content_lines.append(f"(showing {MAX_ITEM_LINES} of {len(payloads)})")
    if lines:
        content_lines.extend(lines)
    content = '\n'.join(content_lines)
    try:
        requests.post(webhook_url, json={"content": content}, timeout=20)
    except Exception:
        pass


def send_discord_change_summary(webhook_url: str, site: Dict, payloads: List[Dict]) -> None:
    send_discord_summary(webhook_url, site, payloads)


def send_discord_items(webhook_url: str, site: Dict, items: List[Dict]) -> None:
    send_discord_summary(webhook_url, site, items)


def send_discord_error(webhook_url: str, site: Dict, errors: List[str]) -> None:
    if not webhook_url or not errors:
        return
    site_id = _pick_str(site, 'id', 'name') or 'site'
    lines = [f"**{site_id} errors**"]
    lines.extend(error.strip() for error in errors[:10] if isinstance(error, str) and error.strip())
    content = '\n'.join(lines)
    try:
        requests.post(webhook_url, json={"content": content}, timeout=20)
    except Exception:
        pass

from __future__ import annotations

import argparse
import base64
import mimetypes
import os
import re
import sys
from io import BytesIO
from typing import List, Tuple
from urllib.parse import urlparse

import requests


DEF_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def _env(name: str, required: bool = True) -> str:
    val = os.getenv(name, "").strip()
    if required and not val:
        print(f"[env] {name} is not set", file=sys.stderr)
        sys.exit(2)
    return val


def _split_urls(items: List[str]) -> List[str]:
    out: List[str] = []
    for it in items:
        if it is None:
            continue
        # split by commas or newlines
        parts = re.split(r"[\n\r,]+", str(it))
        for p in parts:
            u = p.strip()
            if u:
                out.append(u)
    # de-dup, keep order
    seen = set()
    uniq: List[str] = []
    for u in out:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq


def _guess_filename(url: str, content_type: str | None) -> str:
    # drop query/fragment
    path = urlparse(url).path or "image"
    name = os.path.basename(path) or "image"
    name = re.sub(r"[\?\#].*$", "", name)
    if not re.search(r"\.[A-Za-z0-9]{3,4}$", name) and content_type:
        ext = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }.get(content_type.lower())
        if not ext:
            ext = mimetypes.guess_extension(content_type) or ""
        if ext and not name.endswith(ext):
            name += ext
    return name


def _warmup(s: requests.Session, referer: str) -> None:
    try:
        s.get(
            referer,
            headers={
                "User-Agent": DEF_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=12,
            allow_redirects=True,
        )
    except Exception:
        pass


def upload_one(
    session: requests.Session,
    wp_site_url: str,
    auth: Tuple[str, str],
    image_url: str,
    referer: str | None,
    timeout: int = 20,
) -> Tuple[bool, str]:
    headers = {"User-Agent": DEF_UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer

    # Try fetch; if fails, warm up referer once and retry
    for attempt in (1, 2):
        try:
            resp = session.get(image_url, headers=headers, timeout=timeout, allow_redirects=True)
            code = resp.status_code
            if 200 <= code < 300:
                content_type = resp.headers.get("Content-Type", "").split(";")[0].strip() or None
                filename = _guess_filename(image_url, content_type)
                files = {
                    "file": (filename, resp.content, content_type or "application/octet-stream"),
                }
                media_url = wp_site_url.rstrip("/") + "/wp-json/wp/v2/media"
                up = session.post(
                    media_url,
                    headers={"User-Agent": DEF_UA},
                    files=files,
                    auth=auth,
                    timeout=timeout,
                )
                if 200 <= up.status_code < 300:
                    try:
                        data = up.json()
                    except Exception:
                        return False, f"upload parse error code={up.status_code} body={up.text[:200]}"
                    src = data.get("source_url") or data.get("guid", {}).get("rendered")
                    if src:
                        return True, str(src)
                    return False, f"upload ok but no source_url code={up.status_code}"
                return False, f"upload failed code={up.status_code} body={up.text[:200]}"
            else:
                err = f"fetch code={code}"
        except Exception as e:
            err = f"fetch error: {e}"

        if attempt == 1 and referer:
            _warmup(session, referer)
            continue
        return False, err


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Upload image(s) to WordPress media via REST API.")
    p.add_argument("images", nargs="*", help="Image URL(s) or comma/newline separated strings")
    p.add_argument("--referer", "-r", default=os.getenv("WP_REFERER", ""), help="Referer page URL (product page)")
    p.add_argument("--timeout", type=int, default=20, help="Timeout seconds for network calls")
    p.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = p.parse_args(argv)

    wp_site = _env("WP_SITE_URL")
    wp_user = _env("WP_USER")
    wp_pass = _env("WP_APP_PASSWORD")

    urls = _split_urls(args.images)
    if not urls:
        print("No image URLs provided.", file=sys.stderr)
        return 2

    referer = args.referer.strip() or None
    s = requests.Session()
    if referer:
        _warmup(s, referer)

    ok_count = 0
    results: List[str] = []
    for u in urls:
        ok, info = upload_one(s, wp_site, (wp_user, wp_pass), u, referer, timeout=args.timeout)
        if ok:
            ok_count += 1
            print(f"OK\t{u}\t->\t{info}")
            results.append(info)
        else:
            print(f"NG\t{u}\t{info}", file=sys.stderr)

    if args.verbose:
        print(f"Uploaded {ok_count}/{len(urls)}", file=sys.stderr)

    # If single image, print only URL when succeeded for easy capture
    if len(urls) == 1 and ok_count == 1 and results:
        print(results[0])

    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

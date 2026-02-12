import re
import unicodedata
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup


def g(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", str(s).strip())


def abs_url(url: str, base: str) -> str:
    try:
        return urljoin(base, url)
    except Exception:
        return url or ""


def no_query(u: str) -> str:
    try:
        p = urlparse(str(u or ""))
        return p._replace(query="", fragment="").geturl()
    except Exception:
        return str(u or "")


def uniq(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        x = str(x or "")
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def text_with_breaks(el: BeautifulSoup | None) -> str:
    if el is None:
        return ""
    # Convert certain tags to line breaks, then strip tags
    html = str(el)
    html = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", "", html, flags=re.I)
    html = re.sub(r"<img[^>]*>", "", html, flags=re.I)
    html = re.sub(r"<br\s*/?\s*>", "\n", html, flags=re.I)
    html = re.sub(r"</(p|div|li|h[1-6]|section|ul|ol|table|tr|thead|tbody|tfoot)>", r"</\1>\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", html)
    lines = [g(s) for s in re.split(r"\n+", text)]
    lines = [s for s in lines if s]
    return "\n".join(lines)


def best_from_srcset(ss: str) -> str:
    try:
        parts = [p.strip() for p in str(ss or "").split(",") if p.strip()]
        best = ""
        best_w = -1
        for p in parts:
            tokens = p.split()
            if not tokens:
                continue
            url = tokens[0]
            m = re.search(r"\s(\d+)(w|x)$", p)
            w = int(m.group(1)) if m else 0
            if w > best_w:
                best_w = w
                best = url
        return best
    except Exception:
        return ""


def normalize_release_date_jp(s: str) -> str:
    s = str(s or "")
    # Convert full-width digits to half-width
    def z2h_digit(c: str) -> str:
        code = ord(c)
        if 0xFF10 <= code <= 0xFF19:
            return chr(code - 0xFF10 + 0x30)
        return c
    s = "".join(z2h_digit(c) for c in s)
    s = re.sub(r"[\u3000\s]+", " ", s).strip()

    m = re.search(r"(\d{4})\s*(?:[\\/\-.]|年)\s*(\d{1,2})\s*(?:[\\/\-.]|月)\s*(\d{1,2})\s*日?", s)
    if m:
        y, mo, dd = m.group(1), int(m.group(2)), int(m.group(3))
        return f"{y}-{mo:02d}-{dd:02d}"
    m = re.search(r"(\d{4})\s*(?:[\\/\-.]|年)\s*(\d{1,2})\s*月", s)
    if m:
        y, mo = m.group(1), int(m.group(2))
        return f"{y}-{mo:02d}"
    m = re.search(r"(\d{2})\s*年\s*(\d{1,2})\s*月", s)
    if m:
        yy, mo = int(m.group(1)), int(m.group(2))
        y = 1900 + yy if yy >= 70 else 2000 + yy
        return f"{y}-{mo:02d}"
    return g(s)


def normalize_release_date(s: str) -> str:
    s = str(s or "")
    m = re.search(r"(\d{4})\s*[\\/\-.]\s*(\d{1,2})\s*[\\/\-.]\s*(\d{1,2})", s)
    if m:
        y, mo, dd = m.group(1), int(m.group(2)), int(m.group(3))
        return f"{y}-{mo:02d}-{dd:02d}"
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", s)
    if m:
        y, mo = m.group(1), int(m.group(2))
        return f"{y}-{mo:02d}"
    return g(s)


def title_to_key(title: str) -> str:
    """Build a minimal TitleKey: "シリーズ|キャラ|バリアント".

    Rules (lightweight):
    - Normalize width/spaces
    - Drop bracketed noise: 【…】/《…》/[…]
    - Drop common noise words: 再販/予約/送料無料/限定/特典/アクションフィギュア/ノンスケール/塗装済み可動フィギュア/プラスチック製/フィギュア
    - Extract variant from parentheses if it looks like Ver/DX/色/衣装など
    - Series: pick from a small known set; fallback to first token
    - Character: remaining Japanese-ish token (brand wordsを除外)
    """
    t = g(title)
    if not t:
        return ''
    t = unicodedata.normalize('NFKC', t)
    # Remove bracketed blocks 【…】 《…》 […]
    t = re.sub(r'【[^】]*】', ' ', t)
    t = re.sub(r'《[^》]*》', ' ', t)
    t = re.sub(r'\[[^\]]*\]', ' ', t)
    # Parentheses content as candidate variant
    paren = re.findall(r'（([^）]{1,30})）', t)
    variant = ''
    for p in paren:
        ps = g(p)
        if not ps:
            continue
        if re.search(r'(?:ver(?:sion)?|dx|v2|2\.0|衣装|カラー|色|コスチューム|アウトフィット)', ps, flags=re.I):
            variant = ps
            break
        if re.search(r'(再販|予約|送料無料|限定|特典)', ps):
            continue
    # Drop all parentheses blocks fully (JP and ASCII)
    t = re.sub(r'（[^）]*）', ' ', t)
    t = re.sub(r'\([^)]*\)', ' ', t)
    # Noise words
    noise = r'(再販|予約|送料無料|限定販売|限定|特典|アクションフィギュア|ノンスケール|塗装済み可動フィギュア|プラスチック製|フィギュア)'
    t = re.sub(noise, ' ', t)
    # Normalize spaces
    t = re.sub(r'\s+', ' ', t).strip()
    # Known series
    series_list = [
        'ねんどろいど','figma','POP UP PARADE','POP UP PARADE L','POP UP PARADE XL',
        'ARTFX','ARTFX J','BISHOUJO','BISHOJO','KDcolle','POP UP PARADE XLサイズ'
    ]
    series = ''
    for s in series_list:
        if s in t:
            series = s
            break
    tokens = t.split(' ')
    if not series and tokens:
        series = tokens[0]
    # Character: remove brand words and series from tokens; pick a Japanese-ish token
    brand_drop = {'ホロライブプロダクション'}
    remain = [tok for tok in tokens if tok and tok != series and tok not in brand_drop]
    def has_cjk(s: str) -> bool:
        return bool(re.search(r'[\u3040-\u30FF\u4E00-\u9FFF]', s))
    cands = [tok for tok in remain if has_cjk(tok)] or remain
    char = cands[0] if cands else ''
    # Variant: also scan tail tokens if not found from parentheses
    if not variant:
        for tok in reversed(tokens[-4:]):
            if re.search(r'(?:ver(?:sion)?$|dx$|v2$|2\.0$|衣装|カラー|色|コスチューム|アウトフィット)', tok, flags=re.I):
                variant = tok
                break
    key = f"{series}|{char}|{variant}".strip('|')
    return key

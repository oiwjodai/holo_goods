from __future__ import annotations
import os
import re
import unicodedata
from typing import Dict, List, Tuple
import gspread
from google.oauth2.service_account import Credentials
from .creds import ensure_gcp_credentials_path, get_secret


SHEET_HEADERS = [
    'Date','Title','SourceTitle','slug','BodySource','Body','Tags','category','Keyword','AffiliateLink','ImageURL',
    'PriceValue','PriceTaxIncluded','PriceCurrency','JAN','TitleKey','PreorderStart','PreorderEnd','ReleaseDate','ShippingDate',
    'Maker','Materials','AgeRating','Copyright','Series','Modeler','Character','SourceURL','Bonus','overview',
    'UpdatedAt','WPPostID','WPPostURL','SourceHash','NeedsReview','status'
]


def _load_headers() -> List[str]:
    # Fixed header order (matches provided sample CSV)
    return list(SHEET_HEADERS)


def _get_client() -> gspread.Client:
    # Ensure we have a usable credential file (env or keyring-backed)
    cred_path = ensure_gcp_credentials_path()
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive.readonly',
    ]
    creds = Credentials.from_service_account_file(cred_path, scopes=scopes)
    return gspread.authorize(creds)


def _open_worksheet(client: gspread.Client):
    # GOOGLE_SHEETS_ID can come from ENV or keyring
    sheet_id = os.environ.get('GOOGLE_SHEETS_ID') or get_secret('GOOGLE_SHEETS_ID')
    if not sheet_id:
        raise RuntimeError('GOOGLE_SHEETS_ID is not set')
    # Resolve worksheet name with robust defaulting
    raw_name = os.environ.get('SHEETS_WORKSHEET_NAME') or ''
    raw_name = str(raw_name).strip()
    if not raw_name or raw_name in {'Sheet1', 'シート1'}:
        ws_name = 'Product'
    else:
        ws_name = raw_name
    sh = client.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(ws_name)
    except gspread.exceptions.WorksheetNotFound:
        # Create and initialize headers from CSV for brand-new sheet
        ws = sh.add_worksheet(title=ws_name, rows=1000, cols=50)
        hdrs = _load_headers()
        ws.resize(rows=max(ws.row_count, 2), cols=max(ws.col_count, len(hdrs)))
        ws.update('A1', [hdrs])
    try:
        print(f"[sheet] using worksheet: {ws.title}")
    except Exception:
        pass
    # Do not touch existing headers
    return ws


def _col_a1(col_zero_based: int) -> str:
    """Convert zero-based column index to A1 column letters (0 -> A)."""
    n = int(col_zero_based) + 1
    letters = ''
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters or 'A'


def _ensure_date_column_formats(ws, headers: List[str]) -> None:
    """Ensure date columns are formatted as yyyy-mm-dd.

    This locks display to YYYY-MM-DD for date-typed cells while leaving
    text-typed cells (e.g., YYYY-MM) unaffected.
    """
    try:
        hmap = _header_index_map(headers)
        for name in ['PreorderStart', 'PreorderEnd', 'ReleaseDate', 'ShippingDate']:
            key = _canon_key(name)
            if key in hmap:
                col = _col_a1(hmap[key])
                # Apply from row 2 downward to avoid touching header row
                rng = f"{col}2:{col}"
                try:
                    ws.format(rng, {'numberFormat': {'type': 'DATE', 'pattern': 'yyyy-mm-dd'}})
                except Exception:
                    # Non-fatal; proceed with others
                    pass
    except Exception:
        # Formatting is best-effort; ignore errors
        pass


def _canon_key(s: str) -> str:
    s = unicodedata.normalize('NFKC', (s or '')).strip().lstrip('\ufeff')
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^A-Za-z0-9_]+", "", s)
    return s.lower()


def _header_index_map(headers: List[str]) -> Dict[str, int]:
    mp: Dict[str, int] = {}
    for i, h in enumerate(headers):
        key = _canon_key(h)
        if key and key not in mp:
            mp[key] = i
    return mp


def append_payloads(payloads: List[Dict]) -> int:
    if not payloads:
        return 0
    client = _get_client()
    ws = _open_worksheet(client)
    # Prefer current sheet header row; initialize if empty
    hdrs = ws.row_values(1)
    if not hdrs:
        hdrs = list(SHEET_HEADERS)
        ws.resize(rows=max(ws.row_count, 2), cols=max(ws.col_count, len(hdrs)))
        ws.update('A1', [hdrs])
    # Build case/space-insensitive header map
    hmap = _header_index_map(hdrs)
    # Best-effort: enforce date display format for date columns
    _ensure_date_column_formats(ws, hdrs)
    try:
        print(f"[sheet] headers: {hdrs}")
        print(f"[sheet] idx(Date)={hmap.get('date','-')}, idx(SourceURL)={hmap.get('sourceurl','-')}")
    except Exception:
        pass
    rows: List[List[str]] = []
    for p in payloads:
        row = ['' for _ in range(len(hdrs))]
        for k, v in p.items():
            kk = _canon_key(k)
            if kk in hmap:
                val = str(v if v is not None else '')
                # For date-related fields: enforce cell types via input value
                # - If value is YYYY-MM (no day), force TEXT by prefixing an apostrophe
                # - If value is YYYY-MM-DD, keep as-is so USER_ENTERED parses as a date
                if kk in {'preorderstart', 'preorderend', 'releasedate', 'shippingdate'}:
                    try:
                        if re.match(r'^\d{4}-\d{2}$', val):
                            val = "'" + val
                    except Exception:
                        pass
                row[hmap[kk]] = val
        try:
            di = hmap.get('date')
            si = hmap.get('sourceurl')
            print(f"[sheet] row preview: Date@{di}={row[di] if di is not None else ''} | SourceURL@{si}={row[si] if si is not None else ''}")
        except Exception:
            pass
        rows.append(row)
    # Anchor appends to column A to avoid Google 'table range' auto-shifts
    ws.append_rows(rows, value_input_option='USER_ENTERED', table_range='A1')
    return len(rows)


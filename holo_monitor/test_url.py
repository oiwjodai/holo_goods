from __future__ import annotations
import argparse
import json
import os

from dotenv import load_dotenv

from .detail_scrapers import scrape_detail
from .hooks import build_payload
from .sheets import append_payloads
from . import notify

def main() -> None:
    try:
        load_dotenv()
    except Exception:
        pass

    ap = argparse.ArgumentParser(description='Test writing a single detail URL to Google Sheets')
    ap.add_argument('--url', required=True, help='Product detail URL to scrape and write')
    ap.add_argument('--worksheet', default=os.environ.get('SHEETS_WORKSHEET_NAME', 'Goods_Test'), help='Worksheet name to write into')
    ap.add_argument('--write', action='store_true', help='Actually write to the sheet (default: dry run)')
    ap.add_argument('--post-discord', action='store_true', help='Send a Discord item notification (uses DISCORD_WEBHOOK_URL)')
    args = ap.parse_args()

    # Override worksheet by env for the session
    if args.worksheet:
        os.environ['SHEETS_WORKSHEET_NAME'] = args.worksheet

    url = args.url
    data = scrape_detail(url)
    payload = build_payload(url, data)

    if not args.write:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print('\n[dry-run] payload printed. Use --write to append to sheet.')
        return

    wrote = append_payloads([payload])
    print(f'[write] appended {wrote} row(s) to worksheet "{args.worksheet}"')

    if args.post_discord:
        webhook = os.environ.get('DISCORD_WEBHOOK_URL', '')
        if webhook:
            site = {'id': 'test_url'}
            notify.send_discord_items(webhook, site, [payload])
            print('[discord] sent item notification.')
        else:
            print('[discord] skipped: DISCORD_WEBHOOK_URL not set')

if __name__ == '__main__':
    main()

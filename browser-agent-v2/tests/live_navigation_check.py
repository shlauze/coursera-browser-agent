"""Opt-in live check against the dedicated signed-in browser. No submissions.

Run: python -m tests.live_navigation_check (or python tests/live_navigation_check.py
with the project on PYTHONPATH). Results are written to logs/live-navigation.json.
"""
import json
import sys
import time
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from v2.cli import single_runner_lock, DATA
from v2.discovery import discover_in_progress, open_course
from v2.outline import scan_outline


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    report = {"started": time.strftime('%Y-%m-%d %H:%M:%S'), "samples": []}
    with single_runner_lock(DATA / "runner.lock"), sync_playwright() as p:
        browser = p.chromium.connect_over_cdp('http://127.0.0.1:9224', timeout=20000)
        context = browser.contexts[0]
        original = set(context.pages)
        page = context.new_page()
        try:
            began = time.monotonic()
            cards = discover_in_progress(page, on_page=lambda n,c: print(f'PAGE {n}: {c}', flush=True))
            report['queue'] = {"courses": len(cards), "pages": max(c.page_number for c in cards),
                               "seconds": round(time.monotonic()-began, 1)}
            print(json.dumps(report['queue']), flush=True)
            selected = cards[:1]
            for slug in ['convolutional-neural-networks', 'machine-learning-cyber-threat-and-anomaly-detection-mqcsa']:
                card = next((c for c in cards if c.course_slug == slug), None)
                if card and card not in selected:
                    selected.append(card)
            for card in selected:
                row = {'course': card.title, 'action': card.action}
                try:
                    opened = open_course(page, card)
                    snapshot = scan_outline(opened)
                    row.update(items=len(snapshot.items), completed=sum(i.completed for i in snapshot.items),
                               modules=dict(Counter(i.module for i in snapshot.items)),
                               first_incomplete=snapshot.first_incomplete.title if snapshot.first_incomplete else None)
                    if opened != page:
                        opened.close()
                except Exception as exc:
                    row['error'] = str(exc)
                report['samples'].append(row)
                print(json.dumps(row, ensure_ascii=True), flush=True)
        finally:
            for tab in list(context.pages):
                if tab not in original:
                    tab.close()
            Path('logs/live-navigation.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()

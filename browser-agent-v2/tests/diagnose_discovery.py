"""Read-only queue diagnostic: uses an already-running CDP browser."""

from __future__ import annotations

import sys
import os

from playwright.sync_api import sync_playwright

from v2.browser import MY_LEARNING_URL
from v2.discovery import _CARDS_JS, _PAGINATION_JS, _load_all_visible, _wait_for_queue


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9223")
        page = next((p for p in browser.contexts[0].pages if p.url.startswith("https://www.coursera.org/my-learning")), None)
        if page is None:
            page = browser.contexts[0].new_page()
        if not os.environ.get("COURSE_DIAG_CURRENT"):
            page.goto(MY_LEARNING_URL, wait_until="domcontentloaded")
        _wait_for_queue(page)
        for number in range(1, 12):
            _load_all_visible(page)
            cards = page.evaluate(_CARDS_JS)
            state = page.evaluate(_PAGINATION_JS)
            print(f"PAGE {number}: cards={len(cards)} paginator={state}")
            for row in cards:
                print({key: row.get(key) for key in ("title", "group_title", "action", "progress_percent", "action_index")})
                if len(row.get("title") or "") < 3:
                    print("  short-title context:", repr(row.get("excerpt", "")[:180]))
            if os.environ.get("COURSE_DIAG_STRUCTURE") and number == 1:
                structure = page.evaluate("""() => [0,3,8].map(index => {
                  let e=document.querySelector('[data-v2-course-action="'+index+'"]');
                  if (!e) return {index, parents:[]};
                  let parents=[];
                  for(let depth=0;e&&depth<8;e=e.parentElement,depth++) {
                    parents.push({depth,tag:e.tagName,cls:String(e.className).slice(0,80),
                      text:(e.innerText||'').split('\\n').map(x=>x.trim()).filter(Boolean).slice(0,8),
                      headings:[...e.querySelectorAll('h1,h2,h3,h4')].map(x=>x.innerText.trim()).slice(0,5)});
                  }
                  return {index,parents};
                })""")
                for sample in structure:
                    print("STRUCTURE", sample)
            if not state.get("has_next"):
                break
            page.locator('[data-v2-pagination-next="true"]').click()
            page.wait_for_timeout(900)
        # Do not call browser.close(): it is the user's existing Chrome.


if __name__ == "__main__":
    main()

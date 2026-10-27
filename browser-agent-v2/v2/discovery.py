"""Read the complete My Learning queue and open an identified course.

The page is re-read immediately before a click. A changed or ambiguous card
raises ``DiscoveryError`` so the runner can refresh the queue, rather than
clicking an unrelated course after a Coursera rerender.
"""

from __future__ import annotations
from .recovery import read_retry

import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .browser import MY_LEARNING_URL


class DiscoveryError(RuntimeError):
    """The My Learning queue cannot be interpreted safely."""


@dataclass(frozen=True, slots=True)
class CourseCard:
    title: str
    action: str
    progress_percent: int | None
    page_number: int
    action_index: int
    group_title: str = ""
    course_href: str | None = None
    action_href: str | None = None

    @property
    def started(self) -> bool:
        return self.action.casefold() == "resume" or bool(self.progress_percent)

    @property
    def identity(self) -> tuple[str, str]:
        return (_clean(self.group_title).casefold(), _clean(self.title).casefold())

    @property
    def course_slug(self) -> str | None:
        """Stable Coursera slug, independent of card order and current item."""
        for href in (self.course_href, self.action_href):
            if not href:
                continue
            parsed = urlparse(href)
            if parsed.hostname not in {"www.coursera.org", "coursera.org"}:
                continue
            match = re.match(r"/learn/([^/]+)(?:/|$)", parsed.path)
            if match:
                return match.group(1).casefold()
        return None

    @property
    def stable_key(self) -> str:
        return self.course_slug or "::".join(self.identity)


def _clean(value: str) -> str:
    return " ".join(value.split())


def _valid_course_title(value: str) -> bool:
    value = _clean(value)
    return len(value) >= 4 and any(char.isalnum() for char in value) and value.casefold() not in {
        "course", "coursera", "get started", "resume", "courses"
    }


_CARDS_JS = r"""() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const nameOf = e => norm(e.innerText || e.textContent || e.getAttribute('aria-label'));
  const visible = e => !!(e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
  const isAction = e => visible(e) && !e.disabled && /^(Resume|Get started)$/i.test(nameOf(e));
  document.querySelectorAll('[data-v2-course-action]').forEach(e => e.removeAttribute('data-v2-course-action'));
  const actions = [...document.querySelectorAll('a,button')].filter(isAction);
  const countActions = e => [...e.querySelectorAll('a,button')].filter(isAction).length;
  return actions.map((action, actionIndex) => {
    action.setAttribute('data-v2-course-action', String(actionIndex));
    let best = action.closest('.rc-PathwayCardCourseItemModule, .rc-CourseCardModule');
    if (!best) {
      let root = action.parentElement, bestScore = -999;
      best = root;
      for (let depth = 0; root && root !== document.body && depth < 12; root = root.parentElement, depth++) {
        const text = norm(root.innerText);
        if (text.length > 1800) break;
        const lines = (root.innerText || '').split('\n').map(norm).filter(Boolean);
        const n = countActions(root);
        let score = (n === 1 ? 5 : -8) + (/%\s*complete/i.test(text) ? 8 : 0)
          + (/Course\s+\d+\s+of\s+\d+/i.test(text) ? 8 : 0)
          + (lines.length >= 2 ? 1 : 0) - (text.length > 800 ? 3 : 0);
        if (score > bestScore || (score === bestScore && depth > 0)) { best = root; bestScore = score; }
        if (n > 1) break;
      }
    }
    const lines = (best.innerText || '').split('\n').map(norm).filter(Boolean);
    const validTitle = s => s && s.length >= 4 && /[\p{L}\p{N}]/u.test(s)
      && !/^(Course|Coursera|Course Overview|Resume|Get started|Offered by.*|Estimated completion.*)$/i.test(s);
    const statusIndex = lines.findIndex(s => /^Course(?:\s+\d+\s+of\s+\d+)?$/i.test(s) || /\b\d+%\s*complete\b/i.test(s));
    let title = '';
    if (statusIndex > 0) {
      for (let i = statusIndex - 1; i >= Math.max(0, statusIndex - 7); i--) {
        if (validTitle(lines[i])) { title = lines[i]; break; }
      }
    }
    if (!validTitle(title)) {
      const headings = [...best.querySelectorAll('h1,h2,h3,h4,h5,a')].map(nameOf)
        .filter(s => validTitle(s) && s.length < 180);
      title = headings[0] || lines.find(s => validTitle(s) && s.length < 180) || '';
    }
    const progress = norm(best.innerText).match(/\b(\d{1,3})%\s*complete\b/i);
    const titleAnchor = [...best.querySelectorAll('a[href]')].find(a => nameOf(a) === title);
    let groupTitle = '';
    const pathway = best.closest('.rc-PathwayCardModule');
    if (pathway) {
      const h = pathway.querySelector('h1,h2,h3,h4');
      groupTitle = h ? nameOf(h) : '';
    } else {
      for (let up = best.parentElement, depth = 0; up && up !== document.body && depth < 5; up = up.parentElement, depth++) {
        if (countActions(up) > 1) {
          const h = [...up.querySelectorAll('h1,h2,h3,h4')].find(x => !best.contains(x));
          groupTitle = h ? nameOf(h) : '';
          break;
        }
      }
    }
    if (/^(Courses|My Learning|In Progress)$/i.test(groupTitle)) groupTitle = '';
    return {
      title: norm(title), action: nameOf(action), progress_percent: progress ? Number(progress[1]) : null,
      action_index: actionIndex, group_title: groupTitle,
      course_href: titleAnchor ? titleAnchor.href : null,
      action_href: action.tagName === 'A' ? action.href : null,
      excerpt: norm(best.innerText).slice(0, 350)
    };
  });
}"""


_PAGINATION_JS = r"""() => {
  document.querySelectorAll('[data-v2-pagination-next]').forEach(e => e.removeAttribute('data-v2-pagination-next'));
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const labels = [...document.querySelectorAll('*')].filter(e => {
    const own = [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ');
    return /Results per page/i.test(own);
  });
  let pagination = null;
  for (const label of labels) {
    for (let e = label.parentElement, depth = 0; e && depth < 7; e = e.parentElement, depth++) {
      const controls = [...e.querySelectorAll('button,a')].filter(x => x.getClientRects().length);
      const nums = controls.filter(x => /^\d+$/.test(norm(x.innerText || x.getAttribute('aria-label'))));
      const next = controls.some(x => /^(>|›|Next)$/i.test(norm(x.innerText)) || /next page/i.test(x.getAttribute('aria-label') || ''));
      if (nums.length >= 1 && next) { pagination = e; break; }
    }
    if (pagination) break;
  }
  if (!pagination) return {found:false, current:1, has_next:false};
  const controls = [...pagination.querySelectorAll('button,a')].filter(x => x.getClientRects().length);
  const numbers = controls.map(x => Number(norm(x.innerText))).filter(Number.isInteger).filter(x => x > 0 && x < 1000);
  const currentEl = controls.find(x => x.getAttribute('aria-current') === 'page' || x.getAttribute('aria-selected') === 'true');
  const current = currentEl && /^\d+$/.test(norm(currentEl.innerText)) ? Number(norm(currentEl.innerText)) : null;
  const next = controls.find(x => /^(>|›|Next)$/i.test(norm(x.innerText)) || /next page/i.test(x.getAttribute('aria-label') || ''));
  if (next) next.setAttribute('data-v2-pagination-next', 'true');
  const disabled = !next || next.disabled || next.getAttribute('aria-disabled') === 'true';
  return {found:true, current, has_next:!disabled, numbers, next_text:next ? norm(next.innerText) : ''};
}"""


@read_retry
def _wait_for_queue(page: Any) -> None:
    page.locator("body").wait_for(state="visible", timeout=30_000)
    if "coursera.org/login" in page.url or "coursera.org/?authMode" in page.url:
        raise DiscoveryError("Coursera login is required in the dedicated browser")
    try:
        page.get_by_text("In Progress", exact=True).first.wait_for(state="visible", timeout=20_000)
    except Exception as exc:
        raise DiscoveryError(f"My Learning did not show the In Progress tab: {page.url}") from exc


def _load_all_visible(page: Any) -> None:
    # Some My Learning cards appear only after the list is scrolled.
    previous = -1
    stable = 0
    for _ in range(16):
        height = page.evaluate("() => document.documentElement.scrollHeight")
        page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(170)
        if height == previous:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        previous = height


def _read_page_cards(page: Any, number: int) -> list[CourseCard]:
    raw = page.evaluate(_CARDS_JS)
    cards: list[CourseCard] = []
    for row in raw:
        title = _clean(str(row.get("title") or ""))
        action = _clean(str(row.get("action") or ""))
        if not _valid_course_title(title):
            raise DiscoveryError(f"An actionable course on page {number} has no valid title: {row.get('excerpt')!r}")
        if action.casefold() not in {"resume", "get started"}:
            raise DiscoveryError(f"Unrecognized course action {action!r} on page {number}")
        progress = row.get("progress_percent")
        if progress is not None and not 0 <= int(progress) <= 100:
            raise DiscoveryError(f"Invalid course progress {progress!r} for {title!r}")
        cards.append(CourseCard(
            title=title,
            action=action,
            progress_percent=int(progress) if progress is not None else None,
            page_number=number,
            action_index=int(row["action_index"]),
            group_title=_clean(str(row.get("group_title") or "")),
            course_href=row.get("course_href"),
            action_href=row.get("action_href"),
        ))
    return cards


_PASSIVE_CARDS_JS = r"""() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = e => {
    if (!e.getClientRects().length) return false;
    for (let p = e; p; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (s.visibility === 'hidden' || s.display === 'none' || Number(s.opacity) === 0) return false;
    }
    return true;
  };
  const roots = [...document.querySelectorAll('.rc-PathwayCardCourseItemModule,.rc-CourseCardModule')].filter(visible);
  const signature = roots.map(root => {
    const link = [...root.querySelectorAll('a[href]')].find(a => {
      const u = new URL(a.href);
      return visible(a) && /^(www\.)?coursera\.org$/.test(u.hostname) && /^\/learn\/[^/]+\/home(?:\/|$)/.test(u.pathname);
    });
    const lines = (root.innerText || '').split('\n').map(norm);
    const statuses = [...new Set(lines.filter(s => /^(Complete|Not started)$/.test(s)))];
    return [link ? link.href : '', link ? norm(link.innerText) : '', statuses.length === 1 ? statuses[0] : ''];
  });
  // A rendered pathway can contain only completed/not-started courses with no
  // launch controls. It is still a page, but never evidence of course completion.
  const pending = [...document.querySelectorAll('[aria-busy="true"],[role="progressbar"],[data-testid*="loading"],[data-testid*="skeleton"]')].some(visible);
  const launch = [...document.querySelectorAll('a,button,[role="button"]')].some(e => visible(e) &&
    /^(Resume|Get started)$/i.test(norm(e.innerText || e.getAttribute('aria-label'))));
  return {signature, ready: roots.length > 0 && !pending && !launch &&
    signature.every(([href,title,status]) => href && title.length >= 4 && status)};
}"""


def _read_passive_signature(page: Any) -> list[list[str]] | None:
    snapshot = page.evaluate(_PASSIVE_CARDS_JS)
    return snapshot["signature"] if snapshot.get("ready") else None


def _next_page(page: Any, old_cards: list[CourseCard], *, checkpoint=lambda: None) -> bool:
    state = page.evaluate(_PAGINATION_JS)
    if not state.get("found") or not state.get("has_next"):
        return False
    old_signature = [(c.identity, c.action) for c in old_cards]
    # Status text can change when controls disappear during hydration. Compare
    # course identities, not that transient status, to prove the cards changed.
    old_rendered_identity = [row[:2] for row in page.evaluate(_PASSIVE_CARDS_JS).get("signature", [])]
    previous_number = state.get("current")
    last_error = None
    for attempt in range(3):
        checkpoint()
        # A click timeout can occur AFTER Coursera accepted the click. Always
        # reconcile the page before clicking again, or a whole page is skipped.
        if attempt:
            state = page.evaluate(_PAGINATION_JS)
            if previous_number is not None and state.get("current") not in {None, previous_number}:
                raise DiscoveryError("Pagination moved ambiguously while recovering a click")
        try:
            page.locator('[data-v2-pagination-next="true"]').click(timeout=10_000, no_wait_after=True)
        except PlaywrightTimeout as exc:
            last_error = exc
        deadline = time.monotonic() + 45
        pagination = state
        signature = old_signature
        stable_signature = None
        stable = 0
        passive_since = None
        while time.monotonic() < deadline:
            checkpoint()
            page.wait_for_timeout(300)
            pagination = page.evaluate(_PAGINATION_JS)
            current = _read_page_cards(page, 0)
            if not pagination.get("found"):
                stable_signature, stable, passive_since = None, 0, None
                continue
            signature = [(c.identity, c.action) for c in current]
            advanced = previous_number is not None and pagination.get("current") == previous_number + 1
            changed = signature != old_signature
            if not current:
                passive = _read_passive_signature(page) if advanced else None
                if passive and [row[:2] for row in passive] != old_rendered_identity:
                    key = ("passive", passive)
                    if key != stable_signature:
                        stable_signature, passive_since = key, time.monotonic()
                    elif passive_since is not None and time.monotonic() - passive_since >= 1.2:
                        return True
                    stable = 0
                    continue
                stable_signature, stable, passive_since = None, 0, None
            elif changed and (advanced or pagination.get("current") is None):
                passive_since = None
                stable = stable + 1 if signature == stable_signature else 1
                stable_signature = signature
                if stable >= 2:
                    return True
            else:
                stable_signature, stable, passive_since = None, 0, None
        # Only repeat a click if the old page and old cards are still present.
        if pagination.get("current") != previous_number or signature != old_signature:
            raise DiscoveryError("Pagination changed but its course list did not settle")
    raise DiscoveryError("The pagination next control did not change the course list after 3 attempts") from last_error


def discover_in_progress(page: Any, *, max_pages: int = 50, checkpoint=lambda: None, on_page=None, first_page_only=False) -> list[CourseCard]:
    """Return all actionable courses in visual order, reading every page.

    ``Resume`` cards are sorted before ``Get started`` cards, preserving their
    order within each group. The page argument is a Playwright ``Page``.
    """
    page.bring_to_front()
    page.goto(MY_LEARNING_URL, wait_until="domcontentloaded", timeout=45_000)
    _wait_for_queue(page)
    all_cards: list[CourseCard] = []
    signatures: set[tuple[tuple[tuple[str, str], str], ...]] = set()
    for number in range(1, max_pages + 1):
        checkpoint()
        _load_all_visible(page)
        cards = _read_page_cards(page, number)
        signature = tuple((c.identity, c.action) for c in cards)
        if signature in signatures and cards:
            raise DiscoveryError(f"Page {number} repeated an earlier set of course cards")
        signatures.add(signature)
        all_cards.extend(cards)
        if on_page:
            on_page(number, len(cards))
        if first_page_only:
            break
        if not _next_page(page, cards, checkpoint=checkpoint):
            break
    else:
        raise DiscoveryError(f"The queue exceeded the {max_pages}-page safety limit")
    if not all_cards:
        text = page.locator("body").inner_text(timeout=10_000)
        if not re.search(r"no.*(?:course|learning)|nothing.*progress", text, re.I):
            raise DiscoveryError("No actionable courses were found, but Coursera did not show an empty-state message")
    ordered = sorted(all_cards, key=lambda c: (c.action.casefold() != 'resume' if first_page_only else not c.started, c.page_number, c.action_index))
    unique: list[CourseCard] = []
    seen: set[str] = set()
    for card in ordered:
        key = card.stable_key
        if key in seen:
            if card.course_slug is None:
                raise DiscoveryError(f"Duplicate course title without a stable Coursera URL: {card.title!r}")
            continue  # The same enrolled course appears in two pathway cards.
        seen.add(key)
        unique.append(card)
    return unique


def _open_verified_course_home(page: Any, selected: CourseCard) -> Any:
    slug = selected.course_slug
    if not slug:
        raise DiscoveryError("Resume did not navigate and the live card has no verified course URL")
    opened = page.context.new_page()
    try:
        opened.goto(f"https://www.coursera.org/learn/{slug}/home/welcome",
                    wait_until="domcontentloaded", timeout=45_000)
        parsed = urlparse(opened.url)
        if parsed.hostname not in {"www.coursera.org", "coursera.org"} or not parsed.path.startswith(f"/learn/{slug}/"):
            raise DiscoveryError("Course home redirected outside the selected course")
        return opened
    except Exception:
        opened.close()
        raise


def open_course(page: Any, card: CourseCard) -> Any:
    """Re-find and open ``card``; return the actual page, including a popup.

    If the course disappeared or more than one card matches, raise
    ``DiscoveryError``. The runner should rediscover the queue in that case.
    """
    page.bring_to_front()
    page.goto(MY_LEARNING_URL, wait_until="domcontentloaded", timeout=45_000)
    _wait_for_queue(page)
    for number in range(1, 51):
        _load_all_visible(page)
        cards = _read_page_cards(page, number)
        matches = [c for c in cards if (c.course_slug == card.course_slug if card.course_slug
                   else c.identity == card.identity)]
        if len(matches) > 1:
            raise DiscoveryError(f"Ambiguous course card: {card.title!r}; matches={len(matches)}")
        if matches:
            selected = matches[0]
            before = set(page.context.pages)
            old_url = page.url
            try:
                page.locator(f'[data-v2-course-action="{selected.action_index}"]').click(timeout=12_000, no_wait_after=True)
            except PlaywrightTimeout:
                pass  # Reconcile popup/URL below; never blindly click twice.
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                new_pages = [p for p in page.context.pages if p not in before]
                for opened in new_pages:
                    if opened.url.startswith("https://www.coursera.org/learn/"):
                        opened.wait_for_load_state("domcontentloaded", timeout=15_000)
                        if card.course_slug and urlparse(opened.url).path.split('/')[2].casefold() != card.course_slug:
                            raise DiscoveryError("Course click opened a different course")
                        return opened
                if page.url != old_url and page.url.startswith("https://www.coursera.org/learn/"):
                    page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    return page
                page.wait_for_timeout(250)
            return _open_verified_course_home(page, selected)
        if not _next_page(page, cards):
            raise DiscoveryError(f"Course is no longer in the current queue: {card.title!r}")
    raise DiscoveryError(f"Course page {card.page_number} is no longer present")

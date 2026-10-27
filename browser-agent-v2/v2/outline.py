"""Read Coursera's live course outline and choose unconfirmed material.

Only an explicit green/completed indicator counts as completion. Missing or
ambiguous UI is reported to the runner instead of being treated as finished.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from .recovery import read_retry
from .ui import dismiss_learning_overlay, require_clear_outline
from playwright.sync_api import TimeoutError as PlaywrightTimeout


class OutlineError(RuntimeError):
    """The course outline could not be read unambiguously."""


class OutlineNotSettled(OutlineError):
    retryable_read = True


@dataclass(frozen=True, slots=True)
class ItemRecord:
    title: str
    kind: str
    completed: bool
    url: str | None
    module: str
    index: int
    completion_evidence: str = ""


@dataclass(frozen=True, slots=True)
class OutlineSnapshot:
    course_title: str
    items: tuple[ItemRecord, ...]
    source_url: str

    @property
    def first_incomplete(self) -> ItemRecord | None:
        return next((item for item in self.items if not item.completed), None)


_FIND_SIDEBAR_JS = r"""() => {
  const visible = e => !!e.getClientRects().length;
  const candidates = [...document.querySelectorAll('aside,nav,[role=navigation],div')].filter(e => {
    if (!visible(e)) return false;
    const r = e.getBoundingClientRect();
    if (r.left > Math.min(500, innerWidth * .42) || r.width > Math.min(540, innerWidth * .55) || r.height < 220) return false;
    const txt = e.innerText || '';
    const links = [...e.querySelectorAll('a[href*="/learn/"]')].length;
    return /(?:Module|Week)\s*\d+|Course outline/i.test(txt) && (links >= 1 || /(?:Module|Week)\s*\d+/i.test(txt));
  });
  if (!candidates.length) return {found:false};
  candidates.sort((a,b) => {
    const hasHeader = e => !!e.querySelector('button[aria-label="Collapse course outline"]');
    if (hasHeader(a) !== hasHeader(b)) return hasHeader(a) ? -1 : 1;
    const al = a.querySelectorAll('a[href*="/learn/"]').length;
    const bl = b.querySelectorAll('a[href*="/learn/"]').length;
    if (al !== bl) return bl - al;
    return a.getBoundingClientRect().width * a.getBoundingClientRect().height
      - b.getBoundingClientRect().width * b.getBoundingClientRect().height;
  });
  document.querySelectorAll('[data-v2-outline-root]').forEach(x => x.removeAttribute('data-v2-outline-root'));
  candidates[0].setAttribute('data-v2-outline-root','true');
  return {found:true, links:candidates[0].querySelectorAll('a[href*="/learn/"]').length};
}"""


_COLLAPSED_JS = r"""() => {
  document.querySelectorAll('[data-v2-module-toggle]').forEach(e => e.removeAttribute('data-v2-module-toggle'));
  const root = document.querySelector('[data-v2-outline-root]');
  if (!root) return 0;
  const controls = [...root.querySelectorAll('button,[role=button]')].filter(e => {
    const text = (e.innerText || e.getAttribute('aria-label') || '').replace(/\s+/g,' ').trim();
    return e.getAttribute('aria-expanded') === 'false' && /Module\s*\d+|Week\s*\d+/i.test(text);
  });
  controls.forEach((e,i) => e.setAttribute('data-v2-module-toggle',String(i)));
  return controls.length;
}"""


_ITEMS_JS = r"""() => {
  const root = document.querySelector('[data-v2-outline-root]');
  if (!root) return {items:[], title:''};
  const norm = s => (s || '').replace(/\s+/g,' ').trim();
  const coursePath = location.pathname.match(/^\/learn\/([^/]+)/);
  const slug = coursePath && coursePath[1];
  const allLinks = [...root.querySelectorAll('a[href]')].filter(a => {
    try {
      const u = new URL(a.href);
      const suffix = u.pathname.slice(('/learn/' + slug + '/').length);
      return u.hostname.endsWith('coursera.org') && u.pathname.startsWith('/learn/' + slug + '/')
        && !/^(home|grades|notes|discussionForums|messages|info)(\/|$)/i.test(suffix);
    }
    catch { return false; }
  });
  const titleCandidates = [...root.querySelectorAll('h1,h2,h3')].map(e => norm(e.innerText))
    .filter(s => s && !/^(Module|Week)\s*\d+/.test(s));
  const firstLine = norm(root.innerText.split('\n').find(s => s.trim()));
  const title = firstLine && !/^(Today's goal|Module\s*\d+|Week\s*\d+)/i.test(firstLine)
    ? firstLine : titleCandidates.find(s => !/^Today's goal/i.test(s)) || firstLine;
  const nodes = [...root.querySelectorAll('*')];
  const itemRows = [];
  let module = '';
  const green = color => {
    const m = String(color || '').match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    return !!m && Number(m[2]) > Number(m[1]) * 1.18 && Number(m[2]) > Number(m[3]) * 1.15 && Number(m[2]) >= 75;
  };
  for (const node of nodes) {
    const own = norm([...node.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' '));
    if (/^(Module|Week)\s*\d+$/i.test(own)) module = own;
    if (node.tagName !== 'A' || !allLinks.includes(node)) continue;
    let row = node.parentElement;
    for (let depth = 0; row && row !== root && depth < 4; depth++) {
      const lines = (row.innerText || '').split('\n').map(norm).filter(Boolean);
      if (lines.length >= 2) break;
      const parent = row.parentElement;
      if (!parent || parent === root || parent.querySelectorAll('a[href*="/learn/"]').length > 1) break;
      row = parent;
    }
    if (!row || row === root) row = node.parentElement;
    const lines = (row.innerText || '').split('\n').map(norm).filter(Boolean);
    const anchorLines = (node.innerText || '').split('\n').map(norm).filter(Boolean);
    // Offscreen/virtualized links may have empty innerText but a real accessible title.
    const itemTitle = anchorLines[0] || norm(node.getAttribute('aria-label')) || lines[0] || '';
    const detail = lines.filter(x => x !== itemTitle).join(' ');
    // Match the visible type line, preserving negative qualifiers. A substring
    // such as "graded" inside an ungraded/practice label is not graded proof.
    const typePattern = /^((?:(?:Practice|Optional:?|Ungraded|Non[- ]graded|Not graded)\s+)*(?:Graded Programming Assignment|Peer-graded Assignment|Graded App Item|Ungraded App Item|Graded Assignment|Graded Assessment|Graded Quiz|Graded Exam|Graded Lab|Programming Assignment|Review Your Peers|Ungraded Plugin|Discussion Prompt|Role Play|Dialogue|Video|Reading|Supplement|Assignment|Assessment|Exam|Quiz|Lab))(?=\b|Grade:|Results pending)/i;
    const kindMatch = [...lines.filter(x => x !== itemTitle), ...anchorLines.slice(1)]
      .map(line => line.match(typePattern)).find(Boolean);
    const pathKind = node.href.match(/\/learn\/[^/]+\/(lecture|supplement|quiz|assignment-submission|ungradedLab|ungradedLti|programming|coach|ungradedPlugin|ungradedWidget|discussionPrompt)\//i);
    const kinds = {lecture:'Video',supplement:'Reading',quiz:'Quiz','assignment-submission':'Assignment',ungradedlab:'Lab',ungradedlti:'Lab',programming:'Programming Assignment',coach:'Dialogue',ungradedplugin:'Ungraded Plugin',ungradedwidget:'Ungraded Plugin',discussionprompt:'Discussion Prompt'};
    const kind = kindMatch ? kindMatch[1] : pathKind ? (kinds[pathKind[1].toLowerCase()] || 'Unknown') : 'Unknown';
    const statuses = [row, ...row.querySelectorAll('[aria-label],[title],[data-testid]')]
      .map(e => norm([e.getAttribute('aria-label'),e.getAttribute('title'),e.getAttribute('data-testid')].filter(Boolean).join(' ')));
    const explicit = statuses.find(s => /\b(completed|complete|passed)\b/i.test(s) && !/not complete|incomplete/i.test(s));
    const icons = [...row.querySelectorAll('svg,i,img')];
    const anchorRect = node.getBoundingClientRect();
    const greenIcon = icons.some(icon => {
      const rect = icon.getBoundingClientRect();
      if (rect.left > anchorRect.left + 12) return false;
      const candidates = [icon, ...icon.querySelectorAll('path,circle')];
      return candidates.some(part => {
        const style = getComputedStyle(part);
        const pStyle = part.parentElement ? getComputedStyle(part.parentElement) : null;
        return green(style.color) || green(style.fill) || green(style.backgroundColor)
          || (pStyle && (green(pStyle.color) || green(pStyle.backgroundColor)));
      });
    });
    const failed = /\bFailed\b/i.test(detail);
    const completed = !failed && !!(explicit || greenIcon);
    itemRows.push({title:norm(itemTitle), kind, completed, url:node.href,
      module, completion_evidence: failed ? 'failed grade' : explicit ? 'explicit status' : greenIcon ? 'green icon' : ''});
  }
  return {title, items:itemRows};
}"""


def _find_sidebar(page: Any, *, timeout_ms: int = 25_000) -> None:
    # DOMContentLoaded precedes Coursera's React outline. Poll the rendered UI,
    # including the current accessible label used by collapsed desktop panels.
    deadline = time.monotonic() + timeout_ms / 1000
    clicked = False
    while time.monotonic() < deadline:
        dismiss_learning_overlay(page)
        if page.evaluate(_FIND_SIDEBAR_JS).get("found"):
            return
        if not clicked:
            button = page.get_by_role("button", name=re.compile(
                r"^(?:(?:Expand|Open|Show) )?course outline$", re.I))
            visible = [button.nth(i) for i in range(button.count()) if button.nth(i).is_visible()]
            if len(visible) == 1:
                visible[0].click(timeout=8_000)
                clicked = True
        page.wait_for_timeout(250)
    raise OutlineError(f"Course outline was not found on {page.url}")


def _expand_modules(page: Any) -> None:
    for _ in range(100):
        dismiss_learning_overlay(page)
        count = page.evaluate(_COLLAPSED_JS)
        if not count:
            return
        require_clear_outline(page)
        try:
            page.locator('[data-v2-module-toggle="0"]').click(timeout=8_000)
        except PlaywrightTimeout:
            # A goal celebration can mount after the pre-click check.
            if dismiss_learning_overlay(page):
                _find_sidebar(page)
                continue
            require_clear_outline(page)
            raise
        page.wait_for_timeout(500)
        _find_sidebar(page)
    raise OutlineError("More than 100 collapsed modules, or module expansion did not settle")


@read_retry
def scan_outline(page: Any) -> OutlineSnapshot:
    """Expand modules and return their items in exact page order.

    A missing or unreadable outline raises ``OutlineError``; zero extracted
    items is never considered a completed course.
    """
    if not re.match(r"https://www\.coursera\.org/learn/[^/]+", page.url):
        raise OutlineError(f"Expected a Coursera course page, got {page.url}")
    _find_sidebar(page)
    _expand_modules(page)
    # Expanded module contents arrive independently. Wait for a stable roster
    # rather than declaring the course complete from a partial React render.
    deadline = time.monotonic() + 30
    previous = None
    stable = 0
    while time.monotonic() < deadline:
        if not page.evaluate("() => !!document.querySelector('[data-v2-outline-root]')"):
            _find_sidebar(page)
            _expand_modules(page)
            previous, stable = None, 0
        data = page.evaluate(_ITEMS_JS)
        signature = [(i.get('url'), i.get('title'), i.get('completed')) for i in data.get('items', [])]
        stable = stable + 1 if signature and signature == previous else 0
        if stable >= 2:
            break
        previous = signature
        page.wait_for_timeout(250)
    else:
        raise OutlineNotSettled("Course outline did not settle after module expansion")
    raw_items = data.get("items") or []
    if not raw_items:
        raise OutlineError(f"Course outline contains no recognized items: {page.url}")
    items: list[ItemRecord] = []
    seen: set[str] = set()
    for row in raw_items:
        title = " ".join(str(row.get("title") or "").split())
        url = row.get("url")
        if not title or not url:
            raise OutlineError(f"An outline item lacks a title or URL: {row!r}")
        if url in seen:
            continue  # Duplicate UI render of the same Coursera item.
        seen.add(url)
        items.append(ItemRecord(
            title=title,
            kind=str(row.get("kind") or "Unknown"),
            completed=bool(row.get("completed")),
            url=url,
            module=str(row.get("module") or ""),
            index=len(items),
            completion_evidence=str(row.get("completion_evidence") or ""),
        ))
    return OutlineSnapshot(
        course_title=" ".join(str(data.get("title") or "").split()),
        items=tuple(items),
        source_url=page.url,
    )


def open_item(page: Any, item: ItemRecord) -> Any:
    """Re-scan the outline, verify uniqueness, and open the selected item."""
    current = scan_outline(page)
    matches = [row for row in current.items if row.title == item.title and row.url == item.url]
    if len(matches) != 1:
        raise OutlineError(f"Outline item changed before opening: {item.title!r}; matches={len(matches)}")
    target = matches[0]
    if target.completed:
        raise OutlineError("Item became complete before opening; reconcile without resubmitting")
    assert target.url is not None
    parsed = urlparse(target.url)
    if parsed.scheme != "https" or parsed.hostname != "www.coursera.org" or not parsed.path.startswith("/learn/"):
        raise OutlineError(f"Refusing to navigate to an unexpected item URL: {target.url}")
    page.goto(target.url, wait_until="domcontentloaded", timeout=45_000)
    return page

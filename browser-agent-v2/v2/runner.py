"""One-course-at-a-time queue runner with verified progress.

The runner starts at My Learning, reads every In Progress page, and resumes a
started course before starting another. Browser actions have durable intents;
an interrupted action is reconciled against the live outline on the next run.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from playwright.sync_api import Error as BrowserError

from .control import Control
from .discovery import CourseCard, DiscoveryError, discover_in_progress, open_course
from .handlers import HandlerError, handle
from .outline import ItemRecord, OutlineError, OutlineSnapshot, open_item, scan_outline
from .policy import ItemAction, decide, is_coursework
from .store import Store, _safe_event_message
from .groq_client import GroqClient, GroqError


CERTIFICATES_URL = "https://www.coursera.org/my-learning?myLearningTab=CERTIFICATES"


class RunnerAttention(RuntimeError):
    """Coursera evidence is insufficient for another automatic action."""


class StopRequested(RuntimeError):
    pass


class SkipCurrent(RuntimeError):
    pass


def _key(*parts: str) -> str:
    source = "\0".join(" ".join(part.casefold().split()) for part in parts)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]


def course_id(card: CourseCard) -> str:
    return _key(card.group_title, card.title)


def item_id(item: ItemRecord) -> str:
    return _key(item.module, str(item.index), item.title)


def _same_item(left: ItemRecord, right: ItemRecord) -> bool:
    if left.url and right.url:
        return left.url == right.url
    return item_id(left) == item_id(right)


def _pause_lecture_media(page: Any) -> None:
    if "/lecture/" not in page.url:
        return
    # Resume can land on an autoplaying lecture. Stop it while reading outline,
    # including a player that mounts after the shell has loaded.
    page.evaluate("""() => {
        if (!window.__v2SkipLecturePlayback) {
            document.addEventListener('play', event => {
                if (event.target instanceof HTMLMediaElement) event.target.pause();
            }, true);
            window.__v2SkipLecturePlayback = true;
        }
        document.querySelectorAll('video,audio').forEach(media => media.pause());
    }""")


def _certificate_matches(page: Any, course_title: str) -> bool:
    if "/account/accomplishments/verify/" not in page.url:
        return False
    try:
        body = " ".join(page.locator("body").inner_text(timeout=8_000).casefold().split())
        headings = page.locator("h1,h2").all_inner_texts()
    except Exception:
        return False
    title = " ".join(course_title.casefold().split())
    exact_heading = any(" ".join(heading.casefold().split()) == title for heading in headings)
    return exact_heading and ("completed by" in body or "successfully completed" in body)


def _wait_certificate(page: Any, course_title: str) -> bool:
    # A verify route can initially contain only the client-side loading shell.
    for attempt in range(12):
        if _certificate_matches(page, course_title):
            return True
        if attempt < 11:
            page.wait_for_timeout(500)
    return False


def _verified_certificate_url(page: Any, course_title: str) -> str | None:
    if not isinstance(page.url, str) or urlparse(page.url).hostname not in {"www.coursera.org", "coursera.org"}:
        return None
    try:
        result = _course_certificate_url(page, course_title)
    except BrowserError:
        result = None
    return result or _certificate_in_catalog(page, course_title)


_CERTIFICATE_CARDS = r"""title => {
    const norm = s => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
    const links = [...document.querySelectorAll('a[href*="/account/accomplishments/verify/"]')];
    return {signature: links.map(a => a.href).join('|'), urls: links.filter(a => {
        for (let node = a.parentElement; node && node !== document.body; node = node.parentElement) {
            if (node.querySelectorAll('a[href*="/account/accomplishments/verify/"]').length !== 1) break;
            if ((node.innerText || '').split('\n').some(line => norm(line) === norm(title))) return true;
        }
        return false;
    }).map(a => a.href)};
}"""


def _certificate_in_catalog(page: Any, course_title: str) -> str | None:
    # Own tabs only: never navigate the user's attempt or close foreign tabs.
    catalog = page.context.new_page()
    try:
        catalog.goto(CERTIFICATES_URL, wait_until="domcontentloaded", timeout=30_000)
        previous = None
        for _ in range(100):
            snapshot = None
            for poll in range(20):
                snapshot = catalog.evaluate(_CERTIFICATE_CARDS, course_title)
                if snapshot["signature"] and snapshot["signature"] != previous:
                    break
                catalog.wait_for_timeout(500)
            if not snapshot or not snapshot["signature"] or snapshot["signature"] == previous:
                return None
            for url in dict.fromkeys(snapshot["urls"]):
                parsed = urlparse(url)
                if parsed.hostname not in {"www.coursera.org", "coursera.org"} or not re.fullmatch(r"/account/accomplishments/verify/[A-Za-z0-9]+", parsed.path):
                    continue
                certificate = page.context.new_page()
                try:
                    certificate.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    if _wait_certificate(certificate, course_title):
                        return certificate.url
                finally:
                    certificate.close()
            # The last pagination belongs to courses; the first is specializations.
            next_pages = catalog.get_by_role("button", name="Go to next page", exact=True)
            if not next_pages.count() or not next_pages.last.is_enabled():
                return None
            previous = snapshot["signature"]
            next_pages.last.click(timeout=10_000)
        return None
    finally:
        catalog.close()


def _course_certificate_url(page: Any, course_title: str) -> str | None:
    try:
        result = _certificate_on_page(page, course_title)
    except BrowserError:
        result = None
    if result:
        return result
    parsed = urlparse(page.url) if isinstance(page.url, str) else None
    match = re.match(r"/learn/([^/]+)(?:/|$)", parsed.path) if parsed else None
    if not match or parsed.hostname not in {"www.coursera.org", "coursera.org"}:
        return None
    # Certificate links live on the course home, not on the last assessment.
    # Use our own tab so checking cannot interrupt an attempt or close user tabs.
    home = page.context.new_page()
    try:
        home.goto(f"https://www.coursera.org/learn/{match.group(1)}/home/welcome",
                  wait_until="domcontentloaded", timeout=30_000)
        for attempt in range(8):
            try:
                result = _certificate_on_page(home, course_title)
            except BrowserError:
                result = None  # The home route may redirect while hydrating.
            if result:
                return result
            if attempt < 7:
                home.wait_for_timeout(1000)
        return None
    finally:
        home.close()


def _certificate_on_page(page: Any, course_title: str) -> str | None:
    for tab in page.context.pages:
        if _certificate_matches(tab, course_title):
            return tab.url
    links = page.locator('a[href*="/account/accomplishments/verify/"]')
    urls = links.evaluate_all("elements => elements.map(a => a.href)")
    for url in dict.fromkeys(urls):
        tab = page.context.new_page()
        try:
            tab.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if _wait_certificate(tab, course_title):
                return tab.url
        finally:
            tab.close()
    button = page.get_by_role("button", name=re.compile(r"^View certificate page$", re.I))
    if button.count() == 1:
        before = set(page.context.pages)
        button.click(timeout=10_000)
        page.wait_for_timeout(1500)
        for tab in page.context.pages:
            if tab not in before and _certificate_matches(tab, course_title):
                return tab.url
        if _certificate_matches(page, course_title):
            return page.url
    return None


@dataclass(frozen=True)
class RunSummary:
    completed_courses: int
    completed_items: int
    remaining_courses: int
    deferred_courses: int = 0


class Runner:
    def __init__(self, store: Store, control: Control, *, certificate_checks: int = 3) -> None:
        self.store = store
        self.control = control
        self.certificate_checks = certificate_checks
        self.completed_courses = 0
        self.completed_items = 0
        self.deferred: set[str] = set()
        self.blocked_items = set()
        self.groq = GroqClient(store, self.checkpoint)

    def checkpoint(self) -> None:
        if not self.control.wait_if_paused():
            raise StopRequested("Stop requested from dashboard")
        current = self.store.get_meta("current_course_id")
        if current and (self.store.get_course(current) or {}).get("skipped"):
            raise SkipCurrent(f"Course {current} was skipped from the dashboard")

    def _cards(self, page: Any, *, first_page_only=True) -> list[CourseCard]:
        for attempt in range(3):
            self.checkpoint()
            try:
                cards = discover_in_progress(page, first_page_only=first_page_only, checkpoint=self.checkpoint, on_page=lambda number, count:
                    self.store.record_event("INFO", "QUEUE_PAGE", f"Read My Learning page {number}: {count} courses"))
                break
            except (DiscoveryError, BrowserError) as exc:
                if attempt == 2 or "login is required" in str(exc).lower() or page.is_closed():
                    raise DiscoveryError(f"Could not read My Learning: {exc}") from exc
                self.store.record_event("WARNING", "QUEUE_RETRY", f"Retrying My Learning ({attempt + 1}/2): {exc}")
                for _ in range(2 ** attempt):
                    self.checkpoint()
                    page.wait_for_timeout(1000)
        identities = [course_id(card) for card in cards]
        if len(identities) != len(set(identities)):
            repeated = [card.title for card in cards if identities.count(course_id(card)) > 1]
            raise RunnerAttention(f"My Learning has ambiguous duplicate course identities: {repeated}")
        if any("\ufffd" in card.title or len(card.title.strip()) < 3 for card in cards):
            raise RunnerAttention("My Learning contains a course card with an unreadable title")
        for position, card in enumerate(cards):
            self.store.upsert_course(
                course_id(card), card.title, url=card.course_href or card.action_href,
                progress_percent=card.progress_percent,
                status="started" if card.started else "new", order_index=position,
                page_index=card.page_number,
                details={"group": card.group_title, "action": card.action},
            )
        self.store.record_event("INFO", "QUEUE_SCANNED", f"Found {len(cards)} actionable courses across My Learning")
        return cards

    def _defer(self, cid: str, reason: str) -> None:
        reason = _safe_event_message(reason)
        self.deferred.add(cid)
        if self.store.get_course(cid):
            self.store.update_course_status(cid, "attention")
            self.store.set_meta("course_attention:" + cid, reason[:1000])
        self.store.record_event("WARNING", "COURSE_DEFERRED", reason, {"course_id": cid})

    def _reconcile(self, page: Any, cards: list[CourseCard], *, visible_only=False) -> None:
        # Old media/plugin intents are withdrawn by the new user-selected mode.
        # This is cancellation, never a claim of remote completion.
        for action in self.store.pending_actions():
            if action["action_kind"] in {
                ItemAction.VIDEO.value, ItemAction.READING.value, ItemAction.PLUGIN.value,
                ItemAction.DIALOGUE.value, ItemAction.ROLE_PLAY.value,
            }:
                self.store.resolve_action(action["id"], "cancelled", {"reason": "coursework-only mode; completion not claimed"})
                self.store.record_event("INFO", "ACTION_EXCLUDED", f"Cancelled previous {action['action_kind']} intent under coursework-only mode")
        # An ambiguous old submission blocks only its own course. Keep it in
        # the ledger, and continue the rest of the queue without resubmitting.
        for cid in dict.fromkeys(a["course_id"] for a in self.store.pending_actions()):
            if visible_only and cid not in {course_id(card) for card in cards}:
                continue
            self.checkpoint()
            try:
                self._reconcile_course(page, cards, cid)
            except (RunnerAttention, DiscoveryError, OutlineError, HandlerError, BrowserError) as exc:
                self._defer(cid, str(exc))

    def _reconcile_course(self, page: Any, cards: list[CourseCard], cid: str) -> None:
        pending = [a for a in self.store.pending_actions() if a["course_id"] == cid]
        if not pending:
            return
        by_id = {course_id(card): card for card in cards}
        opened = None
        outline = None
        for action in pending:
            if (self.store.get_course(action["course_id"]) or {}).get("skipped"):
                continue
            card = by_id.get(action["course_id"])
            if card is None:
                saved = self.store.get_course(action["course_id"])
                if saved:
                    certificate = _verified_certificate_url(page, saved["title"])
                    if certificate:
                        self.store.resolve_action(action["id"], "confirmed", {"certificate": certificate})
                        self.store.update_course_status(action["course_id"], "complete", progress_percent=100,
                                                        certificate_url=certificate)
                        self.store.record_event("INFO", "ACTION_RECONCILED", f"Exact certificate found for {saved['title']}")
                        continue
                raise RunnerAttention(f"Pending action {action['id']} has no matching In Progress course or exact certificate")
            if opened is None:
                opened = open_course(page, card)
                outline = scan_outline(opened)
            saved_url = (action.get("payload") or {}).get("url")
            item = next((entry for entry in outline.items if
                         (entry.url == saved_url if saved_url else item_id(entry) == action["item_id"])), None)
            if item is None:
                raise RunnerAttention(f"Pending action {action['id']} item disappeared from the outline")
            if item.completed:
                self.store.resolve_action(action["id"], "confirmed", {"outline": "green mark", "url": item.url, "observed_page_url": opened.url})
                self.store.record_event("INFO", "ACTION_RECONCILED", f"Previously pending item is complete: {item.title}")
            elif self._practice_excluded(item):
                # Preserve the unresolved ledger; exclusion is not a grade or cancellation.
                self.store.record_event("WARNING", "PRACTICE_PENDING_EXCLUDED",
                    f"Unresolved practice item retained for review; required work may continue: {item.title}")
                continue
            elif (action["action_kind"] in {ItemAction.READING.value, ItemAction.VIDEO.value}
                  or (action["action_kind"] in {ItemAction.ASSESSMENT.value, ItemAction.PROGRAMMING_LAB.value, ItemAction.PEER.value}
                      and self.store.get_meta(f"submission:{action['id']}") in {None, "not_submitted", "failed"})):
                self.store.resolve_action(action["id"], "failed", {"outline": "still incomplete"})
                self.store.record_event("WARNING", "SAFE_RETRY", f"Will retry unfinished {action['action_kind']}: {item.title}")
            else:
                self.blocked_items.add((cid, item.url))
                self.store.record_event("WARNING", "PENDING_ITEM_ISOLATED", f"Uncertain result retained without replay; continuing other required work: {item.title}")

        return (opened, outline) if opened is not None else None

    def _verify_item(self, page: Any, expected: ItemRecord) -> bool:
        for attempt in range(8):
            self.checkpoint()
            outline = scan_outline(page)
            matching = [item for item in outline.items if _same_item(item, expected)]
            if len(matching) != 1:
                raise RunnerAttention(f"Outline changed while verifying {expected.title!r}")
            if matching[0].completed:
                return True
            if attempt < 7:
                page.wait_for_timeout(1500)
        return False

    def _finish_course(self, page: Any, card: CourseCard, *, excluded_items: int = 0) -> None:
        for attempt in range(1, self.certificate_checks + 1):
            self.checkpoint()
            url = _verified_certificate_url(page, card.title)
            if url:
                self.store.update_course_status(course_id(card), "complete", progress_percent=100, certificate_url=url)
                self.store.record_event("INFO", "CERTIFICATE_VERIFIED", f"Certificate verified for {card.title}", {"url": url})
                self.completed_courses += 1
                return
            if attempt < self.certificate_checks:
                page.wait_for_timeout(2500)
                page.reload(wait_until="domcontentloaded", timeout=30_000)
        raise RunnerAttention(f"Requested assignments and labs are complete, but a matching certificate was not found for {card.title}; {excluded_items} other incomplete materials were excluded by run mode")

    def _watch_course_page(self, page):
        # Diagnostics never contain query parameters, tokens or page contents.
        if not hasattr(self, "_watched_pages"):
            self._watched_pages = set()
        if id(page) in self._watched_pages:
            return
        self._watched_pages.add(id(page))
        from urllib.parse import urlsplit
        def path():
            u = urlsplit(page.url)
            return u.hostname + u.path if u.hostname else u.path
        page.on("close", lambda *_: self.store.record_event("WARNING", "COURSE_TAB_CLOSED", path()))
        page.on("crash", lambda *_: self.store.record_event("ERROR", "COURSE_TAB_CRASHED", path()))
        page.on("framenavigated", lambda frame: self.store.record_event("INFO", "COURSE_TAB_NAVIGATED", path())
                if frame == page.main_frame else None)

    def _optional_excluded(self, cid, item):
        records = self.store.get_meta("verified_optional:" + cid, [])
        return any(row.get("url") == item.url and row.get("title") == item.title
                   and row.get("evidence") and row.get("authorized_skip") is True for row in records)

    def _deferred_item(self, cid, item):
        return any(row.get("url") == item.url and row.get("authorized") is True
                   for row in self.store.get_meta("deferred_items:" + cid, []))

    def _practice_excluded(self, item: ItemRecord) -> bool:
        # Only an explicit visible Practice type is safe to omit. A title alone
        # does not establish that an assessment is optional.
        return bool(self.store.get_meta("required_assessments_first", False)
                    and re.search(r"\bpractice\b", item.kind, re.I))

    def _input_blocked(self, cid, item):
        saved = self.store.get_meta("item_attention:" + item_id(item), {})
        if saved.get("course_id") != cid or saved.get("url") != item.url:
            return False
        if self.store.get_meta("retry_item:" + item_id(item), False):
            return False
        reason = saved.get("reason", "")
        base = re.sub(r"/(?:submit|give-feedback|attempt|view-feedback)/?$", "", item.url or "")
        if reason.startswith("Learner-authored essay required"):
            return not bool(self.store.get_meta("peer_source:" + base))
        if reason.startswith("Peer review needs the actual peer work") or reason.startswith("Coursera requires the project submission"):
            peer_state = self.store.get_meta("peer_state:" + base, {})
            return not self.store.get_meta("peer_source:" + base) and peer_state.get("status") not in {"submitted", "complete"}
        if reason.startswith("File assignment needs an explicitly assigned local file"):
            return not bool(self.store.get_meta("file_source:" + base))
        if reason == "Question includes visual content; text-only model cannot answer it":
            return True
        # Transient timeouts and model failures must remain eligible for rechecking.
        return False

    def _next_coursework(self, cid: str, incomplete: list[ItemRecord]) -> ItemRecord | None:
        candidates = [item for item in incomplete if not item.completed
                      and is_coursework(item.kind)
                      and not self._optional_excluded(cid, item)
                      and not self._deferred_item(cid, item)
                      and not self._input_blocked(cid, item)
                      and not self._practice_excluded(item)
                      and (cid, item.url) not in self.blocked_items]
        if self.store.get_meta("required_assessments_first", False):
            candidates.sort(key=lambda item: not bool(re.search(r"\bgraded\b", item.kind, re.I)))
        return next(iter(candidates), None)

    def _retain_item(self, cid, item, reason, outcome=None):
        self.blocked_items.add((cid, item.url))
        self.store.set_meta("item_attention:" + item_id(item), {"course_id": cid, "url": item.url,
            "title": item.title, "reason": reason, "submission_state": outcome})
        self.store.record_event("WARNING", "ITEM_REVIEW_REQUIRED",
            f"Item retained for review; continuing other required items: {item.title}")

    def _wait_for_overlay(self, page):
        from .ui import UnknownOverlay, require_clear_outline
        self.store.set_meta("confirmation_required", {
            "reason": "Ожидание закрытия окна Coursera. После его исчезновения работа продолжится автоматически.",
            "url": page.url})
        self.store.record_event("INFO", "OVERLAY_WAIT", "Waiting for overlay to disappear; manual pause/stop remain available")
        try:
            while True:
                self.checkpoint()
                try:
                    require_clear_outline(page)
                except UnknownOverlay:
                    page.wait_for_timeout(1000)
                    continue
                self.store.record_event("INFO", "OVERLAY_CLEARED", "Overlay disappeared; continuing without clicking or submitting")
                return
        finally:
            self.store.set_meta("confirmation_required", None)

    def _process_course(self, queue_page: Any, card: CourseCard) -> None:
        self.checkpoint()
        saved = self.store.get_course(course_id(card)) or {}
        if saved.get("status") == "complete" and saved.get("certificate_url"):
            self.store.record_event("INFO", "CERTIFIED_COURSE_SKIPPED",
                                    f"Previously verified certificate: {card.title}")
            return
        self.store.set_meta("current_action_id", None)
        self.store.set_meta("current_item", "")
        self.store.set_meta("current_course_id", course_id(card))
        self.store.set_meta("course_attention:" + course_id(card), "")
        self.store.update_course_status(course_id(card), "started")
        reconciled = self._reconcile_course(queue_page, [card], course_id(card))
        initial_outline = None
        if isinstance(reconciled, tuple) and len(reconciled) == 2:
            page, initial_outline = reconciled
            self.store.record_event("INFO", "COURSE_PAGE_REUSED", "Reused current reconciliation page and outline; no extra queue navigation")
        else:
            page = open_course(queue_page, card)
        self._watch_course_page(page)
        _pause_lecture_media(page)
        self.store.record_event("INFO", "COURSE_OPENED", f"Opened {card.title}")
        seen: dict[str, int] = {}
        for _ in range(500):
            self.checkpoint()
            from .ui import UnknownOverlay
            try:
                outline = initial_outline if initial_outline is not None else scan_outline(page)
                initial_outline = None
            except UnknownOverlay:
                self._wait_for_overlay(page)
                outline = scan_outline(page)
            _pause_lecture_media(page)
            incomplete = [item for item in outline.items if not item.completed]
            excluded = [item for item in incomplete if not is_coursework(item.kind) or self._optional_excluded(course_id(card), item) or self._practice_excluded(item)]
            deferred = [item for item in incomplete if (self._deferred_item(course_id(card), item) or self._input_blocked(course_id(card), item) or (course_id(card), item.url) in self.blocked_items) and not self._practice_excluded(item)]
            unfinished = self._next_coursework(course_id(card), incomplete)
            if not seen and excluded:
                self.store.record_event("INFO", "MATERIALS_EXCLUDED",
                                       f"Coursework-only mode: {len(excluded)} video/reading/optional materials excluded; no completion marks added")
            if unfinished is None:
                if deferred:
                    raise RunnerAttention("Remaining required items await learner input: " + "; ".join(item.title for item in deferred))
                self._finish_course(page, card, excluded_items=len(excluded))
                return
            key = item_id(unfinished)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 3:
                raise RunnerAttention(f"Repeatedly reached unfinished item without progress: {unfinished.title}")
            decision = decide(unfinished.kind, unfinished.title)
            if decision.action == ItemAction.PEER:
                from .peer import assignment_base
                try:
                    assignment_base(unfinished.url or "")
                except HandlerError as exc:
                    self._retain_item(course_id(card), unfinished, "Peer review route is unsupported")
                    continue
            if decision.action == ItemAction.REVIEW_REQUIRED:
                self._retain_item(course_id(card), unfinished, decision.reason)
                continue
            self.store.set_meta("current_item", unfinished.title)
            action_id = self.store.begin_action(
                course_id(card), key, decision.action.value,
                {"title": unfinished.title, "module": unfinished.module, "url": unfinished.url},
            )
            self.store.record_stage(action_id, "preparing")
            self.store.record_event("INFO", "ITEM_OPENING", f"Opening {unfinished.title} ({unfinished.kind})")
            try:
                page = open_item(page, unfinished)
                if decision.action in {ItemAction.ASSESSMENT, ItemAction.PROGRAMMING_LAB, ItemAction.PEER, ItemAction.PLUGIN}:
                    result = handle(page, decision.action, self.checkpoint,
                                    groq=self.groq, store=self.store, action_id=action_id)
                else:
                    result = handle(page, decision.action, self.checkpoint)
                if not self._verify_item(page, unfinished):
                    raise RunnerAttention(f"Coursera has not marked {unfinished.title} complete after: {result.message}")
            except (StopRequested, SkipCurrent, KeyboardInterrupt):
                raise
            except Exception as exc:
                # The click may have reached Coursera. Leave the intent pending
                # so a later run inspects the site before any retry.
                outcome = self.store.get_meta(f"submission:{action_id}")
                if decision.action in {ItemAction.ASSESSMENT, ItemAction.PROGRAMMING_LAB, ItemAction.PEER} and outcome in {"not_submitted", "failed"}:
                    self.store.resolve_action(action_id, "failed", {"submission_state": outcome})
                self.store.record_event("ERROR", "ITEM_UNCLEAR", f"{unfinished.title}: {exc}")
                from playwright.sync_api import TimeoutError as PageTimeout
                if isinstance(exc, (HandlerError, PageTimeout, GroqError)):
                    self._retain_item(course_id(card), unfinished, str(exc), outcome)
                    page.goto(unfinished.url, wait_until="domcontentloaded", timeout=45_000)
                    continue
                raise RunnerAttention(f"Result unclear for {unfinished.title}: {exc}") from exc
            self.store.resolve_action(action_id, "confirmed", {"outline": "green mark", "url": page.url})
            self.store.record_event("INFO", "ITEM_CONFIRMED", f"Green mark confirmed: {unfinished.title}")
            self.completed_items += 1
        raise RunnerAttention(f"Safety limit reached while processing {card.title}")

    def _run_focused(self, page, cards, cid):
        if not any(course_id(c) == cid for c in cards):
            cards = self._cards(page, first_page_only=False)
        card = next((c for c in cards if course_id(c) == cid), None)
        if card is None:
            raise RunnerAttention("Focused course is missing from In Progress; review before selecting another")
        while True:
            self.checkpoint()
            if (self.store.get_course(cid) or {}).get("skipped"):
                raise RunnerAttention("Focused course was skipped; another course will not be opened")
            try:
                self._process_course(page, card)
                return RunSummary(self.completed_courses, self.completed_items, 0)
            except (RunnerAttention, DiscoveryError, OutlineError, HandlerError, BrowserError) as exc:
                self.store.set_meta("focus_blocker", str(exc)[:1500])
                self.store.record_event("ERROR", "FOCUS_BLOCKED", str(exc))
                self.control.request_pause()
                self.checkpoint()
                self.store.set_meta("focus_blocker", None)
                # The next pass reconciles its durable intent before any retry.

    def run(self, browser: Any, *, scan_only: bool = False) -> RunSummary:
        self.deferred.clear()
        self.completed_courses = self.completed_items = 0
        self.control.mark_running()
        self.store.record_event("INFO", "RUN_MODE", "Tests, assignments and labs only; videos, lectures and other learning materials are excluded")
        try:
            self.store.set_meta("current_course_id", None)
            page = browser.page
            cards = self._cards(page)
            if scan_only:
                return RunSummary(0, 0, len(cards))
            focus = self.store.get_meta("focus_course_id")
            if focus:
                return self._run_focused(page, cards, focus)
            self._reconcile(page, cards[:1], visible_only=True)
            processed: set[str] = set()
            refreshed = False
            for _ in range(500):
                self.store.set_meta("current_course_id", None)
                self.checkpoint()
                eligible = [card for card in cards if not (self.store.get_course(course_id(card)) or {}).get("skipped")
                            and course_id(card) not in processed and course_id(card) not in self.deferred]
                if not eligible:
                    if not refreshed:
                        cards = self._cards(page, first_page_only=False)
                        refreshed = True
                        continue
                    remaining = sum((self.store.get_course(course_id(c)) or {}).get("status") != "complete" for c in cards)
                    return RunSummary(self.completed_courses, self.completed_items, remaining, len(self.deferred))
                # Courses last seen with only input-blocked work go after candidates
                # with potentially executable work. They remain in this run's queue.
                eligible.sort(key=lambda c: str(self.store.get_meta("course_attention:" + course_id(c), "")).split(": ",1)[-1].startswith("Remaining required items await learner input"))
                card = eligible[0]
                try:
                    self._process_course(page, card)
                except SkipCurrent:
                    self.store.record_event("WARNING", "COURSE_SKIPPED", f"Skipped current course: {card.title}")
                except (RunnerAttention, DiscoveryError, OutlineError, HandlerError, BrowserError) as exc:
                    self._defer(course_id(card), f"{card.title}: {exc}")
                processed.add(course_id(card))
            raise RunnerAttention("Queue safety limit reached")
        except RunnerAttention as exc:
            self.store.record_event("ERROR", "ATTENTION", str(exc))
            raise
        except (DiscoveryError, OutlineError) as exc:
            self.store.record_event("ERROR", "ATTENTION", str(exc))
            raise RunnerAttention(str(exc)) from exc
        finally:
            self.store.set_meta("current_course_id", None)
            self.control.mark_stopped()

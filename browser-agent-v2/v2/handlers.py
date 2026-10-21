"""Small, bounded handlers for Coursera item types.

Every handler reports what it did. The runner, not the handler, must confirm a
green outline mark before it records completion.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable

from .policy import ItemAction
from .ui import dismiss_learning_overlay


class HandlerError(RuntimeError):
    pass


@dataclass(frozen=True)
class HandlerResult:
    message: str
    attempted: bool = True


def _named_control(page, name):
    pattern = re.compile(r"^\s*" + re.escape(name) + r"\s*$", re.I)
    return page.get_by_role("button", name=pattern).or_(page.get_by_role("link", name=pattern)).locator("visible=true")


def _click_unique(page, name: str, *, optional: bool = False) -> bool:
    button = _named_control(page, name)
    if not optional:
        button.first.wait_for(state="visible", timeout=15_000)
    count = button.count()
    if count == 0 and optional:
        return False
    if count != 1:
        raise HandlerError(f"Expected one visible button '{name}', found {count}")
    button.click(timeout=10_000)
    return True


def _click_one_of(page, names: tuple[str, ...], *, optional: bool = False) -> str | None:
    deadline = time.monotonic() + (2 if optional else 15)
    while True:
        dismiss_learning_overlay(page)
        matches = []
        for name in names:
            button = _named_control(page, name)
            if button.count() == 1:
                matches.append(name)
            elif button.count() > 1:
                raise HandlerError(f"Ambiguous button: {name}")
        if matches or time.monotonic() >= deadline:
            break
        page.wait_for_timeout(250)
    if not matches and optional:
        return None
    if len(matches) != 1:
        raise HandlerError(f"Expected one of {names}; found {matches}")
    _click_unique(page, matches[0])
    return matches[0]


def handle_reading(page, checkpoint: Callable[[], None]) -> HandlerResult:
    checkpoint()
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(1200)
    checkpoint()
    clicked = _click_one_of(page, ("Mark as complete", "Mark as completed"), optional=True)
    return HandlerResult("Scrolled reading and clicked completion control." if clicked else "Scrolled reading; waiting for Coursera outline confirmation.")


def _ensure_video_playing(video, frame, checkpoint: Callable[[], None]) -> None:
    checkpoint()
    # Coursera draws controls over the video. Clicking the underlying element
    # times out, and clicking a playing player can inadvertently pause it.
    result = video.evaluate("""async element => {
        if (!element.paused || element.ended) return {ok: true};
        let timer;
        try {
            await Promise.race([
                element.play(),
                new Promise((_, reject) => {
                    timer = setTimeout(() => reject(new Error('Playback start timed out')), 5000);
                })
            ]);
            return {ok: true};
        } catch (error) {
            return {ok: false, name: error.name, message: error.message};
        } finally { clearTimeout(timer); }
    }""")
    if result["ok"]:
        return
    checkpoint()
    if result.get("name") == "NotAllowedError":
        # A real player control supplies a user gesture when autoplay is denied.
        play = frame.get_by_role("button", name=re.compile(
            r"^\s*(?:Play(?: video)?|Воспроизвести)\s*$", re.I)).locator("visible=true")
        if play.count() == 1:
            play.click(timeout=5_000)
            return
    raise HandlerError(f"Could not start video: {result.get('name')}: {result.get('message')}")


def handle_video(page, checkpoint: Callable[[], None], *, max_seconds: int = 4 * 3600, store=None) -> HandlerResult:
    video = None
    ready_deadline = time.monotonic() + 20
    while time.monotonic() < ready_deadline:
        checkpoint()
        for frame in page.frames:
            matches = frame.locator("video:visible")
            if matches.count() == 1:
                video = matches.first
                break
            if matches.count() > 1:
                raise HandlerError("Multiple video elements; cannot identify the lesson player")
        if video is not None:
            break
        page.wait_for_timeout(250)
    if video is None:
        raise HandlerError("No HTML video element found; player needs a dedicated handler")
    _ensure_video_playing(video, frame, checkpoint)
    deadline = time.monotonic() + max_seconds
    last_position, last_progress = -1, time.monotonic()
    last_report = time.monotonic() - 30
    while time.monotonic() < deadline:
        checkpoint()
        state = video.evaluate("element => ({ended:element.ended, current:element.currentTime, duration:element.duration, paused:element.paused, error:element.error?.message})")
        if state.get("error"):
            raise HandlerError(f"Video media error: {state['error']}")
        if store is not None and (state["ended"] or time.monotonic() - last_report >= 30):
            duration = state.get("duration")
            total = f"{duration:.0f}" if duration is not None else "?"
            store.record_event("INFO", "VIDEO_PROGRESS",
                               f"Video progress: {state['current']:.0f} / {total} seconds",
                               {"url": page.url, **state})
            last_report = time.monotonic()
        if state["ended"]:
            return HandlerResult("Video reached its end.")
        if state["paused"]:
            _ensure_video_playing(video, frame, checkpoint)
        if state["current"] > last_position:
            last_position, last_progress = state["current"], time.monotonic()
        elif time.monotonic() - last_progress > 120:
            raise HandlerError("Video playback stalled for 120 seconds")
        page.wait_for_timeout(1500)
    raise HandlerError(f"Video did not finish within {max_seconds} seconds")


def handle_ungraded_lab(page, checkpoint: Callable[[], None]) -> HandlerResult:
    checkpoint()
    source_url = page.url
    before = set(page.context.pages)
    launch_hrefs = set()
    for name in ("Launch lab", "Resume lab"):
        control = _named_control(page, name)
        if control.count() == 1:
            href = control.evaluate("e => e.href || e.closest('a[href]')?.href || null")
            if href:
                launch_hrefs.add(href)
    launched = _click_one_of(page, ("Launch lab", "Resume lab"))
    deadline = time.monotonic() + 45
    lab_page = None
    while time.monotonic() < deadline:
        checkpoint()
        new_pages = [other for other in page.context.pages if other not in before
                     or (other == page and other.url != source_url)
                     or (other != page and other.url in launch_hrefs)]
        if len(new_pages) > 1:
            raise HandlerError("Lab opened multiple pages; cannot identify the active one")
        if new_pages:
            lab_page = new_pages[0]
            try:
                lab_page.wait_for_load_state("domcontentloaded", timeout=4_000)
                if lab_page.locator("body").count() and lab_page.locator("body").inner_text(timeout=4_000).strip():
                    break
            except Exception:
                pass
        page.wait_for_timeout(1000)
    if lab_page is None:
        raise HandlerError(f"{launched} did not open a lab tab")
    if not lab_page.locator("body").inner_text(timeout=5_000).strip():
        raise HandlerError("Lab tab opened but did not render content")
    if lab_page == page:
        page.goto(source_url, wait_until="domcontentloaded", timeout=45_000)
    page.bring_to_front()
    checkpoint()
    _click_one_of(page, ("Mark as complete", "Mark as completed"))
    return HandlerResult("Lab loaded and Coursera completion control was clicked on the source lesson.")


def handle_conversation(page, action: ItemAction, checkpoint: Callable[[], None]) -> HandlerResult:
    role_play = action == ItemAction.ROLE_PLAY
    start_names = ("Start Role Play",) if role_play else ("Start Dialogue",)
    end_name = "End Role Play" if role_play else "End Dialogue"
    confirm_name = "Yes, end the Role Play" if role_play else "Yes, end the Dialogue"
    checkpoint()
    _click_one_of(page, start_names, optional=True)
    checkpoint()
    _click_unique(page, end_name)
    checkpoint()
    # The reason is optional. Coursera preselects its neutral default, so no
    # arbitrary feedback is fabricated on the learner's behalf.
    _click_unique(page, confirm_name)
    page.wait_for_timeout(1200)
    return HandlerResult(f"{end_name} confirmed; awaiting the green outline mark.")


def handle(page, action: ItemAction, checkpoint: Callable[[], None], *, groq=None, store=None, action_id=None) -> HandlerResult:
    if action == ItemAction.PEER:
        from .peer import handle_peer
        return handle_peer(page, checkpoint, store=store, action_id=action_id)
    if action == ItemAction.PLUGIN:
        from .plugins import handle_plugin
        return handle_plugin(page, checkpoint, store=store)
    if action == ItemAction.ASSESSMENT:
        from .assessments import handle_assessment
        return handle_assessment(page, checkpoint, groq=groq, store=store, action_id=action_id)
    if action == ItemAction.PROGRAMMING_LAB:
        from .notebooks import handle_programming_lab
        return handle_programming_lab(page, checkpoint, groq=groq, store=store, action_id=action_id)
    if action == ItemAction.READING:
        return handle_reading(page, checkpoint)
    if action == ItemAction.VIDEO:
        return handle_video(page, checkpoint, store=store)
    if action == ItemAction.UNGRADED_LAB:
        return handle_ungraded_lab(page, checkpoint)
    if action in (ItemAction.DIALOGUE, ItemAction.ROLE_PLAY):
        return handle_conversation(page, action, checkpoint)
    raise HandlerError(f"No automatic handler for {action.value}")

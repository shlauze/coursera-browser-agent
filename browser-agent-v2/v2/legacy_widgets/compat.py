"""Small Playwright adapter; no dependency on the original runner or its profile."""
from contextvars import ContextVar
import time

from ..handlers import HandlerError

StopRun = HandlerError
active = ContextVar("widget_context")


def pause(seconds):
    page, checkpoint = active.get()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        checkpoint()
        page.wait_for_timeout(min(250, max(1, (deadline - time.monotonic()) * 1000)))


class Tab:
    def __init__(self, page, frame=None):
        self.page = page
        self.frame = frame or page

    def evaluate(self, expression):
        active.get()[1]()
        return self.frame.evaluate(expression)

    def close(self):
        # Releasing a frame adapter must never close its owning course tab.
        pass


def open_frame(owner, frame_url, *, probes=40, delay=.25):
    for _ in range(probes):
        matches = [f for f in owner.page.frames if f != owner.page.main_frame and f.url == frame_url]
        if len(matches) > 1:
            raise StopRun("The lesson contains multiple matching SCORM frames")
        if len(matches) == 1:
            return Tab(owner.page, matches[0])
        pause(delay)
    raise StopRun("The exact SCORM iframe did not finish loading")

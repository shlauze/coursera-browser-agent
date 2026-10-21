"""Restore the legacy passive plugin, audited SCORM and Rise readers."""
import re
import time
from urllib.parse import urlsplit

from .handlers import HandlerError, HandlerResult
from .outline import scan_outline
from .legacy_widgets.compat import Tab, active, pause
from .legacy_widgets.plugin_flow import complete_plugin
from .legacy_widgets import scorm_flow, rise_scorm_flow, rise_best_practices


def handle_plugin(page, checkpoint, *, store=None):
    match = re.fullmatch(r"(/learn/[^/]+)/(ungradedPlugin|ungradedWidget)/([^/]+)(?:/[^/]+)?/?", urlsplit(page.url).path)
    if not match:
        raise HandlerError("Plugin route does not identify a supported lesson")
    path = urlsplit(page.url).path.rstrip('/')
    outline = scan_outline(page)
    matches = [i for i in outline.items if urlsplit(i.url).path.rstrip('/') == path]
    if len(matches) != 1:
        raise HandlerError("Plugin has no unique matching outline item")
    expected = matches[0]
    item = {"key": match[2] + ':' + match[3], "url": page.url, "title": expected.title}

    class Adapter:
        tab = Tab(page)
        course = {"url": 'https://www.coursera.org' + match[1]}

        def event(self, code, **details):
            if store:
                store.record_event("INFO", code.upper(), expected.title, details)

        def verified_item(self, _item, wait=0):
            deadline = time.monotonic() + wait
            while True:
                checkpoint()
                snapshot = scan_outline(page)
                rows = [i for i in snapshot.items if i.url == expected.url]
                if len(rows) == 1 and rows[0].completed:
                    return True
                if time.monotonic() >= deadline:
                    return False
                pause(.5)

    token = active.set((page, checkpoint))
    try:
        # The legacy generic Rise reader was routed for just one course.
        # Its own shell/content checks also identify the same widget structure
        # in other courses. Prefer audited special cases where they exist.
        generic_rise = False
        if match[2] == 'ungradedWidget' and path not in scorm_flow.AUDITED_SCORM and path != rise_best_practices.PATH:
            request = {"path": path, "heading": expected.title, "action": "inspect"}
            try:
                rise_scorm_flow._outer_checked(Adapter.tab.evaluate(
                    rise_scorm_flow._script(rise_scorm_flow.OUTER_JS, request)), request, allow_missing_mark=True)
                generic_rise = True
            except HandlerError:
                pass  # Passive overview frames use the original plugin flow.
        if generic_rise:
            rise_scorm_flow.complete_rise_scorm(Adapter(), item)
        else:
            complete_plugin(Adapter(), item)
    finally:
        active.reset(token)
    return HandlerResult("Plugin activity finished and its Coursera completion mark was verified.")

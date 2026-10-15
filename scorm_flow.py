"""Completion of one audited, static SCORM widget lesson.

The SCORM progress meter did not advance when this lesson was read. Coursera's
own Mark as completed button did confirm completion, so the widget is inspected
and read first, then Coursera's green course-outline mark is required. Other
SCORM widgets can contain exercises and must not inherit this path implicitly.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit


THREAT_MODEL_PATH = (
    "/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
    "/ungradedWidget/3z0Im/threat-model"
)
THREAT_MODEL_FRAME = (
    "https://d10o6em2qtnr4q.cloudfront.net/assets/"
    "89461d5362ad45c887c245550498b22b/index.html"
)


def is_static_scorm(path, heading):
    """Only the previously inspected single-lesson static widget is allowed."""
    return path == THREAT_MODEL_PATH and heading == "Threat model"


OUTER_JS = r"""(request => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const url = new URL(location.href);
  if (url.protocol !== 'https:' || !['coursera.org','www.coursera.org'].includes(url.hostname) ||
      url.username || url.password || (url.port && url.port !== '443') ||
      url.pathname.replace(/\/$/, '') !== request.path || url.search || url.hash)
    return {error:'The selected SCORM lesson URL changed.', url:location.href};
  const roots = [...document.querySelectorAll('#main-container')];
  const headings = [...document.querySelectorAll('h1')].map(node => clean(node.textContent));
  if (roots.length !== 1 || headings.length !== 2 ||
      headings.some(text => text !== request.heading))
    return {error:'The SCORM shell or its two matching lesson headings changed.'};
  const root = roots[0];
  const frames = [...root.querySelectorAll('iframe[data-testid="widget-iframe"]')];
  if (frames.length !== 1 || clean(frames[0].title) !== request.heading ||
      frames[0].src !== request.frame)
    return {error:'The unique audited SCORM iframe changed.'};
  const marks = [...root.querySelectorAll('button[data-testid="mark-complete"]')]
    .filter(node => /^Mark as complete(?:d)?$/i.test(clean(node.innerText || node.textContent)));
  const other = [...root.querySelectorAll('button,[role="button"]')]
    .filter(node => !node.closest('nav,[role="navigation"],aside'))
    .map(node => clean(node.innerText || node.getAttribute('aria-label') || node.textContent))
    .filter(text => /^(?:Start|Launch|Resume|Submit|Check|Validate|Save answer|Continue)(?:\s|$)/i.test(text));
  const modal = [...document.querySelectorAll('[role="dialog"][aria-modal="true"]')]
    .some(node => node.getClientRects().length > 0);
  const result = {url:location.href, ready:document.readyState,
    frame:frames[0].src, mark_count:marks.length, modal, other,
    mark_disabled:marks[0]?.disabled || marks[0]?.getAttribute('aria-disabled') === 'true'};
  if (request.action !== 'mark') return result;
  if (!request.inner_verified || document.readyState !== 'complete' || modal || other.length ||
      marks.length !== 1 || result.mark_disabled)
    return {...result,error:'No unique safe SCORM completion action is available.'};
  marks[0].scrollIntoView({block:'center'});
  marks[0].click();
  return {...result,clicked:'mark'};
})"""


INNER_JS = r"""(request => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  if (location.href !== request.frame || document.readyState !== 'complete')
    return {error:'The audited SCORM frame has not loaded at its exact URL.'};
  const frames = [...document.querySelectorAll('iframe#content-frame')];
  if (frames.length !== 1 || !frames[0].contentDocument ||
      frames[0].contentDocument.readyState !== 'complete')
    return {error:'The unique SCORM content frame is unavailable.'};
  const page = frames[0].contentDocument;
  const wraps = [...page.querySelectorAll('div.page-wrap')];
  if (wraps.length !== 1 || page.querySelectorAll('iframe').length)
    return {error:'The SCORM lesson has an unexpected content structure.'};
  const text = clean(wraps[0].innerText);
  const chromeText = clean(document.body.innerText);
  const progress = clean(document.querySelector('.nav-sidebar-header__progress-text')?.innerText);
  if (text.length < 1500 || !/\bLESSON 1 OF 1\b/i.test(chromeText) ||
      !/^\d{1,3}% COMPLETE$/i.test(progress))
    return {error:'The widget is not the audited one-lesson static SCORM content.'};
  const controls = [...document.querySelectorAll('button,[role="button"]')]
    .map(node => clean(node.innerText || node.getAttribute('aria-label') || node.textContent));
  const allowed = new Set(['SKIP TO LESSON','Open navigation menu','Open search menu','Close search menu']);
  if (controls.some(label => !allowed.has(label)) ||
      [...document.querySelectorAll('input')].some(node => node.type !== 'search') ||
      document.querySelector('textarea,select,[contenteditable="true"]') ||
      page.querySelector('button,[role="button"],input,textarea,select,form,[contenteditable="true"]'))
    return {error:'The SCORM lesson contains an interactive activity.'};
  const wrap = wraps[0];
  const max = Math.max(0, wrap.scrollHeight - wrap.clientHeight);
  if (max < 400)
    return {error:'The SCORM reading area is not the audited scrollable lesson.'};
  if (request.action === 'scroll')
    wrap.scrollTop = Math.min(max, wrap.scrollTop + Math.max(300, Math.floor(wrap.clientHeight * 0.7)));
  else if (request.action !== 'inspect')
    return {error:'Unsupported SCORM action.'};
  return {url:location.href, text_length:text.length, progress,
    lesson:'LESSON 1 OF 1', top:wrap.scrollTop, max};
})"""


def _script(script, request):
    return script + "(" + json.dumps(request, ensure_ascii=False) + ")"


def _checked(snapshot, *, frame=False):
    from coursera_start import StopRun

    if not isinstance(snapshot, dict):
        raise StopRun("The static SCORM page returned an unreadable snapshot.")
    if snapshot.get("error"):
        raise StopRun(str(snapshot["error"]))
    if frame and (snapshot.get("url") != THREAT_MODEL_FRAME or
                  snapshot.get("lesson") != "LESSON 1 OF 1"):
        raise StopRun("The static SCORM frame identity changed during reading.")
    return snapshot


async def open_scorm_frame(owner):
    """Connect to the exact OOPIF child of the current lesson without activation."""
    from coursera_start import CDP_URL, ChromeTab, StopRun, local_json

    source = urlsplit(getattr(owner, "address", ""))
    source_id = source.path.rsplit("/", 1)[-1]
    if source.scheme != "ws" or not source.path.startswith("/devtools/page/") or not source_id:
        raise StopRun("The Coursera lesson Chrome target cannot be identified.")
    targets = (await owner.call("Target.getTargets")).get("targetInfos", [])
    matches = [target for target in targets if isinstance(target, dict) and
               target.get("type") == "iframe" and target.get("parentId") == source_id and
               target.get("url") == THREAT_MODEL_FRAME and
               isinstance(target.get("targetId"), str)]
    if len(matches) != 1:
        raise StopRun("The unique audited SCORM iframe target was not found.")
    pages = await asyncio.to_thread(local_json, CDP_URL + "/json/list")
    target_id = matches[0]["targetId"]
    sockets = [page.get("webSocketDebuggerUrl") for page in pages
               if isinstance(page, dict) and page.get("id") == target_id and
               page.get("type") == "iframe" and page.get("url") == THREAT_MODEL_FRAME and
               isinstance(page.get("webSocketDebuggerUrl"), str)]
    if len(sockets) != 1:
        raise StopRun("The audited SCORM iframe has no unique debugging endpoint.")
    frame = ChromeTab(sockets[0])
    await frame.connect()
    return frame


async def complete_static_scorm(runner, item, path, heading):
    """Read the static lesson, then require Coursera's own green mark."""
    from coursera_start import StopRun

    if not is_static_scorm(path, heading):
        raise StopRun("This SCORM widget has not been audited as a static lesson.")
    if await runner.verified_item(item):
        return
    request = {"path": path, "heading": heading, "frame": THREAT_MODEL_FRAME}
    outer = _checked(await runner.tab.evaluate(_script(OUTER_JS, {**request, "action":"inspect"})))
    if (outer.get("ready") != "complete" or outer.get("frame") != THREAT_MODEL_FRAME or
            outer.get("mark_count") != 1 or outer.get("mark_disabled") or
            outer.get("modal") or outer.get("other")):
        raise StopRun("The static SCORM shell does not expose its unique completion action.")
    frame = await open_scorm_frame(runner.tab)
    try:
        prior = _checked(await frame.evaluate(_script(INNER_JS, {"frame":THREAT_MODEL_FRAME,
                                                                  "action":"inspect"})), frame=True)
        for _ in range(80):
            if prior.get("top", -1) >= prior.get("max", 0) - 2:
                break
            current = _checked(await frame.evaluate(_script(INNER_JS, {"frame":THREAT_MODEL_FRAME,
                                                                        "action":"scroll"})), frame=True)
            if (current.get("max") != prior.get("max") or
                    current.get("text_length") != prior.get("text_length") or
                    current.get("top", 0) <= prior.get("top", -1)):
                raise StopRun("The static SCORM reading area changed before it reached the end.")
            prior = current
            await asyncio.sleep(0.15)
        else:
            raise StopRun("The static SCORM reading area did not reach its end.")
        if prior.get("top", -1) < prior.get("max", 0) - 2:
            raise StopRun("The static SCORM reading area was not read to the end.")
    finally:
        await frame.close()
    marked = _checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action":"mark", "inner_verified":True})))
    if marked.get("clicked") != "mark":
        raise StopRun("Coursera did not acknowledge the static SCORM Mark action.")
    runner.event("static_scorm_mark_clicked", key=item["key"])
    if not await runner.verified_item(item, wait=20):
        raise StopRun("Coursera did not show a green completion mark for the static SCORM lesson.")

"""Completion of specifically audited, static SCORM widget lessons.

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
ADVERSARIAL_INPUTS_PATH = (
    "/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
    "/ungradedWidget/2sbQI/adversarial-inputs"
)
ADVERSARIAL_INPUTS_FRAME = (
    "https://d10o6em2qtnr4q.cloudfront.net/assets/"
    "2077bfa697e84716acc84049bd4f57fe/index.html"
)
GENERATING_EXAMPLES_PATH = (
    "/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
    "/ungradedWidget/XMBvd/generating-adversarial-examples"
)
GENERATING_EXAMPLES_FRAME = (
    "https://d10o6em2qtnr4q.cloudfront.net/assets/"
    "315d813e2a05427384c8ae600781588d/index.html"
)
GENERATING_EXAMPLES_LESSON = "aRBP_qr0B-hKiamrci4zaK5L_QybF3T3"
AUDITED_SCORM = {
    THREAT_MODEL_PATH: {"heading": "Threat model", "frame": THREAT_MODEL_FRAME,
                        "accordion": None},
    ADVERSARIAL_INPUTS_PATH: {"heading": "Adversarial inputs",
                              "frame": ADVERSARIAL_INPUTS_FRAME, "accordion": "Remarks"},
    GENERATING_EXAMPLES_PATH: {"heading": "Generating adversarial examples",
                               "frame": GENERATING_EXAMPLES_FRAME,
                               "accordion": "Remarks", "continue": "DO IT YOURSELF EXERCISE EIGHT"},
}


def is_static_scorm(path, heading):
    """Only explicitly audited URL and heading pairs are allowed."""
    return AUDITED_SCORM.get(path, {}).get("heading") == heading


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
  if (request.accordion !== null && request.accordion !== 'Remarks')
    return {error:'The SCORM accordion type is not audited.'};
  const innerControls = [...page.querySelectorAll('button,[role="button"]')];
  const remarks = request.accordion === 'Remarks'
    ? [...page.querySelectorAll('button.blocks-accordion__header')] : [];
  if (request.accordion === 'Remarks' &&
      (remarks.length !== 1 || innerControls.length !== 1 || remarks[0] !== innerControls[0] ||
       clean(remarks[0].innerText) !== 'Remarks' || remarks[0].type !== 'button' ||
       !remarks[0].closest('h2') || !remarks[0].getAttribute('aria-controls') ||
       !['true','false'].includes(remarks[0].getAttribute('aria-expanded'))))
    return {error:'The unique audited Remarks accordion is unavailable.'};
  if (request.accordion === 'Remarks' && remarks[0].getAttribute('aria-expanded') === 'true') {
    const panel = page.getElementById(remarks[0].getAttribute('aria-controls'));
    if (!panel || !panel.matches('section.blocks-accordion__content') ||
        !panel.getAttribute('aria-labelledby') || clean(panel.innerText).length < 150 ||
        panel.querySelector('button,[role="button"],input,textarea,select,form,[contenteditable="true"]'))
      return {error:'Remarks does not contain the audited static explanation.'};
  }
  const inputs = [...document.querySelectorAll('input')];
  if (controls.some(label => !allowed.has(label)) ||
      inputs.some(node => node.type !== 'search') ||
      (request.accordion === 'Remarks' &&
       (inputs.length !== 1 || inputs[0].disabled !== true)) ||
      document.querySelector('textarea,select,[contenteditable="true"]') ||
      (request.accordion === null && innerControls.length) ||
      page.querySelector('input,textarea,select,form,[contenteditable="true"]'))
    return {error:'The SCORM lesson contains an interactive activity.'};
  const wrap = wraps[0];
  const max = Math.max(0, wrap.scrollHeight - wrap.clientHeight);
  if (max < 400)
    return {error:'The SCORM reading area is not the audited scrollable lesson.'};
  if (request.action === 'expand' && request.accordion === 'Remarks' &&
      remarks[0].getAttribute('aria-expanded') === 'false')
    remarks[0].click();
  else if (request.action === 'scroll' &&
           (request.accordion === null || remarks[0].getAttribute('aria-expanded') === 'true'))
    wrap.scrollTop = Math.min(max, wrap.scrollTop + Math.max(300, Math.floor(wrap.clientHeight * 0.7)));
  else if (request.action !== 'inspect')
    return {error:'Unsupported SCORM action.'};
  return {url:location.href, text_length:text.length, progress,
    lesson:'LESSON 1 OF 1', top:wrap.scrollTop, max,
    accordion_expanded:request.accordion === 'Remarks'
      ? remarks[0].getAttribute('aria-expanded') === 'true' : null,
    clicked:request.action === 'expand' ? 'Remarks' : null};
})"""


EXERCISE_JS = r"""(request => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  if (location.href !== request.frame)
    return {error:'The audited SCORM exercise frame URL changed.'};
  if (document.readyState !== 'complete')
    return {pending:'SCORM exercise frame is loading.'};
  const base = request.frame.replace(/index\.html$/, '');
  const containers = [...document.querySelectorAll('iframe[title="container"]')];
  if (containers.length === 0 && document.querySelectorAll('iframe').length === 0)
    return {pending:'SCORM exercise container is being mounted.'};
  if (containers.length !== 1 || document.querySelectorAll('iframe').length !== 1 ||
      containers[0].src !== base + 'scorm-content/scormdriver/indexAPI.html')
    return {error:'The audited SCORM exercise container changed.'};
  if (!containers[0].contentDocument ||
      containers[0].contentDocument.readyState !== 'complete')
    return {pending:'SCORM exercise container is loading.'};
  const driver = containers[0].contentDocument;
  const frames = [...driver.querySelectorAll('iframe#content-frame')];
  if (frames.length === 0 && driver.querySelectorAll('iframe').length === 0)
    return {pending:'SCORM exercise content frame is being mounted.'};
  if (frames.length !== 1 || clean(frames[0].title) !== request.heading)
    return {error:'The audited SCORM exercise content frame changed.'};
  if (!frames[0].contentDocument || frames[0].contentDocument.readyState !== 'complete' ||
      /\/(?:blank|indexAPI)\.html$/.test(frames[0].contentDocument.URL))
    return {pending:'SCORM exercise content is loading.'};
  const page = frames[0].contentDocument;
  if (page.URL !== base + 'scorm-content/scormcontent/index.html#/lessons/' + request.lesson ||
      page.querySelectorAll('iframe').length)
    return {error:'The SCORM exercise lesson identity changed.'};
  const wraps = [...page.querySelectorAll('div.page-wrap')];
  const text = wraps.length === 1 ? clean(wraps[0].innerText) : '';
  const chromeText = clean(page.body?.innerText);
  const progress = clean(page.querySelector('.nav-sidebar-header__progress-text')?.innerText);
  if (wraps.length !== 1 || text.length < 6500 ||
      !text.includes('fgsm_attack') || !text.includes('plt.plot(epsilons, accuracies') ||
      !/\bLESSON 1 OF 1\b/i.test(chromeText) ||
      !/^\d{1,3}% COMPLETE$/i.test(progress))
    return {error:'The SCORM exercise is not the audited one-lesson reading.'};
  const remarks = [...page.querySelectorAll('button.blocks-accordion__header')];
  if (remarks.length !== 1 || clean(remarks[0].innerText) !== 'Remarks' ||
      remarks[0].type !== 'button' || !remarks[0].closest('h2') ||
      !['true','false'].includes(remarks[0].getAttribute('aria-expanded')))
    return {error:'The audited Remarks explanation changed.'};
  const panel = page.getElementById(remarks[0].getAttribute('aria-controls'));
  if (!panel || !panel.matches('section.blocks-accordion__content') ||
      clean(panel.innerText).length < 250 ||
      panel.querySelector('button,[role="button"],input,textarea,select,form,[contenteditable="true"]'))
    return {error:'Remarks is no longer a static explanation.'};
  const continues = [...page.querySelectorAll('button[data-continue-btn]')];
  const zooms = [...page.querySelectorAll('button[data-rmiz-btn-zoom]')];
  const continued = /In this eighth do it yourself exercise/i.test(text) &&
      /no solution to this exercise/i.test(text);
  const stage = continued && continues.length === 0 && zooms.length === 0
    ? 'continued' :
    !continued && continues.length === 1 && zooms.length === 2 &&
    clean(continues[0].innerText) === request.continue_label ? 'reading' : null;
  if (!stage)
    return {error:'The SCORM exercise page or continuation changed.'};
  const allowed = new Set([
    ...page.querySelectorAll('button.visually-hidden.btn-skip-to-lesson,' +
      'button.nav-control__button,button.nav-sidebar-header__search-btn,' +
      'button.nav-sidebar-search__close'), remarks[0], ...continues, ...zooms]);
  const searchForms = [...page.querySelectorAll('form')];
  if ([...page.querySelectorAll('button,[role="button"]')]
        .filter(node => node.getClientRects().length).some(node => !allowed.has(node)) ||
      page.querySelectorAll('input').length !== 1 ||
      !page.querySelector('input[type="search"][disabled]') ||
      searchForms.length !== 1 ||
      !searchForms[0].matches('form.nav-sidebar-search--disabled') ||
      searchForms[0].querySelectorAll('input').length !== 1 ||
      page.querySelector('textarea,select,[contenteditable="true"]') ||
      document.querySelector('button,input,textarea,select,form,[contenteditable="true"]') ||
      driver.querySelector('button,input,textarea,select,form,[contenteditable="true"]'))
    return {error:'The SCORM exercise contains an unhandled interactive control.'};
  const wrap = wraps[0];
  const max = Math.max(0, wrap.scrollHeight - wrap.clientHeight);
  if (max < 1000)
    return {error:'The SCORM exercise reading area changed.'};
  let clicked = null;
  if (request.action === 'expand') {
    if (remarks[0].getAttribute('aria-expanded') === 'false') {
      remarks[0].click();
      clicked = 'Remarks';
    }
  } else if (request.action === 'scroll') {
    if (remarks[0].getAttribute('aria-expanded') !== 'true')
      return {error:'Remarks must be expanded before reading.'};
    wrap.scrollTop = Math.min(max, wrap.scrollTop +
      Math.max(300, Math.floor(wrap.clientHeight * 0.7)));
  } else if (request.action === 'continue') {
    if (stage !== 'reading' || remarks[0].getAttribute('aria-expanded') !== 'true' ||
        wrap.scrollTop < max - 2)
      return {error:'The exercise reading must finish before continuation.'};
    continues[0].click();
    clicked = request.continue_label;
  } else if (request.action !== 'inspect') {
    return {error:'Unsupported SCORM exercise action.'};
  }
  return {url:location.href, lesson:'LESSON 1 OF 1', progress, stage,
    top:wrap.scrollTop, max, text_length:text.length,
    accordion_expanded:remarks[0].getAttribute('aria-expanded') === 'true', clicked};
})"""


def _script(script, request):
    return script + "(" + json.dumps(request, ensure_ascii=False) + ")"


def _checked(snapshot, *, frame_url=None):
    from coursera_start import StopRun

    if not isinstance(snapshot, dict):
        raise StopRun("The static SCORM page returned an unreadable snapshot.")
    if snapshot.get("error"):
        raise StopRun(str(snapshot["error"]))
    if frame_url and (snapshot.get("url") != frame_url or
                      snapshot.get("lesson") != "LESSON 1 OF 1"):
        raise StopRun("The static SCORM frame identity changed during reading.")
    return snapshot


async def open_scorm_frame(owner, frame_url=THREAT_MODEL_FRAME):
    """Connect to the exact OOPIF child of the current lesson without activation."""
    from coursera_start import CDP_URL, ChromeTab, StopRun, local_json

    source = urlsplit(getattr(owner, "address", ""))
    source_id = source.path.rsplit("/", 1)[-1]
    if source.scheme != "ws" or not source.path.startswith("/devtools/page/") or not source_id:
        raise StopRun("The Coursera lesson Chrome target cannot be identified.")
    targets = (await owner.call("Target.getTargets")).get("targetInfos", [])
    matches = [target for target in targets if isinstance(target, dict) and
               target.get("type") == "iframe" and target.get("parentId") == source_id and
               target.get("url") == frame_url and
               isinstance(target.get("targetId"), str)]
    if len(matches) != 1:
        raise StopRun("The unique audited SCORM iframe target was not found.")
    pages = await asyncio.to_thread(local_json, CDP_URL + "/json/list")
    target_id = matches[0]["targetId"]
    sockets = [page.get("webSocketDebuggerUrl") for page in pages
               if isinstance(page, dict) and page.get("id") == target_id and
               page.get("type") == "iframe" and page.get("url") == frame_url and
               isinstance(page.get("webSocketDebuggerUrl"), str)]
    if len(sockets) != 1:
        raise StopRun("The audited SCORM iframe has no unique debugging endpoint.")
    frame = ChromeTab(sockets[0])
    await frame.connect()
    return frame


async def _exercise_snapshot(frame, request, action):
    from coursera_start import StopRun

    for probe in range(40 if action == "inspect" else 1):
        snapshot = await frame.evaluate(_script(EXERCISE_JS, {**request, "action": action}))
        if isinstance(snapshot, dict) and snapshot.get("pending"):
            if action != "inspect":
                raise StopRun("The SCORM exercise changed while an action was attempted.")
            await asyncio.sleep(0.5)
            continue
        return _checked(snapshot, frame_url=request["frame"])
    raise StopRun("The SCORM exercise did not finish loading within 20 seconds.")


async def _read_exercise_stage(frame, request, prior, stage):
    """Scroll one stable reading screen to its end before advancing."""
    from coursera_start import StopRun

    if prior.get("stage") != stage or prior.get("accordion_expanded") is not True:
        raise StopRun("The SCORM exercise reading stage changed before it was read.")
    for _ in range(100):
        if prior.get("top", -1) >= prior.get("max", 0) - 2:
            return prior
        current = await _exercise_snapshot(frame, request, "scroll")
        if (current.get("stage") != stage or
                current.get("max") != prior.get("max") or
                current.get("text_length") != prior.get("text_length") or
                current.get("top", 0) <= prior.get("top", -1) or
                current.get("accordion_expanded") is not True):
            raise StopRun("The SCORM exercise changed while its reading area was scrolled.")
        prior = current
        await asyncio.sleep(0.1)
    raise StopRun("The SCORM exercise reading area did not reach its end.")


async def complete_exercise_scorm(runner, item, path, heading):
    """Finish the audited two-screen reading exercise and verify Coursera."""
    from coursera_start import StopRun

    if path != GENERATING_EXAMPLES_PATH or heading != "Generating adversarial examples":
        raise StopRun("This SCORM exercise has not been audited.")
    if await runner.verified_item(item):
        return
    request = {"path": path, "heading": heading, "frame": GENERATING_EXAMPLES_FRAME}
    outer = _checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "inspect"})))
    if (outer.get("ready") != "complete" or
            outer.get("frame") != GENERATING_EXAMPLES_FRAME or
            outer.get("mark_count") != 1 or outer.get("mark_disabled") or
            outer.get("modal") or outer.get("other")):
        raise StopRun("The SCORM exercise shell has no unique completion action.")
    frame = await open_scorm_frame(runner.tab, GENERATING_EXAMPLES_FRAME)
    try:
        inner = {"frame": GENERATING_EXAMPLES_FRAME, "heading": heading,
                 "lesson": GENERATING_EXAMPLES_LESSON,
                 "continue_label": "DO IT YOURSELF EXERCISE EIGHT"}
        prior = await _exercise_snapshot(frame, inner, "inspect")
        if prior.get("accordion_expanded") is not True:
            initial_length = prior.get("text_length")
            opened = await _exercise_snapshot(frame, inner, "expand")
            if opened.get("clicked") != "Remarks":
                raise StopRun("The SCORM exercise Remarks explanation did not open.")
            for _ in range(12):
                await asyncio.sleep(0.25)
                prior = await _exercise_snapshot(frame, inner, "inspect")
                if (prior.get("accordion_expanded") is True and
                        isinstance(initial_length, int) and
                        prior.get("text_length", 0) > initial_length):
                    break
            else:
                raise StopRun("SCORM exercise Remarks did not reveal its explanation.")
        if prior.get("stage") == "reading":
            prior = await _read_exercise_stage(frame, inner, prior, "reading")
            clicked = await _exercise_snapshot(frame, inner, "continue")
            if clicked.get("clicked") != inner["continue_label"]:
                raise StopRun("The SCORM exercise continuation was not acknowledged.")
            initial_length = prior.get("text_length")
            for _ in range(20):
                await asyncio.sleep(0.25)
                prior = await _exercise_snapshot(frame, inner, "inspect")
                if (prior.get("stage") == "continued" and
                        isinstance(initial_length, int) and
                        prior.get("text_length", 0) > initial_length):
                    break
            else:
                raise StopRun("The SCORM exercise did not reveal its second reading screen.")
        if prior.get("stage") != "continued":
            raise StopRun("The SCORM exercise did not reach its final reading screen.")
        prior = await _read_exercise_stage(frame, inner, prior, "continued")
        if prior.get("top", -1) < prior.get("max", 0) - 2:
            raise StopRun("The SCORM exercise final reading screen was not read to the end.")
    finally:
        await frame.close()
    marked = _checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "mark", "inner_verified": True})))
    if marked.get("clicked") != "mark":
        raise StopRun("Coursera did not acknowledge the SCORM exercise Mark action.")
    runner.event("scorm_exercise_mark_clicked", key=item["key"])
    if not await runner.verified_item(item, wait=20):
        raise StopRun("Coursera did not show a green completion mark for the SCORM exercise.")


async def complete_static_scorm(runner, item, path, heading):
    """Read the static lesson, then require Coursera's own green mark."""
    from coursera_start import StopRun

    if not is_static_scorm(path, heading):
        raise StopRun("This SCORM widget has not been audited as a static lesson.")
    if path == GENERATING_EXAMPLES_PATH:
        await complete_exercise_scorm(runner, item, path, heading)
        return
    spec = AUDITED_SCORM[path]
    frame_url = spec["frame"]
    if await runner.verified_item(item):
        return
    request = {"path": path, "heading": heading, "frame": frame_url}
    outer = _checked(await runner.tab.evaluate(_script(OUTER_JS, {**request, "action":"inspect"})))
    if (outer.get("ready") != "complete" or outer.get("frame") != frame_url or
            outer.get("mark_count") != 1 or outer.get("mark_disabled") or
            outer.get("modal") or outer.get("other")):
        raise StopRun("The static SCORM shell does not expose its unique completion action.")
    frame = await open_scorm_frame(runner.tab, frame_url)
    try:
        inner_request = {"frame": frame_url, "accordion": spec["accordion"]}
        prior = _checked(await frame.evaluate(_script(INNER_JS, {
            **inner_request, "action":"inspect"})), frame_url=frame_url)
        if spec["accordion"] == "Remarks" and prior.get("accordion_expanded") is not True:
            initial_length = prior.get("text_length")
            opened = _checked(await frame.evaluate(_script(INNER_JS, {
                **inner_request, "action":"expand"})), frame_url=frame_url)
            if opened.get("clicked") != "Remarks":
                raise StopRun("The audited Remarks accordion did not open.")
            for _ in range(12):
                await asyncio.sleep(0.25)
                prior = _checked(await frame.evaluate(_script(INNER_JS, {
                    **inner_request, "action":"inspect"})), frame_url=frame_url)
                if prior.get("accordion_expanded") is True and (
                        isinstance(initial_length, int) and
                        prior.get("text_length", 0) > initial_length):
                    break
            else:
                raise StopRun("Remarks did not reveal additional static lesson text.")
        if spec["accordion"] == "Remarks" and prior.get("accordion_expanded") is not True:
            raise StopRun("Remarks must remain open while the SCORM lesson is read.")
        for _ in range(80):
            if prior.get("top", -1) >= prior.get("max", 0) - 2:
                break
            current = _checked(await frame.evaluate(_script(INNER_JS, {
                **inner_request, "action":"scroll"})), frame_url=frame_url)
            if (current.get("max") != prior.get("max") or
                    current.get("text_length") != prior.get("text_length") or
                    current.get("top", 0) <= prior.get("top", -1) or
                    (spec["accordion"] == "Remarks" and
                     current.get("accordion_expanded") is not True)):
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

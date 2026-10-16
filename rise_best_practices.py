"""Audited one-lesson Rise scenario in Topic 4, Best practices.

This is an ungraded embedded reading activity.  Its answer list is deliberately
bound to the visible question and every visible option.  The caller must still
read the eight accordions, reach the scenario end, and verify Coursera's green
course-outline mark after using the site's completion control.
"""

from __future__ import annotations

import asyncio
import hashlib
import json


PATH = (
    "/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
    "/ungradedWidget/WBdg2/best-practices"
)
FRAME = (
    "https://d10o6em2qtnr4q.cloudfront.net/assets/"
    "493e0b39666b416ea2eb079239c8264f/index.html"
)
LESSON = "jJe393RxGFfn-n45hW9Fr1rV3xmsd2pu"

ACCORDIONS = (
    "Strict data validation and prompt engineering",
    "Harden AI models",
    "Monitor and log AI systems",
    "Cultural commitment",
    "Diverse team",
    "Training and education",
    "Frameworks and documentation",
    "Continuous development",
)

CHOICES = (
    {
        "prompt": "Hey there, long time no see! I've got a favour to ask.",
        "options": (
            "Sure Carlos, I always have time for you, especially since you helped me out with the Milton strategy last year. That was a tough one.",
        ),
        "answer": 0,
    },
    {
        "prompt": "I've just moved up to a new role as AI Security Analyst, and my new boss is a bit of a taskmaster. I need to get this AI monitoring situation right.",
        "options": ("Congrats on the new role!",),
        "answer": 0,
    },
    {
        "prompt": "So, which key principle of responsible AI should I apply when implementing an AI-powered employee performance measuring and rewarding system?",
        "options": (
            "Transparency, fairness, and accountability",
            "Avoiding oversight to reduce friction in development.",
            "Speed and efficiency of deployment above all.",
        ),
        "answer": 0,
    },
    {
        "prompt": "What would be the recommended first step in establishing AI governance within my company?",
        "options": (
            "Avoid a formal structure to stay flexible during deployment",
            "Set up a committee to evaluate and oversee AI risks and ethical considerations.",
            "Only address governance after complaints arise from staff.",
        ),
        "answer": 1,
    },
    {
        "prompt": "Is obtaining employee consent important before deploying behaviour-monitoring AI systems?",
        "options": (
            "It’s only needed if external data is being used.",
            "Well, it’s optional if the company owns the systems.",
            "It ensures compliance with data protection regulations and builds trust.",
        ),
        "answer": 2,
    },
    {
        "prompt": "Well, they're the main points I needed to cover. Are you happy with the advice you've given or would you like to revisit the scenario?",
        "options": (
            "Let's leave it there, we can pick this up again later.",
            "I want to do the scenario again.",
        ),
        "answer": 0,
    },
)

CONTINUATIONS = (
    "Sure Carlos, I always have time for you, especially since you helped me out with the Milton strategy last year. That was a tough one. How about we catch up properly for lunch next week? My shout. In the meantime, I'm in a bind. CONTINUE",
    "Congrats on the new role! Thanks, can you help confirm my approach is grounded in good ethical AI and legal AI practices and aligns with responsible AI principles? CONTINUE",
    "Transparency, fairness, and accountability AI tools in performance measurement and reward systems must be transparent and explainable, fair and unbiased across employee sub-groups. Stakeholders must comply with legal and ethical requirements. CONTINUE",
    "Set up a committee to evaluate and oversee AI risks and ethical considerations. Spot on! An AI Ethics or Governance Committee can offer structure and accountability early in the AI lifecycle, helping prevent harm rather than reacting to it. CONTINUE",
    "It ensures compliance with data protection regulations and builds trust. Exactly. Consent isn’t just a legal requirement in many jurisdictions. It also reinforces ethical practice and transparency, which helps maintain trust in the workplace. CONTINUE",
    "Let's leave it there, we can pick this up again later. You can always come back to the scenario and try it again later. CONTINUE",
)

INTRO = "AI Security scenario What should responsible AI look like? CONTINUE"
END = (
    "Scenario End Those questions really helped me solidify what responsible AI "
    "looks like in practice. It's not just about securing systems—it's about doing "
    "it in a way that respects people and protects their rights. START OVER"
)


_DATA = json.dumps({
    "path": PATH, "frame": FRAME, "lesson": LESSON,
    "accordions": ACCORDIONS, "intro": INTRO, "end": END,
    "choices": CHOICES, "continuations": CONTINUATIONS,
}, ensure_ascii=False)


SCENARIO_JS = r"""(page, wrap, request, clean) => {
  const data = __DATA__;
  const absent = {count:0,state:null,step:0,text:null,prompt:null,
    options:[],answer:null,signature:'[]',buttons:[],clicked:null};
  const blocks = [...page.querySelectorAll('.scenario-block')];
  if (request.path !== data.path) {
    if (blocks.length) return {error:'An unaudited Rise scenario is present.'};
    return absent;
  }
  const expectedURL = data.frame.replace(/index\.html$/, '') +
    'scorm-content/scormcontent/index.html#/lessons/' + data.lesson;
  if (request.frame !== data.frame || request.heading !== 'Best practices' ||
      request.frame_title !== 'Best practices' || page.URL !== expectedURL ||
      blocks.length !== 1 || !wrap.contains(blocks[0]) ||
      !blocks[0].matches('div.scenario-block[role="group"]' +
        '[aria-label="Interactive scenario"]'))
    return {error:'The audited Best practices scenario identity changed.'};
  const headings = [...page.querySelectorAll('button.blocks-accordion__header')]
    .map(button => clean(button.innerText));
  if (headings.length !== data.accordions.length ||
      headings.some((heading,index) => heading !== data.accordions[index]) ||
      !clean(wrap.innerText).includes('Select the response you believe is the most appropriate'))
    return {error:'The Best practices reading or scenario introduction changed.'};
  const block = blocks[0];
  const text = clean(block.innerText);
  const buttons = [...block.querySelectorAll('button,[role="button"]')]
    .filter(button => button.getClientRects().length > 0);
  const prompt = clean(block.querySelector('.scenario-block__dialogue')?.innerText);
  let state = null, step = 0, options = [], answer = null;
  if (text === data.intro && buttons.length === 1 &&
      buttons[0].matches('button.scenario-block__text__continue') &&
      clean(buttons[0].innerText) === 'CONTINUE') {
    state = 'intro';
  } else if (text === data.end && buttons.length === 1 &&
      buttons[0].matches('button.scenario-block__text__end') &&
      clean(buttons[0].innerText) === 'START OVER') {
    state = 'terminal'; step = data.choices.length;
  } else {
    // Rise can leave the previous dialogue in the DOM for one render while
    // replacing its response buttons with the positive feedback panel.
    const feedbackIndex = data.continuations.indexOf(text);
    const choiceIndex = data.choices.findIndex(entry => entry.prompt === prompt);
    if (feedbackIndex >= 0 && buttons.length === 1 &&
        buttons[0].matches('button.scenario-block__dialogue__button') &&
        clean(buttons[0].innerText) === 'CONTINUE') {
      state = 'continue'; step = feedbackIndex;
    } else if (choiceIndex >= 0) {
      const choice = data.choices[choiceIndex];
      options = buttons.map(button => clean(button.innerText).replace(/^\d+\s*/, ''));
      if (buttons.length !== choice.options.length ||
          buttons.some((button,index) =>
            !button.matches('button.scenario-block__response.scenario-block__response--' + index) ||
            clean(button.innerText) !== String(index + 1) + ' ' + choice.options[index]) ||
          options.some((option,index) => option !== choice.options[index]))
        return request.action === 'inspect' &&
          (buttons.length === 0 ||
           (buttons.length === 1 &&
            buttons[0].matches('button.scenario-block__dialogue__button') &&
            clean(buttons[0].innerText) === 'CONTINUE')) ?
          {pending:'The Best practices scenario is transitioning.'} :
          {error:'The Best practices scenario choice changed.'};
      state = 'choice'; step = choiceIndex; answer = choice.answer;
    }
  }
  if (!state) {
    if (!buttons.length && request.action === 'inspect' &&
        !block.querySelector('input,textarea,select,form,[contenteditable="true"]'))
      return {pending:'The Best practices scenario is transitioning.'};
    return {error:'The Best practices scenario has an unrecognized step.'};
  }
  if (block.querySelector('input,textarea,select,form,[contenteditable="true"],a[href]') ||
      buttons.some(button => button.type !== 'button' || button.disabled ||
        button.getAttribute('aria-disabled') === 'true'))
    return {error:'The Best practices scenario contains an unknown action.'};
  const signature = JSON.stringify([state,step,text,options]);
  let clicked = null;
  if (request.action === 'scenario_continue') {
    if (!['intro','continue'].includes(state) || !request.panels_read ||
        request.scenario_signature !== signature)
      return {error:'The Best practices scenario continuation changed.'};
    buttons[0].scrollIntoView({block:'center'});
    if (!buttons[0].getClientRects().length)
      return {error:'The Best practices scenario continuation is hidden.'};
    buttons[0].click(); clicked = 'scenario_continue';
  } else if (request.action === 'scenario_choice') {
    if (state !== 'choice' || !request.panels_read ||
        request.scenario_signature !== signature ||
        request.scenario_answer !== answer)
      return {error:'The Best practices scenario answer changed.'};
    const button = buttons[answer];
    button.scrollIntoView({block:'center'});
    if (!button.getClientRects().length)
      return {error:'The Best practices scenario answer is hidden.'};
    button.click(); clicked = 'scenario_choice';
  } else if (!['inspect','scroll','expand','flip','carousel_dot',
                'process_carousel_dot','tab_select','checklist_check',
                'hotspot_open','hotspot_close','continue'].includes(request.action)) {
    return {error:'Unsupported Best practices scenario action.'};
  }
  return {count:1,state,step,text,prompt:state === 'choice' ? prompt : null,options,
    answer,signature,buttons,clicked};
}""".replace("__DATA__", _DATA)


def validate_snapshot(snapshot, request):
    """Validate the returned scenario fields before choosing an action."""
    from coursera_start import StopRun

    count = snapshot.get("scenario_count")
    if request.get("path") != PATH:
        if count != 0:
            raise StopRun("An unaudited Rise scenario was returned.")
        return
    if count != 1 or snapshot.get("scenario_state") not in {
        "intro", "choice", "continue", "terminal",
    }:
        raise StopRun("The Best practices scenario snapshot is incomplete.")
    state = snapshot["scenario_state"]
    step = snapshot.get("scenario_step")
    text = snapshot.get("scenario_text")
    prompt = snapshot.get("scenario_prompt")
    options = snapshot.get("scenario_options")
    answer = snapshot.get("scenario_answer")
    signature = snapshot.get("scenario_signature")
    if (type(step) is not int or not isinstance(text, str) or
            not isinstance(options, list) or not isinstance(signature, str)):
        raise StopRun("The Best practices scenario identity is incomplete.")
    if state == "intro":
        valid = step == 0 and text == INTRO and prompt is None and not options and answer is None
    elif state == "terminal":
        valid = (step == len(CHOICES) and text == END and prompt is None and
                 not options and answer is None)
    elif state == "choice":
        valid = (0 <= step < len(CHOICES) and
                 prompt == CHOICES[step]["prompt"] and
                 options == list(CHOICES[step]["options"]) and
                 answer == CHOICES[step]["answer"])
    else:
        valid = (0 <= step < len(CONTINUATIONS) and
                 text == CONTINUATIONS[step] and prompt is None and
                 not options and answer is None)
    if not valid or signature != json.dumps(
            [state, step, text, options], ensure_ascii=False, separators=(",", ":")):
        raise StopRun("The Best practices scenario changed or returned inconsistent content.")


def next_action(snapshot):
    """Return an audited action and the exact evidence it must match."""
    state = snapshot["scenario_state"]
    if state in {"intro", "continue"}:
        return "scenario_continue", {"scenario_signature": snapshot["scenario_signature"]}
    if state == "choice":
        return "scenario_choice", {
            "scenario_signature": snapshot["scenario_signature"],
            "scenario_answer": snapshot["scenario_answer"],
        }
    if state == "terminal":
        return None
    raise ValueError("Unknown Best practices scenario state")


INNER_JS = r"""(request => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const scenarioGuard = __SCENARIO__;
  if (location.href !== request.frame || request.frame !== '__FRAME__')
    return {error:'The Best practices asset frame changed.'};
  if (document.readyState !== 'complete')
    return {pending:'The Best practices asset frame is loading.'};
  const base = request.frame.replace(/index\.html$/, '');
  const containers = [...document.querySelectorAll('iframe[title="container"]')];
  if (!containers.length && !document.querySelector('iframe'))
    return {pending:'The Best practices container is mounting.'};
  if (containers.length !== 1 || document.querySelectorAll('iframe').length !== 1 ||
      containers[0].src !== base + 'scorm-content/scormdriver/indexAPI.html')
    return {error:'The Best practices container changed.'};
  const driver = containers[0].contentDocument;
  if (!driver || driver.readyState !== 'complete')
    return {pending:'The Best practices driver is loading.'};
  const frames = [...driver.querySelectorAll('iframe')];
  const content = driver.querySelector('iframe#content-frame');
  if (!content && !frames.length)
    return {pending:'The Best practices content frame is mounting.'};
  if (frames.length !== 4 || frames[0] !== content ||
      clean(content?.title) !== 'Best practices')
    return {error:'The Best practices frame chain changed.'};
  const page = content.contentDocument;
  if (!page || page.readyState !== 'complete')
    return {pending:'The Best practices lesson is loading.'};
  const contentIndex = base + 'scorm-content/scormcontent/index.html';
  const expected = contentIndex + '#/lessons/__LESSON__';
  if (page.URL !== expected && new Set([
      contentIndex, contentIndex + '#', contentIndex + '#/',
      contentIndex + '#/lessons', contentIndex + '#/lessons/'
    ]).has(page.URL) &&
      !page.querySelector('.scenario-block,.quiz-block,textarea,select,' +
        '[contenteditable="true"]'))
    return {pending:'The Best practices lesson route is initializing.'};
  if (page.URL !== expected || page.querySelector('iframe'))
    return {error:'The Best practices lesson identity changed.'};
  const wraps = [...page.querySelectorAll('div.page-wrap')];
  const wrap = wraps[0];
  if (wraps.length !== 1 || !wrap || wrap.clientHeight < 250 ||
      !clean(page.body?.innerText).includes('LESSON 1 OF 1') ||
      !clean(wrap.innerText).startsWith('Best practices LESSON 1 OF 1 '))
    return {pending:'The Best practices one-lesson scaffold is loading.'};
  const progress = clean(page.querySelector('.nav-sidebar-header__progress-text')?.innerText);
  if (!/^\d{1,3}% COMPLETE$/.test(progress))
    return {pending:'The Best practices progress header is loading.'};
  const nav = [...page.querySelectorAll(
    'button.visually-hidden.btn-skip-to-lesson,button.nav-control__button,' +
    'button.nav-sidebar-header__search-btn,button.nav-sidebar-search__close')];
  const zoom = [...page.querySelectorAll('button[data-rmiz-btn-zoom],button[data-rmiz-btn-unzoom]')];
  const inputs = [...page.querySelectorAll('input')];
  const forms = [...page.querySelectorAll('form')];
  if (nav.length !== 4 || inputs.length !== 1 ||
      !inputs[0].matches('input[type="search"][disabled]') ||
      forms.length !== 1 || !forms[0].matches('form.nav-sidebar-search--disabled') ||
      page.querySelector('textarea,select,[contenteditable="true"],video,audio,canvas,' +
        '[draggable="true"],[role="radio"],[role="checkbox"],.quiz-block,' +
        '.knowledge-check,.sorting-activity,.matching-block') ||
      document.querySelector('button,input,textarea,select,form,[contenteditable="true"]') ||
      driver.querySelector('button,input,textarea,select,form,[contenteditable="true"]'))
    return {error:'The Best practices lesson has unknown interactive controls.'};
  const panels = [...page.querySelectorAll('button.blocks-accordion__header')];
  const accordions = [];
  for (const button of panels) {
    const id = button.getAttribute('aria-controls');
    const panel = id && page.getElementById(id);
    const expanded = button.getAttribute('aria-expanded');
    if (button.type !== 'button' || !button.closest('h2') ||
        !button.getClientRects().length || !['true','false'].includes(expanded) ||
        !panel?.matches('section.blocks-accordion__content') ||
        clean(panel.textContent).length < 150 ||
        panel.querySelector('button,[role="button"],input,textarea,select,form,' +
          '[contenteditable="true"],a[href]'))
      return {error:'A Best practices accordion changed or contains an activity.'};
    accordions.push({id,heading:clean(button.innerText),
      content:clean(panel.textContent),expanded:expanded === 'true'});
  }
  if (accordions.length !== 8 || new Set(accordions.map(x => x.id)).size !== 8)
    return {error:'The Best practices accordion inventory changed.'};
  const accordionSignature = JSON.stringify(
    accordions.map(x => [x.heading,x.content]));
  const scenario = scenarioGuard(page,wrap,{...request,action:'inspect'},clean);
  if (scenario.error || scenario.pending) return scenario;
  if (scenario.count !== 1)
    return {error:'The Best practices scenario was not found.'};
  const allowed = new Set([...nav,...zoom,...panels,...scenario.buttons]);
  const unknown = [...page.querySelectorAll('button,[role="button"]')]
    .filter(button => button.getClientRects().length > 0 && !allowed.has(button));
  const links = [...page.querySelectorAll('a[href]')];
  if (unknown.length || links.length !== 1 ||
      !links[0].matches('a.nav-sidebar__outline-item__link[aria-current="page"]' +
        '[data-link="lesson-link-item"]') ||
      links[0].getAttribute('href') !== '#/lessons/__LESSON__' ||
      zoom.some(button => !button.closest('[data-rmiz]')))
    return {error:'The Best practices lesson has an unexpected button or link.'};
  const top = wrap.scrollTop;
  const max = Math.max(0,wrap.scrollHeight - wrap.clientHeight);
  let clicked = scenario.clicked;
  if (request.action === 'expand') {
    const selected = panels.find(button =>
      button.getAttribute('aria-controls') === request.expand_id);
    if (!selected || selected.getAttribute('aria-expanded') !== 'false' ||
        request.accordion_signature !== accordionSignature)
      return {error:'The selected Best practices accordion changed.'};
    selected.scrollIntoView({block:'center'});
    if (!selected.getClientRects().length)
      return {error:'The selected Best practices accordion is hidden.'};
    selected.click(); clicked = 'expand';
  } else if (request.action === 'scroll') {
    if (!request.panels_read ||
        request.accordion_signature !== accordionSignature || top >= max - 2)
      return {error:'The Best practices reading cannot safely advance.'};
    wrap.scrollTop = Math.min(max,top + Math.max(300,Math.floor(wrap.clientHeight * .7)));
    clicked = 'scroll';
  } else if (['scenario_continue','scenario_choice'].includes(request.action)) {
    if (!request.panels_read ||
        request.accordion_signature !== accordionSignature || top < max - 2)
      return {error:'The Best practices scenario is not ready for this action.'};
    const acted = scenarioGuard(page,wrap,request,clean);
    if (acted.error || acted.pending || acted.clicked !== request.action)
      return {error:'The Best practices scenario action was not acknowledged.'};
    clicked = acted.clicked;
  } else if (request.action !== 'inspect')
    return {error:'Unsupported Best practices reader action.'};
  return {url:location.href,lesson_url:page.URL,progress,
    top:wrap.scrollTop,max,accordion_count:accordions.length,
    accordions,accordion_signature:accordionSignature,
    scenario_count:scenario.count,scenario_state:scenario.state,
    scenario_step:scenario.step,scenario_text:scenario.text,
    scenario_prompt:scenario.prompt,scenario_options:scenario.options,
    scenario_answer:scenario.answer,scenario_signature:scenario.signature,
    clicked};
})""".replace("__SCENARIO__", SCENARIO_JS).replace(
    "__FRAME__", FRAME).replace("__LESSON__", LESSON)


def _script(source, request):
    return source + "(" + json.dumps(request, ensure_ascii=False) + ")"


def _accordion_key(entry):
    digest = hashlib.sha256(entry["content"].encode("utf-8")).hexdigest()
    return entry["heading"], digest


def _checked_snapshot(value):
    from coursera_start import StopRun

    if not isinstance(value, dict):
        raise StopRun("The Best practices iframe returned an unreadable result.")
    if value.get("error"):
        raise StopRun(str(value["error"]))
    return value


async def _inner(frame, request, action, *, args=None, probes=80, delay=.25):
    from coursera_start import StopRun

    attempts = probes if action == "inspect" else 1
    pending = None
    for _ in range(attempts):
        value = _checked_snapshot(await frame.evaluate(_script(
            INNER_JS, {**request, **(args or {}), "action": action})))
        if value.get("pending"):
            if action != "inspect":
                raise StopRun("The Best practices lesson changed during an action.")
            pending = str(value["pending"])
            await asyncio.sleep(delay)
            continue
        if (value.get("url") != FRAME or
                value.get("lesson_url") != FRAME.replace("index.html", "") +
                "scorm-content/scormcontent/index.html#/lessons/" + LESSON or
                value.get("accordion_count") != len(ACCORDIONS) or
                not isinstance(value.get("accordions"), list) or
                len(value["accordions"]) != len(ACCORDIONS) or
                not isinstance(value.get("accordion_signature"), str) or
                type(value.get("top")) is not int or
                type(value.get("max")) is not int or
                value["top"] < 0 or value["max"] < 0 or
                value["top"] > value["max"] + 2 or
                not isinstance(value.get("progress"), str)):
            raise StopRun("The Best practices reading snapshot is inconsistent.")
        for index, entry in enumerate(value["accordions"]):
            if (not isinstance(entry, dict) or
                    entry.get("heading") != ACCORDIONS[index] or
                    not isinstance(entry.get("id"), str) or not entry["id"] or
                    not isinstance(entry.get("content"), str) or
                    len(entry["content"]) < 150 or
                    type(entry.get("expanded")) is not bool):
                raise StopRun("The Best practices accordion changed.")
        try:
            signed = json.loads(value["accordion_signature"])
        except ValueError:
            raise StopRun("The Best practices accordion signature is unreadable.") from None
        if signed != [[entry["heading"], entry["content"]]
                      for entry in value["accordions"]]:
            raise StopRun("The Best practices accordion content changed.")
        validate_snapshot(value, request)
        return value
    raise StopRun("The Best practices lesson did not settle. Last pending: " + str(pending))


def _remember_expanded(snapshot, visited):
    for entry in snapshot["accordions"]:
        if entry["expanded"]:
            visited.add(_accordion_key(entry))


def _expected_transition(before, after):
    source = before["scenario_state"]
    target = after["scenario_state"]
    step = before["scenario_step"]
    next_step = after["scenario_step"]
    if source == "intro":
        return target == "choice" and next_step == 0
    if source == "choice":
        return target == "continue" and next_step == step
    if source == "continue":
        return (target == "choice" and next_step == step + 1 if step < len(CHOICES) - 1
                else target == "terminal" and next_step == len(CHOICES))
    return False


async def complete_best_practices(runner, item, *, max_actions=160,
                                  settle_delay=.25):
    """Read the exact Best practices Rise activity and require a green mark."""
    from coursera_start import StopRun
    from plugin_flow import lesson_heading, plugin_path
    from rise_scorm_flow import OUTER_JS, _outer_checked
    from scorm_flow import open_scorm_frame

    path = plugin_path(item, runner.course["url"])
    heading = lesson_heading(item.get("title"))
    if path != PATH or heading != "Best practices" or item.get("key") != "ungradedWidget:WBdg2":
        raise StopRun("The Best practices item identity changed.")
    if await runner.verified_item(item):
        return
    request = {"path": PATH, "heading": heading}
    outer = _outer_checked(await runner.tab.evaluate(_script(
        OUTER_JS, {**request, "action": "inspect"})), request)
    if outer.get("frame") != FRAME or outer.get("frame_title") != heading:
        raise StopRun("The Best practices Rise frame changed.")
    request.update(frame=FRAME, frame_title=heading)
    frame = await open_scorm_frame(runner.tab, FRAME)
    try:
        snapshot = await _inner(frame, request, "inspect", delay=settle_delay,
                                probes=160)
        visited = set()
        _remember_expanded(snapshot, visited)
        for action_count in range(max_actions):
            unread = [entry for entry in snapshot["accordions"]
                      if _accordion_key(entry) not in visited]
            if unread:
                action = "expand"
                args = {"expand_id": unread[0]["id"],
                        "accordion_signature": snapshot["accordion_signature"]}
            elif snapshot["top"] < snapshot["max"] - 2:
                action = "scroll"
                args = {"panels_read": True,
                        "accordion_signature": snapshot["accordion_signature"]}
            elif snapshot["scenario_state"] != "terminal":
                action, args = next_action(snapshot)
                args.update(panels_read=True,
                            accordion_signature=snapshot["accordion_signature"])
            else:
                await asyncio.sleep(1)
                stable = await _inner(frame, request, "inspect", delay=settle_delay)
                if (stable["scenario_state"] == "terminal" and
                        stable["scenario_signature"] == snapshot["scenario_signature"] and
                        stable["accordion_signature"] == snapshot["accordion_signature"] and
                        stable["top"] >= stable["max"] - 2 and
                        len(visited) == len(ACCORDIONS)):
                    break
                snapshot = stable
                _remember_expanded(snapshot, visited)
                continue
            before = snapshot
            result = await _inner(frame, request, action, args=args, delay=settle_delay)
            if result.get("clicked") != action:
                raise StopRun("The Best practices reading action was not acknowledged.")
            for _ in range(80):
                await asyncio.sleep(settle_delay)
                snapshot = await _inner(frame, request, "inspect", delay=settle_delay)
                _remember_expanded(snapshot, visited)
                if action == "expand" and _accordion_key(unread[0]) in visited:
                    break
                if action == "scroll" and snapshot["top"] > before["top"]:
                    break
                if action in {"scenario_continue", "scenario_choice"} and \
                        snapshot["scenario_signature"] != before["scenario_signature"]:
                    if not _expected_transition(before, snapshot):
                        raise StopRun("The Best practices scenario branched unexpectedly.")
                    break
            else:
                raise StopRun("The Best practices activity did not advance.")
        else:
            raise StopRun("The Best practices activity exceeded its action limit.")
        if (len(visited) != len(ACCORDIONS) or
                snapshot["scenario_state"] != "terminal" or
                snapshot["top"] < snapshot["max"] - 2):
            raise StopRun("Best practices did not reach its verified terminal state.")
    finally:
        await frame.close()
    if await runner.verified_item(item, wait=2):
        return
    confirmed = _outer_checked(await runner.tab.evaluate(_script(
        OUTER_JS, {**request, "action": "inspect"})), request)
    if confirmed.get("frame") != FRAME or confirmed.get("frame_title") != heading:
        raise StopRun("The Best practices Coursera shell changed before completion.")
    marked = _checked_snapshot(await runner.tab.evaluate(_script(
        OUTER_JS, {**request, "action": "mark", "inner_verified": True})))
    if marked.get("clicked") != "mark":
        raise StopRun("Coursera did not acknowledge Best practices completion.")
    runner.event("best_practices_mark_clicked", key=item["key"])
    if not await runner.verified_item(item, wait=20):
        raise StopRun("Coursera did not show a green mark for Best practices.")

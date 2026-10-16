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
POISONING_PATH = (
    "/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
    "/ungradedWidget/UTogj/poisoning-attacks"
)
POISONING_FRAME = (
    "https://d10o6em2qtnr4q.cloudfront.net/assets/"
    "6b1982df242d468e88966062dac08f14/index.html"
)
POISONING_LESSON = "JhpKL53OWGxnm-Sh8YhbxJ0_T0yj0HjH"
SUMMARY_PATH = (
    "/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
    "/ungradedWidget/SkhZ8/summary"
)
SUMMARY_FRAME = (
    "https://d10o6em2qtnr4q.cloudfront.net/assets/"
    "fbb5f4a9b0dc4e11b8d68a4dc5a1a128/index.html"
)
SUMMARY_LESSON = "56FMtji_7_FYymjsaI8AndYlYR6AP4as"
OVERVIEW4_PATH = (
    "/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
    "/ungradedWidget/YFamB/overview"
)
OVERVIEW4_FRAME = (
    "https://d10o6em2qtnr4q.cloudfront.net/assets/"
    "b4ebb91474174e9c8aa39006dd54027b/index.html"
)
OVERVIEW4_LESSON = "SmxA7DGtQgLXIL9FdUZUvzS-YBku6KOX"
INTRODUCTION4_PATH = (
    "/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
    "/ungradedWidget/xBnh8/introduction"
)
INTRODUCTION4_FRAME = (
    "https://d10o6em2qtnr4q.cloudfront.net/assets/"
    "98a1815dd12a428db3b9a6448325e7f2/index.html"
)
INTRODUCTION4_LESSON = "_mLgVvWjXWTqWjxfSRihYTCOqDrdou1h"
DEFENCE4_PATH = (
    "/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
    "/ungradedWidget/gTEoB/defence-techniques-and-strategies"
)
DEFENCE4_FRAME = (
    "https://d10o6em2qtnr4q.cloudfront.net/assets/"
    "7593944453f8458fb0f5c677ef10dc9d/index.html"
)
DEFENCE4_LESSON = "eih4-kXzAFPtZWonw92N1bgBl3WCi9fa"
GENAI4_PATH = (
    "/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
    "/ungradedWidget/6uBZN/defences-for-attacks-on-genai-models"
)
GENAI4_FRAME = (
    "https://d10o6em2qtnr4q.cloudfront.net/assets/"
    "fb3d4742f7634ff2af1c80bd5a6bce4e/index.html"
)
GENAI4_LESSON = "duv5gTQ28HWXmT1REiSOU3TUgjnK2G_N"
AUDITED_SCORM = {
    THREAT_MODEL_PATH: {"heading": "Threat model", "frame": THREAT_MODEL_FRAME,
                        "accordion": None},
    ADVERSARIAL_INPUTS_PATH: {"heading": "Adversarial inputs",
                              "frame": ADVERSARIAL_INPUTS_FRAME, "accordion": "Remarks"},
    GENERATING_EXAMPLES_PATH: {"heading": "Generating adversarial examples",
                               "frame": GENERATING_EXAMPLES_FRAME,
                               "accordion": "Remarks", "continue": "DO IT YOURSELF EXERCISE EIGHT"},
    POISONING_PATH: {"heading": "Poisoning attacks", "frame": POISONING_FRAME,
                     "accordion": None, "continue": "DO IT YOURSELF EXERCISE NINE"},
    SUMMARY_PATH: {"heading": "Summary", "frame": SUMMARY_FRAME,
                   "frame_title": "Topic 3: Summary", "accordion": None},
    OVERVIEW4_PATH: {"heading": "Overview", "frame": OVERVIEW4_FRAME,
                     "frame_title": "Topic 4: Overview", "accordion": None},
    INTRODUCTION4_PATH: {"heading": "Introduction", "frame": INTRODUCTION4_FRAME,
                         "frame_title": "Topic 4: Introduction", "accordion": None},
    DEFENCE4_PATH: {"heading": "Defence techniques and strategies",
                    "frame": DEFENCE4_FRAME, "accordion": None},
    GENAI4_PATH: {"heading": "Defences for attacks on GenAI models",
                  "frame": GENAI4_FRAME, "accordion": None},
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
  if (frames.length !== 1 || clean(frames[0].title) !== (request.frame_title || request.heading) ||
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


POISONING_JS = r"""(request => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  if (location.href !== request.frame)
    return {error:'The audited poisoning lesson frame URL changed.'};
  if (document.readyState !== 'complete')
    return {pending:'Poisoning lesson frame is loading.'};
  const base = request.frame.replace(/index\.html$/, '');
  const containers = [...document.querySelectorAll('iframe[title="container"]')];
  if (!containers.length && !document.querySelectorAll('iframe').length)
    return {pending:'Poisoning lesson container is being mounted.'};
  if (containers.length !== 1 || document.querySelectorAll('iframe').length !== 1 ||
      containers[0].src !== base + 'scorm-content/scormdriver/indexAPI.html')
    return {error:'The poisoning lesson container changed.'};
  const driver = containers[0].contentDocument;
  if (!driver || driver.readyState !== 'complete')
    return {pending:'Poisoning lesson container is loading.'};
  const frames = [...driver.querySelectorAll('iframe#content-frame')];
  if (!frames.length && !driver.querySelectorAll('iframe').length)
    return {pending:'Poisoning lesson content is being mounted.'};
  if (frames.length !== 1 || clean(frames[0].title) !== request.heading)
    return {error:'The poisoning lesson content frame changed.'};
  const page = frames[0].contentDocument;
  if (!page || page.readyState !== 'complete' ||
      /\/(?:blank|indexAPI)\.html$/.test(page.URL))
    return {pending:'Poisoning lesson content is loading.'};
  if (page.URL !== base + 'scorm-content/scormcontent/index.html#/lessons/' + request.lesson ||
      page.querySelectorAll('iframe').length)
    return {error:'The poisoning lesson identity changed.'};
  const wraps = [...page.querySelectorAll('div.page-wrap')];
  const text = wraps.length === 1 ? clean(wraps[0].innerText) : '';
  const chromeText = clean(page.body?.innerText);
  const progress = clean(page.querySelector('.nav-sidebar-header__progress-text')?.innerText);
  if (wraps.length !== 1 || text.length < 2000 ||
      !text.includes('RandomForestClassifier') || !text.includes('RocCurveDisplay') ||
      !/\bLESSON 1 OF 1\b/i.test(chromeText) ||
      !/^\d{1,3}% COMPLETE$/i.test(progress))
    return {error:'This is not the audited poisoning lesson reading.'};
  const continues = [...page.querySelectorAll('button.continue-btn')];
  const continued = /In this ninth do it yourself exercise/i.test(text) &&
      /no solutions to this exercise/i.test(text);
  const stage = continued && continues.length === 0 ? 'continued' :
    !continued && continues.length === 1 &&
    clean(continues[0].innerText) === request.continue_label ? 'reading' : null;
  if (!stage)
    return {error:'The poisoning lesson screen or continuation changed.'};
  const allowed = new Set([
    ...page.querySelectorAll('button.visually-hidden.btn-skip-to-lesson,' +
      'button.nav-control__button,button.nav-sidebar-header__search-btn,' +
      'button.nav-sidebar-search__close'), ...continues]);
  const forms = [...page.querySelectorAll('form')];
  if ([...page.querySelectorAll('button,[role="button"]')]
        .filter(node => node.getClientRects().length).some(node => !allowed.has(node)) ||
      page.querySelectorAll('input').length !== 1 ||
      !page.querySelector('input[type="search"][disabled]') ||
      forms.length !== 1 || !forms[0].matches('form.nav-sidebar-search--disabled') ||
      forms[0].querySelectorAll('input').length !== 1 ||
      page.querySelector('textarea,select,[contenteditable="true"]') ||
      document.querySelector('button,input,textarea,select,form,[contenteditable="true"]') ||
      driver.querySelector('button,input,textarea,select,form,[contenteditable="true"]'))
    return {error:'The poisoning lesson contains an unhandled interactive control.'};
  const wrap = wraps[0];
  const max = Math.max(0, wrap.scrollHeight - wrap.clientHeight);
  if (max < 1000)
    return {error:'The poisoning lesson reading area changed.'};
  let clicked = null;
  if (request.action === 'scroll') {
    wrap.scrollTop = Math.min(max, wrap.scrollTop +
      Math.max(300, Math.floor(wrap.clientHeight * 0.7)));
  } else if (request.action === 'continue') {
    if (stage !== 'reading' || wrap.scrollTop < max - 2)
      return {error:'The poisoning lesson reading must finish before continuation.'};
    continues[0].click();
    clicked = request.continue_label;
  } else if (request.action !== 'inspect') {
    return {error:'Unsupported poisoning lesson action.'};
  }
  return {url:location.href, lesson:'LESSON 1 OF 1', progress, stage,
    top:wrap.scrollTop, max, text_length:text.length, clicked};
})"""


SUMMARY_JS = r"""(request => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  if (location.href !== request.frame || request.heading !== 'Summary' ||
      request.lesson !== '56FMtji_7_FYymjsaI8AndYlYR6AP4as')
    return {error:'The audited Summary frame or lesson identity changed.'};
  if (document.readyState !== 'complete')
    return {pending:'The Summary frame is loading.'};
  const base = request.frame.replace(/index\.html$/, '');
  const containers = [...document.querySelectorAll('iframe[title="container"]')];
  if (!containers.length && !document.querySelectorAll('iframe').length)
    return {pending:'The Summary container is mounting.'};
  if (containers.length !== 1 || document.querySelectorAll('iframe').length !== 1 ||
      containers[0].src !== base + 'scorm-content/scormdriver/indexAPI.html')
    return {error:'The Summary container changed.'};
  const driver = containers[0].contentDocument;
  if (!driver || driver.readyState !== 'complete')
    return {pending:'The Summary container is loading.'};
  const frames = [...driver.querySelectorAll('iframe#content-frame')];
  const driverFrames = [...driver.querySelectorAll('iframe')];
  if (!frames.length && !driverFrames.length)
    return {pending:'The Summary content is mounting.'};
  const auxiliary = driverFrames.filter(frame => frame !== frames[0]);
  if (frames.length !== 1 || driverFrames.length !== 4 ||
      driverFrames[0] !== frames[0] || clean(frames[0].title) !== 'Topic 3: Summary' ||
      auxiliary.some((frame,index) => clean(frame.title) !== 'Intentionally blank' ||
        frame.src !== base + 'scorm-content/scormdriver/' +
          (index === 0 ? 'AICCComm.html' : 'blank.html')))
    return {error:'The Summary content frame changed.'};
  const page = frames[0].contentDocument;
  if (!page || page.readyState !== 'complete' ||
      /\/(?:blank|indexAPI)\.html$/.test(page.URL))
    return {pending:'The Summary content is loading.'};
  if (page.URL !== base + 'scorm-content/scormcontent/index.html#/lessons/' + request.lesson ||
      page.querySelectorAll('iframe').length)
    return {error:'The Summary lesson identity changed.'};
  const wraps = [...page.querySelectorAll('div.page-wrap')];
  const text = wraps.length === 1 ? clean(wraps[0].innerText) : '';
  const pageText = clean(page.body?.innerText);
  const progress = clean(page.querySelector('.nav-sidebar-header__progress-text')?.innerText);
  if (wraps.length !== 1 || text.length < 4500 ||
      !text.startsWith('Topic complete LESSON 1 OF 1') ||
      !text.includes('Congratulations, you have now completed this topic!') ||
      !text.includes('You are now able to:') || !text.includes('References') ||
      !/\bLESSON 1 OF 1\b/.test(pageText) || !/^\d{1,3}% COMPLETE$/.test(progress))
    return {error:'This is not the audited Summary reading.'};
  const outcomes = [
    'Describe security risks in real-world AI applications.',
    'Explore AI-specific threats such as model poisoning, adversarial attacks, and backdoor exploits.',
    'Understand risks associated with model leakage, data privacy breaches, and AI-powered surveillance.',
    'Evaluate challenges in AI model alignment, censorship, and ethical concerns.'
  ];
  const lists = [...page.querySelectorAll('div.block-list--checkboxes')];
  const boxes = lists.length === 1 ? [...lists[0].querySelectorAll('input.block-list__checkbox-input')] : [];
  if (lists.length !== 1 || boxes.length !== outcomes.length ||
      [...page.querySelectorAll('input.block-list__checkbox-input')].length !== boxes.length ||
      boxes.some((box,index) => box.type !== 'checkbox' || box.disabled || box.required ||
        box.closest('form') || !box.closest('label.block-list__option') ||
        clean(box.closest('label.block-list__option')
          .querySelector('.block-list__content')?.innerText) !== outcomes[index]))
    return {error:'The Summary self-check list changed.'};
  const allowed = new Set([...page.querySelectorAll(
    'button.visually-hidden.btn-skip-to-lesson,button.nav-control__button,' +
    'button.nav-sidebar-header__search-btn,button.nav-sidebar-search__close')]);
  const forms = [...page.querySelectorAll('form')];
  const inputs = [...page.querySelectorAll('input')];
  if ([...page.querySelectorAll('button,[role="button"]')]
        .filter(node => node.getClientRects().length).some(node => !allowed.has(node)) ||
      inputs.length !== 5 || inputs.some(node => node !== page.querySelector('input[type="search"][disabled]') &&
        !boxes.includes(node)) ||
      forms.length !== 1 || !forms[0].matches('form.nav-sidebar-search--disabled') ||
      forms[0].querySelectorAll('input').length !== 1 ||
      page.querySelector('textarea,select,[contenteditable="true"]') ||
      document.querySelector('button,input,textarea,select,form,[contenteditable="true"]') ||
      driver.querySelector('button,input,textarea,select,form,[contenteditable="true"]'))
    return {error:'The Summary lesson contains an unhandled interactive control.'};
  const wrap = wraps[0];
  const max = Math.max(0, wrap.scrollHeight - wrap.clientHeight);
  if (max < 1200)
    return {error:'The Summary reading area changed.'};
  if (request.action === 'scroll')
    wrap.scrollTop = Math.min(max, wrap.scrollTop +
      Math.max(300, Math.floor(wrap.clientHeight * 0.7)));
  else if (request.action !== 'inspect')
    return {error:'Unsupported Summary action.'};
  return {url:location.href, lesson:'LESSON 1 OF 1', progress, stage:'reading',
    top:wrap.scrollTop, max, text_length:text.length, checkbox_count:boxes.length};
})"""


OVERVIEW4_JS = r"""(request => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  if (location.href !== request.frame || request.heading !== 'Overview' ||
      request.lesson !== 'SmxA7DGtQgLXIL9FdUZUvzS-YBku6KOX')
    return {error:'The audited Topic 4 Overview frame or lesson identity changed.'};
  if (document.readyState !== 'complete')
    return {pending:'The Topic 4 Overview frame is loading.'};
  const base = request.frame.replace(/index\.html$/, '');
  const containers = [...document.querySelectorAll('iframe[title="container"]')];
  if (!containers.length && !document.querySelectorAll('iframe').length)
    return {pending:'The Topic 4 Overview container is mounting.'};
  if (containers.length !== 1 || document.querySelectorAll('iframe').length !== 1 ||
      containers[0].src !== base + 'scorm-content/scormdriver/indexAPI.html')
    return {error:'The Topic 4 Overview container changed.'};
  const driver = containers[0].contentDocument;
  if (!driver || driver.readyState !== 'complete')
    return {pending:'The Topic 4 Overview container is loading.'};
  const frames = [...driver.querySelectorAll('iframe#content-frame')];
  const driverFrames = [...driver.querySelectorAll('iframe')];
  if (!frames.length && !driverFrames.length)
    return {pending:'The Topic 4 Overview content is mounting.'};
  const auxiliary = driverFrames.filter(frame => frame !== frames[0]);
  if (frames.length !== 1 || driverFrames.length !== 4 ||
      driverFrames[0] !== frames[0] || clean(frames[0].title) !== 'Topic 4: Overview' ||
      auxiliary.some((frame,index) => clean(frame.title) !== 'Intentionally blank' ||
        frame.src !== base + 'scorm-content/scormdriver/' +
          (index === 0 ? 'AICCComm.html' : 'blank.html')))
    return {error:'The Topic 4 Overview content frame changed.'};
  const page = frames[0].contentDocument;
  if (!page || page.readyState !== 'complete' ||
      /\/(?:blank|indexAPI)\.html$/.test(page.URL))
    return {pending:'The Topic 4 Overview content is loading.'};
  if (page.URL !== base + 'scorm-content/scormcontent/index.html#/lessons/' + request.lesson ||
      page.querySelectorAll('iframe').length)
    return {error:'The Topic 4 Overview lesson identity changed.'};
  const wraps = [...page.querySelectorAll('div.page-wrap')];
  const text = wraps.length === 1 ? clean(wraps[0].innerText) : '';
  const pageText = clean(page.body?.innerText);
  const progress = clean(page.querySelector('.nav-sidebar-header__progress-text')?.innerText);
  if (wraps.length !== 1 || text.length < 800 ||
      !text.startsWith('Topic overview LESSON 1 OF 1') ||
      !text.includes('Testing AI security and developing robust AI controls') ||
      !text.includes('methods for testing AI security.') ||
      !/\bLESSON 1 OF 1\b/.test(pageText) || !/^\d{1,3}% COMPLETE$/.test(progress))
    return {error:'This is not the audited Topic 4 Overview reading.'};
  const outcomes = [
    'Recommend guidelines and common defences against AI security attacks.',
    'Apply AI controls to mitigate security risks.',
    'Contrast the trade-offs between security, utility, and robustness of AI models.',
    'Plan processes to conduct AI security testing and benchmarking.'
  ];
  const lists = [...page.querySelectorAll('div.block-list--checkboxes')];
  const boxes = lists.length === 1 ? [...lists[0].querySelectorAll('input.block-list__checkbox-input')] : [];
  if (lists.length !== 1 || boxes.length !== outcomes.length ||
      [...page.querySelectorAll('input.block-list__checkbox-input')].length !== boxes.length ||
      boxes.some((box,index) => box.type !== 'checkbox' || box.disabled || box.required ||
        box.closest('form') || !box.closest('label.block-list__option') ||
        clean(box.closest('label.block-list__option')
          .querySelector('.block-list__content')?.innerText) !== outcomes[index]))
    return {error:'The Topic 4 Overview self-check list changed.'};
  const allowed = new Set([...page.querySelectorAll(
    'button.visually-hidden.btn-skip-to-lesson,button.nav-control__button,' +
    'button.nav-sidebar-header__search-btn,button.nav-sidebar-search__close')]);
  const forms = [...page.querySelectorAll('form')];
  const inputs = [...page.querySelectorAll('input')];
  if ([...page.querySelectorAll('button,[role="button"]')]
        .filter(node => node.getClientRects().length).some(node => !allowed.has(node)) ||
      inputs.length !== 5 || inputs.some(node => node !== page.querySelector('input[type="search"][disabled]') &&
        !boxes.includes(node)) ||
      forms.length !== 1 || !forms[0].matches('form.nav-sidebar-search--disabled') ||
      forms[0].querySelectorAll('input').length !== 1 ||
      page.querySelector('textarea,select,[contenteditable="true"]') ||
      document.querySelector('button,input,textarea,select,form,[contenteditable="true"]') ||
      driver.querySelector('button,input,textarea,select,form,[contenteditable="true"]'))
    return {error:'The Topic 4 Overview lesson contains an unhandled interactive control.'};
  const wrap = wraps[0];
  const max = Math.max(0, wrap.scrollHeight - wrap.clientHeight);
  if (wrap.clientHeight < 250 || max < 250)
    return {error:'The Topic 4 Overview reading area changed.'};
  if (request.action === 'scroll')
    wrap.scrollTop = Math.min(max, wrap.scrollTop +
      Math.max(300, Math.floor(wrap.clientHeight * 0.7)));
  else if (request.action !== 'inspect')
    return {error:'Unsupported Topic 4 Overview action.'};
  return {url:location.href, lesson:'LESSON 1 OF 1', progress, stage:'reading',
    top:wrap.scrollTop, max, text_length:text.length, checkbox_count:boxes.length};
})"""


INTRODUCTION4_JS = r"""(request => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  if (location.href !== request.frame || request.heading !== 'Introduction' ||
      request.lesson !== '_mLgVvWjXWTqWjxfSRihYTCOqDrdou1h')
    return {error:'The audited Topic 4 Introduction frame or lesson identity changed.'};
  if (document.readyState !== 'complete')
    return {pending:'The Topic 4 Introduction frame is loading.'};
  const base = request.frame.replace(/index\.html$/, '');
  const containers = [...document.querySelectorAll('iframe[title="container"]')];
  if (!containers.length && !document.querySelectorAll('iframe').length)
    return {pending:'The Topic 4 Introduction container is mounting.'};
  if (containers.length !== 1 || document.querySelectorAll('iframe').length !== 1 ||
      containers[0].src !== base + 'scorm-content/scormdriver/indexAPI.html')
    return {error:'The Topic 4 Introduction container changed.'};
  const driver = containers[0].contentDocument;
  if (!driver || driver.readyState !== 'complete')
    return {pending:'The Topic 4 Introduction container is loading.'};
  const frames = [...driver.querySelectorAll('iframe#content-frame')];
  const driverFrames = [...driver.querySelectorAll('iframe')];
  if (!frames.length && !driverFrames.length)
    return {pending:'The Topic 4 Introduction content is mounting.'};
  const auxiliary = driverFrames.filter(frame => frame !== frames[0]);
  if (frames.length !== 1 || driverFrames.length !== 4 ||
      driverFrames[0] !== frames[0] || clean(frames[0].title) !== 'Topic 4: Introduction' ||
      auxiliary.some((frame,index) => clean(frame.title) !== 'Intentionally blank' ||
        frame.src !== base + 'scorm-content/scormdriver/' +
          (index === 0 ? 'AICCComm.html' : 'blank.html')))
    return {error:'The Topic 4 Introduction content frame changed.'};
  const page = frames[0].contentDocument;
  if (!page || page.readyState !== 'complete' ||
      /\/(?:blank|indexAPI)\.html$/.test(page.URL))
    return {pending:'The Topic 4 Introduction content is loading.'};
  if (page.URL !== base + 'scorm-content/scormcontent/index.html#/lessons/' + request.lesson ||
      page.querySelectorAll('iframe').length)
    return {error:'The Topic 4 Introduction lesson identity changed.'};
  const visible = node => {
    if (!node || !node.getClientRects().length) return false;
    for (let current = node; current; current = current.parentElement) {
      const style = page.defaultView.getComputedStyle(current);
      if (current.hidden || current.inert || current.getAttribute('aria-hidden') === 'true' ||
          style.display === 'none' || style.visibility === 'hidden' ||
          style.visibility === 'collapse') return false;
    }
    return true;
  };
  const wraps = [...page.querySelectorAll('div.page-wrap')];
  const text = wraps.length === 1 ? clean(wraps[0].innerText) : '';
  const pageText = clean(page.body?.innerText);
  const progress = clean(page.querySelector('.nav-sidebar-header__progress-text')?.innerText);
  if (wraps.length !== 1 || !text.startsWith('Introduction LESSON 1 OF 1') ||
      !text.includes('The security, robustness, and privacy guarantees') ||
      !text.includes('Security controls are countermeasures or safeguards') ||
      !text.includes('This topic consists of the following subtopics:') ||
      !text.includes('AI Security Testing and Benchmarking.') ||
      !/\bLESSON 1 OF 1\b/.test(pageText) || !/^\d{1,3}% COMPLETE$/.test(progress))
    return {error:'This is not the audited Topic 4 Introduction reading.'};
  const continues = [...page.querySelectorAll('button.continue-btn')];
  const cards = [...page.querySelectorAll('li.flashcard')];
  const continued = text.includes('Know thine enemy') &&
    text.includes('Flip each card to discover the key reasons for adversarial attacks on an AI system.') &&
    text.includes('Attackers can also be interested in stealing the model itself');
  const stage = continued && text.length >= 4300 && continues.length === 0 && cards.length === 3
    ? 'continued' : !continued && text.length >= 1900 && text.length < 3000 &&
      continues.length === 1 && cards.length === 0 &&
      clean(continues[0].innerText) === 'CONTINUE' && continues[0].type === 'button'
      ? 'reading' : null;
  if (!stage)
    return {error:'The Topic 4 Introduction stage or continuation changed.'};
  const pairs = [
    ['Unauthorised manipulation', 'To impact the predictions made by the model.'],
    ['Privacy breaches', 'To retrieve the data it was trained on.'],
    ['Reverse engineering.', 'To steal the model']
  ];
  let flipped = 0;
  let nextCard = null;
  const cardButtons = [];
  if (stage === 'continued') {
    for (let index = 0; index < cards.length; index++) {
      const card = cards[index];
      const sides = [...card.querySelectorAll('.flashcard-side')];
      const buttons = [...card.querySelectorAll('button.flashcard-side-flip__btn')];
      const front = sides.find(side => side.classList.contains('flashcard-side--front'));
      const back = sides.find(side => side.classList.contains('flashcard-side--back'));
      if (!visible(card) || card.getAttribute('role') !== 'listitem' ||
          card.getAttribute('aria-hidden') !== 'false' ||
          sides.length !== 2 || !front || !back || buttons.length !== 2 ||
          !buttons[0].id.endsWith('-' + index + '-front-flip-btn') ||
          !buttons[1].id.endsWith('-' + index + '-back-flip-btn') ||
          clean(front.innerText) !== 'Click to flip ' + pairs[index][0] ||
          clean(back.innerText) !== 'Click to flip ' + pairs[index][1])
        return {error:'The Topic 4 Introduction flashcard activity changed.'};
      const frontActive = buttons[0].tabIndex === 0 && buttons[1].tabIndex === -1 &&
        !front.classList.contains('flashcard-side--flipped') &&
        !back.classList.contains('flashcard-side--flipped');
      const backActive = buttons[0].tabIndex === -1 && buttons[1].tabIndex === 0 &&
        front.classList.contains('flashcard-side--flipped') &&
        back.classList.contains('flashcard-side--flipped');
      if (!frontActive && !backActive)
        return {error:'The Topic 4 Introduction flashcard state changed.'};
      if (backActive) flipped++;
      else if (nextCard === null) nextCard = buttons[0];
      cardButtons.push(...buttons);
    }
  }
  const zoom = [...page.querySelectorAll('button[data-rmiz-btn-zoom][aria-label="Zoom image"]')];
  const unzoom = [...page.querySelectorAll('button[data-rmiz-btn-unzoom][aria-label="Unzoom image"]')];
  const media = [...page.querySelectorAll('[data-rmiz]')];
  const unloadedImage = media.length === 1 &&
    media[0].querySelectorAll('[data-rmiz-content="not-found"] img').length === 1 &&
    media[0].querySelector('[data-rmiz-content="not-found"] img')
      .getAttribute('src') === 'assets/S4AI%20Topic%20Intros_Topic%204.png';
  const allowed = new Set([...page.querySelectorAll(
    'button.visually-hidden.btn-skip-to-lesson,button.nav-control__button,' +
    'button.nav-sidebar-header__search-btn,button.nav-sidebar-search__close'),
    ...zoom, ...unzoom, ...continues, ...cardButtons]);
  const forms = [...page.querySelectorAll('form')];
  const inputs = [...page.querySelectorAll('input')];
  if (!(zoom.length === 1 ||
        (stage === 'continued' && zoom.length === 0 && unloadedImage)) ||
      unzoom.length > 1 ||
      [...page.querySelectorAll('button,[role="button"]')]
        .filter(node => node.getClientRects().length).some(node => !allowed.has(node)) ||
      inputs.length !== 1 || !inputs[0].matches('input[type="search"][disabled]') ||
      forms.length !== 1 || !forms[0].matches('form.nav-sidebar-search--disabled') ||
      forms[0].querySelectorAll('input').length !== 1 ||
      page.querySelector('textarea,select,[contenteditable="true"]') ||
      document.querySelector('button,input,textarea,select,form,[contenteditable="true"]') ||
      driver.querySelector('button,input,textarea,select,form,[contenteditable="true"]'))
    return {error:'The Topic 4 Introduction has an unhandled interactive control.'};
  const wrap = wraps[0];
  const max = Math.max(0, wrap.scrollHeight - wrap.clientHeight);
  if (wrap.clientHeight < 250 || (stage === 'reading' && max < 1500) ||
      (stage === 'continued' && max < 3000))
    return {error:'The Topic 4 Introduction reading area changed.'};
  let clicked = null;
  if (request.action === 'scroll') {
    wrap.scrollTop = Math.min(max, wrap.scrollTop +
      Math.max(300, Math.floor(wrap.clientHeight * 0.7)));
  } else if (request.action === 'continue') {
    if (stage !== 'reading' || wrap.scrollTop < max - 2)
      return {error:'The Topic 4 Introduction must be read before continuation.'};
    continues[0].click();
    clicked = 'CONTINUE';
  } else if (request.action === 'flip') {
    if (stage !== 'continued' || !nextCard)
      return {error:'No unviewed Topic 4 Introduction flashcard is available.'};
    nextCard.scrollIntoView({block:'center'});
    if (!visible(nextCard) || !visible(nextCard.closest('li.flashcard')))
      return {error:'The Topic 4 Introduction flashcard button is not visible.'};
    nextCard.click();
    clicked = 'flip';
  } else if (request.action !== 'inspect') {
    return {error:'Unsupported Topic 4 Introduction action.'};
  }
  return {url:location.href, lesson:'LESSON 1 OF 1', progress, stage,
    top:wrap.scrollTop, max, text_length:text.length,
    card_count:cards.length, flipped_count:flipped, clicked};
})"""


DEFENCE4_JS = r"""(request => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  if (location.href !== request.frame || request.heading !== 'Defence techniques and strategies' ||
      request.lesson !== 'eih4-kXzAFPtZWonw92N1bgBl3WCi9fa')
    return {error:'The audited Defence techniques frame or lesson identity changed.'};
  if (document.readyState !== 'complete')
    return {pending:'The Defence techniques frame is loading.'};
  const base = request.frame.replace(/index\.html$/, '');
  const containers = [...document.querySelectorAll('iframe[title="container"]')];
  if (!containers.length && !document.querySelectorAll('iframe').length)
    return {pending:'The Defence techniques container is mounting.'};
  if (containers.length !== 1 || document.querySelectorAll('iframe').length !== 1 ||
      containers[0].src !== base + 'scorm-content/scormdriver/indexAPI.html')
    return {error:'The Defence techniques container changed.'};
  const driver = containers[0].contentDocument;
  if (!driver || driver.readyState !== 'complete')
    return {pending:'The Defence techniques container is loading.'};
  const frames = [...driver.querySelectorAll('iframe#content-frame')];
  const driverFrames = [...driver.querySelectorAll('iframe')];
  if (!frames.length && !driverFrames.length)
    return {pending:'The Defence techniques content is mounting.'};
  const auxiliary = driverFrames.filter(frame => frame !== frames[0]);
  if (frames.length !== 1 || driverFrames.length !== 4 ||
      driverFrames[0] !== frames[0] ||
      clean(frames[0].title) !== 'Defence techniques and strategies' ||
      auxiliary.some((frame,index) => clean(frame.title) !== 'Intentionally blank' ||
        frame.src !== base + 'scorm-content/scormdriver/' +
          (index === 0 ? 'AICCComm.html' : 'blank.html')))
    return {error:'The Defence techniques content frame changed.'};
  const page = frames[0].contentDocument;
  if (!page || page.readyState !== 'complete' ||
      /\/(?:blank|indexAPI)\.html$/.test(page.URL))
    return {pending:'The Defence techniques content is loading.'};
  if (page.URL !== base + 'scorm-content/scormcontent/index.html#/lessons/' + request.lesson ||
      page.querySelectorAll('iframe').length)
    return {error:'The Defence techniques lesson identity changed.'};
  const visible = node => {
    if (!node || !node.getClientRects().length) return false;
    for (let current = node; current; current = current.parentElement) {
      const style = page.defaultView.getComputedStyle(current);
      if (current.hidden || current.inert || current.getAttribute('aria-hidden') === 'true' ||
          style.display === 'none' || style.visibility === 'hidden' ||
          style.visibility === 'collapse') return false;
    }
    return true;
  };
  const wraps = [...page.querySelectorAll('div.page-wrap')];
  const wrap = wraps[0];
  const text = wrap ? clean(wrap.innerText) : '';
  const progress = clean(page.querySelector('.nav-sidebar-header__progress-text')?.innerText);
  if (wraps.length !== 1 || !text.startsWith('Defence techniques and strategies LESSON 1 OF 1') ||
      !text.includes('In the previous topics, we explored different types of adversarial AI attacks.') ||
      !/^\d{1,3}% COMPLETE$/.test(progress) ||
      !/\bLESSON 1 OF 1\b/.test(clean(page.body?.innerText)))
    return {error:'This is not the audited Defence techniques lesson.'};
  const markers = [
    'Defences for evasion attacks', 'Defences for poisoning attacks',
    'Defences for inversion attacks', 'Defences for model extraction attacks'];
  const present = markers.map(marker => text.includes(marker));
  if (present.some((found,index) => found && present.slice(0,index).some(previous => !previous)))
    return {error:'The Defence techniques reading order changed.'};
  const accordions = [...page.querySelectorAll('button.blocks-accordion__header')];
  const cards = [...page.querySelectorAll('li.flashcard')];
  const continues = [...page.querySelectorAll('button.continue-btn')];
  const scenario = page.querySelector('.scenario-block');
  const started = scenario?.classList.contains('scenario-block--initiated') || false;
  const terminal = started && clean(scenario.innerText).startsWith('Scenario End ');
  let stage = !present[0] ? 'intro' : !present[1] ? 'evasion' :
    !present[2] ? 'poisoning' : !present[3] ? 'inversion' :
    terminal ? 'terminal' : started ? 'scenario' : 'scenario_intro';
  const expectedAccordions = ['Data sanitisation','Outlier detection','Robust optimisation'];
  const panelStarts = [
    'Filtering and cleaning the training data',
    'Detecting and excluding outliers in the training data',
    'Similar to adversarial training, with this defence technique'];
  const needsAccordions = !['intro','evasion'].includes(stage);
  let accordionExpanded = 0, nextAccordion = null;
  if (accordions.length !== (needsAccordions ? 3 : 0))
    return {error:'The Defence techniques accordion count changed.'};
  for (let index = 0; index < accordions.length; index++) {
    const button = accordions[index];
    const panel = page.getElementById(button.getAttribute('aria-controls'));
    const expanded = button.getAttribute('aria-expanded');
    if (!visible(button) || clean(button.innerText) !== expectedAccordions[index] ||
        button.type !== 'button' || !panel ||
        !clean(panel.textContent).startsWith(panelStarts[index]) ||
        !['true','false'].includes(expanded))
      return {error:'The Defence techniques accordion activity changed.'};
    if (expanded === 'true') accordionExpanded++;
    else if (!nextAccordion) nextAccordion = button;
  }
  const cardStarts = [
    ['Differential privacy','A mathematical privacy notion that has been widely used'],
    ['Homomorphic encryption','Allows computations and predictions to be performed'],
    ['Secure multi-party computation (SMC)','Enables multiple parties to collaboratively']];
  const needsCards = ['inversion','scenario_intro','scenario','terminal'].includes(stage);
  let flipped = 0, nextCard = null;
  const cardButtons = [];
  if (cards.length !== (needsCards ? 3 : 0))
    return {error:'The Defence techniques flashcard count changed.'};
  for (let index = 0; index < cards.length; index++) {
    const card = cards[index];
    const front = card.querySelector('.flashcard-side--front');
    const back = card.querySelector('.flashcard-side--back');
    const buttons = [...card.querySelectorAll('button.flashcard-side-flip__btn')];
    if (!visible(card) || card.getAttribute('role') !== 'listitem' ||
        card.getAttribute('aria-hidden') !== 'false' || !front || !back ||
        buttons.length !== 2 ||
        !buttons[0].id.endsWith('-' + index + '-front-flip-btn') ||
        !buttons[1].id.endsWith('-' + index + '-back-flip-btn') ||
        clean(front.innerText) !== 'Click to flip ' + cardStarts[index][0] ||
        !clean(back.innerText).startsWith('Click to flip ' + cardStarts[index][1]))
      return {error:'The Defence techniques flashcard activity changed.'};
    const frontActive = buttons[0].tabIndex === 0 && buttons[1].tabIndex === -1 &&
      !front.classList.contains('flashcard-side--flipped') &&
      !back.classList.contains('flashcard-side--flipped');
    const backActive = buttons[0].tabIndex === -1 && buttons[1].tabIndex === 0 &&
      front.classList.contains('flashcard-side--flipped') &&
      back.classList.contains('flashcard-side--flipped');
    if (!frontActive && !backActive)
      return {error:'The Defence techniques flashcard state changed.'};
    if (backActive) flipped++;
    else if (!nextCard) nextCard = buttons[0];
    cardButtons.push(...buttons);
  }
  const stageMin = {intro:200,evasion:4500,poisoning:6500,inversion:10000,
    scenario_intro:12000,scenario:12000,terminal:12000};
  const stageScrollMin = {intro:0,evasion:4500,poisoning:6000,inversion:8500,
    scenario_intro:10000,scenario:10500,terminal:10500};
  const max = Math.max(0, wrap.scrollHeight - wrap.clientHeight);
  if (text.length < stageMin[stage] || wrap.clientHeight < 250 ||
      (stage === 'intro' ? max > 100 : max < stageScrollMin[stage]))
    return {error:'The Defence techniques reading area changed.'};
  if (['scenario_intro','scenario','terminal'].includes(stage) &&
      !text.includes('This activity helped identify effective AI security controls'))
    return {error:'The Defence techniques scenario summary changed.'};
  const scenarioButtons = scenario ? [...scenario.querySelectorAll('button,[role="button"]')]
    .filter(visible) : [];
  if (stage === 'scenario_intro' && !started && continues.length === 0) {
    // Rise can restore the scenario after removing its launch button but before
    // setting scenario-block--initiated. No new interactive control may appear.
    const staticButtons = new Set([...accordions,...cardButtons,
      ...page.querySelectorAll(
        'button.visually-hidden.btn-skip-to-lesson,button.nav-control__button,' +
        'button.nav-sidebar-header__search-btn,button.nav-sidebar-search__close'),
      ...page.querySelectorAll('button[data-rmiz-btn-zoom]'),
      ...page.querySelectorAll('button[data-rmiz-btn-unzoom]')]);
    if (scenarioButtons.length || [...page.querySelectorAll('button,[role="button"]')]
        .filter(visible).some(button => !staticButtons.has(button)) ||
        [...page.querySelectorAll('input')].some(input =>
          !input.matches('input[type="search"][disabled]')) ||
        page.querySelector('textarea,select,[contenteditable="true"]'))
      return {error:'The Defence techniques scenario transition has unknown controls.'};
    return {pending:'The Defence techniques scenario is restoring.'};
  }
  if (continues.length !== (['scenario','terminal'].includes(stage) ? 0 : 1) ||
      (continues.length && (continues[0].type !== 'button' ||
        clean(continues[0].innerText) !==
          (stage === 'scenario_intro' ? 'Bring on the scenario!' : 'CONTINUE'))))
    return {error:'The Defence techniques continuation changed.'};
  const choices = [
    {prompt:'Can I run a few questions by you?', options:['Sure thing'], pick:0},
    {prompt:'slightly tweaking a query in subtle ways',
      options:['Adversarial training','Differential privacy','Implementing access controls'],pick:0},
    {prompt:'prevent membership inference attacks on this system',
      options:['Access control','Differential privacy','Defensive distillation'],pick:1},
    {prompt:'How can we prevent someone from stealing our AI system',
      options:['Use watermarking','Implement access controls','Encrypt training data'],pick:0},
    {prompt:'Are you happy with the advice you\'ve given or would you like to revisit the scenario?',
      options:["Let's leave it there, we can pick this up again later.",
        'I want to do the scenario again.'],pick:0}
  ];
  const dialogueEndings = [
    'How to improve AI defence against potential attacks on the system at XYZCo?',
    "Hi I'm Chantelle Patel from the Business intelligence department of XYZCo.",
    "who've done well here in the past.",
    'protect the system from potential attacks.',
    "Thanks, let's start.",
    'so the AI learns to recognise and respond correctly to them.',
    'membership unsuccessful.',
    'just one more to lock in.',
    'to trace and detect stolen systems.',
    'try it again later.'];
  let scenarioState = null, scenarioPrompt = null, scenarioOptions = [], choice = null;
  if (stage === 'scenario_intro' &&
      scenarioButtons.some(button => button !== continues[0]))
    return {error:'The Defence techniques scenario preview has unexpected controls.'};
  if (stage === 'scenario') {
    const responses = [...scenario.querySelectorAll('button.scenario-block__response')]
      .filter(visible);
    if (responses.length) {
      scenarioPrompt = clean(scenario.querySelector('.scenario-block__dialogue')?.innerText);
      choice = choices.find(entry => scenarioPrompt.includes(entry.prompt));
      scenarioOptions = responses.map(button => clean(button.innerText).replace(/^\d+\s*/, ''));
      if (!choice || responses.length !== choice.options.length ||
          scenarioButtons.length !== responses.length ||
          scenarioOptions.some((option,index) => option !== choice.options[index]))
        return {error:'The Defence techniques scenario decision changed.'};
      scenarioState = 'choice';
    } else if (!scenarioButtons.length) {
      // Rise briefly removes the previous CONTINUE control before mounting the
      // next dialogue. The caller polls pending snapshots for at most 20 seconds.
      return {pending:'The Defence techniques scenario is transitioning.'};
    } else if (scenarioButtons.length === 1 &&
        clean(scenarioButtons[0].innerText) === 'CONTINUE' &&
        scenarioButtons[0].matches(
          'button.scenario-block__text__continue,button.scenario-block__dialogue__button') &&
        dialogueEndings.some(ending =>
          clean(scenario.innerText).endsWith(ending + ' CONTINUE'))) {
      scenarioState = 'continue';
    } else return {error:'The Defence techniques scenario controls changed.'};
  } else if (stage === 'terminal') {
    if (scenarioButtons.length !== 1 ||
        !scenarioButtons[0].matches('button.scenario-block__text__end') ||
        clean(scenarioButtons[0].innerText) !== 'START OVER' ||
        !clean(scenario.innerText).includes('I feel much more confident'))
      return {error:'The Defence techniques scenario did not reach its audited end.'};
    scenarioState = 'terminal';
  }
  const zoom = [...page.querySelectorAll('button[data-rmiz-btn-zoom]')];
  const unzoom = [...page.querySelectorAll('button[data-rmiz-btn-unzoom]')];
  const nav = [...page.querySelectorAll(
    'button.visually-hidden.btn-skip-to-lesson,button.nav-control__button,' +
    'button.nav-sidebar-header__search-btn,button.nav-sidebar-search__close')];
  const allowed = new Set([...nav,...zoom,...unzoom,...accordions,...cardButtons,
    ...continues,...scenarioButtons]);
  const inputs = [...page.querySelectorAll('input')];
  const forms = [...page.querySelectorAll('form')];
  if (nav.length !== 4 || zoom.length > 5 || unzoom.length > 5 ||
      zoom.some(button => !button.closest('[data-rmiz]') ||
        !/^Zoom image/.test(button.getAttribute('aria-label') || '')) ||
      unzoom.some(button => visible(button) ||
        button.getAttribute('aria-label') !== 'Unzoom image') ||
      [...page.querySelectorAll('button,[role="button"]')]
        .filter(visible).some(button => !allowed.has(button)) ||
      inputs.length !== 1 || !inputs[0].matches('input[type="search"][disabled]') ||
      forms.length !== 1 || !forms[0].matches('form.nav-sidebar-search--disabled') ||
      forms[0].querySelectorAll('input').length !== 1 ||
      page.querySelector('textarea,select,[contenteditable="true"]') ||
      document.querySelector('button,input,textarea,select,form,[contenteditable="true"]') ||
      driver.querySelector('button,input,textarea,select,form,[contenteditable="true"]'))
    return {error:'The Defence techniques lesson has an unhandled interactive control.'};
  let clicked = null;
  if (request.action === 'scroll') {
    if (stage === 'scenario')
      return {error:'The active Defence techniques scenario cannot be read as static text.'};
    wrap.scrollTop = Math.min(max, wrap.scrollTop +
      Math.max(300, Math.floor(wrap.clientHeight * 0.7)));
  } else if (request.action === 'continue') {
    if (!['intro','evasion','poisoning','inversion','scenario_intro'].includes(stage) ||
        wrap.scrollTop < max - 2 || (stage === 'poisoning' && accordionExpanded !== 3) ||
        (stage === 'inversion' && flipped !== 3))
      return {error:'The Defence techniques stage is not ready to continue.'};
    continues[0].click();
    clicked = stage === 'scenario_intro' ? 'scenario_start' : 'CONTINUE';
  } else if (request.action === 'expand') {
    if (!needsAccordions || !nextAccordion)
      return {error:'No unopened Defence techniques accordion remains.'};
    nextAccordion.scrollIntoView({block:'center'});
    if (!visible(nextAccordion)) return {error:'The Defence techniques accordion is not visible.'};
    nextAccordion.click(); clicked = 'expand';
  } else if (request.action === 'flip') {
    if (!needsCards || accordionExpanded !== 3 || !nextCard)
      return {error:'No unviewed Defence techniques flashcard remains.'};
    nextCard.scrollIntoView({block:'center'});
    if (!visible(nextCard) || !visible(nextCard.closest('li.flashcard')))
      return {error:'The Defence techniques flashcard is not visible.'};
    nextCard.click(); clicked = 'flip';
  } else if (request.action === 'scenario_continue') {
    if (stage !== 'scenario' || scenarioState !== 'continue')
      return {error:'The Defence techniques scenario has no audited continuation.'};
    scenarioButtons[0].click(); clicked = 'scenario_continue';
  } else if (request.action === 'scenario_choice') {
    if (stage !== 'scenario' || scenarioState !== 'choice' || !choice)
      return {error:'The Defence techniques scenario has no audited answer.'};
    const button = scenarioButtons[choice.pick];
    if (!visible(button)) return {error:'The Defence techniques answer is not visible.'};
    button.click(); clicked = 'scenario_choice';
  } else if (request.action !== 'inspect') {
    return {error:'Unsupported Defence techniques action.'};
  }
  return {url:location.href, lesson:'LESSON 1 OF 1', progress, stage,
    top:wrap.scrollTop,max,text_length:text.length,accordion_count:accordions.length,
    accordion_expanded:accordionExpanded,card_count:cards.length,flipped_count:flipped,
    scenario_state:scenarioState,scenario_prompt:scenarioPrompt,
    scenario_options:scenarioOptions,scenario_text:scenario ? clean(scenario.innerText) : null,
    clicked};
})"""


GENAI4_JS = r"""(request => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  if (location.href !== request.frame ||
      request.heading !== 'Defences for attacks on GenAI models' ||
      request.lesson !== 'duv5gTQ28HWXmT1REiSOU3TUgjnK2G_N')
    return {error:'The audited GenAI defences frame or lesson identity changed.'};
  if (document.readyState !== 'complete')
    return {pending:'The GenAI defences frame is loading.'};
  const base = request.frame.replace(/index\.html$/, '');
  const containers = [...document.querySelectorAll('iframe[title="container"]')];
  if (!containers.length && !document.querySelectorAll('iframe').length)
    return {pending:'The GenAI defences container is mounting.'};
  if (containers.length !== 1 || document.querySelectorAll('iframe').length !== 1 ||
      containers[0].src !== base + 'scorm-content/scormdriver/indexAPI.html')
    return {error:'The GenAI defences container changed.'};
  const driver = containers[0].contentDocument;
  if (!driver || driver.readyState !== 'complete')
    return {pending:'The GenAI defences container is loading.'};
  const frames = [...driver.querySelectorAll('iframe#content-frame')];
  const driverFrames = [...driver.querySelectorAll('iframe')];
  if (!frames.length && !driverFrames.length)
    return {pending:'The GenAI defences content is mounting.'};
  const auxiliary = driverFrames.filter(frame => frame !== frames[0]);
  if (frames.length !== 1 || driverFrames.length !== 4 || driverFrames[0] !== frames[0] ||
      clean(frames[0].title) !== 'Defences for attacks on GenAI models' ||
      auxiliary.some((frame,index) => clean(frame.title) !== 'Intentionally blank' ||
        frame.src !== base + 'scorm-content/scormdriver/' +
          (index === 0 ? 'AICCComm.html' : 'blank.html')))
    return {error:'The GenAI defences content frame changed.'};
  const page = frames[0].contentDocument;
  if (!page || page.readyState !== 'complete' ||
      /\/(?:blank|indexAPI)\.html$/.test(page.URL))
    return {pending:'The GenAI defences content is loading.'};
  if (page.URL !== base + 'scorm-content/scormcontent/index.html#/lessons/' + request.lesson ||
      !['Defences for attacks on GenAI models',
        'Defences for attacks on GenAI models - Defences for attacks on GenAI models']
        .includes(page.title) ||
      page.querySelectorAll('iframe').length)
    return {error:'The GenAI defences lesson identity changed.'};
  const visible = node => {
    if (!node || !node.getClientRects().length) return false;
    for (let current = node; current; current = current.parentElement) {
      const style = page.defaultView.getComputedStyle(current);
      if (current.hidden || current.inert || current.getAttribute('aria-hidden') === 'true' ||
          style.display === 'none' || style.visibility === 'hidden' ||
          style.visibility === 'collapse') return false;
    }
    return true;
  };
  const wraps = [...page.querySelectorAll('div.page-wrap')];
  const wrap = wraps[0];
  const text = wrap ? clean(wrap.innerText) : '';
  const progress = clean(page.querySelector('.nav-sidebar-header__progress-text')?.innerText);
  if (wraps.length !== 1 ||
      !text.startsWith('Defences for attacks on GenAI models LESSON 1 OF 1 Generative AI (GenAI) models are trained on large datasets') ||
      !/^(?:0|100)% COMPLETE$/.test(progress) ||
      !/\bLESSON 1 OF 1\b/.test(clean(page.body?.innerText)))
    return {error:'This is not the audited GenAI defences lesson.'};
  const second = text.includes('Security-enhancing technologies in AI application development');
  const scenario = page.querySelector('.scenario-block');
  const started = scenario?.classList.contains('scenario-block--initiated') || false;
  const terminal = started && clean(scenario.innerText).startsWith('Scenario End ');
  const stage = !second ? 'reading' : terminal ? 'terminal' :
    started ? 'scenario' : 'scenario_intro';
  if (['scenario_intro','scenario','terminal'].includes(stage) &&
      !text.includes('This activity helped highlight the unique challenges of LLM-based systems'))
    return {error:'The GenAI defences scenario summary changed.'};
  const max = Math.max(0, wrap.scrollHeight - wrap.clientHeight);
  // The completed scenario can render at 6067 normalized characters. Its
  // exact Scenario End, START OVER, and summary checks below remain required.
  const minLength = {reading:3500,scenario_intro:5000,scenario:5200,terminal:6000};
  const minScroll = {reading:3000,scenario_intro:4500,scenario:5000,terminal:6000};
  if (text.length < minLength[stage] || wrap.clientHeight < 250 ||
      max < minScroll[stage])
    return {error:'The GenAI defences reading area changed.'};
  const accordions = [...page.querySelectorAll('button.blocks-accordion__header')];
  const accordionLabels = ['Text moderation','Image moderation','Multimodal moderation',
    'Output filtering','Input filtering'];
  const panelStarts = [
    'Analyses, reviews, and filters harmful content in input prompts and outputs of GenAI models.',
    'This blocks images that contain offensive or unsafe content.',
    'Multimodal content moderation analyses and reviews content in multiple modes',
    'Analyses and filters if the output from the GenAI models',
    'Analyses and blocks if the input (prompt) to the GenAI models'];
  if (accordions.length !== 5) return {error:'The GenAI defences accordion count changed.'};
  let openIndex = -1;
  for (let index = 0; index < 5; index++) {
    const button = accordions[index];
    const panel = page.getElementById(button.getAttribute('aria-controls'));
    const expanded = button.getAttribute('aria-expanded');
    if (!visible(button) || button.type !== 'button' ||
        clean(button.innerText) !== accordionLabels[index] || !panel ||
        !clean(panel.textContent).startsWith(panelStarts[index]) ||
        !['true','false'].includes(expanded))
      return {error:'The GenAI defences accordion activity changed.'};
    if (expanded === 'true') {
      if (openIndex !== -1 || !clean(panel.innerText).startsWith(panelStarts[index]))
        return {error:'The GenAI defences accordion state changed.'};
      openIndex = index;
    }
  }
  const continues = [...page.querySelectorAll('button.continue-btn')];
  const scenarioButtons = scenario ? [...scenario.querySelectorAll('button,[role="button"]')]
    .filter(visible) : [];
  const nav = [...page.querySelectorAll(
    'button.visually-hidden.btn-skip-to-lesson,button.nav-control__button,' +
    'button.nav-sidebar-header__search-btn,button.nav-sidebar-search__close')];
  const zoom = [...page.querySelectorAll('button[data-rmiz-btn-zoom]')];
  const unzoom = [...page.querySelectorAll('button[data-rmiz-btn-unzoom]')];
  const staticButtons = new Set([...nav,...zoom,...unzoom,...accordions]);
  if (stage === 'scenario_intro' && !started && continues.length === 0) {
    // The first scenario CONTINUE can mount just before Rise adds its initiated
    // class. Wait for the class rather than treating the known control as new.
    const firstQuestion = 'How to improve appropriate AI security controls on the system at XYZCo?';
    const firstControlMounting = scenarioButtons.length === 1 &&
      scenarioButtons[0].matches('button.scenario-block__text__continue') &&
      clean(scenarioButtons[0].innerText) === 'CONTINUE' &&
      clean(scenario?.innerText).endsWith(firstQuestion + ' CONTINUE');
    const transitionButtons = new Set([...staticButtons,
      ...(firstControlMounting ? scenarioButtons : [])]);
    const transitionInputs = [...page.querySelectorAll('input')];
    const transitionForms = [...page.querySelectorAll('form')];
    if ((!firstControlMounting && scenarioButtons.length) ||
        [...page.querySelectorAll('button,[role="button"]')]
          .filter(visible).some(button => !transitionButtons.has(button)) ||
        transitionInputs.length !== 1 ||
        !transitionInputs[0].matches('input[type="search"][disabled]') ||
        transitionForms.length !== 1 ||
        !transitionForms[0].matches('form.nav-sidebar-search--disabled') ||
        page.querySelector('textarea,select,[contenteditable="true"]'))
      return {error:'The GenAI defences scenario transition has unknown controls.'};
    return {pending:'The GenAI defences scenario is restoring.'};
  }
  if (continues.length !== (['scenario','terminal'].includes(stage) ? 0 : 1) ||
      (continues.length && (continues[0].type !== 'button' ||
        clean(continues[0].innerText) !==
          (stage === 'scenario_intro' ? 'Bring on the scenario!' : 'CONTINUE'))))
    return {error:'The GenAI defences continuation changed.'};
  const choices = [
    {prompt:'Hi again! Remember me? Chantelle Patel from the Business intelligence department.',
      options:['Hi Chantelle, what can I help with today?'],pick:0},
    {prompt:'You were so helpful last time, could I run a few ideas past you again?',
      options:['Happy to help'],pick:0},
    {prompt:'unique challenge in implementing AI security controls for LLM-based AI systems compared to general AI systems?',
      options:['Prompt injection','Model stealing','Training data poisoning'],pick:0},
    {prompt:'How do you think metaprompts help the AI system to prevent jailbreaking?',
      options:['Analyses, reviews, and filters harmful content in input prompts and outputs',
        'Analyses and filters outputs',
        'Provide instructions to the system on what it should output, or how to behave'],pick:2},
    {prompt:'How can we improve the accuracy, trust and relevance of the outputs provided by our recommendation system?',
      options:['Use Retrieval-Augmented Generation (RAG) services',
        'Use content moderation techniques','Implement prompt engineering techniques'],pick:0},
    {prompt:"Are you happy with the advice you've given or would you like to revisit the scenario?",
      options:["Let's leave it there, we can pick this up again later.",
        'I want to do the scenario again.'],pick:0}
  ];
  const dialogueEndings = [
    'How to improve appropriate AI security controls on the system at XYZCo?',
    'Well ....',
    'With the growing focus on Large Language Models (LLMs), our company is now planning to shift towards developing an LLM-based job recommendation system.',
    "I was hoping you'd say that.",
    'generating harmful content.',
    'what to output in certain scenarios.',
    'integrates them in the system’s outputs.',
    'try it again later.'];
  let scenarioState = null, scenarioPrompt = null, scenarioOptions = [], choice = null;
  if (stage === 'scenario_intro' &&
      scenarioButtons.some(button => button !== continues[0]))
    return {error:'The GenAI defences scenario preview has unexpected controls.'};
  if (stage === 'scenario') {
    const responses = [...scenario.querySelectorAll('button.scenario-block__response')]
      .filter(visible);
    if (responses.length) {
      scenarioPrompt = clean(scenario.querySelector('.scenario-block__dialogue')?.innerText);
      choice = choices.find(entry => scenarioPrompt.includes(entry.prompt));
      scenarioOptions = responses.map(button => clean(button.innerText).replace(/^\d+\s*/, ''));
      if (!choice || responses.length !== choice.options.length ||
          scenarioButtons.length !== responses.length ||
          scenarioOptions.some((option,index) => option !== choice.options[index]))
        return {error:'The GenAI defences scenario decision changed.'};
      scenarioState = 'choice';
    } else if (!scenarioButtons.length) {
      return {pending:'The GenAI defences scenario is transitioning.'};
    } else if (scenarioButtons.length === 1 &&
        clean(scenarioButtons[0].innerText) === 'CONTINUE' &&
        scenarioButtons[0].matches(
          'button.scenario-block__text__continue,button.scenario-block__dialogue__button') &&
        dialogueEndings.some(ending =>
          clean(scenario.innerText).endsWith(ending + ' CONTINUE'))) {
      scenarioState = 'continue';
    } else return {error:'The GenAI defences scenario controls changed.'};
  } else if (stage === 'terminal') {
    if (scenarioButtons.length !== 1 ||
        !scenarioButtons[0].matches('button.scenario-block__text__end') ||
        clean(scenarioButtons[0].innerText) !== 'START OVER' ||
        !clean(scenario.innerText).includes("I'll recommend secure measures for the LLM-based system."))
      return {error:'The GenAI defences scenario did not reach its audited end.'};
    scenarioState = 'terminal';
  }
  const allowed = new Set([...staticButtons,...continues,...scenarioButtons]);
  const inputs = [...page.querySelectorAll('input')];
  const forms = [...page.querySelectorAll('form')];
  if (nav.length !== 4 || zoom.length > 2 || unzoom.length > 2 ||
      zoom.some(button => !button.closest('[data-rmiz]') ||
        !/^Zoom image/.test(button.getAttribute('aria-label') || '')) ||
      unzoom.some(button => visible(button) ||
        button.getAttribute('aria-label') !== 'Unzoom image') ||
      [...page.querySelectorAll('button,[role="button"]')]
        .filter(visible).some(button => !allowed.has(button)) ||
      inputs.length !== 1 || !inputs[0].matches('input[type="search"][disabled]') ||
      forms.length !== 1 || !forms[0].matches('form.nav-sidebar-search--disabled') ||
      forms[0].querySelectorAll('input').length !== 1 ||
      page.querySelector('textarea,select,[contenteditable="true"],li.flashcard') ||
      document.querySelector('button,input,textarea,select,form,[contenteditable="true"]') ||
      driver.querySelector('button,input,textarea,select,form,[contenteditable="true"]'))
    return {error:'The GenAI defences lesson has an unhandled interactive control.'};
  let clicked = null;
  if (request.action === 'scroll') {
    if (stage === 'scenario')
      return {error:'The active GenAI defences scenario cannot be read as static text.'};
    wrap.scrollTop = Math.min(max, wrap.scrollTop +
      Math.max(300, Math.floor(wrap.clientHeight * 0.7)));
  } else if (request.action === 'open_accordion') {
    const index = request.index;
    if (stage !== 'reading' || !Number.isInteger(index) || index < 0 || index >= 5 ||
        openIndex === index)
      return {error:'No audited GenAI defences accordion is ready to open.'};
    accordions[index].scrollIntoView({block:'center'});
    if (!visible(accordions[index]))
      return {error:'The GenAI defences accordion is not visible.'};
    accordions[index].click(); clicked = 'open_accordion';
  } else if (request.action === 'continue') {
    if (!['reading','scenario_intro'].includes(stage) ||
        wrap.scrollTop < max - 2 ||
        (stage === 'reading' && request.accordions_reviewed !== true))
      return {error:'The GenAI defences stage is not ready to continue.'};
    continues[0].click(); clicked = stage === 'scenario_intro' ? 'scenario_start' : 'CONTINUE';
  } else if (request.action === 'scenario_continue') {
    if (stage !== 'scenario' || scenarioState !== 'continue')
      return {error:'The GenAI defences scenario has no audited continuation.'};
    scenarioButtons[0].click(); clicked = 'scenario_continue';
  } else if (request.action === 'scenario_choice') {
    if (stage !== 'scenario' || scenarioState !== 'choice' || !choice)
      return {error:'The GenAI defences scenario has no audited answer.'};
    const button = scenarioButtons[choice.pick];
    if (!visible(button)) return {error:'The GenAI defences answer is not visible.'};
    button.click(); clicked = 'scenario_choice';
  } else if (request.action !== 'inspect')
    return {error:'Unsupported GenAI defences action.'};
  return {url:location.href,lesson:'LESSON 1 OF 1',progress,stage,
    top:wrap.scrollTop,max,text_length:text.length,accordion_count:accordions.length,
    accordion_open_index:openIndex,scenario_state:scenarioState,
    scenario_prompt:scenarioPrompt,scenario_options:scenarioOptions,
    scenario_text:scenario ? clean(scenario.innerText) : null,clicked};
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


async def open_scorm_frame(owner, frame_url=THREAT_MODEL_FRAME, *, probes=40, delay=.25):
    """Wait briefly for the exact OOPIF child of this lesson, without activation."""
    from coursera_start import CDP_URL, ChromeTab, StopRun, local_json

    source = urlsplit(getattr(owner, "address", ""))
    source_id = source.path.rsplit("/", 1)[-1]
    if source.scheme != "ws" or not source.path.startswith("/devtools/page/") or not source_id:
        raise StopRun("The Coursera lesson Chrome target cannot be identified.")
    # Coursera inserts the iframe element before Chrome exposes its OOPIF
    # target (and sometimes before /json/list exposes the target socket).
    # Keep the exact parent/asset identity and bound the wait; never attach to
    # another tab's matching asset or choose among ambiguous matches.
    for attempt in range(probes):
        targets = (await owner.call("Target.getTargets")).get("targetInfos", [])
        matches = [target for target in targets if isinstance(target, dict) and
                   target.get("type") == "iframe" and target.get("parentId") == source_id and
                   target.get("url") == frame_url and
                   isinstance(target.get("targetId"), str)]
        if len(matches) > 1:
            raise StopRun("The unique audited SCORM iframe target was not found.")
        if len(matches) == 1:
            pages = await asyncio.to_thread(local_json, CDP_URL + "/json/list")
            target_id = matches[0]["targetId"]
            sockets = [page.get("webSocketDebuggerUrl") for page in pages
                       if isinstance(page, dict) and page.get("id") == target_id and
                       page.get("type") == "iframe" and page.get("url") == frame_url and
                       isinstance(page.get("webSocketDebuggerUrl"), str)]
            if len(sockets) > 1:
                raise StopRun("The audited SCORM iframe has no unique debugging endpoint.")
            if len(sockets) == 1:
                frame = ChromeTab(sockets[0])
                await frame.connect()
                return frame
        if attempt + 1 < probes:
            await asyncio.sleep(delay)
    raise StopRun("The unique audited SCORM iframe target was not found within the probe limit.")


async def _exercise_snapshot(frame, request, action, *, script=EXERCISE_JS):
    from coursera_start import StopRun

    for probe in range(40 if action == "inspect" else 1):
        snapshot = await frame.evaluate(_script(script, {**request, "action": action}))
        if isinstance(snapshot, dict) and snapshot.get("pending"):
            if action != "inspect":
                raise StopRun("The SCORM exercise changed while an action was attempted.")
            await asyncio.sleep(0.5)
            continue
        return _checked(snapshot, frame_url=request["frame"])
    raise StopRun("The SCORM exercise did not finish loading within 20 seconds.")


async def _read_exercise_stage(frame, request, prior, stage, *, script=EXERCISE_JS):
    """Scroll one stable reading screen to its end before advancing."""
    from coursera_start import StopRun

    if prior.get("stage") != stage or (
            script == EXERCISE_JS and prior.get("accordion_expanded") is not True):
        raise StopRun("The SCORM exercise reading stage changed before it was read.")
    for _ in range(100):
        if prior.get("top", -1) >= prior.get("max", 0) - 2:
            return prior
        current = await _exercise_snapshot(frame, request, "scroll", script=script)
        if (current.get("stage") != stage or
                current.get("max") != prior.get("max") or
                current.get("text_length") != prior.get("text_length") or
                current.get("top", 0) <= prior.get("top", -1) or
                (script == EXERCISE_JS and current.get("accordion_expanded") is not True)):
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


async def complete_poisoning_scorm(runner, item, path, heading):
    """Read both audited poisoning screens; require Coursera's automatic mark."""
    from coursera_start import StopRun

    if path != POISONING_PATH or heading != "Poisoning attacks":
        raise StopRun("This poisoning lesson has not been audited.")
    if await runner.verified_item(item):
        return
    request = {"path": path, "heading": heading, "frame": POISONING_FRAME}
    outer = _checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "inspect"})))
    if (outer.get("ready") != "complete" or outer.get("frame") != POISONING_FRAME or
            outer.get("mark_count") != 1 or outer.get("mark_disabled") or
            outer.get("modal") or outer.get("other")):
        raise StopRun("The poisoning lesson shell changed before its reading was completed.")
    frame = await open_scorm_frame(runner.tab, POISONING_FRAME)
    try:
        inner = {"frame": POISONING_FRAME, "heading": heading,
                 "lesson": POISONING_LESSON,
                 "continue_label": "DO IT YOURSELF EXERCISE NINE"}
        prior = await _exercise_snapshot(frame, inner, "inspect", script=POISONING_JS)
        if prior.get("stage") == "reading":
            prior = await _read_exercise_stage(frame, inner, prior, "reading",
                                               script=POISONING_JS)
            clicked = await _exercise_snapshot(frame, inner, "continue", script=POISONING_JS)
            if clicked.get("clicked") != inner["continue_label"]:
                raise StopRun("The poisoning lesson continuation was not acknowledged.")
            initial_length = prior.get("text_length")
            for _ in range(20):
                await asyncio.sleep(0.25)
                prior = await _exercise_snapshot(frame, inner, "inspect", script=POISONING_JS)
                if (prior.get("stage") == "continued" and
                        isinstance(initial_length, int) and
                        prior.get("text_length", 0) > initial_length):
                    break
            else:
                raise StopRun("The poisoning lesson did not reveal its second reading screen.")
            runner.event("scorm_poisoning_exercise_opened", key=item["key"])
        if prior.get("stage") != "continued":
            raise StopRun("The poisoning lesson did not reach its final reading screen.")
        prior = await _read_exercise_stage(frame, inner, prior, "continued",
                                           script=POISONING_JS)
        if prior.get("top", -1) < prior.get("max", 0) - 2:
            raise StopRun("The poisoning lesson final reading screen was not read to the end.")
    finally:
        await frame.close()
    if not await runner.verified_item(item, wait=20):
        raise StopRun("Coursera did not show a green completion mark for the poisoning lesson.")


async def complete_summary_scorm(runner, item, path, heading):
    """Read the audited Summary and use Coursera's explicit completion action."""
    from coursera_start import StopRun

    if path != SUMMARY_PATH or heading != "Summary":
        raise StopRun("This SCORM Summary has not been audited.")
    if await runner.verified_item(item):
        return
    request = {"path": path, "heading": heading, "frame": SUMMARY_FRAME,
               "frame_title": "Topic 3: Summary"}
    outer = _checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "inspect"})))
    if (outer.get("ready") != "complete" or outer.get("frame") != SUMMARY_FRAME or
            outer.get("mark_count") != 1 or outer.get("mark_disabled") or
            outer.get("modal") or outer.get("other")):
        raise StopRun("The audited Summary shell changed before reading.")
    frame = await open_scorm_frame(runner.tab, SUMMARY_FRAME)
    try:
        inner = {"frame": SUMMARY_FRAME, "heading": heading, "lesson": SUMMARY_LESSON}
        prior = await _exercise_snapshot(frame, inner, "inspect", script=SUMMARY_JS)
        prior = await _read_exercise_stage(frame, inner, prior, "reading", script=SUMMARY_JS)
        if prior.get("top", -1) < prior.get("max", 0) - 2 or prior.get("checkbox_count") != 4:
            raise StopRun("The audited Summary was not read to the end.")
    finally:
        await frame.close()
    marked = _checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "mark", "inner_verified": True})))
    if marked.get("clicked") != "mark":
        raise StopRun("Coursera did not acknowledge the Summary Mark action.")
    runner.event("scorm_summary_mark_clicked", key=item["key"])
    if not await runner.verified_item(item, wait=20):
        raise StopRun("Coursera did not show a green completion mark for the Summary.")


async def complete_overview4_scorm(runner, item, path, heading):
    """Read the audited Topic 4 Overview before Coursera marks it complete."""
    from coursera_start import StopRun

    if path != OVERVIEW4_PATH or heading != "Overview":
        raise StopRun("This SCORM Topic 4 Overview has not been audited.")
    if await runner.verified_item(item):
        return
    request = {"path": path, "heading": heading, "frame": OVERVIEW4_FRAME,
               "frame_title": "Topic 4: Overview"}
    outer = _checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "inspect"})))
    if (outer.get("ready") != "complete" or outer.get("frame") != OVERVIEW4_FRAME or
            outer.get("mark_count") != 1 or outer.get("mark_disabled") or
            outer.get("modal") or outer.get("other")):
        raise StopRun("The audited Topic 4 Overview shell changed before reading.")
    frame = await open_scorm_frame(runner.tab, OVERVIEW4_FRAME)
    try:
        inner = {"frame": OVERVIEW4_FRAME, "heading": heading, "lesson": OVERVIEW4_LESSON}
        prior = await _exercise_snapshot(frame, inner, "inspect", script=OVERVIEW4_JS)
        prior = await _read_exercise_stage(frame, inner, prior, "reading", script=OVERVIEW4_JS)
        if prior.get("top", -1) < prior.get("max", 0) - 2 or prior.get("checkbox_count") != 4:
            raise StopRun("The audited Topic 4 Overview was not read to the end.")
    finally:
        await frame.close()
    marked = _checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "mark", "inner_verified": True})))
    if marked.get("clicked") != "mark":
        raise StopRun("Coursera did not acknowledge the Topic 4 Overview Mark action.")
    runner.event("scorm_overview4_mark_clicked", key=item["key"])
    if not await runner.verified_item(item, wait=20):
        raise StopRun("Coursera did not show a green completion mark for Topic 4 Overview.")


async def complete_introduction4_scorm(runner, item, path, heading):
    """Read Topic 4 Introduction, view its three cards, then require a green mark."""
    from coursera_start import StopRun

    if path != INTRODUCTION4_PATH or heading != "Introduction":
        raise StopRun("This SCORM Topic 4 Introduction has not been audited.")
    if await runner.verified_item(item):
        return
    request = {"path": path, "heading": heading, "frame": INTRODUCTION4_FRAME,
               "frame_title": "Topic 4: Introduction"}
    outer = _checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "inspect"})))
    if (outer.get("ready") != "complete" or outer.get("frame") != INTRODUCTION4_FRAME or
            outer.get("mark_count") != 1 or outer.get("mark_disabled") or
            outer.get("modal") or outer.get("other")):
        raise StopRun("The audited Topic 4 Introduction shell changed before reading.")
    frame = await open_scorm_frame(runner.tab, INTRODUCTION4_FRAME)
    try:
        inner = {"frame": INTRODUCTION4_FRAME, "heading": heading,
                 "lesson": INTRODUCTION4_LESSON}
        prior = await _exercise_snapshot(frame, inner, "inspect", script=INTRODUCTION4_JS)
        if prior.get("stage") == "reading":
            prior = await _read_exercise_stage(frame, inner, prior, "reading",
                                               script=INTRODUCTION4_JS)
            if prior.get("top", -1) < prior.get("max", 0) - 2:
                raise StopRun("The Topic 4 Introduction reading was not finished.")
            continued = await _exercise_snapshot(frame, inner, "continue",
                                                  script=INTRODUCTION4_JS)
            if continued.get("clicked") != "CONTINUE":
                raise StopRun("The Topic 4 Introduction continuation was not acknowledged.")
            runner.event("scorm_introduction4_continued", key=item["key"])
            for _ in range(16):
                await asyncio.sleep(0.25)
                prior = await _exercise_snapshot(frame, inner, "inspect",
                                                 script=INTRODUCTION4_JS)
                if prior.get("stage") == "continued":
                    break
            else:
                raise StopRun("The Topic 4 Introduction did not reveal its flashcards.")
        if prior.get("stage") != "continued" or prior.get("card_count") != 3:
            raise StopRun("The Topic 4 Introduction did not reach its audited final flashcard page.")
        for _ in range(3):
            flipped_before = prior.get("flipped_count")
            if flipped_before == 3:
                break
            if not isinstance(flipped_before, int) or flipped_before < 0 or flipped_before > 2:
                raise StopRun("The Topic 4 Introduction flashcard count changed.")
            clicked = await _exercise_snapshot(frame, inner, "flip",
                                               script=INTRODUCTION4_JS)
            if clicked.get("clicked") != "flip":
                raise StopRun("A Topic 4 Introduction flashcard did not open.")
            for _ in range(10):
                await asyncio.sleep(0.2)
                prior = await _exercise_snapshot(frame, inner, "inspect",
                                                 script=INTRODUCTION4_JS)
                if (prior.get("stage") == "continued" and
                        prior.get("flipped_count") == flipped_before + 1):
                    break
            else:
                raise StopRun("A Topic 4 Introduction flashcard did not reveal its back.")
        if prior.get("flipped_count") != 3:
            raise StopRun("All Topic 4 Introduction flashcards were not viewed.")
        prior = await _read_exercise_stage(frame, inner, prior, "continued",
                                           script=INTRODUCTION4_JS)
        if (prior.get("top", -1) < prior.get("max", 0) - 2 or
                prior.get("card_count") != 3 or prior.get("flipped_count") != 3):
            raise StopRun("The Topic 4 Introduction final reading was not finished.")
    finally:
        await frame.close()
    marked = _checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "mark", "inner_verified": True})))
    if marked.get("clicked") != "mark":
        raise StopRun("Coursera did not acknowledge the Topic 4 Introduction Mark action.")
    runner.event("scorm_introduction4_mark_clicked", key=item["key"])
    if not await runner.verified_item(item, wait=20):
        raise StopRun("Coursera did not show a green completion mark for Topic 4 Introduction.")


async def complete_defence4_scorm(runner, item, path, heading):
    """Finish the audited reading, disclosure, flashcard and scenario sequence."""
    from coursera_start import StopRun

    if path != DEFENCE4_PATH or heading != "Defence techniques and strategies":
        raise StopRun("This Defence techniques SCORM lesson has not been audited.")
    if await runner.verified_item(item):
        return
    request = {"path": path, "heading": heading, "frame": DEFENCE4_FRAME}
    outer = _checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "inspect"})))
    if (outer.get("ready") != "complete" or outer.get("frame") != DEFENCE4_FRAME or
            outer.get("mark_count") != 1 or outer.get("mark_disabled") or
            outer.get("modal") or outer.get("other")):
        raise StopRun("The audited Defence techniques shell changed before reading.")
    frame = await open_scorm_frame(runner.tab, DEFENCE4_FRAME)
    try:
        inner = {"frame": DEFENCE4_FRAME, "heading": heading,
                 "lesson": DEFENCE4_LESSON}
        prior = await _exercise_snapshot(frame, inner, "inspect", script=DEFENCE4_JS)
        for step in range(40):
            stage = prior.get("stage")
            if stage in {"poisoning", "inversion", "scenario_intro", "scenario", "terminal"}:
                for _ in range(3):
                    expanded_before = prior.get("accordion_expanded")
                    if expanded_before == 3:
                        break
                    if not isinstance(expanded_before, int) or not 0 <= expanded_before <= 2:
                        raise StopRun("The Defence techniques accordion count changed.")
                    clicked = await _exercise_snapshot(frame, inner, "expand",
                                                       script=DEFENCE4_JS)
                    if clicked.get("clicked") != "expand":
                        raise StopRun("A Defence techniques accordion did not open.")
                    for _ in range(12):
                        await asyncio.sleep(0.2)
                        prior = await _exercise_snapshot(frame, inner, "inspect",
                                                         script=DEFENCE4_JS)
                        if (prior.get("stage") == stage and
                                prior.get("accordion_expanded") == expanded_before + 1):
                            break
                    else:
                        raise StopRun("A Defence techniques accordion did not expand.")
                if prior.get("accordion_expanded") != 3:
                    raise StopRun("All Defence techniques explanations were not opened.")
            if stage in {"inversion", "scenario_intro", "scenario", "terminal"}:
                if prior.get("accordion_expanded") != 3:
                    raise StopRun("Earlier Defence techniques explanations are closed.")
                for _ in range(3):
                    flipped_before = prior.get("flipped_count")
                    if flipped_before == 3:
                        break
                    if not isinstance(flipped_before, int) or not 0 <= flipped_before <= 2:
                        raise StopRun("The Defence techniques flashcard count changed.")
                    clicked = await _exercise_snapshot(frame, inner, "flip",
                                                       script=DEFENCE4_JS)
                    if clicked.get("clicked") != "flip":
                        raise StopRun("A Defence techniques flashcard did not open.")
                    for _ in range(12):
                        await asyncio.sleep(0.2)
                        prior = await _exercise_snapshot(frame, inner, "inspect",
                                                         script=DEFENCE4_JS)
                        if (prior.get("stage") == stage and
                                prior.get("flipped_count") == flipped_before + 1):
                            break
                    else:
                        raise StopRun("A Defence techniques flashcard did not reveal its back.")
                if prior.get("flipped_count") != 3:
                    raise StopRun("All Defence techniques flashcards were not viewed.")
            if stage == "terminal":
                prior = await _read_exercise_stage(frame, inner, prior, "terminal",
                                                   script=DEFENCE4_JS)
                if (prior.get("top", -1) < prior.get("max", 0) - 2 or
                        prior.get("scenario_state") != "terminal" or
                        prior.get("accordion_expanded") != 3 or
                        prior.get("flipped_count") != 3):
                    raise StopRun("The Defence techniques final scenario was not fully read.")
                break
            if stage in {"intro", "evasion", "poisoning", "inversion", "scenario_intro"}:
                prior = await _read_exercise_stage(frame, inner, prior, stage,
                                                   script=DEFENCE4_JS)
                if prior.get("top", -1) < prior.get("max", 0) - 2:
                    raise StopRun("The Defence techniques stage was not read to its end.")
                clicked = await _exercise_snapshot(frame, inner, "continue",
                                                   script=DEFENCE4_JS)
                expected = "scenario_start" if stage == "scenario_intro" else "CONTINUE"
                if clicked.get("clicked") != expected:
                    raise StopRun("The Defence techniques continuation was not acknowledged.")
                runner.event("scorm_defence4_stage", key=item["key"], stage=stage)
            elif stage == "scenario":
                state = prior.get("scenario_state")
                action = ("scenario_choice" if state == "choice" else
                          "scenario_continue" if state == "continue" else None)
                if action is None or not isinstance(prior.get("scenario_text"), str):
                    raise StopRun("The Defence techniques scenario is in an unknown state.")
                clicked = await _exercise_snapshot(frame, inner, action,
                                                   script=DEFENCE4_JS)
                if clicked.get("clicked") != action:
                    raise StopRun("The Defence techniques scenario did not accept the action.")
                runner.event("scorm_defence4_scenario_step", key=item["key"], action=action)
            else:
                raise StopRun("The Defence techniques lesson entered an unaudited stage.")
            before = prior.get("scenario_text")
            for _ in range(24):
                await asyncio.sleep(0.25)
                current = await _exercise_snapshot(frame, inner, "inspect",
                                                   script=DEFENCE4_JS)
                if current.get("stage") != stage or (
                        stage == "scenario" and current.get("scenario_text") != before):
                    prior = current
                    break
            else:
                raise StopRun("The Defence techniques lesson did not advance after the action.")
        else:
            raise StopRun("The Defence techniques lesson exceeded its audited step limit.")
    finally:
        await frame.close()
    marked = _checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "mark", "inner_verified": True})))
    if marked.get("clicked") != "mark":
        raise StopRun("Coursera did not acknowledge the Defence techniques Mark action.")
    runner.event("scorm_defence4_mark_clicked", key=item["key"])
    if not await runner.verified_item(item, wait=20):
        raise StopRun("Coursera did not show a green mark for Defence techniques.")


async def complete_genai4_scorm(runner, item, path, heading):
    """Read the audited GenAI lesson and finish its bounded security scenario."""
    from coursera_start import StopRun

    if path != GENAI4_PATH or heading != "Defences for attacks on GenAI models":
        raise StopRun("This GenAI defences SCORM lesson has not been audited.")
    if await runner.verified_item(item):
        return
    request = {"path": path, "heading": heading, "frame": GENAI4_FRAME}
    outer = _checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "inspect"})))
    if (outer.get("ready") != "complete" or outer.get("frame") != GENAI4_FRAME or
            outer.get("mark_count") != 1 or outer.get("mark_disabled") or
            outer.get("modal") or outer.get("other")):
        raise StopRun("The audited GenAI defences shell changed before reading.")
    frame = await open_scorm_frame(runner.tab, GENAI4_FRAME)
    try:
        inner = {"frame": GENAI4_FRAME, "heading": heading,
                 "lesson": GENAI4_LESSON}
        prior = await _exercise_snapshot(frame, inner, "inspect", script=GENAI4_JS)
        for step in range(40):
            stage = prior.get("stage")
            if stage == "reading":
                for index in range(5):
                    if prior.get("accordion_count") != 5:
                        raise StopRun("The GenAI defences explanation count changed.")
                    if prior.get("accordion_open_index") != index:
                        clicked = await _exercise_snapshot(
                            frame, {**inner, "index": index}, "open_accordion",
                            script=GENAI4_JS)
                        if clicked.get("clicked") != "open_accordion":
                            raise StopRun("A GenAI defences explanation did not open.")
                        for _ in range(12):
                            await asyncio.sleep(0.2)
                            prior = await _exercise_snapshot(
                                frame, inner, "inspect", script=GENAI4_JS)
                            if (prior.get("stage") == "reading" and
                                    prior.get("accordion_open_index") == index):
                                break
                        else:
                            raise StopRun("A GenAI defences explanation did not reveal its text.")
                prior = await _read_exercise_stage(
                    frame, inner, prior, "reading", script=GENAI4_JS)
                if (prior.get("top", -1) < prior.get("max", 0) - 2 or
                        prior.get("accordion_open_index") != 4):
                    raise StopRun("The GenAI defences first page was not fully read.")
                clicked = await _exercise_snapshot(
                    frame, {**inner, "accordions_reviewed": True}, "continue",
                    script=GENAI4_JS)
                if clicked.get("clicked") != "CONTINUE":
                    raise StopRun("The GenAI defences first continuation failed.")
                runner.event("scorm_genai4_stage", key=item["key"], stage=stage)
            elif stage == "scenario_intro":
                prior = await _read_exercise_stage(
                    frame, inner, prior, "scenario_intro", script=GENAI4_JS)
                if prior.get("top", -1) < prior.get("max", 0) - 2:
                    raise StopRun("The GenAI defences scenario instructions were not read.")
                clicked = await _exercise_snapshot(
                    frame, inner, "continue", script=GENAI4_JS)
                if clicked.get("clicked") != "scenario_start":
                    raise StopRun("The GenAI defences scenario did not start.")
                runner.event("scorm_genai4_stage", key=item["key"], stage=stage)
            elif stage == "scenario":
                state = prior.get("scenario_state")
                action = ("scenario_choice" if state == "choice" else
                          "scenario_continue" if state == "continue" else None)
                if action is None or not isinstance(prior.get("scenario_text"), str):
                    raise StopRun("The GenAI defences scenario entered an unknown state.")
                clicked = await _exercise_snapshot(
                    frame, inner, action, script=GENAI4_JS)
                if clicked.get("clicked") != action:
                    raise StopRun("The GenAI defences scenario did not accept the action.")
                runner.event("scorm_genai4_scenario_step", key=item["key"], action=action)
            elif stage == "terminal":
                prior = await _read_exercise_stage(
                    frame, inner, prior, "terminal", script=GENAI4_JS)
                if (prior.get("top", -1) < prior.get("max", 0) - 2 or
                        prior.get("scenario_state") != "terminal"):
                    raise StopRun("The GenAI defences final scenario was not fully read.")
                break
            else:
                raise StopRun("The GenAI defences lesson entered an unaudited stage.")
            before = prior.get("scenario_text")
            for _ in range(24):
                await asyncio.sleep(0.25)
                current = await _exercise_snapshot(
                    frame, inner, "inspect", script=GENAI4_JS)
                if current.get("stage") != stage or (
                        stage == "scenario" and current.get("scenario_text") != before):
                    prior = current
                    break
            else:
                raise StopRun("The GenAI defences lesson did not advance after the action.")
        else:
            raise StopRun("The GenAI defences lesson exceeded its audited step limit.")
    finally:
        await frame.close()
    marked = _checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "mark", "inner_verified": True})))
    if marked.get("clicked") != "mark":
        raise StopRun("Coursera did not acknowledge the GenAI defences Mark action.")
    runner.event("scorm_genai4_mark_clicked", key=item["key"])
    if not await runner.verified_item(item, wait=20):
        raise StopRun("Coursera did not show a green mark for GenAI defences.")


async def complete_static_scorm(runner, item, path, heading):
    """Read the static lesson, then require Coursera's own green mark."""
    from coursera_start import StopRun

    if not is_static_scorm(path, heading):
        raise StopRun("This SCORM widget has not been audited as a static lesson.")
    if path == GENERATING_EXAMPLES_PATH:
        await complete_exercise_scorm(runner, item, path, heading)
        return
    if path == POISONING_PATH:
        await complete_poisoning_scorm(runner, item, path, heading)
        return
    if path == SUMMARY_PATH:
        await complete_summary_scorm(runner, item, path, heading)
        return
    if path == OVERVIEW4_PATH:
        await complete_overview4_scorm(runner, item, path, heading)
        return
    if path == INTRODUCTION4_PATH:
        await complete_introduction4_scorm(runner, item, path, heading)
        return
    if path == DEFENCE4_PATH:
        await complete_defence4_scorm(runner, item, path, heading)
        return
    if path == GENAI4_PATH:
        await complete_genai4_scorm(runner, item, path, heading)
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

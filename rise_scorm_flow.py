"""Conservative reader for one-lesson Rise SCORM widgets.

This module is deliberately separate from production routing. It recognizes
static reading controls only and requires a Coursera green mark after reading.
Unknown activities stop the run for human review.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re

from plugin_flow import lesson_heading, plugin_path
from scorm_flow import open_scorm_frame


MAX_STAGES = 12
MAX_ACTIONS = 240
SETTLE_PROBES = 20


CONTENT_LINK_GUARD_JS = r"""(page, wrap, clean, includeHidden = false) => {
  const current = new URL(page.URL);
  const links = [...wrap.querySelectorAll('a[href]')]
    .filter(link => includeHidden || link.getClientRects().length > 0);
  const actionLabel = /^(?:start|launch|resume|submit|check|validate|complete|continue|try|practice|begin|run|play)(?:$|\s+(?:(?:to\s+)?(?:a\s+|the\s+)?)?(?:lab|quiz|assessment|exercise|activity|course|lesson|module|test|scenario|simulation|video|answer|response|again)\b)|^(?:open|take|do)\s+(?:a\s+|the\s+)?(?:lab|quiz|assessment|exercise|activity)\b/i;
  const actionClass = /(?:^|[\s_-])(?:btn|button|cta|launch|start|continue|submit|check|quiz|activity|exercise)(?:$|[\s_-])/i;
  for (const link of links) {
    const href = link.getAttribute('href');
    let destination;
    try { destination = new URL(href, page.URL); }
    catch { return 'The Rise reading contains an invalid content link.'; }
    const label = clean(link.innerText || link.textContent || link.getAttribute('aria-label'));
    if (link.hasAttribute('download') || link.hasAttribute('onclick') ||
        link.hasAttribute('aria-controls') || link.hasAttribute('aria-expanded') ||
        link.hasAttribute('aria-haspopup') || link.getAttribute('role') === 'button' ||
        actionClass.test(String(link.className || '')) || actionLabel.test(label) ||
        !['http:', 'https:'].includes(destination.protocol) ||
        /\/(?:lab|quiz|assessment|exercise|activity|simulation|test)(?:\/|$)/i.test(destination.pathname) ||
        (destination.origin === current.origin &&
         /^#\/(?:lessons|quiz|assessment|activity)(?:\/|$)/i.test(destination.hash)))
      return 'The Rise reading contains an activity-like content link.';
  }
  return null;
}"""


OUTER_JS = r"""(request => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const url = new URL(location.href);
  if (url.protocol !== 'https:' ||
      !['coursera.org', 'www.coursera.org'].includes(url.hostname) ||
      url.username || url.password || (url.port && url.port !== '443') ||
      url.pathname.replace(/\/$/, '') !== request.path || url.search || url.hash)
    return {error:'The active Coursera lesson identity changed.', url:location.href};
  if (!['inspect', 'mark'].includes(request.action))
    return {error:'Unsupported Coursera lesson action.'};
  const roots = [...document.querySelectorAll('#main-container')];
  const headings = [...document.querySelectorAll('h1')].map(node => clean(node.textContent));
  if (roots.length !== 1 || headings.length !== 2 ||
      headings.some(value => value !== request.heading))
    return {error:'The Coursera lesson heading or shell changed.'};
  const root = roots[0];
  const frames = [...root.querySelectorAll('iframe[data-testid="widget-iframe"]')];
  if (frames.length !== 1)
    return {error:'Expected one Rise widget iframe.'};
  const frame = frames[0];
  const title = clean(frame.title);
  if (title !== request.heading &&
      !(/^Topic [0-9]+: /.test(title) &&
        title.replace(/^Topic [0-9]+: /, '') === request.heading))
    return {error:'The Rise widget title differs from the course outline.'};
  if (!/^https:\/\/d10o6em2qtnr4q\.cloudfront\.net\/assets\/[0-9a-f]{32}\/index\.html$/.test(frame.src))
    return {error:'The Rise widget asset URL is unrecognized.'};
  const marks = [...root.querySelectorAll('button[data-testid="mark-complete"]')]
    .filter(node => /^Mark as complete(?:d)?$/i.test(clean(node.innerText || node.textContent)));
  const modal = [...document.querySelectorAll('[role="dialog"][aria-modal="true"]')]
    .some(node => node.getClientRects().length > 0);
  const other = [...root.querySelectorAll('button,[role="button"]')]
    .filter(node => !node.closest('nav,[role="navigation"],aside') && !marks.includes(node))
    .map(node => clean(node.innerText || node.getAttribute('aria-label') || node.textContent))
    .filter(value => /^(?:Start|Launch|Resume|Submit|Check|Validate|Save answer|Continue)(?:\s|$)/i.test(value));
  const result = {url:location.href, ready:document.readyState, frame:frame.src,
    frame_title:title, mark_count:marks.length,
    mark_disabled:marks[0]?.disabled || marks[0]?.getAttribute('aria-disabled') === 'true',
    modal, other};
  if (request.action === 'inspect') return result;
  if (!request.inner_verified || document.readyState !== 'complete' || modal ||
      other.length || marks.length !== 1 || result.mark_disabled ||
      frame.src !== request.frame || title !== request.frame_title)
    return {...result, error:'No unique safe Rise completion action is available.'};
  marks[0].scrollIntoView({block:'center'});
  marks[0].click();
  return {...result, clicked:'mark'};
})"""


INNER_JS = r"""(request => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const contentLinkGuard = __CONTENT_LINK_GUARD__;
  if (!['inspect','expand','flip','carousel_dot','hotspot_open',
        'hotspot_close','scroll','continue'].includes(request.action))
    return {error:'Unsupported Rise reader action.'};
  if (location.href !== request.frame)
    return {error:'The Rise asset frame URL changed.'};
  if (document.readyState !== 'complete')
    return {pending:'Rise asset frame is loading.'};
  const base = request.frame.replace(/index\.html$/, '');
  const containers = [...document.querySelectorAll('iframe[title="container"]')];
  if (containers.length === 0 && document.querySelectorAll('iframe').length === 0)
    return {pending:'Rise container is mounting.'};
  if (containers.length !== 1 || document.querySelectorAll('iframe').length !== 1 ||
      containers[0].src !== base + 'scorm-content/scormdriver/indexAPI.html')
    return {error:'The Rise container identity changed.'};
  const driver = containers[0].contentDocument;
  if (!driver || driver.readyState !== 'complete')
    return {pending:'Rise driver is loading.'};
  const contentFrames = [...driver.querySelectorAll('iframe#content-frame')];
  const driverFrames = [...driver.querySelectorAll('iframe')];
  if (contentFrames.length === 0 && driverFrames.length === 0)
    return {pending:'Rise lesson is mounting.'};
  if (contentFrames.length !== 1 || driverFrames.length !== 4 ||
      driverFrames[0] !== contentFrames[0] ||
      clean(contentFrames[0].title) !== request.frame_title)
    return {error:'The Rise content frame chain changed.'};
  const auxiliary = driverFrames.slice(1);
  if (auxiliary.some((frame, index) => clean(frame.title) !== 'Intentionally blank' ||
      frame.src !== base + 'scorm-content/scormdriver/' +
        (index === 0 ? 'AICCComm.html' : 'blank.html')))
    return {error:'The Rise driver contains an unexpected frame.'};
  const page = contentFrames[0].contentDocument;
  if (!page)
    return request.action === 'inspect' && !request.lesson ?
      {pending:'Rise lesson content is loading.'} :
      {error:'The Rise lesson document disappeared.'};
  const contentIndex = base + 'scorm-content/scormcontent/index.html';
  const initialRoutes = new Set([
    'about:blank',
    base + 'scorm-content/scormdriver/blank.html',
    base + 'scorm-content/scormdriver/indexAPI.html',
    contentIndex, contentIndex + '#', contentIndex + '#/',
    contentIndex + '#/lessons', contentIndex + '#/lessons/'
  ]);
  const lessonPrefix = contentIndex + '#/lessons/';
  const lesson = page.URL.startsWith(lessonPrefix) ? page.URL.slice(lessonPrefix.length) : '';
  const innerIframeCount = page.querySelectorAll('iframe').length;
  const validLesson = /^[A-Za-z0-9_-]{20,80}$/.test(lesson);
  const contentSuffix = page.URL.startsWith(contentIndex) ?
    page.URL.slice(contentIndex.length) : null;
  const contentIndexTransition = !validLesson && contentSuffix !== null &&
    (contentSuffix === '' || contentSuffix.startsWith('#') ||
     contentSuffix.startsWith('?'));
  const suffixDiagnostic = contentSuffix === null ? {} : {
    suffix_kind:contentSuffix.startsWith('?') ? 'query' :
      contentSuffix.startsWith('#') ? 'hash' :
      contentSuffix === '' ? 'none' : 'other',
    suffix_length:contentSuffix.length,
    has_hash:contentSuffix.includes('#')
  };
  const mountActivityError = () => {
    const activitySelector = 'textarea,select,[contenteditable="true"],video,audio,' +
      'canvas,[draggable="true"],[role="radio"],[role="checkbox"],' +
      '[role="combobox"],[role="textbox"],.scenario-block,.quiz-block,' +
      '.knowledge-check,.sorting-activity,.matching-block';
    const outerControl = document.querySelector(
      'button,input,textarea,select,form,[contenteditable="true"]') ||
      driver.querySelector(
        'button,input,textarea,select,form,[contenteditable="true"]');
    const activeForm = [...page.querySelectorAll('form')].some(
      form => !form.matches('form.nav-sidebar-search--disabled'));
    const activeInput = [...page.querySelectorAll('input')].some(
      input => !input.matches('input[type="search"][disabled]'));
    const knownButtons = 'button.visually-hidden.btn-skip-to-lesson,' +
      'button.nav-control__button,button.nav-sidebar-header__search-btn,' +
      'button.nav-sidebar-search__close,button[data-rmiz-btn-zoom],' +
      'button[data-rmiz-btn-unzoom],button.blocks-accordion__header,' +
      'button.flashcard-side-flip__btn,button.continue-btn';
    const unknownButton = [...page.querySelectorAll('button,[role="button"]')]
      .some(node => node.getClientRects().length && !node.matches(knownButtons));
    const wrap = page.querySelector('div.page-wrap');
    const contentLinkError = wrap ? contentLinkGuard(page, wrap, clean) : null;
    return contentLinkError ||
      (outerControl || activeForm || activeInput ||
       page.querySelector(activitySelector) || unknownButton ?
        'The incomplete Rise lesson contains an interactive activity.' : null);
  };
  const identityError = () => {
    const routeClass = page.URL.startsWith(lessonPrefix) ? 'lesson-route' :
      page.URL.startsWith(contentIndex) ? 'content-index-route' :
      page.URL.startsWith(base) ? 'other-asset-route' :
      page.URL === 'about:blank' ? 'blank-route' : 'outside-asset-route';
    return {error:'The Rise lesson URL or inner identity changed.',
      diagnostic: {route_class:routeClass,
        observed_lesson:validLesson ? lesson : null,
        expected_lesson:request.lesson || null,
        inner_iframe_count:innerIframeCount, ...suffixDiagnostic}};
  };
  const initialRoute = initialRoutes.has(page.URL);
  const initialPending = initialRoute || contentIndexTransition;
  const pendingDiagnostic = contentIndexTransition ?
    {route_class:'content-index-transition', ...suffixDiagnostic} : null;
  if (page.readyState !== 'complete') {
    if (request.action === 'inspect' &&
        ((!request.lesson && initialPending) ||
         (validLesson && (!request.lesson || request.lesson === lesson)))) {
      const activityError = mountActivityError();
      if (activityError)
        return {error:activityError, diagnostic:pendingDiagnostic};
      return {pending:'Rise lesson content is loading.',
        diagnostic:pendingDiagnostic};
    }
    return identityError();
  }
  if (request.action === 'inspect' && !request.lesson && initialPending) {
    const activityError = mountActivityError();
    if (activityError)
      return {error:activityError, diagnostic:pendingDiagnostic};
    return {pending:'Rise lesson route is initializing.',
      diagnostic:pendingDiagnostic};
  }
  if (!validLesson || (request.lesson && request.lesson !== lesson) ||
      innerIframeCount)
    return identityError();
  const wraps = [...page.querySelectorAll('div.page-wrap')];
  const wrap = wraps[0];
  const text = wraps.length === 1 ? clean(wrap.innerText) : '';
  const bodyText = clean(page.body?.innerText);
  const progress = clean(page.querySelector('.nav-sidebar-header__progress-text')?.innerText);
  const lessonMarker = /\bLESSON 1 OF 1\b/i.test(bodyText);
  const progressValid = /^\d{1,3}% COMPLETE$/i.test(progress);
  const guardDiagnostic = {wrap_count:wraps.length, text_length:text.length,
    body_length:bodyText.length, lesson_marker:lessonMarker,
    progress_nodes:page.querySelectorAll('.nav-sidebar-header__progress-text').length,
    progress_valid:progressValid};
  if (wraps.length !== 1 || text.length < 200 ||
      !lessonMarker || !progressValid) {
    const activityError = mountActivityError();
    if (activityError)
      return {error:activityError, diagnostic:guardDiagnostic};
    const otherLesson = /\bLESSON\s+\d+\s+OF\s+\d+\b/i.test(bodyText);
    const initialMount = request.action === 'inspect' && !request.lesson &&
      wraps.length <= 1 && (!otherLesson || lessonMarker) &&
      (!progress || progressValid);
    if (initialMount)
      return {pending:'Rise one-lesson scaffold is loading.',
        diagnostic:guardDiagnostic};
    return {error:'This is not a recognized one-lesson Rise reading page.',
      diagnostic:guardDiagnostic};
  }
  const contentLinkError = contentLinkGuard(page, wrap, clean);
  if (contentLinkError) return {error:contentLinkError};
  if (document.querySelector('button,input,textarea,select,form,[contenteditable="true"]') ||
      driver.querySelector('button,input,textarea,select,form,[contenteditable="true"]'))
    return {error:'An outer Rise frame has an interactive control.'};
  const searchForms = [...page.querySelectorAll('form')];
  const inputs = [...page.querySelectorAll('input')];
  if (searchForms.length !== 1 ||
      !searchForms[0].matches('form.nav-sidebar-search--disabled') ||
      inputs.length !== 1 || !inputs[0].matches('input[type="search"][disabled]') ||
      searchForms[0].querySelectorAll('input').length !== 1 ||
      page.querySelector('textarea,select,[contenteditable="true"],video,audio,canvas,' +
        '[draggable="true"],[role="radio"],[role="checkbox"],[role="combobox"],' +
        '[role="textbox"],.scenario-block,.quiz-block,.knowledge-check,' +
        '.sorting-activity,.matching-block'))
    return {error:'The Rise lesson has an activity or editable form.'};
  const panels = [...page.querySelectorAll('button.blocks-accordion__header')];
  if (panels.length > 24 || panels.some((button) => {
      const id = button.getAttribute('aria-controls');
      const panel = id && page.getElementById(id);
      return button.type !== 'button' || !button.closest('h2') ||
        !['true','false'].includes(button.getAttribute('aria-expanded')) ||
        !panel || !panel.matches('section.blocks-accordion__content') ||
        clean(button.innerText).length < 2 ||
        clean(panel.textContent).length < 15 ||
        panel.querySelector('button,[role="button"],input,textarea,select,form,[contenteditable="true"]') ||
        (button.getAttribute('aria-expanded') === 'true' && clean(panel.innerText).length < 15);
    }) ||
    new Set(panels.map(button => button.getAttribute('aria-controls'))).size !== panels.length)
    return {error:'A Rise accordion is not a static explanation.'};
  const accordions = panels.map(button => {
    const panel = page.getElementById(button.getAttribute('aria-controls'));
    return {id:button.getAttribute('aria-controls'), heading:clean(button.innerText),
      content:clean(panel.textContent),
      expanded:button.getAttribute('aria-expanded') === 'true'};
  });
  const accordionSignature = JSON.stringify(accordions.map(
    ({id,heading,content}) => [id,heading,content]));
  const cards = [...page.querySelectorAll('li.flashcard')];
  let flipped = 0, nextCard = null;
  if (cards.length > 24) return {error:'Too many Rise flashcards.'};
  for (let index = 0; index < cards.length; index++) {
    const card = cards[index];
    const sides = [...card.querySelectorAll('.flashcard-side')];
    const front = card.querySelector('.flashcard-side--front');
    const back = card.querySelector('.flashcard-side--back');
    const buttons = [...card.querySelectorAll('button.flashcard-side-flip__btn')];
    if (card.getAttribute('role') !== 'listitem' ||
        card.getAttribute('aria-hidden') !== 'false' ||
        !card.getClientRects().length || sides.length !== 2 || !front || !back ||
        buttons.length !== 2 ||
        !buttons[0].id.endsWith('-' + index + '-front-flip-btn') ||
        !buttons[1].id.endsWith('-' + index + '-back-flip-btn') ||
        clean(front.innerText).length < 10 || clean(back.innerText).length < 10 ||
        card.querySelector('input,textarea,select,form,[contenteditable="true"]'))
      return {error:'A Rise flashcard is not a static two-sided card.'};
    const frontActive = buttons[0].tabIndex === 0 && buttons[1].tabIndex === -1 &&
      !front.classList.contains('flashcard-side--flipped') &&
      !back.classList.contains('flashcard-side--flipped');
    const backActive = buttons[0].tabIndex === -1 && buttons[1].tabIndex === 0 &&
      front.classList.contains('flashcard-side--flipped') &&
      back.classList.contains('flashcard-side--flipped');
    if (!frontActive && !backActive)
      return {error:'A Rise flashcard has an unrecognized flip state.'};
    if (backActive) flipped++;
    else if (!nextCard) nextCard = buttons[0];
  }
  const continues = [...page.querySelectorAll('button.continue-btn')];
  if (continues.length > 1 ||
      continues.some(button => button.type !== 'button' ||
        clean(button.innerText) !== 'CONTINUE'))
    return {error:'Rise continuation is not a unique static Continue control.'};
  // Quote Carousel is a static reading control. Only the active, non-inert
  // slide counts as read; hidden slides can still report client rectangles.
  const carouselRegions = [...page.querySelectorAll(
    '[role="region"][aria-roledescription="Carousel"]')];
  const carouselButtons = [...page.querySelectorAll(
    'button.carousel-controls-item-btn,button.carousel-controls-prev,' +
    'button.carousel-controls-next')];
  let carouselCount = 0, carouselActiveIndex = 0;
  let carouselContents = [], carouselDots = [], carouselPrev = [], carouselNext = [];
  if (carouselRegions.length > 1 ||
      (carouselRegions.length === 0 && carouselButtons.length))
    return {error:'The Rise carousel inventory is unrecognized.'};
  if (carouselRegions.length === 1) {
    const region = carouselRegions[0];
    const slides = [...region.querySelectorAll('.carousel-slide')];
    carouselDots = [...region.querySelectorAll('button.carousel-controls-item-btn')];
    carouselPrev = [...region.querySelectorAll('button.carousel-controls-prev')];
    carouselNext = [...region.querySelectorAll('button.carousel-controls-next')];
    carouselCount = slides.length;
    if (!region.matches('div.carousel[aria-label="Quote Carousel"]') ||
        !wrap.contains(region) || carouselCount < 2 || carouselCount > 12 ||
        carouselDots.length !== carouselCount || carouselPrev.length !== 1 ||
        carouselNext.length !== 1 ||
        carouselButtons.length !== carouselCount + 2 ||
        carouselPrev[0].type !== 'button' ||
        carouselPrev[0].getAttribute('aria-label') !== 'Go to previous slide' ||
        carouselNext[0].type !== 'button' ||
        carouselNext[0].getAttribute('aria-label') !== 'Go to next slide')
      return {error:'The Rise Quote Carousel controls changed.'};
    for (let index = 0; index < carouselCount; index++) {
      const slide = slides[index], dot = carouselDots[index];
      const content = clean(slide.textContent);
      const current = dot.getAttribute('aria-current');
      const active = current === 'true';
      if (slide.getAttribute('role') !== 'group' ||
          slide.getAttribute('aria-roledescription') !== 'Slide' ||
          slide.getAttribute('aria-label') !== `${index + 1} of ${carouselCount}` ||
          dot.type !== 'button' ||
          dot.getAttribute('aria-label') !== `Go to slide ${index + 1}` ||
          !['true','false'].includes(current) ||
          dot.tabIndex !== (active ? 0 : -1) ||
          slide.hasAttribute('hidden') === active ||
          slide.hasAttribute('inert') === active ||
          content.length < 25 ||
          slide.querySelector('button,[role="button"],input,textarea,select,form,' +
            '[contenteditable="true"],video,audio,canvas,[role="radio"],' +
            '[role="checkbox"],[role="combobox"],[role="textbox"]') ||
          contentLinkGuard(page, slide, clean, true))
        return {error:'A Rise Quote Carousel slide is not static reading.'};
      carouselContents.push(content);
      if (active) {
        if (carouselActiveIndex) return {error:'Multiple Rise carousel slides are active.'};
        carouselActiveIndex = index + 1;
      }
    }
    if (!carouselActiveIndex || new Set(carouselContents).size !== carouselCount)
      return {error:'The Rise Quote Carousel slide state is inconsistent.'};
  }
  const carouselSignature = JSON.stringify(carouselContents);
  // Rise labeled graphics expose static explanatory dialogs through markers.
  // The marker's Viewed status is durable read evidence after the dialog closes.
  const graphicGroups = [...page.querySelectorAll(
    'div.view-labeled-graphic[role="group"][aria-label="Labeled graphic"]')];
  const allMarkers = [...page.querySelectorAll('button.labeled-graphic-marker')];
  const allBubbles = [...page.querySelectorAll('dialog.bubble__body')];
  let hotspotMarkers = [], hotspotButtons = [], hotspotDialogButtons = [];
  let hotspotOpenIndex = 0, hotspotViewedCount = 0;
  if (graphicGroups.length > 1 ||
      (graphicGroups.length === 0 && (allMarkers.length || allBubbles.length)))
    return {error:'The Rise labeled graphic inventory is unrecognized.'};
  if (graphicGroups.length === 1) {
    const group = graphicGroups[0];
    const markers = [...group.querySelectorAll('button.labeled-graphic-marker')];
    const bubbles = [...page.querySelectorAll('#portal dialog.bubble__body')];
    if (!wrap.contains(group) || markers.length < 2 || markers.length > 12 ||
        markers.length !== allMarkers.length || bubbles.length !== markers.length ||
        bubbles.length !== allBubbles.length ||
        !group.querySelector('.labeled-graphic-canvas__figure > ol[role="list"]'))
      return {error:'The Rise labeled graphic structure changed.'};
    const ids = new Set(), labels = new Set(), contents = new Set();
    for (let index = 0; index < markers.length; index++) {
      const marker = markers[index];
      const fullLabel = marker.getAttribute('aria-label') || '';
      const match = /^Marker, (.{3,100}), (Right|Left|Up|Down) arrow, (Not viewed|Viewed)$/.exec(fullLabel);
      const id = marker.getAttribute('aria-controls');
      const dialog = id ? page.getElementById(id) : null;
      const content = clean(dialog?.textContent);
      const viewed = match?.[3] === 'Viewed';
      const expanded = marker.getAttribute('aria-expanded') === 'true';
      if (!match || !id || ids.has(id) || !dialog ||
          dialog.tagName !== 'DIALOG' || !dialog.classList.contains('bubble__body') ||
          !dialog.closest('#portal') || marker.type !== 'button' ||
          !marker.closest('li.map-item[role="listitem"]') ||
          !['true','false'].includes(marker.getAttribute('aria-expanded')) ||
          labels.has(fullLabel.replace(/, (?:Not viewed|Viewed)$/, '')) ||
          content.length < 100 || contents.has(content) ||
          !content.startsWith(match[1]) ||
          dialog.getAttribute('aria-hidden') !== (expanded ? 'false' : 'true') ||
          dialog.hasAttribute('open') !== expanded ||
          !!dialog.getClientRects().length !== expanded ||
          (expanded && !viewed) ||
          dialog.querySelector('input,textarea,select,form,[contenteditable="true"],' +
            'video,audio,canvas,[role="button"],[role="radio"],[role="checkbox"],' +
            '[role="combobox"],[role="textbox"]') ||
          contentLinkGuard(page, dialog, clean, true))
        return {error:'A Rise labeled graphic marker is not static reading.'};
      const close = [...dialog.querySelectorAll('button.bubble__close')];
      const prev = [...dialog.querySelectorAll('button.bubble__prev')];
      const next = [...dialog.querySelectorAll('button.bubble__next')];
      const dialogButtons = [...dialog.querySelectorAll('button')];
      if (close.length !== 1 || prev.length !== 1 || next.length !== 1 ||
          dialogButtons.length !== 3 ||
          close[0].type !== 'button' ||
          close[0].getAttribute('aria-label') !== 'Close modal' ||
          prev[0].type !== 'button' || prev[0].getAttribute('aria-label') !== 'Previous' ||
          next[0].type !== 'button' || next[0].getAttribute('aria-label') !== 'Next' ||
          (expanded && !close[0].getClientRects().length))
        return {error:'The Rise labeled graphic dialog controls changed.'};
      ids.add(id);
      labels.add(fullLabel.replace(/, (?:Not viewed|Viewed)$/, ''));
      contents.add(content);
      hotspotMarkers.push({id, label:fullLabel.replace(/, (?:Not viewed|Viewed)$/, ''),
        content, viewed, expanded});
      hotspotButtons.push(marker);
      hotspotDialogButtons.push(...dialogButtons);
      if (viewed) hotspotViewedCount++;
      if (expanded) {
        if (hotspotOpenIndex)
          return {error:'Multiple Rise labeled graphic dialogs are open.'};
        hotspotOpenIndex = index + 1;
      }
    }
  }
  const hotspotSignature = JSON.stringify(hotspotMarkers.map(
    ({id,label,content}) => [id,label,content]));
  const hotspotsRead = hotspotViewedCount === hotspotMarkers.length &&
    hotspotOpenIndex === 0;
  const allowed = new Set([
    ...page.querySelectorAll('button.visually-hidden.btn-skip-to-lesson,' +
      'button.nav-control__button,button.nav-sidebar-header__search-btn,' +
      'button.nav-sidebar-search__close,button[data-rmiz-btn-zoom],' +
      'button[data-rmiz-btn-unzoom]'),
    ...panels, ...continues, ...carouselDots, ...carouselPrev, ...carouselNext,
    ...hotspotButtons, ...hotspotDialogButtons,
    ...page.querySelectorAll('button.flashcard-side-flip__btn')
  ]);
  const unknown = [...page.querySelectorAll('button,[role="button"]')]
    .filter(node => node.getClientRects().length && !allowed.has(node));
  if (unknown.length)
    return {error:'Rise lesson has an unrecognized button or activity.'};
  const max = Math.max(0, wrap.scrollHeight - wrap.clientHeight);
  const top = wrap.scrollTop;
  const expanded = panels.filter(button => button.getAttribute('aria-expanded') === 'true').length;
  const signature = text + ':controls:' + panels.length + ':' + cards.length +
    ':' + continues.length + ':' + carouselSignature + ':' + hotspotSignature +
    ':' + hotspotViewedCount + ':' + hotspotOpenIndex;
  if (request.action !== 'inspect' && request.stage_signature !== signature)
    return {error:'The Rise reading stage changed before the requested action.'};
  if (!['inspect','hotspot_open','hotspot_close'].includes(request.action) &&
      !hotspotsRead)
    return {error:'The Rise labeled graphic must be read and closed first.'};
  let clicked = null;
  if (request.action === 'expand') {
    const matches = panels.filter(node =>
      node.getAttribute('aria-controls') === request.expand_id &&
      node.getAttribute('aria-expanded') === 'false');
    if (matches.length !== 1 || request.accordion_signature !== accordionSignature)
      return {error:'The requested static Rise accordion changed.'};
    const button = matches[0];
    button.scrollIntoView({block:'center'});
    if (!button.getClientRects().length)
      return {error:'The requested Rise accordion is not visible.'};
    button.click();
    clicked = 'expand';
  } else if (request.action === 'flip') {
    if (!nextCard) return {error:'No unread Rise flashcard remains.'};
    nextCard.scrollIntoView({block:'center'});
    if (!nextCard.getClientRects().length ||
        !nextCard.closest('li.flashcard').getClientRects().length)
      return {error:'The next Rise flashcard is not visible.'};
    nextCard.click();
    clicked = 'flip';
  } else if (request.action === 'carousel_dot') {
    const index = request.carousel_index;
    if (carouselCount === 0 || !Number.isInteger(index) ||
        index < 1 || index > carouselCount ||
        index === carouselActiveIndex ||
        request.carousel_signature !== carouselSignature)
      return {error:'The requested Rise Quote Carousel slide changed.'};
    const dot = carouselDots[index - 1];
    dot.scrollIntoView({block:'center'});
    if (!dot.getClientRects().length || dot.disabled ||
        dot.getAttribute('aria-disabled') === 'true')
      return {error:'The requested Rise Quote Carousel dot is unavailable.'};
    dot.click();
    clicked = 'carousel_dot';
  } else if (request.action === 'hotspot_open') {
    const index = request.hotspot_index;
    if (!Number.isInteger(index) || index < 1 ||
        index > hotspotMarkers.length || hotspotOpenIndex ||
        hotspotMarkers[index - 1].viewed ||
        request.hotspot_signature !== hotspotSignature)
      return {error:'The requested Rise labeled graphic marker changed.'};
    const marker = hotspotButtons[index - 1];
    marker.scrollIntoView({block:'center'});
    if (!marker.getClientRects().length || marker.disabled ||
        marker.getAttribute('aria-disabled') === 'true')
      return {error:'The requested Rise labeled graphic marker is unavailable.'};
    marker.click();
    clicked = 'hotspot_open';
  } else if (request.action === 'hotspot_close') {
    const index = request.hotspot_index;
    if (!Number.isInteger(index) || index !== hotspotOpenIndex ||
        request.hotspot_signature !== hotspotSignature)
      return {error:'The open Rise labeled graphic dialog changed.'};
    const dialog = page.getElementById(hotspotMarkers[index - 1].id);
    const close = dialog?.querySelector('button.bubble__close[aria-label="Close modal"]');
    if (!close || !close.getClientRects().length || close.disabled ||
        close.getAttribute('aria-disabled') === 'true')
      return {error:'The Rise labeled graphic close control is unavailable.'};
    close.click();
    clicked = 'hotspot_close';
  } else if (request.action === 'scroll') {
    if (!request.panels_read || request.accordion_signature !== accordionSignature ||
        !request.carousel_read || !request.hotspots_read || !hotspotsRead ||
        flipped !== cards.length || top >= max - 2)
      return {error:'Rise reading controls must be inspected before scrolling.'};
    wrap.scrollTop = Math.min(max, top + Math.max(300, Math.floor(wrap.clientHeight * .7)));
    clicked = 'scroll';
  } else if (request.action === 'continue') {
    if (!request.panels_read || request.accordion_signature !== accordionSignature ||
        !request.carousel_read || !request.hotspots_read || !hotspotsRead ||
        flipped !== cards.length ||
        top < max - 2 || continues.length !== 1)
      return {error:'The Rise stage was not read before continuing.'};
    continues[0].click();
    clicked = 'continue';
  }
  return {url:location.href, lesson_url:page.URL, lesson, progress,
    text_length:text.length, signature, top, max, accordions,
    accordion_signature:accordionSignature, accordion_count:panels.length,
    expanded_count:expanded, card_count:cards.length, flipped_count:flipped,
    continue_count:continues.length, carousel_count:carouselRegions.length,
    carousel_active_index:carouselActiveIndex, carousel_contents:carouselContents,
    carousel_signature:carouselSignature,
    hotspot_count:hotspotMarkers.length, hotspot_markers:hotspotMarkers,
    hotspot_signature:hotspotSignature, hotspot_viewed_count:hotspotViewedCount,
    hotspot_open_index:hotspotOpenIndex, clicked};
})"""
INNER_JS = INNER_JS.replace("__CONTENT_LINK_GUARD__", CONTENT_LINK_GUARD_JS)


def _script(source, request):
    return source + "(" + json.dumps(request, ensure_ascii=False) + ")"


def _checked(value):
    from coursera_start import StopRun

    if not isinstance(value, dict):
        raise StopRun("The Rise widget returned an unreadable snapshot.")
    if value.get("error"):
        diagnostic = value.get("diagnostic")
        detail = (" " + json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)
                  if isinstance(diagnostic, dict) else "")
        raise StopRun(str(value["error"]) + detail)
    return value


def _outer_checked(value, request):
    from coursera_start import StopRun

    value = _checked(value)
    if (value.get("ready") != "complete" or value.get("mark_count") != 1 or
            value.get("mark_disabled") or value.get("modal") or value.get("other") or
            not re.fullmatch(
                r"https://d10o6em2qtnr4q[.]cloudfront[.]net/assets/[0-9a-f]{32}/index[.]html",
                str(value.get("frame", ""))) or
            not (value.get("frame_title") == request["heading"] or
                 re.fullmatch(
                     r"Topic [0-9]+: " + re.escape(request["heading"]),
                     str(value.get("frame_title", ""))))):
        raise StopRun("The Coursera lesson is not a recognized static Rise widget.")
    return value


async def _inner(frame, request, action, *, action_args=None,
                 probes=SETTLE_PROBES, delay=.25):
    from coursera_start import StopRun

    attempts = probes if action == "inspect" else 1
    last_pending_diagnostic = None
    for _ in range(attempts):
        value = _checked(await frame.evaluate(_script(
            INNER_JS, {**request, **(action_args or {}), "action": action})))
        if value.get("pending"):
            if action != "inspect":
                raise StopRun("The Rise widget changed during an action.")
            last_pending_diagnostic = value.get("diagnostic")
            await asyncio.sleep(delay)
            continue
        if (value.get("url") != request["frame"] or
                not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", str(value.get("lesson", ""))) or
                (request.get("lesson") and value["lesson"] != request["lesson"]) or
                value.get("lesson_url") != request["frame"].replace("index.html", "") +
                    "scorm-content/scormcontent/index.html#/lessons/" + value["lesson"]):
            raise StopRun("The Rise iframe or lesson identity changed.")
        for field in ("top", "max", "text_length", "accordion_count",
                      "expanded_count", "card_count", "flipped_count", "continue_count",
                      "carousel_count", "carousel_active_index", "hotspot_count",
                      "hotspot_viewed_count", "hotspot_open_index"):
            if not isinstance(value.get(field), int) or value[field] < 0:
                raise StopRun("The Rise reading snapshot is incomplete.")
        if (value["expanded_count"] > value["accordion_count"] or
                value["flipped_count"] > value["card_count"] or
                value["continue_count"] > 1 or value["top"] > value["max"] + 2 or
                not isinstance(value.get("signature"), str) or
                not re.fullmatch(r"\d{1,3}% COMPLETE", str(value.get("progress", "")))):
            raise StopRun("The Rise reading snapshot is inconsistent.")
        accordions = value.get("accordions")
        if (not isinstance(accordions, list) or
                len(accordions) != value["accordion_count"] or
                not isinstance(value.get("accordion_signature"), str) or
                len({entry.get("id") for entry in accordions
                     if isinstance(entry, dict)}) != len(accordions)):
            raise StopRun("The Rise accordion inventory is incomplete.")
        for entry in accordions:
            if (not isinstance(entry, dict) or
                    not isinstance(entry.get("id"), str) or not entry["id"] or
                    not isinstance(entry.get("heading"), str) or
                    len(entry["heading"]) < 2 or
                    not isinstance(entry.get("content"), str) or
                    len(entry["content"]) < 15 or
                    not isinstance(entry.get("expanded"), bool)):
                raise StopRun("The Rise accordion identity or contents changed.")
        if sum(entry["expanded"] for entry in accordions) != value["expanded_count"]:
            raise StopRun("The Rise accordion expansion state is inconsistent.")
        contents = value.get("carousel_contents")
        carousel_count = value["carousel_count"]
        active_index = value["carousel_active_index"]
        signature = value.get("carousel_signature")
        if (carousel_count not in (0, 1) or
                not isinstance(contents, list) or
                not isinstance(signature, str)):
            raise StopRun("The Rise carousel inventory is incomplete.")
        if carousel_count == 0:
            if active_index != 0 or contents or signature != "[]":
                raise StopRun("The Rise carousel absence state is inconsistent.")
        elif (not 2 <= len(contents) <= 12 or
              not 1 <= active_index <= len(contents) or
              any(not isinstance(content, str) or len(content) < 25
                  for content in contents) or
              len(set(contents)) != len(contents)):
            raise StopRun("The Rise Quote Carousel slides are incomplete.")
        try:
            signature_contents = json.loads(signature)
        except (TypeError, ValueError):
            raise StopRun("The Rise Quote Carousel signature is unreadable.") from None
        if signature_contents != contents:
            raise StopRun("The Rise Quote Carousel contents changed unexpectedly.")
        hotspots = value.get("hotspot_markers")
        hotspot_count = value["hotspot_count"]
        viewed_count = value["hotspot_viewed_count"]
        open_index = value["hotspot_open_index"]
        hotspot_signature = value.get("hotspot_signature")
        if (not isinstance(hotspots, list) or
                not isinstance(hotspot_signature, str) or
                len(hotspots) != hotspot_count or
                not (hotspot_count == 0 or 2 <= hotspot_count <= 12) or
                viewed_count > hotspot_count or open_index > hotspot_count):
            raise StopRun("The Rise labeled graphic inventory is incomplete.")
        if hotspot_count == 0:
            if viewed_count or open_index or hotspot_signature != "[]":
                raise StopRun("The Rise labeled graphic absence state is inconsistent.")
        else:
            for entry in hotspots:
                if (not isinstance(entry, dict) or
                        not isinstance(entry.get("id"), str) or not entry["id"] or
                        not isinstance(entry.get("label"), str) or
                        not re.fullmatch(
                            r"Marker, .{3,100}, (?:Right|Left|Up|Down) arrow",
                            entry["label"]) or
                        not isinstance(entry.get("content"), str) or
                        len(entry["content"]) < 100 or
                        not isinstance(entry.get("viewed"), bool) or
                        not isinstance(entry.get("expanded"), bool) or
                        (entry["expanded"] and not entry["viewed"])):
                    raise StopRun("A Rise labeled graphic marker is incomplete.")
            if (len({entry["id"] for entry in hotspots}) != hotspot_count or
                    len({entry["label"] for entry in hotspots}) != hotspot_count or
                    len({entry["content"] for entry in hotspots}) != hotspot_count or
                    sum(entry["viewed"] for entry in hotspots) != viewed_count or
                    sum(entry["expanded"] for entry in hotspots) != bool(open_index) or
                    (open_index and not hotspots[open_index - 1]["expanded"])):
                raise StopRun("The Rise labeled graphic marker states are inconsistent.")
        try:
            signature_hotspots = json.loads(hotspot_signature)
        except (TypeError, ValueError):
            raise StopRun("The Rise labeled graphic signature is unreadable.") from None
        if signature_hotspots != [
            [entry["id"], entry["label"], entry["content"]] for entry in hotspots
        ]:
            raise StopRun("The Rise labeled graphic contents changed unexpectedly.")
        return value
    detail = (" " + json.dumps(last_pending_diagnostic, ensure_ascii=False,
                               sort_keys=True)
              if isinstance(last_pending_diagnostic, dict) else "")
    raise StopRun("The Rise lesson did not finish loading within the probe limit." +
                  detail)


def _accordion_key(index, entry):
    """The ordinal, title and content survive a DOM re-render with new IDs."""
    digest = hashlib.sha256(entry["content"].encode("utf-8")).hexdigest()
    return index, entry["heading"], digest


def _remember_expanded(snapshot, visited):
    for index, entry in enumerate(snapshot["accordions"]):
        if entry["expanded"]:
            visited.add(_accordion_key(index, entry))


def _unvisited_accordions(snapshot, visited):
    return [
        entry for index, entry in enumerate(snapshot["accordions"])
        if _accordion_key(index, entry) not in visited
    ]


def _carousel_key(snapshot, index):
    """A slide is identified by its ordinal and the entire carousel contents."""
    return snapshot["carousel_signature"], index


def _remember_active_carousel(snapshot, visited):
    if snapshot["carousel_count"]:
        visited.add(_carousel_key(snapshot, snapshot["carousel_active_index"]))


def _unvisited_carousel(snapshot, visited):
    if not snapshot["carousel_count"]:
        return []
    return [index for index in range(1, len(snapshot["carousel_contents"]) + 1)
            if _carousel_key(snapshot, index) not in visited]


def _terminal(snapshot, visited, visited_slides):
    return (
        not _unvisited_accordions(snapshot, visited) and
        not _unvisited_carousel(snapshot, visited_slides) and
        snapshot["hotspot_viewed_count"] == snapshot["hotspot_count"] and
        snapshot["hotspot_open_index"] == 0 and
        snapshot["flipped_count"] == snapshot["card_count"] and
        snapshot["top"] >= snapshot["max"] - 2 and
        snapshot["continue_count"] == 0
    )


async def complete_rise_scorm(
    runner, item, *, max_stages=MAX_STAGES, max_actions=MAX_ACTIONS,
    settle_delay=.25, terminal_delay=1.0
):
    """Read a recognized Rise widget, then require its Coursera green mark.

    The caller decides whether to route a widget here. This module never
    claims an unsupported widget or a different course item is complete.
    """
    from coursera_start import StopRun

    path = plugin_path(item, runner.course["url"])
    if "/ungradedWidget/" not in path:
        raise StopRun("The generic Rise reader only supports ungradedWidget.")
    heading = lesson_heading(item.get("title"))
    if not heading:
        raise StopRun("The Rise widget has no reliable course-outline heading.")
    if await runner.verified_item(item):
        return
    request = {"path": path, "heading": heading}
    outer = _outer_checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "inspect"})), request)
    request.update(frame=outer["frame"], frame_title=outer["frame_title"])
    frame = await open_scorm_frame(runner.tab, request["frame"])
    try:
        snapshot = await _inner(frame, request, "inspect", delay=settle_delay)
        request["lesson"] = snapshot["lesson"]
        stages = 0
        actions = 0
        terminal_probes = 0
        visited = set()
        visited_slides = set()
        _remember_expanded(snapshot, visited)
        _remember_active_carousel(snapshot, visited_slides)
        while actions < max_actions:
            unread = _unvisited_accordions(snapshot, visited)
            unread_slides = _unvisited_carousel(snapshot, visited_slides)
            unread_hotspots = [index for index, marker in enumerate(
                snapshot["hotspot_markers"], 1) if not marker["viewed"]]
            if snapshot["hotspot_open_index"]:
                action = "hotspot_close"
            elif unread_hotspots:
                action = "hotspot_open"
            elif unread:
                action = "expand"
            elif snapshot["flipped_count"] < snapshot["card_count"]:
                action = "flip"
            elif unread_slides:
                action = "carousel_dot"
            elif snapshot["top"] < snapshot["max"] - 2:
                action = "scroll"
            elif snapshot["continue_count"] == 1:
                action = "continue"
            else:
                if not _terminal(snapshot, visited, visited_slides):
                    raise StopRun("The Rise widget has not reached a static terminal stage.")
                terminal_probes += 1
                if terminal_probes > SETTLE_PROBES:
                    raise StopRun("The Rise terminal stage did not stabilize.")
                await asyncio.sleep(terminal_delay)
                follow = await _inner(frame, request, "inspect", delay=settle_delay)
                _remember_expanded(follow, visited)
                _remember_active_carousel(follow, visited_slides)
                stable = (
                    follow["signature"] == snapshot["signature"] and
                    follow["accordion_signature"] == snapshot["accordion_signature"] and
                    follow["carousel_signature"] == snapshot["carousel_signature"] and
                    follow["carousel_active_index"] == snapshot["carousel_active_index"] and
                    follow["hotspot_signature"] == snapshot["hotspot_signature"] and
                    follow["hotspot_viewed_count"] == snapshot["hotspot_viewed_count"] and
                    follow["hotspot_open_index"] == snapshot["hotspot_open_index"] and
                    follow["top"] == snapshot["top"] and
                    follow["max"] == snapshot["max"] and
                    follow["flipped_count"] == snapshot["flipped_count"] and
                    follow["progress"] == snapshot["progress"]
                )
                snapshot = follow
                if stable and _terminal(snapshot, visited, visited_slides):
                    break
                continue
            before = snapshot
            action_args = {
                "accordion_signature": snapshot["accordion_signature"],
                "stage_signature": snapshot["signature"],
                "panels_read": not unread,
                "carousel_read": not unread_slides,
                "hotspots_read": (not unread_hotspots and
                                  snapshot["hotspot_open_index"] == 0),
            }
            if action == "expand":
                action_args["expand_id"] = unread[0]["id"]
                expected_key = _accordion_key(
                    snapshot["accordions"].index(unread[0]), unread[0])
            elif action == "carousel_dot":
                action_args["carousel_index"] = unread_slides[0]
                action_args["carousel_signature"] = snapshot["carousel_signature"]
            elif action in ("hotspot_open", "hotspot_close"):
                action_args["hotspot_index"] = (
                    unread_hotspots[0] if action == "hotspot_open"
                    else snapshot["hotspot_open_index"])
                action_args["hotspot_signature"] = snapshot["hotspot_signature"]
            result = await _inner(
                frame, request, action, action_args=action_args, delay=settle_delay)
            if result.get("clicked") != action:
                raise StopRun("The Rise reading action was not acknowledged.")
            actions += 1
            terminal_probes = 0
            if action == "continue":
                stages += 1
                if stages >= max_stages:
                    raise StopRun("The Rise widget exceeded its stage limit.")
                for _ in range(SETTLE_PROBES):
                    await asyncio.sleep(settle_delay)
                    snapshot = await _inner(frame, request, "inspect", delay=settle_delay)
                    _remember_expanded(snapshot, visited)
                    _remember_active_carousel(snapshot, visited_slides)
                    if snapshot["signature"] != before["signature"]:
                        visited_slides.clear()
                        _remember_active_carousel(snapshot, visited_slides)
                        break
                else:
                    raise StopRun("Rise Continue did not reveal another reading stage.")
            else:
                for _ in range(SETTLE_PROBES):
                    await asyncio.sleep(settle_delay)
                    snapshot = await _inner(frame, request, "inspect", delay=settle_delay)
                    _remember_expanded(snapshot, visited)
                    _remember_active_carousel(snapshot, visited_slides)
                    if (action == "expand" and expected_key in visited or
                            action == "flip" and
                            snapshot["flipped_count"] > before["flipped_count"] or
                            action == "carousel_dot" and
                            snapshot["carousel_signature"] == before["carousel_signature"] and
                            snapshot["carousel_active_index"] == action_args["carousel_index"] or
                            action == "hotspot_open" and
                            snapshot["hotspot_signature"] == before["hotspot_signature"] and
                            snapshot["hotspot_open_index"] == action_args["hotspot_index"] and
                            snapshot["hotspot_markers"][action_args["hotspot_index"] - 1]["viewed"] or
                            action == "hotspot_close" and
                            snapshot["hotspot_signature"] == before["hotspot_signature"] and
                            snapshot["hotspot_open_index"] == 0 and
                            snapshot["hotspot_markers"][action_args["hotspot_index"] - 1]["viewed"] or
                            action == "scroll" and snapshot["top"] > before["top"]):
                        break
                else:
                    raise StopRun("The Rise reading control did not advance.")
        else:
            raise StopRun("The Rise widget exceeded its reading action limit.")
        if not _terminal(snapshot, visited, visited_slides):
            raise StopRun("The Rise widget has not reached a static terminal stage.")
    finally:
        await frame.close()
    if await runner.verified_item(item, wait=2):
        return
    confirmed = _outer_checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "inspect"})), request)
    if (confirmed["frame"] != request["frame"] or
            confirmed["frame_title"] != request["frame_title"]):
        raise StopRun("The Coursera widget changed before completion.")
    marked = _checked(await runner.tab.evaluate(_script(OUTER_JS, {
        **request, "action": "mark", "inner_verified": True})))
    if marked.get("clicked") != "mark":
        raise StopRun("Coursera did not acknowledge the Rise completion action.")
    runner.event("rise_scorm_mark_clicked", key=item["key"], stages=stages, actions=actions)
    if not await runner.verified_item(item, wait=20):
        raise StopRun("Coursera did not show a green completion mark for the Rise widget.")

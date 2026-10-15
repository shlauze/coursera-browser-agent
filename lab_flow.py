"""Verified launch and completion workflow for Coursera ungraded labs.

The lab's external page is observed only for loading. Its content is never
interpreted as instructions or evidence that the Coursera lesson is complete.
"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlsplit

import coursera_start as engine


POLL_SECONDS = 1.5
PAGE_PROBES = 15
LAUNCH_PROBES = 20
READY_PROBES = 90


LAB_PAGE_JS = r"""(request => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = element => {
    if (!element || !element.getClientRects().length) return false;
    for (let current = element; current; current = current.parentElement) {
      const style = getComputedStyle(current);
      if (current.hidden || current.inert || current.getAttribute('aria-hidden') === 'true' ||
          style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility)) return false;
    }
    return true;
  };
  const current = new URL(location.href);
  if (current.protocol !== 'https:' || !['coursera.org', 'www.coursera.org'].includes(current.hostname) ||
      current.username || current.password || (current.port && current.port !== '443') ||
      current.pathname.replace(/\/$/, '') !== request.path)
    return {error: 'The selected tab is not the expected Coursera lab.', url: location.href};
  const goalDialogs = [...document.querySelectorAll('[role="dialog"][aria-modal="true"][data-testid="scroll-container"]')]
    .filter(visible).filter(element => {
      const text = clean(element.innerText);
      return text.startsWith("You've completed today's goals!") &&
        text.includes('Continue to use this momentum to get you closer to completing this course.');
    });
  if (goalDialogs.length > 1)
    return {error: 'Multiple daily-goal dialogs are visible.', url: location.href};
  if (goalDialogs.length === 1) {
    const buttons = [...goalDialogs[0].querySelectorAll('button,[role="button"]')].filter(visible)
      .filter(element => /^Continue learning$/i.test(clean(element.innerText || element.getAttribute('aria-label'))));
    const result = {url: location.href, ready: document.readyState, goal_modal: true,
      continue_count: buttons.length};
    if (request.action === 'dismiss_goal') {
      if (buttons.length !== 1 || buttons[0].disabled || buttons[0].getAttribute('aria-disabled') === 'true')
        return {...result, error: 'The daily-goal dialog has no unique enabled Continue learning button.'};
      buttons[0].click();
      return {...result, clicked: 'dismiss_goal'};
    }
    return result;
  }
  if (request.action === 'dismiss_goal')
    return {url: location.href, ready: document.readyState, goal_modal: false, clicked: 'already_closed'};
  const drawers = [...document.querySelectorAll('[role="dialog"][aria-modal="true"][data-testid="drawer-toggle-drawer"]')]
    .filter(visible);
  if (drawers.length > 1)
    return {error: 'Multiple course sidebar drawers are visible.', url: location.href};
  if (drawers.length === 1) {
    const buttons = [...drawers[0].querySelectorAll('button,[role="button"]')].filter(visible)
      .filter(element => /^Close$/i.test(clean(element.getAttribute('aria-label'))));
    const result = {url: location.href, ready: document.readyState, sidebar_drawer: true,
      close_count: buttons.length};
    if (request.action === 'close_drawer') {
      if (buttons.length !== 1 || buttons[0].disabled || buttons[0].getAttribute('aria-disabled') === 'true')
        return {...result, error: 'The course sidebar drawer has no unique enabled Close button.'};
      buttons[0].click();
      return {...result, clicked: 'close_drawer'};
    }
    return result;
  }
  if (request.action === 'close_drawer')
    return {url: location.href, ready: document.readyState, sidebar_drawer: false, clicked: 'already_closed'};
  const mains = [...document.querySelectorAll('main,[role="main"]')].filter(visible);
  const outerMains = mains.filter(element => !mains.some(other => other !== element && other.contains(element)));
  // Coursera's observed ungradedLab layout has no semantic main element.
  // An exact lesson URL plus one visible h1 allows a narrow body fallback.
  if (outerMains.length > 1)
    return {error: 'Multiple visible lab lesson areas.', url: location.href, ready: document.readyState};
  const root = outerMains[0] || document.body;
  // The course sidebar can temporarily render a second h1 for the daily-goal
  // banner. Identify the lesson by its own roster heading instead.
  const heading = [...root.querySelectorAll('h1')].filter(visible)
    .map(element => clean(element.innerText))
    .filter(value => value === request.heading);
  if (heading.length !== 1)
    return {error: 'Expected one visible lab heading.', url: location.href, ready: document.readyState};
  const actions = [...root.querySelectorAll('button,a[href],[role="button"]')].filter(visible).map(element => ({
    element,
    label: clean(element.innerText || element.getAttribute('aria-label')),
    disabled: !!element.disabled || element.getAttribute('aria-disabled') === 'true',
    href: element.matches('a[href]') ? element.href : element.closest('a[href]')?.href || null
  }));
  const launch = actions.filter(action => /^(?:Launch|Resume) lab$/i.test(action.label));
  const mark = actions.filter(action => /^Mark as complete(?:d)?$/i.test(action.label));
  const result = {url: location.href, ready: document.readyState, heading: heading[0],
    launch_count: launch.length, mark_count: mark.length,
    launch_disabled: launch[0]?.disabled ?? null, mark_disabled: mark[0]?.disabled ?? null,
    launch_href: launch[0]?.href || null, launch_label: launch.length === 1 ? launch[0].label : null};
  if (!request.action || request.action === 'inspect') return result;
  const selected = request.action === 'launch' ? launch : request.action === 'mark' ? mark : [];
  if (selected.length !== 1 || selected[0].disabled || document.readyState !== 'complete')
    return {...result, error: 'The requested lab action is missing, ambiguous or disabled.'};
  selected[0].element.scrollIntoView({block: 'center'});
  selected[0].element.click();
  return {...result, clicked: request.action};
})"""


EXTERNAL_READY_JS = r"""(() => {
  const address = new URL(location.href);
  const body = document.body;
  const textLength = (body?.innerText || '').trim().length;
  const surfaceCount = body?.querySelectorAll('main,[role="main"],iframe,canvas,form').length || 0;
  return {url: location.href, ready: document.readyState,
    rendered: textLength >= 30 || surfaceCount > 0,
    browser_error: !!document.querySelector('#main-frame-error, .interstitial-wrapper')};
})()"""


def _lab_path(item, course_url):
    course = urlsplit(course_url)
    lesson = urlsplit(item.get('url', ''))
    if (course.scheme != 'https' or course.hostname not in {'coursera.org', 'www.coursera.org'} or
            course.username or course.password or course.port not in {None, 443} or
            not re.fullmatch(r'/learn/[A-Za-z0-9][A-Za-z0-9_-]*', course.path.rstrip('/')) or
            course.query or course.fragment):
        raise engine.StopRun('The configured Coursera course URL is invalid for a lab.')
    path = lesson.path.rstrip('/')
    match = re.fullmatch(re.escape(course.path.rstrip('/')) +
                         r'/(ungradedLab|ungradedLti)/([A-Za-z0-9_-]+)(?:/[A-Za-z0-9_-]+)?', path)
    if (lesson.scheme != 'https' or lesson.hostname not in {'coursera.org', 'www.coursera.org'} or
            lesson.username or lesson.password or lesson.port not in {None, 443} or
            lesson.query or lesson.fragment or not match or
            item.get('key') != match[1] + ':' + match[2]):
        raise engine.StopRun('The lab URL or item identity does not match the configured course.')
    return path


async def _page_targets(tab):
    response = await tab.call('Target.getTargets')
    infos = response.get('targetInfos')
    if not isinstance(infos, list):
        raise engine.StopRun('Chrome did not return page targets for the lab launch.')
    return {info['targetId']: info for info in infos
            if isinstance(info, dict) and info.get('type') == 'page'
            and isinstance(info.get('targetId'), str) and info['targetId']}


def _source_target(tab, targets, path):
    target_id = urlsplit(getattr(tab, 'address', '')).path.rstrip('/').split('/')[-1]
    if target_id in targets:
        return target_id
    matches = [target_id for target_id, info in targets.items()
               if urlsplit(info.get('url', '')).path.rstrip('/') == path
               and urlsplit(info.get('url', '')).hostname in {'coursera.org', 'www.coursera.org'}]
    if len(matches) == 1:
        return matches[0]
    raise engine.StopRun('The original Coursera lab tab could not be identified uniquely.')


async def _lab_snapshot(tab, path, heading, action='inspect'):
    result = await tab.evaluate(LAB_PAGE_JS + '(' + json.dumps({'path': path, 'heading': heading,
                                                                 'action': action}) + ')')
    if not isinstance(result, dict):
        raise engine.StopRun('The Coursera lab page returned an unreadable snapshot.')
    return result


async def _wait_lab_page(tab, path, heading, *, required_action='launch'):
    last = {}
    goal_dismissed = False
    drawer_dismissed = False
    for _ in range(PAGE_PROBES):
        last = await _lab_snapshot(tab, path, heading)
        if last.get('error') and 'not the expected Coursera lab' in last['error']:
            raise engine.StopRun(last['error'])
        if last.get('goal_modal'):
            if not goal_dismissed:
                dismissed = await _lab_snapshot(tab, path, heading, 'dismiss_goal')
                if dismissed.get('clicked') not in {'dismiss_goal', 'already_closed'}:
                    raise engine.StopRun(dismissed.get('error', 'The Coursera daily-goal dialog could not be dismissed.'))
                goal_dismissed = True
            await asyncio.sleep(POLL_SECONDS)
            continue
        if last.get('sidebar_drawer'):
            if not drawer_dismissed:
                closed = await _lab_snapshot(tab, path, heading, 'close_drawer')
                if closed.get('clicked') not in {'close_drawer', 'already_closed'}:
                    raise engine.StopRun(closed.get('error', 'The Coursera course sidebar drawer could not be closed.'))
                drawer_dismissed = True
            await asyncio.sleep(POLL_SECONDS)
            continue
        if (not last.get('error') and last.get('ready') == 'complete' and last.get('heading')
                and (required_action is None or last.get(required_action + '_count'))):
            return last
        await asyncio.sleep(POLL_SECONDS)
    raise engine.StopRun('The Coursera lab page did not finish loading with its expected controls.')


def _external_url(url, source_path):
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return (parsed.scheme in {'http', 'https'} and bool(parsed.hostname) and
            not (parsed.hostname in {'coursera.org', 'www.coursera.org'}
                 and parsed.path.rstrip('/') == source_path))


async def _external_target_snapshot(target_id):
    """Inspect only load state of the newly opened page, never its content."""
    pages = engine.local_json(engine.CDP_URL + '/json/list')
    page = next((row for row in pages if row.get('type') == 'page' and row.get('id') == target_id), None)
    address = page.get('webSocketDebuggerUrl') if page else None
    if not address:
        return None
    popup = engine.ChromeTab(address)
    try:
        await asyncio.wait_for(popup.connect(), timeout=8)
        return await asyncio.wait_for(popup.evaluate(EXTERNAL_READY_JS), timeout=8)
    finally:
        await popup.close()


def _pick_new_target(targets, old_ids, source_id, launch_href):
    created = {target_id: info for target_id, info in targets.items()
               if target_id not in old_ids and target_id != source_id}
    if not created:
        return None
    opened_by_source = {target_id: info for target_id, info in created.items()
                        if info.get('openerId') == source_id}
    if len(opened_by_source) == 1:
        return next(iter(opened_by_source))
    if len(opened_by_source) > 1:
        raise engine.StopRun('Launch lab opened multiple windows from the course tab.')
    if launch_href:
        expected = urlsplit(launch_href)
        matching = [target_id for target_id, info in created.items()
                    if urlsplit(info.get('url', '')).hostname == expected.hostname]
        if len(matching) == 1:
            return matching[0]
    if len(created) == 1:
        raise engine.StopRun('A new window appeared, but Chrome did not link it to Launch lab.')
    raise engine.StopRun('Launch lab opened several unrelated windows; no unique lab target was identified.')


async def _wait_launched_site(tab, path, source_id, before_targets, launch_href):
    """Return ('popup', id, host) or ('same_tab', id, host) after site load."""
    old_ids = set(before_targets)
    target_id = None
    mode = None
    stable = 0
    for probe in range(LAUNCH_PROBES + READY_PROBES):
        targets = await _page_targets(tab)
        if source_id not in targets:
            raise engine.StopRun('The original Coursera lab tab closed during Launch lab.')
        if mode is None:
            target_id = _pick_new_target(targets, old_ids, source_id, launch_href)
            if target_id:
                mode = 'popup'
            elif _external_url(targets[source_id].get('url', ''), path):
                target_id, mode = source_id, 'same_tab'
            elif probe >= LAUNCH_PROBES - 1:
                raise engine.StopRun('Launch lab did not open a new window or navigate to a lab site.')
        if mode:
            if target_id not in targets:
                raise engine.StopRun('The launched lab window closed before its site loaded.')
            try:
                snapshot = (await _external_target_snapshot(target_id) if mode == 'popup'
                            else await tab.evaluate(EXTERNAL_READY_JS))
            except (OSError, TimeoutError, RuntimeError):
                snapshot = None
            if snapshot and snapshot.get('browser_error'):
                raise engine.StopRun('The launched lab site displayed a browser loading error.')
            if (snapshot and _external_url(snapshot.get('url', ''), path)
                    and snapshot.get('ready') == 'complete' and snapshot.get('rendered')):
                stable += 1
                if stable >= 2:
                    return mode, target_id, urlsplit(snapshot['url']).hostname
            else:
                stable = 0
            if probe >= LAUNCH_PROBES + READY_PROBES - 1:
                break
        await asyncio.sleep(POLL_SECONDS)
    raise engine.StopRun('The launched lab site did not finish loading in the allowed time.')


async def _verify_green_with_outline(tab, item, path, heading, verify_completion, *, wait):
    # Lab actions need the drawer closed, but the green check is displayed in
    # that same course outline. Reopen it only after the lab action has ended.
    await _wait_lab_page(tab, path, heading, required_action=None)
    result = await tab.evaluate(engine.LESSON_OUTLINE_JS + '(' + json.dumps(item['key']) + ')')
    if result.get('error'):
        raise engine.StopRun('The lab outline could not be opened for completion verification: ' + result['error'])
    return await verify_completion(item, wait=wait)


async def complete_lab(tab, item, course_url, *, verify_completion, event=None):
    """Launch or resume a lab, wait for its site, then verify Coursera's green mark.

    The caller must first open the exact lab lesson. `verify_completion` is the
    site's sidebar verifier, accepting `(item, wait=seconds)` and returning a
    boolean. This function never treats a click itself as completion.
    """
    path = _lab_path(item, course_url)
    heading = (item.get('aria_label') or item.get('title') or '').strip()
    if not heading:
        raise engine.StopRun('The Coursera lab has no lesson heading in the course roster.')
    initial = await _lab_snapshot(tab, path, heading)
    if initial.get('error') == 'The selected tab is not the expected Coursera lab.':
        raise engine.StopRun(initial['error'])
    if await verify_completion(item):
        return
    page = await _wait_lab_page(tab, path, heading)
    if page['launch_count'] != 1 or page['launch_disabled']:
        raise engine.StopRun('The lab page needs exactly one available Launch lab or Resume lab action.')
    before = await _page_targets(tab)
    source_id = _source_target(tab, before, path)
    launch = await _lab_snapshot(tab, path, heading, 'launch')
    if launch.get('clicked') != 'launch':
        raise engine.StopRun('Launch/Resume lab was not clicked on the expected lesson: ' + launch.get('error', 'unknown change'))
    if event:
        event('lab_launched', key=item['key'], action=page.get('launch_label'))
    mode, launched_id, host = await _wait_launched_site(tab, path, source_id, before, page.get('launch_href'))
    if event:
        event('lab_site_loaded', key=item['key'], host=host, mode=mode)
    if mode == 'same_tab':
        await tab.navigate(item['url'])
    else:
        # Launch lab can open another Coursera course tab. Leaving it open
        # makes the next queue start reject the browser as ambiguous.
        closed = await tab.call('Target.closeTarget', {'targetId': launched_id})
        if closed.get('success') is not True:
            raise engine.StopRun('The launched lab window could not be closed after it loaded.')
        if event:
            event('lab_popup_closed', key=item['key'])
        response = await tab.call('Target.activateTarget', {'targetId': source_id})
        if response is None:
            raise engine.StopRun('Chrome could not return to the Coursera lab tab.')
    returned = await _lab_snapshot(tab, path, heading)
    if returned.get('error') == 'The selected tab is not the expected Coursera lab.':
        # Some launchers both spawn a popup and redirect the source target.
        # Return that same managed Coursera tab to the exact known lesson.
        await tab.navigate(item['url'])
        returned = await _lab_snapshot(tab, path, heading)
        if returned.get('error') == 'The selected tab is not the expected Coursera lab.':
            raise engine.StopRun(returned['error'])
    if await _verify_green_with_outline(tab, item, path, heading, verify_completion, wait=3):
        return
    page = await _wait_lab_page(tab, path, heading, required_action='mark')
    if page['mark_count'] != 1 or page['mark_disabled']:
        raise engine.StopRun('Mark as completed is no longer available on the lab page.')
    mark = await _lab_snapshot(tab, path, heading, 'mark')
    if mark.get('clicked') != 'mark':
        raise engine.StopRun('Mark as completed was not clicked on the expected lesson: ' + mark.get('error', 'unknown change'))
    if event:
        event('lab_mark_clicked', key=item['key'])
    if await _verify_green_with_outline(tab, item, path, heading, verify_completion, wait=20):
        return
    await tab.navigate(item['url'])
    await _wait_lab_page(tab, path, heading, required_action=None)
    if await _verify_green_with_outline(tab, item, path, heading, verify_completion, wait=15):
        return
    raise engine.StopRun('The lab was launched and marked, but its green Coursera completion check was not confirmed.')

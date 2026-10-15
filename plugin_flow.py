"""Narrow completion flow for Coursera's simple ungraded plugin lessons.

Plugin pages can contain an embedded activity or an external tool.  Those
require their own handler.  This flow only handles a rendered lesson whose
sole completion action is an explicit ``Mark as complete`` button, and then
requires the ordinary Coursera green mark.
"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlsplit


PLUGIN_TYPES = frozenset({"ungradedPlugin", "ungradedWidget"})
PROBES = 12
PROBE_DELAY = 1.5


PLUGIN_JS = r"""(request => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = element => {
    if (!element || !element.getClientRects().length) return false;
    for (let node = element; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (node.hidden || node.inert || node.getAttribute('aria-hidden') === 'true' ||
          style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility)) return false;
    }
    return true;
  };
  const current = new URL(location.href);
  if (current.protocol !== 'https:' || !['coursera.org', 'www.coursera.org'].includes(current.hostname) ||
      current.username || current.password || (current.port && current.port !== '443') ||
      current.pathname.replace(/\/$/, '') !== request.path)
    return {error:'Wrong Coursera plugin lesson; no action made.', url:location.href};
  // Coursera's widget shell uses #main-container without a main landmark.
  const roots = [...document.querySelectorAll('main,[role="main"],#main-container')].filter(visible)
    .filter(element => ![...document.querySelectorAll('main,[role="main"],#main-container')]
      .some(other => other !== element && visible(other) && other.contains(element)));
  if (roots.length !== 1)
    return {error:'Expected one visible plugin lesson area.', url:location.href};
  const root = roots[0];
  const modalCount = [...document.querySelectorAll('[role="dialog"][aria-modal="true"]')]
    .filter(visible).length;
  const headings = [...root.querySelectorAll('h1')].filter(visible)
    .map(element => clean(element.innerText));
  // The widget shell duplicates its title in a screen-reader-only h1.
  if (!headings.length || headings.some(heading => heading !== request.heading))
    return {error:'Plugin lesson heading does not match the roster.', url:location.href};
  const controls = [...root.querySelectorAll('button,[role="button"]')].filter(visible)
    .filter(element => !element.closest('nav,[role="navigation"],aside'));
  const label = element => clean(element.innerText || element.getAttribute('aria-label'));
  const mark = controls.filter(element => /^Mark as complete(?:d)?$/i.test(label(element)));
  const otherActions = controls.map(label).filter(text =>
    /^(?:Start|Launch|Resume|Submit|Check|Validate|Save answer|Continue)(?:\s|$)/i.test(text));
  const frames = [...root.querySelectorAll('iframe')].filter(visible);
  // Only the observed passive Topic overview iframe is a completion-only
  // widget. A loaded iframe with any other title or lesson remains an activity.
  const knownOverviewFrame = request.path.includes('/ungradedWidget/') &&
    request.heading === 'Overview' && frames.length === 1 &&
    clean(frames[0].title) === 'Topic overview' &&
    /^https:\/\/d10o6em2qtnr4q\.cloudfront\.net\/assets\/[0-9a-f]{32}\/index\.html$/.test(frames[0].src);
  const passiveOverview = knownOverviewFrame &&
    performance.getEntriesByName(frames[0].src).some(entry =>
      entry.initiatorType === 'iframe' && entry.responseEnd > 0);
  const activityInputs = [...root.querySelectorAll('input,textarea,select,[contenteditable="true"],iframe')]
    .filter(visible).filter(element => !element.closest('nav,[role="navigation"],aside'));
  const unhandledInputs = activityInputs.filter(element => !passiveOverview || element !== frames[0]);
  const result = {url:location.href, ready:document.readyState, heading:headings[0],
    mark_count:mark.length, mark_disabled:mark[0]?.disabled ||
      mark[0]?.getAttribute('aria-disabled') === 'true',
    activity_count:unhandledInputs.length, frame_pending:knownOverviewFrame && !passiveOverview,
    passive_overview:passiveOverview,
    modal_count:modalCount,
    other_actions:otherActions.slice(0,8)};
  if (request.action !== 'mark') return result;
  if (!(document.readyState === 'complete' ||
        (document.readyState === 'interactive' && passiveOverview)) ||
      modalCount || unhandledInputs.length || otherActions.length ||
      mark.length !== 1 || result.mark_disabled)
    return {...result, error:'This plugin has no unique safe completion action.'};
  mark[0].scrollIntoView({block:'center'});
  mark[0].click();
  return {...result, clicked:'mark'};
})"""


def plugin_path(item, course_url):
    """Validate the exact course, plugin type and identity before any action."""
    from coursera_start import StopRun

    course = urlsplit(course_url)
    lesson = urlsplit(item.get("url", ""))
    course_path = course.path.rstrip("/")
    if (course.scheme != "https" or course.hostname not in {"coursera.org", "www.coursera.org"} or
            course.username or course.password or course.port not in {None, 443} or
            not re.fullmatch(r"/learn/[A-Za-z0-9][A-Za-z0-9_-]*", course_path) or
            course.query or course.fragment):
        raise StopRun("The configured course URL is invalid for a plugin lesson.")
    path = lesson.path.rstrip("/")
    match = re.fullmatch(re.escape(course_path) +
                         r"/(ungradedPlugin|ungradedWidget)/([A-Za-z0-9_-]+)(?:/[A-Za-z0-9_-]+)?", path)
    if (lesson.scheme != "https" or lesson.hostname not in {"coursera.org", "www.coursera.org"} or
            lesson.username or lesson.password or lesson.port not in {None, 443} or
            lesson.query or lesson.fragment or not match or
            item.get("key") != match[1] + ":" + match[2]):
        raise StopRun("The plugin lesson URL or identity does not match the configured course.")
    return path


def lesson_heading(title):
    """Strip known outline metadata and its optional Resume/Get started control."""
    if not isinstance(title, str):
        return ""
    cleaned = " ".join(title.split())
    match = re.fullmatch(
        # The outline snapshot can contain U+FFFD in place of its bullet.
        # Accept it only at the separator position before a numeric duration.
        r"(.+?)\s+Ungraded\s+(?:Plugin|Widget)(?:\s*[•·\uFFFD]\s*\d+\s*min(?:utes?)?"
        r"|\s+\.\s+Duration:\s+\d+\s+minutes?\s+\d+\s+min)?"
        r"(?:\s+(?:Resume|Get started))?",
        cleaned, flags=re.I)
    if match:
        return match.group(1).strip()
    # A changed control or extra outline text must not become the expected
    # lesson heading. It would otherwise be mistaken for a changed page.
    if re.search(r"\s+Ungraded\s+(?:Plugin|Widget)\b", cleaned, re.I):
        return ""
    return cleaned


async def complete_plugin(runner, item, *, probes=PROBES, probe_delay=PROBE_DELAY):
    """Complete a simple plugin lesson only after Coursera confirms its mark."""
    from coursera_start import StopRun

    path = plugin_path(item, runner.course["url"])
    heading = lesson_heading(item.get("title"))
    if not heading:
        raise StopRun("The plugin lesson has no roster heading.")
    from scorm_flow import complete_static_scorm, is_static_scorm
    if is_static_scorm(path, heading):
        await complete_static_scorm(runner, item, path, heading)
        return
    if await runner.verified_item(item):
        return
    last = None
    for _ in range(probes):
        last = await runner.tab.evaluate(PLUGIN_JS + "(" + json.dumps({
            "path": path, "heading": heading, "action": "inspect"}) + ")")
        if not isinstance(last, dict):
            raise StopRun("The plugin lesson returned an unreadable page snapshot.")
        if last.get("error") and not last["error"].startswith("Expected one visible plugin lesson area"):
            raise StopRun(last["error"])
        if last.get("modal_count"):
            raise StopRun("A modal dialog covers the plugin lesson; completion is not available safely.")
        if (last.get("activity_count") and not last.get("frame_pending")) or last.get("other_actions"):
            raise StopRun("Plugin lesson contains an embedded activity or additional action; "
                          "the simple completion flow cannot perform it safely.")
        ready = last.get("ready") == "complete" or (last.get("ready") == "interactive" and
                                                   last.get("passive_overview") is True)
        if (ready and last.get("mark_count") == 1 and
                not last.get("mark_disabled") and not last.get("activity_count")):
            break
        await asyncio.sleep(probe_delay)
    else:
        raise StopRun("Plugin lesson did not expose a unique available Mark as complete action: " +
                      item.get("title", ""))
    marked = await runner.tab.evaluate(PLUGIN_JS + "(" + json.dumps({
        "path": path, "heading": heading, "action": "mark"}) + ")")
    if not isinstance(marked, dict) or marked.get("clicked") != "mark":
        raise StopRun("Plugin completion action was not confirmed on the exact lesson: " +
                      str(marked.get("error") if isinstance(marked, dict) else marked))
    runner.event("plugin_mark_clicked", key=item["key"])
    if await runner.verified_item(item, wait=20):
        return
    raise StopRun("Coursera did not confirm a green completion mark for the plugin lesson: " +
                  item.get("title", ""))

"""Recognize a Coursera graded file-upload assignment without editing it.

The quiz solver only handles answer controls that it can inspect and verify.
An uploaded project needs a learner-authored file, so this probe reports the
exact form type to the queue and never clicks, fills, uploads, or submits.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit


UPLOAD_PROBE_JS = r"""(request => {
  const clean = text => (text || '').replace(/\s+/g, ' ').trim();
  const visible = element => {
    if (!element || !element.getClientRects().length) return false;
    for (let parent = element; parent; parent = parent.parentElement) {
      const style = getComputedStyle(parent);
      if (parent.hidden || parent.inert || parent.getAttribute('aria-hidden') === 'true' ||
          style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility)) return false;
    }
    return true;
  };
  let expected;
  try { expected = new URL(request.url); } catch { return {match: false}; }
  const base = expected.pathname.replace(/\/$/, '');
  if (location.protocol !== 'https:' ||
      !['www.coursera.org', 'coursera.org'].includes(location.hostname) ||
      location.pathname.replace(/\/$/, '') !== base + '/attempt')
    return {match: false, reason: 'different_attempt'};
  const dialogs = [...document.querySelectorAll('[role="dialog"],dialog[open]')].filter(visible);
  const scope = dialogs.at(-1) || document.body;
  const uploads = [...scope.querySelectorAll('[data-testid="part-Submission_FileUploadQuestion"]')]
    .filter(visible);
  const buttons = [...scope.querySelectorAll('button,[role="button"]')].filter(visible);
  const addFile = buttons.filter(element => clean(element.innerText ||
    element.getAttribute('aria-label')) === 'Add File' && !element.disabled &&
    element.getAttribute('aria-disabled') !== 'true');
  const answerEditors = [...scope.querySelectorAll(
    'textarea,[contenteditable="true"],[role="textbox"],input[type="radio"],input[type="number"],select')]
    .filter(visible);
  const answerChecks = [...scope.querySelectorAll('input[type="checkbox"],[role="checkbox"]')]
    .filter(visible).filter(element => !/honor code|understand and agree|work is your own/i.test(
      clean(element.closest('label')?.innerText || element.parentElement?.innerText || '')));
  const otherUploads = [...scope.querySelectorAll('input[type="file"]')]
    .filter(visible);
  return {match: uploads.length === 1 && addFile.length === 1 &&
    answerEditors.length === 0 && answerChecks.length === 0 && otherUploads.length <= 1,
    upload_questions: uploads.length, add_file_buttons: addFile.length,
    other_answer_controls: answerEditors.length + answerChecks.length,
    url: location.href};
})"""


def _identity(item):
    """Only a graded assignment with an exact Coursera lesson URL is eligible."""
    if not isinstance(item, dict) or not isinstance(item.get("url"), str):
        return False
    try:
        url = urlsplit(item["url"])
        match = re.fullmatch(
            r"/learn/([A-Za-z0-9_-]+)/assignment-submission/([A-Za-z0-9_-]+)/[A-Za-z0-9_-]+/?",
            url.path,
        )
        return bool(
            url.scheme == "https" and url.hostname in {"www.coursera.org", "coursera.org"}
            and url.port in {None, 443} and not url.username and not url.password
            and not url.query and not url.fragment and match
            and item.get("key") == "assignment:" + match.group(2)
            and isinstance(item.get("title"), str) and
            re.search(r"\bGraded Assignment\b", item["title"], re.I)
        )
    except ValueError:
        return False


async def inspect_graded_file_upload(tab, item):
    """Return an actionable manual-work reason, or ``None`` if not this form.

    The browser evaluation is read-only. The result never treats a file-upload
    project as a completed assignment; a later Coursera outline/certificate
    check must still verify actual completion.
    """
    if not _identity(item):
        return None
    result = await tab.evaluate(UPLOAD_PROBE_JS + "(" + json.dumps(
        {"url": item["url"]}, ensure_ascii=False) + ")")
    if not isinstance(result, dict) or not result.get("match"):
        return None
    observed = urlsplit(result.get("url", ""))
    expected = urlsplit(item["url"])
    if (observed.scheme != "https" or observed.hostname not in {"www.coursera.org", "coursera.org"}
            or observed.path.rstrip("/") != expected.path.rstrip("/") + "/attempt"
            or observed.query or observed.fragment):
        return None
    return ("Это оцениваемый проект с загрузкой файла (Add File). Нужен собственный "
            "готовый файл учащегося; автоматическая отправка и подтверждение Honor Code "
            "не выполнены. После загрузки и оценки Coursera запустите скрипт снова.")

"""Verified Coursera video completion across late lesson redirects.

Coursera can change a lesson URL after ``open_item`` has observed the correct
page (for example, while a preceding quiz is finishing).  Video playback must
be retried on the requested lecture; a result returned from another URL is
never evidence that the requested video played.
"""

import asyncio
import json
from urllib.parse import urlsplit


def same_lecture_page(actual_url, expected_url):
    """Accept only the exact Coursera lecture URL for the requested item."""
    actual = urlsplit(actual_url or "")
    expected = urlsplit(expected_url or "")
    return (
        actual.hostname in {"coursera.org", "www.coursera.org"}
        and expected.hostname in {"coursera.org", "www.coursera.org"}
        and "/lecture/" in expected.path
        and actual.path.rstrip("/") == expected.path.rstrip("/")
    )


async def complete_video(runner, item, *, page_attempts=4, media_probes=6, probe_delay=2):
    """Play and confirm one video, recovering from delayed site navigation.

    ``runner`` is a CourseRunner with open_item, verified_item, tab, and event.
    Completion still requires Coursera's own completed sidebar row.  This
    function never marks a video green by its playback position alone.
    """
    from coursera_start import StopRun, VIDEO_JS

    expected_url = item["url"]
    expected_path = urlsplit(expected_url).path.rstrip("/")
    if not same_lecture_page(expected_url, expected_url):
        raise StopRun("Expected a Coursera video lesson URL: " + expected_url)
    last_result = {}
    for page_attempt in range(1, page_attempts + 1):
        try:
            await runner.open_item(item)
        except StopRun as exc:
            # A redirect that races with open_item can make its page check fail.
            # Preserve hard failures such as leaving Coursera or a missing
            # course outline, which retries cannot safely repair here.
            if "The lesson did not load:" not in str(exc):
                raise
            runner.event("video_open_retry", key=item["key"], attempt=page_attempt, reason=str(exc))
            last_result = {"error": str(exc)}
            continue
        if await runner.verified_item(item):
            return
        for probe in range(media_probes):
            result = await runner.tab.evaluate(VIDEO_JS + "(" + json.dumps(expected_path) + ")")
            last_result = result
            runner.event("video_action", key=item["key"], attempt=page_attempt, probe=probe + 1, result=result)
            print("VIDEO_END:", item["title"], json.dumps(result, ensure_ascii=False), flush=True)
            if not same_lecture_page(result.get("url"), expected_url):
                runner.event("video_page_drift", key=item["key"], attempt=page_attempt,
                             expected_url=expected_url, observed_url=result.get("url", ""))
                break
            if result.get("error"):
                if probe + 1 < media_probes:
                    await asyncio.sleep(probe_delay)
                continue
            # The player can report ended before the progress mutation reaches
            # the outline.  Also accept a server mark that arrives despite a
            # player result with ended=false; the sidebar is authoritative.
            if await runner.verified_item(item, wait=12):
                return
            break
        # A page may redirect immediately after VIDEO_JS returns.  Check the
        # site mark once before reopening the exact lesson on the next retry.
        if await runner.verified_item(item):
            return
    detail = last_result.get("error") or last_result.get("url") or "no player result"
    raise StopRun("Video completion was not confirmed after navigation retries: "
                  + item["title"] + " (last observation: " + str(detail) + ")")

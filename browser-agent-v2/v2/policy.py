"""Classify Coursera items before any browser action.

The classifier is deliberately conservative: an unknown item is never treated
as a reading or a lab merely because a familiar word appears in its title.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class ItemAction(str, Enum):
    READING = "reading"
    VIDEO = "video"
    UNGRADED_LAB = "ungraded_lab"
    DIALOGUE = "dialogue"
    ROLE_PLAY = "role_play"
    ASSESSMENT = "assessment"
    PROGRAMMING_LAB = "programming_lab"
    PLUGIN = "plugin"
    PEER = "peer"
    REVIEW_REQUIRED = "review_required"


@dataclass(frozen=True)
class Decision:
    action: ItemAction
    reason: str


def is_coursework(kind: str) -> bool:
    """The requested run mode includes tests, assignments and labs only."""
    value = " ".join((kind or "").casefold().split())
    return any(word in value for word in ("assignment", "quiz", "assessment", "exam", "lab", "peer review", "review your peers"))


def decide(kind: str, title: str = "") -> Decision:
    """Select a handler from the *visible Coursera type*, never the URL alone."""
    value = " ".join((kind or "").casefold().split())
    title_value = " ".join((title or "").casefold().split())
    if value in {"ungraded plugin", "ungraded widget", "ungradedplugin", "ungradedwidget"}:
        return Decision(ItemAction.PLUGIN, "Read supported plugin/SCORM activities and verify Coursera completion.")
    if "programming" in value or ("lab" in value and re.search(r"\bgraded\b", value)):
        return Decision(ItemAction.PROGRAMMING_LAB, "Inspect the actual lab and its feedback; defer an unsupported environment.")
    if any(word in value for word in ("peer review", "peer-graded", "review your peers")):
        return Decision(ItemAction.PEER, "Inspect peer submission and review state before any write.")
    if any(word in value for word in ("assignment", "quiz", "assessment", "exam")):
        return Decision(ItemAction.ASSESSMENT, "Inspect the form and answer supported question types one at a time.")
    if "role play" in value or "role-play" in value:
        return Decision(ItemAction.ROLE_PLAY, "Role Play has an explicit end-and-feedback flow.")
    if "dialogue" in value or "dialog" in value:
        return Decision(ItemAction.DIALOGUE, "Dialogue has an explicit end-and-feedback flow.")
    if "lab" in value:
        return Decision(ItemAction.UNGRADED_LAB, "Ungraded lab can be launched and marked complete if Coursera offers that control.")
    if "video" in value or "lecture" in value:
        return Decision(ItemAction.VIDEO, "Video must actually reach the end before completion is checked.")
    if "reading" in value or "article" in value:
        return Decision(ItemAction.READING, "Reading completion is checked from the live outline.")
    if "optional" in title_value and "reflection" in title_value:
        return Decision(ItemAction.REVIEW_REQUIRED, "Open-response reflection requires a task-specific answer and authorship check.")
    return Decision(ItemAction.REVIEW_REQUIRED, f"Unsupported or unclear Coursera item type: {kind or 'unknown'}")

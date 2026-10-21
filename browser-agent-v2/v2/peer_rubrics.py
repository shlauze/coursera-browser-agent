"""Observed rubric requirements and review drafts; never infer scores from headings."""
import hashlib
import re
from .handlers import HandlerError


def parse_requirements(text):
    limit=re.search(r"Word limit:\s*(\d+)",text,re.I)
    section=text.split("Grading Criteria Overview",1)[-1] if "Grading Criteria Overview" in text else ""
    criteria=re.findall(r"Did the learner\b[^?]+\?",section)
    criteria=list(dict.fromkeys(" ".join(c.split()) for c in criteria))
    return {"word_limit":int(limit[1]) if limit else None,
            "criteria":[{"id":hashlib.sha256(c.encode()).hexdigest(),"text":c,"choices":["Yes","No"]} for c in criteria],
            "source":"observed assignment instructions"}


def validate_review_draft(draft, submission_id, rubric):
    if not submission_id or not isinstance(draft,dict) or draft.get("submission_id")!=submission_id:
        raise HandlerError("Review draft does not match the observed submission")
    rows=draft.get("criteria",[])
    expected={r['id'] for r in rubric}
    if not expected or len(rows)!=len(expected) or {r.get('id') for r in rows}!=expected:
        raise HandlerError("Review must cover every observed criterion exactly once")
    for row in rows:
        if row.get('choice') not in {'Yes','No'} or not isinstance(row.get('evidence'),str) or not row['evidence'].strip():
            raise HandlerError("Each rubric choice needs supporting evidence from the actual work")
    return draft

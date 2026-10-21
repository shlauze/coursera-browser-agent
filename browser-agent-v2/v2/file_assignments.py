"""Explicit local-file sources; never submit an empty or uncertain upload."""
import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit
from .handlers import HandlerError


def base_url(url):
    u=urlsplit(url)
    if u.hostname not in {"coursera.org", "www.coursera.org"} or not re.fullmatch(r"/learn/[^/]+/assignment-submission/[^/]+/[^/]+(?:/(?:attempt|view-feedback))?/?",u.path):
        raise HandlerError("Unknown file assignment URL")
    return "https://www.coursera.org"+re.sub(r"/(?:attempt|view-feedback)/?$", "", u.path).rstrip("/")


def require_file_source(store, url):
    base=base_url(url)
    source=store.get_meta("file_source:"+base)
    if not isinstance(source,dict) or source.get("assignment_url")!=base:
        raise HandlerError("File assignment needs an explicitly assigned local file; empty upload blocked")
    path=Path(source.get("path", ""))
    if not path.is_file() or path.stat().st_size==0:
        raise HandlerError("Assignment file is missing or empty")
    if hashlib.sha256(path.read_bytes()).hexdigest()!=source.get("sha256"):
        raise HandlerError("Assignment file changed; review its content before upload")
    return path


def upload_and_submit(page, checkpoint, store, action_id):
    from .assessments import inspect
    if store.get_meta(f"submission:{action_id}") in {"submitting","pending","passed"}:
        raise HandlerError("File submission already sent or uncertain; reconcile first")
    path=require_file_source(store,page.url)
    checkpoint();inspect(page)
    field=page.locator('input[type="file"]')
    if field.count()!=1:
        raise HandlerError("File upload field is not unique")
    field.set_input_files(str(path))
    # A local file-input value alone does not prove the server accepted it.
    receipt=page.get_by_text(path.name,exact=True).locator('visible=true')
    receipt.first.wait_for(state="visible",timeout=60000)
    if receipt.count()!=1 or 'No uploaded file' in page.locator('body').inner_text():
        raise HandlerError("Uploaded file receipt is not uniquely confirmed")
    checkpoint();inspect(page)
    submit=page.get_by_role('button',name='Submit',exact=True).locator('visible=true')
    if submit.count()!=1 or not submit.is_enabled():
        raise HandlerError("File Submit is unavailable; uploaded draft preserved")
    store.set_meta(f"submission:{action_id}","submitting")
    store.record_stage(action_id,"submitting")
    submit.click(timeout=10000)
    store.record_event("INFO","FILE_SUBMIT_CLICKED","File submitted once; remote result must be reconciled")
    raise HandlerError("File submitted once; awaiting verified remote grade")

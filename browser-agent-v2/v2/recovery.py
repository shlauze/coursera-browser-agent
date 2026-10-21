"""Bounded retries for explicitly read/navigation-only operations."""
from functools import wraps
from playwright.sync_api import Error, TimeoutError

def transient(error):
    return getattr(error, "retryable_read", False) is True or isinstance(error, TimeoutError) or (isinstance(error, Error) and any(
        text in str(error).lower() for text in ("execution context was destroyed", "cannot find context", "not attached to the dom")))

def read_retry(function):
    @wraps(function)
    def wrapped(page, *args, **kwargs):
        for attempt in range(3):
            try:
                return function(page, *args, **kwargs)
            except Exception as error:
                if attempt == 2 or not transient(error):
                    raise
                page.wait_for_timeout(500 * (attempt + 1))
    return wrapped

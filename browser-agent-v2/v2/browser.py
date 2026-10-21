"""Dedicated Chrome connection for the second-generation course runner.

The runner owns one Playwright connection and one primary Coursera tab.  Chrome
uses a separate persistent profile, so the original runner's cookies and tabs
are never modified by this module.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


MY_LEARNING_URL = "https://www.coursera.org/my-learning?myLearningTab=IN_PROGRESS"


class BrowserUnavailable(RuntimeError):
    """Chrome or its DevTools endpoint could not be reached."""


def _chrome_executable(explicit: str | Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    configured = os.environ.get("COURSE_V2_CHROME_PATH")
    if configured:
        candidates.append(Path(configured))
    on_path = shutil.which("chrome") or shutil.which("chrome.exe")
    if on_path:
        candidates.append(Path(on_path))
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise BrowserUnavailable("Google Chrome was not found; set COURSE_V2_CHROME_PATH")


def _devtools_ready(endpoint: str) -> bool:
    try:
        with urllib.request.urlopen(f"{endpoint}/json/version", timeout=1.5) as response:
            return b"Browser" in response.read(512)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


class BrowserSession:
    """Connect to Chrome over CDP; launch a dedicated profile if needed.

    ``connect()`` is idempotent. ``close()`` only disconnects automation and
    deliberately leaves the visible Chrome window open. The caller may invoke
    ``shutdown_browser()`` if it explicitly wants to close the Chrome process
    created by this session.
    """

    def __init__(
        self,
        profile_dir: str | Path,
        *,
        port: int = 9224,
        chrome_path: str | Path | None = None,
        startup_timeout: float = 20.0,
    ) -> None:
        self.profile_dir = Path(profile_dir).resolve()
        self.port = port
        self.chrome_path = chrome_path
        self.startup_timeout = startup_timeout
        self.endpoint = f"http://127.0.0.1:{port}"
        self.process: subprocess.Popen[bytes] | None = None
        self._playwright: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None

    def connect(self) -> "BrowserSession":
        if self.page is not None:
            return self
        if not _devtools_ready(self.endpoint):
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            chrome = _chrome_executable(self.chrome_path)
            flags = [
                str(chrome),
                f"--remote-debugging-port={self.port}",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={self.profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                MY_LEARNING_URL,
            ]
            self.process = subprocess.Popen(
                flags,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if _devtools_ready(self.endpoint):
                    break
                if self.process.poll() is not None:
                    raise BrowserUnavailable("Dedicated Chrome exited before DevTools became ready")
                time.sleep(0.25)
            else:
                raise BrowserUnavailable(f"Chrome DevTools did not start at {self.endpoint}")

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailable("Playwright is not installed; run pip install -r requirements.txt") from exc
        self._playwright = sync_playwright().start()
        try:
            self.browser = self._playwright.chromium.connect_over_cdp(self.endpoint, timeout=20_000)
            self.context = self.browser.contexts[0]
            # Own a fresh tab; never navigate a user's existing My Learning tab.
            self.page = self.context.new_page()
            self.page.goto(MY_LEARNING_URL, wait_until="domcontentloaded", timeout=45_000)
            self.page.set_default_timeout(12_000)
            return self
        except Exception:
            self.close()
            raise

    def goto(self, url: str, *, timeout_ms: int = 45_000) -> Any:
        if self.page is None:
            raise BrowserUnavailable("Call connect() before navigating")
        return self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    def close(self) -> None:
        self.page = None
        self.context = None
        self.browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def shutdown_browser(self) -> None:
        """Close only the Chrome process started by this instance."""
        self.close()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None

    def __enter__(self) -> "BrowserSession":
        return self.connect()

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close()

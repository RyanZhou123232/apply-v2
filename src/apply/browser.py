"""Browser setup: a headed, persistent Chrome profile that looks like a normal desktop browser.

One run at a time may use the profile (file lock). The legacy mode (fresh Chromium context,
headless by default) is kept behind a flag for tests and comparison.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

log = logging.getLogger(__name__)

PROFILE_DIR = Path(os.environ.get("APPLY_PROFILE_DIR") or
                   ("/home/box/.apply-v2-profile" if sys.platform != "win32" else Path.home() / ".apply-v2-profile"))
VIEWPORT = {"width": 1440, "height": 900}
LOCALE = "en-US"
TIMEZONE = "America/New_York"
# Playwright adds --enable-automation by default; it shows the "controlled by automated software"
# banner and sets navigator.webdriver. Drop it and the matching Blink feature.
IGNORE_DEFAULT_ARGS = ["--enable-automation"]
EXTRA_ARGS = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check",
              f"--lang={LOCALE}"]


class ProfileBusy(RuntimeError):
    """Another run holds the persistent profile."""


try:  # POSIX (the box)
    import fcntl

    def _try_lock(fh) -> bool:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def _unlock(fh) -> None:
        fcntl.flock(fh, fcntl.LOCK_UN)
except ImportError:  # Windows
    import msvcrt

    def _try_lock(fh) -> bool:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def profile_lock(profile_dir: Path = PROFILE_DIR) -> Iterator[Path]:
    """Exclusive, non-blocking lock on the profile. Released automatically if the process dies."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    fh = open(profile_dir.parent / f".{profile_dir.name}.lock", "a+")
    if not _try_lock(fh):
        fh.close()
        raise ProfileBusy(f"browser profile {profile_dir} is in use by another run")
    try:
        yield profile_dir
    finally:
        _unlock(fh)
        fh.close()


def chrome_channel() -> Optional[str]:
    """Use the real Google Chrome when installed (APPLY_BROWSER_CHANNEL overrides; 'chromium' forces bundled)."""
    ch = os.environ.get("APPLY_BROWSER_CHANNEL")
    if ch:
        return None if ch == "chromium" else ch
    return "chrome" if shutil.which("google-chrome") or shutil.which("google-chrome-stable") else None


def ensure_display(headed: bool) -> bool:
    """Headed needs an X display on Linux. Falls back to APPLY_DISPLAY, else headless (with a warning)."""
    if not headed or sys.platform != "linux" or os.environ.get("DISPLAY"):
        return headed
    if os.environ.get("APPLY_DISPLAY"):
        os.environ["DISPLAY"] = os.environ["APPLY_DISPLAY"]
        return True
    log.warning("no DISPLAY / APPLY_DISPLAY set; running headless (bot-detection risk is higher)")
    return False


def launch_persistent(p, headed: bool = True, profile_dir: Path = PROFILE_DIR):
    """Return a persistent BrowserContext. Caller must hold profile_lock(profile_dir)."""
    headed = ensure_display(headed)
    return p.chromium.launch_persistent_context(
        str(profile_dir), channel=chrome_channel(), headless=not headed,
        viewport=VIEWPORT, screen=VIEWPORT, locale=LOCALE, timezone_id=TIMEZONE,
        ignore_default_args=IGNORE_DEFAULT_ARGS, args=EXTRA_ARGS, accept_downloads=False)


def launch_legacy(p, headed: bool = False):
    """Old behaviour: fresh bundled Chromium, throwaway context. Returns (browser, context)."""
    browser = p.chromium.launch(headless=not ensure_display(headed))
    context = browser.new_context(viewport={"width": 1280, "height": 1000}, locale=LOCALE, timezone_id=TIMEZONE)
    return browser, context


@contextmanager
def open_context(p, headed: bool = True, persistent: bool = True, profile_dir: Path = PROFILE_DIR):
    """`with open_context(p) as context:` — persistent locked profile by default, legacy when persistent=False."""
    if not persistent:
        browser, context = launch_legacy(p, headed)
        try:
            yield context
        finally:
            browser.close()
        return
    with profile_lock(profile_dir):
        context = launch_persistent(p, headed, profile_dir)
        try:
            yield context
        finally:
            context.close()


def first_page(context):
    """Reuse the blank tab a persistent context opens with, closing stray restored tabs."""
    pages = list(context.pages)
    page = pages[0] if pages else context.new_page()
    for extra in pages[1:]:
        try:
            extra.close()
        except Exception:
            pass
    return page

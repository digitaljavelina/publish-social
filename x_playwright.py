#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "playwright",
# ]
# ///
"""
x_playwright.py - post to X (Twitter) by driving a real logged-in browser with
Playwright, instead of the paid X API.

The X API bills per post (~$0.015, ~$0.20 with a link). This script avoids that
entirely: it logs in once in a real browser, saves the session, then reuses it to
type a post, attach a photo or video, and click Post - exactly as a human would,
for free.

It is a thin CLI with two subcommands:

    # 1. One time: log in by hand and save the session (opens a real browser).
    uv run x_playwright.py login

    # 2. Post. Text only, or with one photo/video (urls are just text):
    uv run x_playwright.py post --text "hello from a browser, no API"
    uv run x_playwright.py post --text "with a pic" --media ./photo.jpg
    uv run x_playwright.py post --text "with a clip" --media ./clip.mp4 --print-url

publish.py shells out to this script when X_TRANSPORT=browser, so the same post
file can go to X for free alongside the other networks. It also works standalone.

The session is stored as a Playwright storage_state JSON (cookies + localStorage)
at $X_PLAYWRIGHT_STATE, defaulting to ~/.config/publish-social/x-state.json. It
contains live login cookies, so it is written 0600 and must never be committed.

Browsers: this uses Chromium. On a normal machine, run `playwright install
chromium` once and it is found automatically. If your environment ships a
pre-provisioned Chromium whose build doesn't match the pip Playwright version,
point $PLAYWRIGHT_CHROMIUM_EXECUTABLE at the binary (the common
/opt/pw-browsers/chromium path is also auto-detected) and that one is launched.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

X_LOGIN_URL = "https://x.com/login"
X_HOME_URL = "https://x.com/home"
X_COMPOSE_URL = "https://x.com/compose/post"

# X marks its UI with stable data-testid attributes; we lean on those rather than
# brittle text or class selectors.
SEL_COMPOSER = '[data-testid="tweetTextarea_0"]'
SEL_FILE_INPUT = '[data-testid="fileInput"]'
SEL_POST_BUTTON = '[data-testid="tweetButton"]'        # the modal composer's Post
SEL_LOGGED_IN = '[data-testid="SideNav_NewTweet_Button"]'  # only present when logged in

# Media can take a while to upload+process (especially video); X disables the Post
# button until it finishes, which is the signal we wait on.
MEDIA_UPLOAD_TIMEOUT_MS = 300_000
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}


class XPlaywrightError(Exception):
    """A user-facing failure (not logged in, post never confirmed, etc.)."""


def state_path() -> Path:
    """Where the saved login session lives (override with $X_PLAYWRIGHT_STATE)."""
    override = os.environ.get("X_PLAYWRIGHT_STATE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "publish-social" / "x-state.json"


def _chromium_executable() -> str | None:
    """Return an explicit Chromium path to launch, or None to use Playwright's own.

    On a normal machine you run `playwright install chromium` and Playwright finds
    its managed build automatically (returns None here). Some environments instead
    ship a pre-provisioned Chromium whose build may not match the pip Playwright
    version; point $PLAYWRIGHT_CHROMIUM_EXECUTABLE at it (or rely on the common
    /opt/pw-browsers/chromium path) and we launch that binary directly.
    """
    override = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if override and Path(override).exists():
        return override
    managed = Path("/opt/pw-browsers/chromium")
    if managed.exists():
        return str(managed)
    return None


def _launch_chromium(p, *, headless: bool):
    """Launch Chromium, preferring an explicit executable when one is configured."""
    exe = _chromium_executable()
    if exe:
        return p.chromium.launch(headless=headless, executable_path=exe)
    return p.chromium.launch(headless=headless)


# --------------------------------------------------------------------------- #
# login: open a real browser, let the human sign in, save the session.
# --------------------------------------------------------------------------- #


def login(headless: bool = False) -> Path:
    """Open X's login page in a visible browser, wait for the user to finish
    signing in (including any 2FA), then persist the session to disk.

    Headed by default - you cannot type a password into a headless window. We
    detect success by the compose button appearing, which only renders once
    you're authenticated, then save cookies + localStorage as storage_state.
    """
    from playwright.sync_api import sync_playwright

    dest = state_path()
    dest.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = _launch_chromium(p, headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto(X_LOGIN_URL)
        print(
            "A browser window has opened. Log in to X there (username, password, "
            "and 2FA if prompted).\nWaiting up to 5 minutes for you to finish..."
        )
        try:
            page.wait_for_selector(SEL_LOGGED_IN, timeout=300_000)
        except Exception as exc:  # timeout or navigation issue
            browser.close()
            raise XPlaywrightError(
                "Did not detect a logged-in session within 5 minutes. Re-run "
                "`x_playwright.py login` and complete the sign-in."
            ) from exc

        context.storage_state(path=str(dest))
        browser.close()

    try:
        dest.chmod(0o600)  # live login cookies - keep them private
    except OSError:
        pass
    print(f"Saved X session to {dest}")
    return dest


# --------------------------------------------------------------------------- #
# post: reuse the saved session to compose and send one post.
# --------------------------------------------------------------------------- #


def _validate_media(media: Path) -> None:
    if not media.is_file():
        raise XPlaywrightError(f"Media file not found: {media}")
    suffix = media.suffix.lower()
    if suffix not in IMAGE_SUFFIXES | VIDEO_SUFFIXES:
        raise XPlaywrightError(
            f"Unsupported media type {suffix!r}. Allowed: "
            f"{sorted(IMAGE_SUFFIXES | VIDEO_SUFFIXES)}"
        )


def _tweet_url_from_response(resp) -> str | None:
    """Pull the canonical post URL out of X's CreateTweet GraphQL response."""
    try:
        data = resp.json()
        result = data["data"]["create_tweet"]["tweet_results"]["result"]
        rest_id = result["rest_id"]
        screen = (
            result["core"]["user_results"]["result"]["legacy"]["screen_name"]
        )
        return f"https://x.com/{screen}/status/{rest_id}"
    except Exception:
        return None


def post(
    text: str,
    media: Path | None = None,
    *,
    headless: bool = True,
    dry_run: bool = False,
) -> str:
    """Compose and send one post to X through a logged-in browser, returning the
    new post's URL.

    Steps, mirroring what a person does: open the composer, type the text, attach
    one optional photo/video and wait for the upload to finish, then click Post.
    The URL is read from X's own CreateTweet response (most reliable); if that is
    missed, we fall back to the "View" link in the confirmation toast.
    """
    from playwright.sync_api import sync_playwright

    if media is not None:
        _validate_media(media)

    saved = state_path()

    if dry_run:
        what = f"text ({len(text)} chars)"
        if media is not None:
            what += f" + media {media.name}"
        session = "session ready" if saved.is_file() else f"NO session at {saved} (run login first)"
        print(f"[dry-run] would post to X via browser: {what} [{session}]")
        return "(dry-run; nothing posted)"

    if not saved.is_file():
        raise XPlaywrightError(
            f"No saved X session at {saved}. Run `x_playwright.py login` first."
        )

    with sync_playwright() as p:
        browser = _launch_chromium(p, headless=headless)
        context = browser.new_context(storage_state=str(saved))
        page = context.new_page()
        try:
            return _compose_and_send(page, text, media)
        finally:
            browser.close()


def _compose_and_send(page, text: str, media: Path | None) -> str:
    page.goto(X_COMPOSE_URL)

    # If the session has expired, X bounces us to the login/flow page and the
    # composer never appears - surface that as a clear, actionable error.
    try:
        page.wait_for_selector(SEL_COMPOSER, timeout=30_000)
    except Exception as exc:
        if "/login" in page.url or "/i/flow" in page.url:
            raise XPlaywrightError(
                "X session has expired (got redirected to login). Re-run "
                "`x_playwright.py login` to refresh it."
            ) from exc
        raise XPlaywrightError(
            "Could not find the X composer. X's UI may have changed, or the "
            "session is invalid; try `x_playwright.py login` again."
        ) from exc

    page.click(SEL_COMPOSER)
    page.fill(SEL_COMPOSER, text)

    if media is not None:
        # The file input is present but visually hidden; set_input_files drives it
        # directly without needing the click-to-open native dialog.
        page.set_input_files(SEL_FILE_INPUT, str(media))
        # X disables the Post button while the attachment uploads/processes. Wait
        # for it to become enabled again - the clean "upload done" signal.
        page.wait_for_function(
            """(sel) => {
                const b = document.querySelector(sel);
                return b && b.getAttribute('aria-disabled') !== 'true';
            }""",
            arg=SEL_POST_BUTTON,
            timeout=MEDIA_UPLOAD_TIMEOUT_MS,
        )

    # Click Post and capture X's CreateTweet response in the same step, so we can
    # read the new post's id straight from the server's reply.
    try:
        with page.expect_response(
            lambda r: "CreateTweet" in r.url, timeout=120_000
        ) as resp_info:
            page.click(SEL_POST_BUTTON)
        url = _tweet_url_from_response(resp_info.value)
    except Exception:
        url = None

    if url:
        return url

    # Fallback: the confirmation toast shows a "View" link to the new post.
    try:
        toast = page.wait_for_selector(
            '[data-testid="toast"] a[href*="/status/"]', timeout=15_000
        )
        href = toast.get_attribute("href") if toast else None
        if href:
            return href if href.startswith("http") else f"https://x.com{href}"
    except Exception:
        pass

    raise XPlaywrightError(
        "Posted, but could not read back the post URL (the post may still have "
        "gone through). Check your X profile to confirm."
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Post to X via a logged-in browser (Playwright), bypassing the paid API."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="Open a browser to sign in and save the session.")
    p_login.add_argument(
        "--headless", action="store_true",
        help="Advanced: run headless (you usually want a visible window to log in).",
    )

    p_post = sub.add_parser("post", help="Compose and send one post.")
    p_post.add_argument("--text", required=True, help="The post text (urls are just text).")
    p_post.add_argument("--media", help="Optional path to one photo or video to attach.")
    p_post.add_argument("--headed", action="store_true", help="Show the browser window (for debugging).")
    p_post.add_argument("--dry-run", action="store_true", help="Validate inputs; post nothing.")
    p_post.add_argument(
        "--print-url", action="store_true",
        help="Print only the resulting post URL on the last stdout line (for scripting).",
    )

    args = parser.parse_args()

    if args.command == "login":
        login(headless=args.headless)
        return 0

    if args.command == "post":
        media = Path(args.media).expanduser().resolve() if args.media else None
        url = post(args.text, media, headless=not args.headed, dry_run=args.dry_run)
        if args.print_url:
            print(url)
        else:
            print(f"Posted to X: {url}")
        return 0

    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except XPlaywrightError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)

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

It is a thin CLI:

    # 1. One time: log in by hand and save the session (opens a real browser).
    uv run x_playwright.py login

    # 1b. If X rate-limits the automated login ("We've temporarily limited your
    #     login"), skip it: log in normally in your own browser, export the
    #     cookies, and import them into the same saved session instead.
    uv run x_playwright.py import-session                       # paste auth_token + ct0
    uv run x_playwright.py import-session --cookies-file x.txt  # or a cookies export

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
import getpass
import json
import os
import sys
import time
from pathlib import Path

X_LOGIN_URL = "https://x.com/login"
X_HOME_URL = "https://x.com/home"
X_COMPOSE_URL = "https://x.com/compose/post"

# X marks its UI with stable data-testid attributes; we lean on those rather than
# brittle text or class selectors.
SEL_COMPOSER = '[data-testid="tweetTextarea_0"]'
SEL_FILE_INPUT = '[data-testid="fileInput"]'
SEL_ATTACHMENTS = '[data-testid="attachments"]'        # the media preview, once attached
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
# import-session: build the saved session from cookies you export by hand from a
# browser where you're already logged in - no automated login at all.
#
# X heavily defends its *login* flow against automation (you may hit "We've
# temporarily limited your login"). A session that is already valid is not
# defended the same way. So rather than driving the login form, log in normally
# in your own browser, export the cookies, and drop them into the same
# storage_state file `post` reads. The two that matter are auth_token (the
# session) and ct0 (the CSRF token X requires for write actions like posting).
# --------------------------------------------------------------------------- #

X_COOKIE_DOMAIN_SUFFIXES = (".x.com", ".twitter.com")
AUTH_COOKIE = "auth_token"   # without this you are not logged in
CSRF_COOKIE = "ct0"          # X requires this to post; it pairs with auth_token

# Browser exports spell sameSite a few different ways; Playwright wants exactly
# one of Strict / Lax / None.
_SAME_SITE_MAP = {
    "no_restriction": "None", "none": "None", "unspecified": "Lax",
    "lax": "Lax", "strict": "Strict",
}


def _is_x_cookie(domain: str) -> bool:
    d = domain.lower().lstrip(".")
    return any(d == s.lstrip(".") or d.endswith(s) for s in X_COOKIE_DOMAIN_SUFFIXES)


def _normalize_cookie(raw: dict) -> dict | None:
    """Coerce one exported cookie into Playwright storage_state shape, or None if
    it has no name/value to work with."""
    name = raw.get("name")
    value = raw.get("value")
    if not name or value is None:
        return None
    # Different exporters call the expiry 'expires' or 'expirationDate', and it
    # may be a float; a session cookie has none, which Playwright marks as -1.
    # In Netscape cookies.txt, an expiry of 0 also means "session cookie" - take
    # any non-positive value as -1 so Playwright doesn't drop it as expired.
    expires = raw.get("expires", raw.get("expirationDate"))
    try:
        expires = int(float(expires)) if expires is not None else -1
    except (TypeError, ValueError):
        expires = -1
    if expires <= 0:
        expires = -1
    same = _SAME_SITE_MAP.get(str(raw.get("sameSite", "")).lower(), "Lax")
    return {
        "name": name,
        "value": value,
        "domain": raw.get("domain", ".x.com"),
        "path": raw.get("path") or "/",
        "expires": expires,
        "httpOnly": bool(raw.get("httpOnly", raw.get("httponly", False))),
        "secure": bool(raw.get("secure", True)),
        "sameSite": same,
    }


def _parse_cookies_file(path: Path) -> list[dict]:
    """Parse a cookies export into normalized X cookies. Accepts either a JSON
    array (Cookie-Editor / EditThisCookie style) or a Netscape cookies.txt."""
    if not path.is_file():
        raise XPlaywrightError(f"Cookies file not found: {path}")
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        raise XPlaywrightError(f"Cookies file is empty: {path}")

    raw: list[dict] = []
    if text[:1] in "[{":
        data = json.loads(text)
        if isinstance(data, dict):  # a full storage_state, or {"cookies": [...]}
            data = data.get("cookies", [])
        raw = [c for c in data if isinstance(c, dict)]
    else:
        # Netscape: domain \t flag \t path \t secure \t expires \t name \t value.
        # Some tools mark httpOnly cookies with a leading "#HttpOnly_" on the line.
        for line in text.splitlines():
            stripped = line.strip()
            http_only = False
            if stripped.startswith("#HttpOnly_"):
                stripped = stripped[len("#HttpOnly_"):]
                http_only = True
            elif not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split("\t")
            if len(parts) != 7:
                continue
            domain, _flag, c_path, secure, expires, name, value = parts
            raw.append({
                "name": name, "value": value, "domain": domain, "path": c_path,
                "secure": secure.upper() == "TRUE", "httpOnly": http_only,
                "expires": expires,
            })

    normalized = (_normalize_cookie(c) for c in raw)
    return [c for c in normalized if c and _is_x_cookie(c["domain"])]


def _manual_cookie(name: str, value: str, *, http_only: bool) -> dict:
    """Build one cookie for a value pasted by hand, with sane X-style attributes
    and a year-out expiry so it persists rather than dying with the session."""
    one_year = 60 * 60 * 24 * 365
    return {
        "name": name, "value": value, "domain": ".x.com", "path": "/",
        "expires": int(time.time()) + one_year, "httpOnly": http_only,
        "secure": True, "sameSite": "Lax",
    }


def import_session(cookies_file: Path | None = None) -> Path:
    """Write the saved session from cookies exported from a browser you logged
    into by hand, skipping X's automated-login defenses entirely.

    With --cookies-file we parse an export (JSON array or Netscape cookies.txt)
    and keep the X cookies. Without one, we prompt for the two values that matter
    (auth_token and ct0); getpass keeps them off-screen and out of shell history.
    """
    if cookies_file is not None:
        cookies = _parse_cookies_file(cookies_file)
    else:
        print(
            "Paste the X cookies from a browser where you're logged in.\n"
            "Find them in DevTools > Application (or Storage) > Cookies > "
            "https://x.com\n"
        )
        auth = getpass.getpass(f"{AUTH_COOKIE} value: ").strip()
        if not auth:
            raise XPlaywrightError(f"{AUTH_COOKIE} is required - nothing imported.")
        ct0 = getpass.getpass(f"{CSRF_COOKIE} value (press Enter to skip): ").strip()
        cookies = [_manual_cookie(AUTH_COOKIE, auth, http_only=True)]
        if ct0:
            cookies.append(_manual_cookie(CSRF_COOKIE, ct0, http_only=False))

    names = {c["name"] for c in cookies}
    if AUTH_COOKIE not in names:
        raise XPlaywrightError(
            f"No {AUTH_COOKIE!r} cookie found, so this would not be a logged-in "
            "session. Export cookies for x.com while signed in, and try again."
        )
    if CSRF_COOKIE not in names:
        print(
            f"Warning: no {CSRF_COOKIE!r} cookie found. Browsing will work, but "
            "posting may fail; re-export including ct0 if it does.",
            file=sys.stderr,
        )

    dest = state_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"cookies": cookies, "origins": []}, indent=2))
    try:
        dest.chmod(0o600)  # live login cookies - keep them private
    except OSError:
        pass
    print(f"Imported {len(cookies)} X cookie(s); saved session to {dest}")
    print("Verify with: uv run x_playwright.py post --text 'test' --dry-run")
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

        # Confirm the file actually attached: X renders a media preview. This is
        # the load-bearing check. The Post button is ALREADY enabled by the text
        # alone, so "Post is enabled" is NOT a valid "media is ready" signal -
        # without this we race ahead and post text-only when the attach silently
        # fails or hasn't registered yet.
        try:
            page.wait_for_selector(SEL_ATTACHMENTS, timeout=60_000)
        except Exception as exc:
            raise XPlaywrightError(
                "The media never appeared in the composer, so nothing was posted. "
                "X may have rejected the file (type, size, or length)."
            ) from exc

        # Then wait for the upload/processing to finish. X disables Post while the
        # media uploads and re-enables it when ready, but there is a brief window
        # right after attaching where Post is still enabled. So first wait for it
        # to go disabled (tolerating media that uploads too fast to ever disable),
        # then wait for it to come back enabled - the true "upload done" signal.
        _btn_disabled = (
            "(s) => { const b = document.querySelector(s);"
            " return b && b.getAttribute('aria-disabled') === 'true'; }"
        )
        _btn_enabled = (
            "(s) => { const b = document.querySelector(s);"
            " return b && b.getAttribute('aria-disabled') !== 'true'; }"
        )
        try:
            page.wait_for_function(_btn_disabled, arg=SEL_POST_BUTTON, timeout=10_000)
        except Exception:
            pass  # a small/fast upload may never visibly disable the button
        page.wait_for_function(
            _btn_enabled, arg=SEL_POST_BUTTON, timeout=MEDIA_UPLOAD_TIMEOUT_MS
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

    p_import = sub.add_parser(
        "import-session",
        help="Build the session from cookies exported from your own logged-in browser (no automated login).",
    )
    p_import.add_argument(
        "--cookies-file",
        help="Path to a cookies export (Netscape cookies.txt or JSON array). "
             "If omitted, you'll be prompted to paste auth_token and ct0.",
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

    if args.command == "import-session":
        cookies_file = (
            Path(args.cookies_file).expanduser() if args.cookies_file else None
        )
        import_session(cookies_file=cookies_file)
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

#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "atproto",
#   "Mastodon.py",
#   "tweepy",
#   "Pillow",
#   "requests",
#   "PyYAML",
#   "python-dotenv",
# ]
# ///
"""
publish.py - publish a social post to Bluesky, Mastodon, Threads, LinkedIn, and X.

Reads a Markdown post file (frontmatter plus one fenced code block per platform),
optionally attaches one image (see images.py), publishes to the selected
platforms, then marks the file posted and fills in its Publish Tracking table.

Run it with uv so dependencies resolve from the PEP 723 header above:

    uv run publish.py --auto --dry-run --platforms bluesky,mastodon
    uv run publish.py --auto --platforms bluesky,mastodon

Gating: a file only publishes when its frontmatter has `status: ready` and
`approved: true`. That is the human review gate; keep it.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

# images.py sits next to this file; the script's own directory is on sys.path.
import images

def _resolve_env_path() -> Path:
    """Find .env, preferring a stable per-user location outside the repo.

    A .env kept next to the script can be lost on a git pull or reinstall, so a
    stable per-user location is preferred. Resolution order:
      1. $PUBLISH_SOCIAL_ENV  - explicit override (any path)
      2. ~/.config/publish-social/.env - stable default; survives updates
      3. next to this script  - fallback
    """
    override = os.environ.get("PUBLISH_SOCIAL_ENV")
    if override:
        return Path(override).expanduser()
    stable = Path.home() / ".config" / "publish-social" / ".env"
    if stable.exists():
        return stable
    return Path(__file__).resolve().parent / ".env"


ENV_PATH = _resolve_env_path()
DEFAULT_POSTS_DIR = Path(
    os.environ.get("SOCIAL_POSTS_DIR", str(Path.home() / "social-posts"))
).expanduser()

# X posts via pay-per-use OAuth 1.0a (see the setup guide). It is the only
# platform that costs money: ~$0.015 per text/image post, ~$0.20 if the post
# contains a link. Posting an `x` section spends real credits.
SUPPORTED = ("bluesky", "mastodon", "threads", "linkedin", "x")

# Canonical display names, matching the `## Heading` and Publish Tracking table.
DISPLAY = {
    "bluesky": "Bluesky",
    "mastodon": "Mastodon",
    "threads": "Threads",
    "linkedin": "LinkedIn",
    "x": "X",
}

# Soft character ceilings. We warn rather than block, since the generator
# already targets these and an occasional overage is the human's call.
CHAR_LIMITS = {"bluesky": 300, "mastodon": 500, "threads": 500, "linkedin": 3000, "x": 280}


class PublishError(Exception):
    """A user-facing failure (bad gate, missing creds, etc.). Printed without a traceback."""


# --------------------------------------------------------------------------- #
# Reading the post file
# --------------------------------------------------------------------------- #


@dataclass
class Post:
    path: Path
    frontmatter: dict
    body: str

    @property
    def slug(self) -> str:
        return self.path.stem


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict, body) for a `---`-delimited markdown file."""
    if not text.startswith("---"):
        raise PublishError("File has no YAML frontmatter (expected a leading '---').")
    end = text.find("\n---", 3)
    if end == -1:
        raise PublishError("Frontmatter is not closed with '---'.")
    fm_text = text[3:end].strip("\n")
    body = text[end + 4 :].lstrip("\n")
    fm = yaml.safe_load(fm_text) or {}
    if not isinstance(fm, dict):
        raise PublishError("Frontmatter did not parse to a mapping.")
    return fm, body


def load_post(path: Path) -> Post:
    fm, body = split_frontmatter(path.read_text(encoding="utf-8"))
    return Post(path=path, frontmatter=fm, body=body)


def extract_post_text(body: str, platform: str) -> str | None:
    """
    Pull the first fenced code block under the `## <Platform>` heading.

    Matches the heading by its first word (`## Bluesky (~270 chars)` -> bluesky),
    then captures the first ``` ... ``` block before the next `## ` heading.
    """
    lines = body.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            first_word = line[3:].strip().lower().split()
            if first_word and first_word[0] == platform:
                start = i + 1
                break
    if start is None:
        return None

    buf: list[str] = []
    in_block = False
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.lstrip().startswith("```"):
            if not in_block:
                in_block = True
                continue
            break  # close of the first block
        if in_block:
            buf.append(line)
    text = "\n".join(buf).strip()
    return text or None


def strip_threads_hashtags(text: str) -> str:
    """Remove hashtags from Threads text.

    Threads promotes the first hashtag in a post to a single header "topic tag"
    (rendered as `username > Topic`) and leaves any others as plain text, so there
    is no way to keep a hashtag on Threads without that header tag. We drop them
    and tidy the whitespace left behind. The other platforms keep their hashtags.
    """
    text = re.sub(r"(?<!\w)#\w+", "", text)   # drop #hashtag tokens (leaves "C#" alone)
    text = re.sub(r"[ \t]+\n", "\n", text)    # trailing spaces on a line
    text = re.sub(r"[ \t]{2,}", " ", text)    # gaps left mid-line
    text = re.sub(r"\n{3,}", "\n\n", text)    # blank-line runs
    return text.strip()


# --------------------------------------------------------------------------- #
# Selecting which file to publish
# --------------------------------------------------------------------------- #


def select_file(args: argparse.Namespace) -> Path:
    if args.file:
        path = Path(args.file).expanduser().resolve()
        if not path.is_file():
            raise PublishError(f"No such file: {path}")
        return path

    if not args.auto:
        raise PublishError("Pass --file <path> or --auto to pick a ready post.")

    if not DEFAULT_POSTS_DIR.is_dir():
        raise PublishError(f"Posts directory not found: {DEFAULT_POSTS_DIR}")

    ready = []
    for md in DEFAULT_POSTS_DIR.glob("*.md"):
        try:
            fm, _ = split_frontmatter(md.read_text(encoding="utf-8"))
        except PublishError:
            continue
        if str(fm.get("status", "")).lower() == "ready":
            ready.append(md)

    if not ready:
        raise PublishError(
            f"No posts with `status: ready` in {DEFAULT_POSTS_DIR}. "
            "Mark a file ready first (and set `approved: true`)."
        )
    # Most recently modified wins when several are ready.
    return max(ready, key=lambda p: p.stat().st_mtime)


def check_gates(post: Post) -> None:
    status = str(post.frontmatter.get("status", "")).lower()
    if status != "ready":
        raise PublishError(f"File status is '{status or 'unset'}', expected 'ready'.")
    if post.frontmatter.get("approved") is not True:
        raise PublishError("File does not have approved: true.")


# --------------------------------------------------------------------------- #
# Per-platform publishing. Each returns the public URL of the new post.
# --------------------------------------------------------------------------- #


def post_bluesky(text: str, hosted: "images.HostedImage | None") -> str:
    from atproto import Client

    handle = require_env("BLUESKY_HANDLE")
    client = Client()
    client.login(handle, require_env("BLUESKY_APP_PASSWORD"))

    if hosted:
        embed = images.build_bluesky_embed(client, hosted.local_path, hosted.alt)
        resp = client.send_post(text=text, embed=embed)
    else:
        resp = client.send_post(text=text)

    rkey = resp.uri.rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def post_mastodon(text: str, hosted: "images.HostedImage | None") -> str:
    from mastodon import Mastodon

    m = Mastodon(
        access_token=require_env("MASTODON_ACCESS_TOKEN"),
        api_base_url=require_env("MASTODON_INSTANCE_URL"),
    )
    media_ids = None
    if hosted:
        media_ids = [images.upload_mastodon_media(m, hosted.local_path, hosted.alt)]
    status = m.status_post(text, media_ids=media_ids)
    return status["url"]


def post_threads(text: str, hosted: "images.HostedImage | None") -> str:
    import requests

    user_id = require_env("THREADS_USER_ID")
    token = threads_access_token()  # rolls the 60-day token forward near expiry
    base = "https://graph.threads.net/v1.0"

    if hosted:
        post_id = images.post_threads_image(
            user_id, token, text, hosted.public_url, hosted.alt, base_url=base
        )
    else:
        create = requests.post(
            f"{base}/{user_id}/threads",
            params={"media_type": "TEXT", "text": text, "access_token": token},
            timeout=30,
        )
        create.raise_for_status()
        creation_id = create.json()["id"]
        publish = requests.post(
            f"{base}/{user_id}/threads_publish",
            params={"creation_id": creation_id, "access_token": token},
            timeout=30,
        )
        publish.raise_for_status()
        post_id = publish.json()["id"]

    permalink = requests.get(
        f"{base}/{post_id}",
        params={"fields": "permalink", "access_token": token},
        timeout=30,
    ).json()
    return permalink.get("permalink", f"https://www.threads.net/t/{post_id}")


def post_linkedin(text: str, hosted: "images.HostedImage | None") -> str:
    # Posts as a LinkedIn organization page, not a personal profile.
    # Requires w_organization_social (Community Management API).
    import requests

    org_urn = require_env("LINKEDIN_ORG_URN")
    token = linkedin_access_token()  # refreshes proactively if near expiry
    try:
        return _create_linkedin_post(text, hosted, token, org_urn)
    except requests.HTTPError as exc:
        # Reactive fallback: if the token was rejected (401) and a refresh token
        # is configured, mint a fresh one and retry the post exactly once. A 401
        # means nothing was created, so the retry cannot double-post.
        resp = getattr(exc, "response", None)
        if resp is not None and resp.status_code == 401 and _linkedin_refresh_configured():
            print("  LinkedIn returned 401; refreshing the access token and retrying once...")
            token = refresh_linkedin_token()
            return _create_linkedin_post(text, hosted, token, org_urn)
        raise


def _create_linkedin_post(
    text: str, hosted: "images.HostedImage | None", token: str, org_urn: str
) -> str:
    import requests

    headers = {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": images.LINKEDIN_API_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }
    body = {
        "author": org_urn,
        "commentary": text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if hosted:
        image_urn = images.register_linkedin_image(token, org_urn, hosted.local_path)
        body["content"] = {"media": {"id": image_urn, "altText": hosted.alt or ""}}

    resp = requests.post(
        "https://api.linkedin.com/rest/posts", headers=headers, json=body, timeout=30
    )
    resp.raise_for_status()
    urn = resp.headers.get("x-restli-id") or resp.headers.get("x-linkedin-id", "")
    return f"https://www.linkedin.com/feed/update/{urn}/" if urn else "(posted; URN not returned)"


def post_x(text: str, hosted: "images.HostedImage | None") -> str:
    # Pay-per-use posting via OAuth 1.0a user context. The four key/secret values
    # are static (minted in the X developer portal) and do not expire, so X needs
    # no refresh machinery. Posting here spends real credits: ~$0.015 for this
    # text/image post, ~$0.20 if `text` contains a link.
    import tweepy

    api_key = require_env("X_API_KEY")
    api_secret = require_env("X_API_SECRET")
    access_token = require_env("X_ACCESS_TOKEN")
    access_secret = require_env("X_ACCESS_TOKEN_SECRET")

    client = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
    )

    media_ids = None
    if hosted:
        media_ids = [
            images.upload_x_media(
                api_key, api_secret, access_token, access_secret,
                hosted.local_path, hosted.alt,
            )
        ]

    resp = client.create_tweet(text=text, media_ids=media_ids)
    tweet_id = resp.data["id"]
    username = os.environ.get("X_USERNAME", "").lstrip("@")
    if username:
        return f"https://x.com/{username}/status/{tweet_id}"
    return f"https://x.com/i/web/status/{tweet_id}"


POSTERS = {
    "bluesky": post_bluesky,
    "mastodon": post_mastodon,
    "threads": post_threads,
    "linkedin": post_linkedin,
    "x": post_x,
}


def require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise PublishError(f"{name} not set. Check {ENV_PATH}.")
    return val


# --------------------------------------------------------------------------- #
# LinkedIn token refresh. Access tokens last 60 days; refresh tokens last a
# year and renew without a browser, so a configured refresh token keeps posting
# hands-off until the refresh token itself expires.
# --------------------------------------------------------------------------- #


def update_env_values(updates: dict[str, str]) -> None:
    """Set or replace KEY=VALUE lines in the resolved .env, preserving the rest.

    Used to persist refreshed OAuth tokens back to disk. Keeps file mode at 600.
    """
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        m = re.match(r"^([A-Za-z0-9_]+)=", line)
        if m and m.group(1) in remaining:
            key = m.group(1)
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    for key, val in remaining.items():
        out.append(f"{key}={val}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        ENV_PATH.chmod(0o600)
    except OSError:
        pass


def _linkedin_refresh_configured() -> bool:
    """True only if we have everything needed to mint a new access token."""
    return all(
        os.environ.get(k)
        for k in ("LINKEDIN_REFRESH_TOKEN", "LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET")
    )


def refresh_linkedin_token() -> str:
    """Exchange the stored refresh token for a fresh 60-day access token.

    LinkedIn rotates the refresh token on use, so we persist whatever it returns
    plus an expiry stamp, and mirror all three into the live environment.
    """
    import time

    import requests

    refresh = require_env("LINKEDIN_REFRESH_TOKEN")
    resp = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": require_env("LINKEDIN_CLIENT_ID"),
            "client_secret": require_env("LINKEDIN_CLIENT_SECRET"),
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise PublishError(
            f"LinkedIn token refresh failed (HTTP {resp.status_code}): {resp.text[:200]}. "
            "The refresh token is likely expired or revoked; re-authorize in the browser "
            "(see the LinkedIn setup guide) to mint a new token pair."
        )
    data = resp.json()
    access = data["access_token"]
    new_refresh = data.get("refresh_token", refresh)
    expires_in = int(data.get("expires_in", 0))
    expires_at = int(time.time()) + expires_in
    update_env_values(
        {
            "LINKEDIN_ACCESS_TOKEN": access,
            "LINKEDIN_REFRESH_TOKEN": new_refresh,
            "LINKEDIN_TOKEN_EXPIRES_AT": str(expires_at),
        }
    )
    os.environ["LINKEDIN_ACCESS_TOKEN"] = access
    os.environ["LINKEDIN_REFRESH_TOKEN"] = new_refresh
    os.environ["LINKEDIN_TOKEN_EXPIRES_AT"] = str(expires_at)
    print(f"  LinkedIn access token refreshed (valid ~{round(expires_in / 86400)}d).")
    return access


def linkedin_access_token() -> str:
    """Return a usable LinkedIn access token, refreshing first if it is missing
    or within two days of expiry and a refresh token is configured.

    Falls back to the stored token untouched when no refresh token is set, so
    setups that renew manually keep working exactly as before.
    """
    token = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
    if _linkedin_refresh_configured():
        import time

        exp = os.environ.get("LINKEDIN_TOKEN_EXPIRES_AT", "")
        near_expiry = (not token) or (exp.isdigit() and time.time() >= int(exp) - 172_800)
        if near_expiry:
            return refresh_linkedin_token()
    if not token:
        raise PublishError(f"LINKEDIN_ACCESS_TOKEN not set. Check {ENV_PATH}.")
    return token


# --------------------------------------------------------------------------- #
# Threads token refresh. The long-lived token lasts 60 days and rolls forward
# without a browser: Threads refreshes the access token itself (no separate
# refresh token or client secret), as long as the token is at least 24h old.
# --------------------------------------------------------------------------- #


def refresh_threads_token() -> str:
    """Roll the long-lived Threads token's 60-day expiry, and persist it to .env."""
    import time

    import requests

    current = require_env("THREADS_ACCESS_TOKEN")
    resp = requests.get(
        "https://graph.threads.net/refresh_access_token",
        params={"grant_type": "th_refresh_token", "access_token": current},
        timeout=30,
    )
    if resp.status_code != 200:
        raise PublishError(
            f"Threads token refresh failed (HTTP {resp.status_code}): {resp.text[:200]}. "
            "A token under 24h old cannot be refreshed; if it has expired, re-authorize in the browser."
        )
    data = resp.json()
    token = data["access_token"]
    expires_in = int(data.get("expires_in", 0))
    expires_at = int(time.time()) + expires_in
    update_env_values(
        {"THREADS_ACCESS_TOKEN": token, "THREADS_TOKEN_EXPIRES_AT": str(expires_at)}
    )
    os.environ["THREADS_ACCESS_TOKEN"] = token
    os.environ["THREADS_TOKEN_EXPIRES_AT"] = str(expires_at)
    print(f"  Threads access token refreshed (valid ~{round(expires_in / 86400)}d).")
    return token


def threads_access_token() -> str:
    """Return a usable Threads access token, rolling it forward first if it is
    within two days of a recorded expiry. Falls back to the stored token when no
    expiry stamp exists yet, so nothing breaks before the first refresh.
    """
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if token:
        import time

        exp = os.environ.get("THREADS_TOKEN_EXPIRES_AT", "")
        if exp.isdigit() and time.time() >= int(exp) - 172_800:
            return refresh_threads_token()
    if not token:
        raise PublishError(f"THREADS_ACCESS_TOKEN not set. Check {ENV_PATH}.")
    return token


# --------------------------------------------------------------------------- #
# Writing results back into the file
# --------------------------------------------------------------------------- #


def mark_posted(path: Path, posted: dict[str, str], when: datetime) -> None:
    """
    Edit the file in place: flip frontmatter `status` to posted, stamp
    published-at, and fill the Publish Tracking row for each posted platform.

    Targeted string edits only, so the post body and frontmatter comments survive.
    """
    text = path.read_text(encoding="utf-8")
    fm_end = text.find("\n---", 3)
    fm, rest = text[: fm_end + 1], text[fm_end + 1 :]

    fm = re.sub(r"(?m)^status:.*$", "status: posted", fm, count=1)
    if re.search(r"(?m)^published-at:", fm):
        fm = re.sub(r"(?m)^published-at:.*$", f"published-at: {when.isoformat(timespec='seconds')}", fm)
    else:
        fm = re.sub(
            r"(?m)^status: posted$",
            f"status: posted\npublished-at: {when.isoformat(timespec='seconds')}",
            fm,
            count=1,
        )

    date_str = when.strftime("%Y-%m-%d")
    for platform, url in posted.items():
        display = DISPLAY.get(platform, platform.capitalize())
        # Match the whole tracking row for this platform (case-insensitive, since
        # the table uses "LinkedIn") and rebuild it. Cleaner than capturing cells.
        pattern = re.compile(rf"(?im)^\|\s*{re.escape(display)}\s*\|[^\n]*$")
        rest, n = pattern.subn(f"| {display} | ☑ | {date_str} | {url} | |", rest)
        if n == 0:
            # Table missing or differently shaped; non-fatal.
            print(f"  note: could not find a tracking row for {display}")

    path.write_text(fm + rest, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def resolve_image(post: Post):
    if not post.frontmatter.get("image"):
        return None
    try:
        cfg = images.ImageHostConfig.from_env()
        return images.resolve_prepare_and_host(post.path, post.frontmatter, cfg)
    except (RuntimeError, FileNotFoundError, ValueError, images.ImageTooLargeError) as exc:
        raise PublishError(f"Image prep failed: {exc}") from exc


def choose_platforms(args: argparse.Namespace, post: Post) -> list[str]:
    if args.platforms:
        requested = [p.strip().lower() for p in args.platforms.split(",") if p.strip()]
    else:
        requested = [str(p).lower() for p in post.frontmatter.get("platforms", [])]

    chosen = []
    for p in requested:
        if p not in SUPPORTED:
            print(f"  skipping unsupported platform: {p}")
            continue
        chosen.append(p)
    if not chosen:
        raise PublishError(f"No supported platforms selected (supported: {', '.join(SUPPORTED)}).")
    return chosen


def confirm(platforms: list[str]) -> bool:
    if not sys.stdin.isatty():
        return True  # non-interactive (cron, pipe): the gates already passed
    answer = input(f"Publish to {', '.join(platforms)} for real? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a social post across platforms.")
    parser.add_argument("--file", help="Path to a specific post file.")
    parser.add_argument("--auto", action="store_true", help="Auto-pick the most recent ready post.")
    parser.add_argument("--platforms", help="Comma list, e.g. bluesky,mastodon. Defaults to the file's platforms.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would post; change nothing.")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip the interactive confirmation.")
    args = parser.parse_args()

    load_dotenv(ENV_PATH)

    path = select_file(args)
    post = load_post(path)
    check_gates(post)
    platforms = choose_platforms(args, post)
    hosted = resolve_image(post)

    print(f"File: {path}")
    if hosted:
        print(f"Image: {hosted.local_path.name} -> {hosted.public_url}")

    # Build the per-platform text up front so a missing section fails before we post anything.
    texts: dict[str, str] = {}
    for p in platforms:
        text = extract_post_text(post.body, p)
        if not text:
            raise PublishError(f"No '## {p.capitalize()}' code block found in {path.name}.")
        if p == "threads":
            text = strip_threads_hashtags(text)  # avoid Threads' header topic-tag
        over = len(text) - CHAR_LIMITS.get(p, 10_000)
        flag = f"  (OVER limit by {over})" if over > 0 else ""
        if args.dry_run:
            print(f"\n--- {p} ({len(text)} chars){flag} ---\n{text}")
        elif over > 0:
            print(f"  warning: {p} text is over the {CHAR_LIMITS[p]}-char limit by {over}")
        texts[p] = text

    if args.dry_run:
        print("\nDry run only. Nothing was posted.")
        return 0

    if not args.yes and not confirm(platforms):
        print("Aborted.")
        return 1

    posted: dict[str, str] = {}
    failed: dict[str, str] = {}
    for p in platforms:
        print(f"Posting to {p}...")
        try:
            url = POSTERS[p](texts[p], hosted)
            print(f"OK: {url}")
            posted[p] = url
        except Exception as exc:  # one platform failing should not lose the others
            print(f"FAILED ({p}): {exc}")
            failed[p] = str(exc)

    if posted:
        mark_posted(path, posted, datetime.now())
        print(f"File marked as posted: {path}")
    if failed:
        print(f"\n{len(failed)} platform(s) failed: {', '.join(failed)}. File left as-is for retry.")
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublishError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)

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
publish.py - publish a social post to Bluesky, Mastodon, Threads, LinkedIn, X,
Instagram, Facebook, and YouTube.

Reads a Markdown post file (frontmatter plus one fenced code block per platform),
optionally attaches one image OR one video (see images.py), publishes to the
selected platforms, then marks the file posted and fills in its Publish Tracking
table.

YouTube is the one video-only platform: it publishes the post's `video:` as a
Short (a vertical/square clip <=180s is auto-classified as one), using
`youtube-title:` from the frontmatter as the title and the `## YouTube` block as
the description.

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
SUPPORTED = ("bluesky", "mastodon", "threads", "linkedin", "x", "instagram", "facebook", "youtube")

# Instagram requires media (no text-only posts) and posts video as a Reel.
# YouTube requires a video (it publishes the post's video as a Short).

# Canonical display names, matching the `## Heading` and Publish Tracking table.
DISPLAY = {
    "bluesky": "Bluesky",
    "mastodon": "Mastodon",
    "threads": "Threads",
    "linkedin": "LinkedIn",
    "x": "X",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "youtube": "YouTube",
}

# Soft character ceilings. We warn rather than block, since the generator
# already targets these and an occasional overage is the human's call.
# Instagram captions allow ~2,200 characters. X's 25,000 reflects an active
# Premium subscription (the free tier caps at 280); X enforces the real limit
# server-side, so this only governs the dry-run warning.
# YouTube's 5,000 governs the `## YouTube` block, which is the video DESCRIPTION;
# the title is a separate `youtube-title:` field capped at 100 chars (checked in main).
CHAR_LIMITS = {
    "bluesky": 300, "mastodon": 500, "threads": 500, "linkedin": 3000,
    "x": 25_000, "instagram": 2200, "facebook": 63206, "youtube": 5000,
}

# Minimum credentials each platform needs before it can be offered or attempted.
# --check uses this to build the platform menu; a platform with any of these unset
# in the resolved .env is treated as not configured and is not offered as a choice.
REQUIRED_ENV = {
    "bluesky": ("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"),
    "mastodon": ("MASTODON_INSTANCE_URL", "MASTODON_ACCESS_TOKEN"),
    "threads": ("THREADS_USER_ID", "THREADS_ACCESS_TOKEN"),
    "linkedin": ("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_ORG_URN"),
    "x": ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"),
    "instagram": ("INSTAGRAM_USER_ID", "INSTAGRAM_ACCESS_TOKEN"),
    "facebook": ("FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_ACCESS_TOKEN"),
    # YouTube's durable credential is the refresh token (access tokens last ~1h
    # and are minted from it per run), so that plus the OAuth client is required.
    "youtube": ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"),
}


# X has two transports: the paid OAuth API (default), or a free browser-driven
# path (Playwright) that reuses a logged-in session. Set X_TRANSPORT=browser to
# post X for free. See x_playwright.py.
def x_transport() -> str:
    return os.environ.get("X_TRANSPORT", "api").strip().lower()


def _x_browser_state_path() -> Path:
    """Where x_playwright.py saves the logged-in browser session."""
    override = os.environ.get("X_PLAYWRIGHT_STATE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "publish-social" / "x-state.json"


def platform_configured(platform: str) -> bool:
    """True only if everything the platform needs to post is present.

    X in browser mode is "configured" when a saved Playwright session exists,
    not when the API keys are set - the browser path uses no API credentials.
    """
    if platform == "x" and x_transport() == "browser":
        return _x_browser_state_path().is_file()
    return all(os.environ.get(k) for k in REQUIRED_ENV.get(platform, ()))


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


# A link in an X post triggers the ~$0.20 surcharge (vs ~$0.015 without) and is
# easy to include by accident, so we detect one to gate it behind a double
# confirm. We over-detect on purpose: a false positive costs only an extra
# prompt, while a miss would let a link publish unconfirmed. This matches
# explicit URLs, www.* hosts, and bare domains on common web TLDs (so dev tokens
# like "publish.py" or "README.md" do not trip it). #hashtags and @mentions are
# not links to X and are not matched. The TLD list is intentionally web-leaning,
# not exhaustive.
_X_LINK_RE = re.compile(
    r"(?ix)\b("
    r"https?://\S+"                               # explicit scheme
    r"|www\.\S+"                                  # www host
    r"|[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:"  # bare domain + subdomains
    r"com|org|net|io|dev|ai|co|app|me|xyz|blog|news|gg|so|sh|to|info|biz|tv|us|"
    r"be|ly|fm|link|page|site|online|store|shop|live|media|club|tech|design"
    r")(?:/\S*)?"                                 # optional path
    r")"
)


def find_link(text: str) -> str | None:
    """Return the first link-like substring in `text`, or None if it has none.

    Used only to decide whether the X double-confirm guardrail fires. X auto-links
    URLs and bare domains (but not #hashtags or @mentions) and bills more when a
    post carries a link, so this leans toward catching rather than missing.
    """
    m = _X_LINK_RE.search(text)
    return m.group(0) if m else None


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


def post_bluesky(text: str, media: "images.HostedImage | images.HostedVideo | None") -> str:
    from atproto import Client

    handle = require_env("BLUESKY_HANDLE")
    client = Client()
    client.login(handle, require_env("BLUESKY_APP_PASSWORD"))

    if media and media.kind == "video":
        # atproto uploads to video.bsky.app, polls processing, and posts in one call.
        resp = client.send_video(
            text=text, video=media.local_path.read_bytes(), video_alt=media.alt or None
        )
    elif media:
        embed = images.build_bluesky_embed(client, media.local_path, media.alt)
        resp = client.send_post(text=text, embed=embed)
    else:
        resp = client.send_post(text=text)

    rkey = resp.uri.rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def post_mastodon(text: str, media: "images.HostedImage | images.HostedVideo | None") -> str:
    from mastodon import Mastodon

    m = Mastodon(
        access_token=require_env("MASTODON_ACCESS_TOKEN"),
        api_base_url=require_env("MASTODON_INSTANCE_URL"),
    )
    media_ids = None
    if media and media.kind == "video":
        media_ids = [images.upload_mastodon_video(m, media.local_path, media.alt)]
    elif media:
        media_ids = [images.upload_mastodon_media(m, media.local_path, media.alt)]
    status = m.status_post(text, media_ids=media_ids)
    return status["url"]


def post_threads(text: str, media: "images.HostedImage | images.HostedVideo | None") -> str:
    import requests

    user_id = require_env("THREADS_USER_ID")
    token = threads_access_token()  # rolls the 60-day token forward near expiry
    base = "https://graph.threads.net/v1.0"

    if media and media.kind == "video":
        post_id = images.post_threads_video(
            user_id, token, text, media.public_url, base_url=base
        )
    elif media:
        post_id = images.post_threads_image(
            user_id, token, text, media.public_url, media.alt, base_url=base
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


def post_linkedin(text: str, media: "images.HostedImage | images.HostedVideo | None") -> str:
    # Posts as a LinkedIn organization page, not a personal profile.
    # Requires w_organization_social (Community Management API).
    import requests

    org_urn = require_env("LINKEDIN_ORG_URN")
    token = linkedin_access_token()  # refreshes proactively if near expiry
    try:
        return _create_linkedin_post(text, media, token, org_urn)
    except requests.HTTPError as exc:
        # Reactive fallback: if the token was rejected (401) and a refresh token
        # is configured, mint a fresh one and retry the post exactly once. A 401
        # means nothing was created, so the retry cannot double-post.
        resp = getattr(exc, "response", None)
        if resp is not None and resp.status_code == 401 and _linkedin_refresh_configured():
            print("  LinkedIn returned 401; refreshing the access token and retrying once...")
            token = refresh_linkedin_token()
            return _create_linkedin_post(text, media, token, org_urn)
        raise


def _create_linkedin_post(
    text: str, media: "images.HostedImage | images.HostedVideo | None", token: str, org_urn: str
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
    if media and media.kind == "video":
        video_urn = images.register_linkedin_video(token, org_urn, media.local_path)
        body["content"] = {"media": {"id": video_urn, "title": media.alt or ""}}
    elif media:
        image_urn = images.register_linkedin_image(token, org_urn, media.local_path)
        body["content"] = {"media": {"id": image_urn, "altText": media.alt or ""}}

    resp = requests.post(
        "https://api.linkedin.com/rest/posts", headers=headers, json=body, timeout=30
    )
    resp.raise_for_status()
    urn = resp.headers.get("x-restli-id") or resp.headers.get("x-linkedin-id", "")
    return f"https://www.linkedin.com/feed/update/{urn}/" if urn else "(posted; URN not returned)"


def post_x(text: str, media: "images.HostedImage | images.HostedVideo | None") -> str:
    # Route to the free browser path or the paid API path based on X_TRANSPORT.
    if x_transport() == "browser":
        return post_x_browser(text, media)
    return post_x_api(text, media)


def post_x_browser(text: str, media: "images.HostedImage | images.HostedVideo | None") -> str:
    """Post to X for free by driving a logged-in browser via x_playwright.py.

    Runs the sibling script as its own `uv` process so Playwright (a heavy dep
    with its own browser binaries) stays isolated from publish.py - it is only
    installed when this path is actually used. X takes a direct file upload, so
    photos and videos attach from media.local_path, same as the API path. The
    script prints the new post's URL on its last stdout line (--print-url).
    """
    import subprocess

    script = Path(__file__).resolve().parent / "x_playwright.py"
    if not script.is_file():
        raise PublishError(f"x_playwright.py not found next to publish.py ({script}).")

    cmd = ["uv", "run", "--quiet", str(script), "post", "--text", text, "--print-url"]
    if media is not None:
        cmd += ["--media", str(media.local_path)]

    env = dict(os.environ)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        msg = detail[-1] if detail else f"exit {proc.returncode}"
        raise PublishError(f"X (browser) post failed: {msg}")

    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        raise PublishError("X (browser) post produced no URL on stdout.")
    return lines[-1].strip()


def post_x_api(text: str, media: "images.HostedImage | images.HostedVideo | None") -> str:
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
    if media and media.kind == "video":
        media_ids = [
            images.upload_x_video(
                api_key, api_secret, access_token, access_secret,
                media.local_path, media.alt,
            )
        ]
    elif media:
        media_ids = [
            images.upload_x_media(
                api_key, api_secret, access_token, access_secret,
                media.local_path, media.alt,
            )
        ]

    resp = client.create_tweet(text=text, media_ids=media_ids)
    tweet_id = resp.data["id"]
    username = os.environ.get("X_USERNAME", "").lstrip("@")
    if username:
        return f"https://x.com/{username}/status/{tweet_id}"
    return f"https://x.com/i/web/status/{tweet_id}"


def post_instagram(text: str, media: "images.HostedImage | images.HostedVideo | None") -> str:
    # Publishes to a Business/Creator account via the Graph API (Instagram Login).
    # Instagram has no text-only posts, so media is required; video posts as a Reel.
    import requests

    if media is None:
        raise PublishError("Instagram requires an image or video; this post has neither.")

    user_id = require_env("INSTAGRAM_USER_ID")
    token = instagram_access_token()  # rolls the 60-day token forward near expiry
    base = images.INSTAGRAM_API_BASE

    if media.kind == "video":
        media_id = images.post_instagram_video(user_id, token, text, media.public_url, base_url=base)
    else:
        media_id = images.post_instagram_image(user_id, token, text, media.public_url, base_url=base)

    permalink = requests.get(
        f"{base}/{media_id}",
        params={"fields": "permalink", "access_token": token},
        timeout=30,
    ).json().get("permalink")
    return permalink or f"(posted; Instagram media {media_id})"


def post_facebook(text: str, media: "images.HostedImage | images.HostedVideo | None") -> str:
    # Posts to a Facebook Page via the Graph API. A Page access token minted from a
    # long-lived user token is non-expiring, so Facebook needs no refresh machinery
    # (like X). Image/video are fetched by Facebook from the public media URL;
    # text-only posts need no media host.
    import requests

    page_id = require_env("FACEBOOK_PAGE_ID")
    token = require_env("FACEBOOK_PAGE_ACCESS_TOKEN")
    base = images.FACEBOOK_API_BASE

    if media and media.kind == "video":
        post_id = images.post_facebook_video(page_id, token, text, media.public_url, base_url=base)
    elif media:
        post_id = images.post_facebook_photo(page_id, token, text, media.public_url, base_url=base)
    else:
        resp = requests.post(
            f"{base}/{page_id}/feed",
            params={"message": text, "access_token": token},
            timeout=30,
        )
        resp.raise_for_status()
        post_id = resp.json()["id"]
    return f"https://www.facebook.com/{post_id}"


def post_youtube(
    text: str,
    media: "images.HostedImage | images.HostedVideo | None",
    *,
    title: str,
    privacy: str = "public",
) -> str:
    # Publishes the post's video as a YouTube Short via the Data API v3. YouTube
    # ingests the file directly (like Bluesky/X), so no public media host is used.
    # `text` is the video description; `title` and `privacy` come from frontmatter
    # (youtube-title / youtube-privacy). A vertical or square clip <=180s is
    # auto-classified as a Short; a landscape one posts as a regular video.
    if not (media and media.kind == "video"):
        raise PublishError("YouTube requires a video; this post has none.")

    token = youtube_access_token()  # mints a fresh ~1h token from the refresh token
    video_id = images.upload_youtube_video(
        token, media.local_path, title=title, description=text, privacy=privacy
    )
    return f"https://www.youtube.com/shorts/{video_id}"


# YouTube is dispatched separately (see main): it needs the title and privacy from
# frontmatter, which the uniform (text, media) poster signature does not carry.
POSTERS = {
    "bluesky": post_bluesky,
    "mastodon": post_mastodon,
    "threads": post_threads,
    "linkedin": post_linkedin,
    "x": post_x,
    "instagram": post_instagram,
    "facebook": post_facebook,
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
    """Return a usable Threads access token, keeping its 60-day window rolled
    forward automatically.

    On first use there is no recorded expiry, so we refresh once to establish the
    `THREADS_TOKEN_EXPIRES_AT` stamp (and roll the window forward). After that we
    refresh only within two days of expiry. A token under 24h old cannot be
    refreshed yet; that case falls back to the stored token and retries next run.
    """
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if not token:
        raise PublishError(f"THREADS_ACCESS_TOKEN not set. Check {ENV_PATH}.")
    import time

    exp = os.environ.get("THREADS_TOKEN_EXPIRES_AT", "")
    if not exp.isdigit():
        try:
            return refresh_threads_token()
        except PublishError as exc:
            print(f"  Threads: deferring first token refresh ({exc}); using token as-is.")
            return token
    if time.time() >= int(exp) - 172_800:
        return refresh_threads_token()
    return token


# --------------------------------------------------------------------------- #
# Instagram token refresh. The long-lived Instagram token lasts 60 days and rolls
# forward without a browser the same way Threads does: exchange the current token
# for a fresh 60-day one (the token must be at least 24h old to refresh).
# --------------------------------------------------------------------------- #


def refresh_instagram_token() -> str:
    """Roll the long-lived Instagram token's 60-day expiry, and persist it to .env."""
    import time

    import requests

    current = require_env("INSTAGRAM_ACCESS_TOKEN")
    resp = requests.get(
        "https://graph.instagram.com/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": current},
        timeout=30,
    )
    if resp.status_code != 200:
        raise PublishError(
            f"Instagram token refresh failed (HTTP {resp.status_code}): {resp.text[:200]}. "
            "A token under 24h old cannot be refreshed; if it has expired, re-authorize in the browser."
        )
    data = resp.json()
    token = data["access_token"]
    expires_in = int(data.get("expires_in", 0))
    expires_at = int(time.time()) + expires_in
    update_env_values(
        {"INSTAGRAM_ACCESS_TOKEN": token, "INSTAGRAM_TOKEN_EXPIRES_AT": str(expires_at)}
    )
    os.environ["INSTAGRAM_ACCESS_TOKEN"] = token
    os.environ["INSTAGRAM_TOKEN_EXPIRES_AT"] = str(expires_at)
    print(f"  Instagram access token refreshed (valid ~{round(expires_in / 86400)}d).")
    return token


def instagram_access_token() -> str:
    """Return a usable Instagram access token, keeping its 60-day window rolled
    forward automatically.

    On first use there is no recorded expiry, so we refresh once to establish the
    `INSTAGRAM_TOKEN_EXPIRES_AT` stamp (and roll the window forward). After that we
    refresh only within two days of expiry. A token under 24h old cannot be
    refreshed yet; that case falls back to the stored token and retries next run.
    """
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    if not token:
        raise PublishError(f"INSTAGRAM_ACCESS_TOKEN not set. Check {ENV_PATH}.")
    import time

    exp = os.environ.get("INSTAGRAM_TOKEN_EXPIRES_AT", "")
    if not exp.isdigit():
        try:
            return refresh_instagram_token()
        except PublishError as exc:
            print(f"  Instagram: deferring first token refresh ({exc}); using token as-is.")
            return token
    if time.time() >= int(exp) - 172_800:
        return refresh_instagram_token()
    return token


# --------------------------------------------------------------------------- #
# YouTube (Google) token. Unlike the 60-day Meta/LinkedIn tokens, a Google access
# token lasts only ~1 hour, but the refresh token is durable and does NOT rotate
# on use. So the refresh token is the stored credential, and we mint a short-lived
# access token from it, caching it in .env until it nears expiry.
# --------------------------------------------------------------------------- #


def refresh_youtube_token() -> str:
    """Exchange the stored Google refresh token for a fresh ~1h access token."""
    import time

    import requests

    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": require_env("YOUTUBE_REFRESH_TOKEN"),
            "client_id": require_env("YOUTUBE_CLIENT_ID"),
            "client_secret": require_env("YOUTUBE_CLIENT_SECRET"),
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise PublishError(
            f"YouTube token refresh failed (HTTP {resp.status_code}): {resp.text[:200]}. "
            "The refresh token is likely expired or revoked (Google expires unused tokens, "
            "and tokens for an app still in 'testing' mode last only 7 days). Re-authorize in "
            "the browser to mint a new refresh token (see the YouTube setup guide)."
        )
    data = resp.json()
    access = data["access_token"]
    expires_in = int(data.get("expires_in", 0))
    expires_at = int(time.time()) + expires_in
    update_env_values(
        {"YOUTUBE_ACCESS_TOKEN": access, "YOUTUBE_TOKEN_EXPIRES_AT": str(expires_at)}
    )
    os.environ["YOUTUBE_ACCESS_TOKEN"] = access
    os.environ["YOUTUBE_TOKEN_EXPIRES_AT"] = str(expires_at)
    print(f"  YouTube access token refreshed (valid ~{round(expires_in / 60)}m).")
    return access


def youtube_access_token() -> str:
    """Return a usable Google access token, minting a fresh one from the refresh
    token when the cached one is missing or within two minutes of expiry.

    The refresh token (not the access token) is the durable credential, so a
    refresh can always run as long as YOUTUBE_REFRESH_TOKEN is set.
    """
    import time

    token = os.environ.get("YOUTUBE_ACCESS_TOKEN", "")
    exp = os.environ.get("YOUTUBE_TOKEN_EXPIRES_AT", "")
    if token and exp.isdigit() and time.time() < int(exp) - 120:
        return token
    return refresh_youtube_token()


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


def resolve_media(post: Post, platforms: list[str]):
    has_image = bool(post.frontmatter.get("image"))
    has_video = bool(post.frontmatter.get("video"))
    if has_image and has_video:
        raise PublishError(
            "Post sets both `image:` and `video:`. Use one or the other - Bluesky "
            "cannot attach both, so the model is one image OR one video per post."
        )
    if not has_image and not has_video:
        return None
    # Only Threads/Instagram/Facebook fetch media from a public URL; every other
    # platform uploads the local file directly. So host (rsync) the clip only when
    # one of those three is a target. A direct-upload-only post (e.g. YouTube,
    # Bluesky, X) then needs no media host at all - cfg stays None and the prepared
    # local file is used as-is.
    needs_host = bool({"threads", "instagram", "facebook"} & set(platforms))
    try:
        cfg = images.ImageHostConfig.from_env() if needs_host else None
        if has_video:
            return images.resolve_prepare_and_host_video(post.path, post.frontmatter, cfg)
        return images.resolve_prepare_and_host(post.path, post.frontmatter, cfg)
    except (
        RuntimeError, FileNotFoundError, ValueError,
        images.ImageTooLargeError, images.VideoTooLargeError, images.VideoTooLongError,
    ) as exc:
        raise PublishError(f"Media prep failed: {exc}") from exc


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


def resolve_youtube_settings(post: Post, media) -> tuple[str, str]:
    """Validate and return (title, privacy) for a YouTube upload.

    YouTube is the one video-only platform: it publishes the post's `video:` as a
    Short. The title comes from `youtube-title:` (required, <=100 chars, no angle
    brackets) and privacy from `youtube-privacy:` (public/unlisted/private,
    default public). Raises PublishError on any problem so it surfaces before
    anything is posted, including in a dry run.
    """
    if not (media and media.kind == "video"):
        raise PublishError(
            "YouTube publishes a video as a Short, but this post has no `video:`. "
            "Add one, or drop youtube from the platforms."
        )
    title = str(post.frontmatter.get("youtube-title", "")).strip()
    if not title:
        raise PublishError("YouTube needs a `youtube-title:` in the frontmatter (the video title).")
    if len(title) > 100:
        raise PublishError(f"youtube-title is {len(title)} chars; YouTube caps titles at 100.")
    if "<" in title or ">" in title:
        raise PublishError("youtube-title cannot contain '<' or '>' (YouTube rejects them).")
    privacy = str(post.frontmatter.get("youtube-privacy", "public")).strip().lower()
    if privacy not in {"public", "unlisted", "private"}:
        raise PublishError(
            f"youtube-privacy must be public, unlisted, or private (got {privacy!r})."
        )
    return title, privacy


def confirm(platforms: list[str]) -> bool:
    if not sys.stdin.isatty():
        return True  # non-interactive (cron, pipe): the gates already passed
    answer = input(f"Publish to {', '.join(platforms)} for real? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def confirm_x_link(link: str) -> bool:
    """Two-step confirmation before publishing a link to X.

    A link on X bills ~$0.20 (vs ~$0.015 without) and is easy to include by
    accident, so we require an explicit yes, then a second "are you sure". This
    gate is deliberately NOT bypassed by --yes: a link on X must always be
    verified by a human. A non-interactive run (cron/pipe, or no TTY) cannot
    confirm, so it returns False and the caller skips X. Returns True only when
    both answers are yes.
    """
    print(f"\n  X GUARDRAIL: the X post contains a link -> {link}")
    print("  Publishing a link on X bills ~$0.20 (a link-free post is ~$0.015).")
    if not sys.stdin.isatty():
        print("  Non-interactive run: cannot double-confirm a link, so X will be skipped.")
        return False
    first = input("  Confirm 1 of 2 - publish this link to X? [y/N] ").strip().lower()
    if first not in {"y", "yes"}:
        return False
    second = input("  Confirm 2 of 2 - are you sure? this posts the link and bills ~$0.20 [y/N] ").strip().lower()
    return second in {"y", "yes"}


def run_check(args: argparse.Namespace) -> int:
    """Report which platforms are offerable and post nothing.

    A platform is offerable when its credentials are present in the resolved .env
    and, when a file is selected, the post has a `## <Platform>` text block. Prints
    a per-platform table and a final `OFFER:` comma list for building the platform
    menu. Never prints credential values.
    """
    blocks = None
    try:
        path = select_file(args)
        post = load_post(path)
        blocks = {p for p in SUPPORTED if extract_post_text(post.body, p)}
        print(f"File: {path}")
    except PublishError as exc:
        print(f"(no file selected: {exc}; reporting credentials only)")

    print(f"  {'platform':10} {'creds':5} {'block':5} offerable")
    offer: list[str] = []
    for p in SUPPORTED:
        creds = platform_configured(p)
        has_block = None if blocks is None else (p in blocks)
        ok = creds and (True if has_block is None else has_block)
        if ok:
            offer.append(p)
        block_str = "-" if has_block is None else ("yes" if has_block else "no")
        print(f"  {p:10} {('yes' if creds else 'no'):5} {block_str:5} {'yes' if ok else 'no'}")
    print(f"OFFER: {','.join(offer)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a social post across platforms.")
    parser.add_argument("--file", help="Path to a specific post file.")
    parser.add_argument("--auto", action="store_true", help="Auto-pick the most recent ready post.")
    parser.add_argument("--platforms", help="Comma list, e.g. bluesky,mastodon. Defaults to the file's platforms.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would post; change nothing.")
    parser.add_argument("--check", action="store_true",
                        help="Report which platforms have credentials (and text blocks, with a file) and print an OFFER list. Posts nothing.")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip the interactive confirmation.")
    parser.add_argument("--x-transport", choices=("api", "browser"), dest="x_transport",
                        help="How to post X for this run: 'api' (paid) or 'browser' (free, via Playwright). "
                             "Overrides X_TRANSPORT in .env; default is api.")
    args = parser.parse_args()

    load_dotenv(ENV_PATH)

    # A per-run --x-transport flag overrides the env var (precedence:
    # flag > X_TRANSPORT > default api). Setting it in the environment here means
    # every x_transport() read - offer logic, dry-run notes, guardrail, the
    # subprocess that posts via the browser - sees the chosen transport.
    if args.x_transport:
        os.environ["X_TRANSPORT"] = args.x_transport

    if args.check:
        return run_check(args)

    path = select_file(args)
    post = load_post(path)
    check_gates(post)
    platforms = choose_platforms(args, post)
    media = resolve_media(post, platforms)

    print(f"File: {path}")
    if media:
        dest = media.public_url or "(direct upload; no media host needed)"
        print(f"{media.kind.capitalize()}: {media.local_path.name} -> {dest}")

    # YouTube is video-only and carries a title/privacy from frontmatter; validate
    # them up front so a misconfigured post fails before anything is posted.
    yt_title = yt_privacy = None
    if "youtube" in platforms:
        yt_title, yt_privacy = resolve_youtube_settings(post, media)

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
            if p == "x" and x_transport() == "browser":
                print("  note: browser transport -> posts free via a logged-in"
                      " browser (no API cost, no link surcharge).")
            elif p == "x" and find_link(text):
                print("  note: contains a link -> X bills ~$0.20, and a real post"
                      " will require a double confirmation.")
            if p == "youtube":
                print(f"  title ({len(yt_title)} chars): {yt_title}")
                print(f"  privacy: {yt_privacy}")
                if media and media.kind == "video" and not media.is_vertical:
                    print("  note: video is landscape; YouTube Shorts want a vertical/square "
                          "clip <=180s, so this will post as a regular video, not a Short.")
        elif over > 0:
            print(f"  warning: {p} text is over the {CHAR_LIMITS[p]}-char limit by {over}")
        texts[p] = text

    if args.dry_run:
        print("\nDry run only. Nothing was posted.")
        return 0

    # X link guardrail: never publish a link to X without an explicit double
    # confirmation. A link bills ~$0.20 (vs ~$0.015) and is easy to include by
    # accident. Skipped entirely when X is not a target or its text has no link;
    # runs even under --yes, since a link on X must always be verified by a human.
    # The browser transport is free, so the cost guardrail does not apply there.
    if "x" in platforms and x_transport() != "browser":
        x_link = find_link(texts["x"])
        if x_link and not confirm_x_link(x_link):
            print("Skipping X: link not confirmed. Other platforms are unaffected.")
            platforms = [p for p in platforms if p != "x"]
            if not platforms:
                print("No platforms left to post. Aborted.")
                return 1

    if not args.yes and not confirm(platforms):
        print("Aborted.")
        return 1

    # YouTube needs the title/privacy that the uniform poster signature does not
    # carry, so bind them here from the values validated above.
    posters = dict(POSTERS)
    if "youtube" in platforms:
        posters["youtube"] = lambda text, media: post_youtube(
            text, media, title=yt_title, privacy=yt_privacy
        )

    posted: dict[str, str] = {}
    failed: dict[str, str] = {}
    for p in platforms:
        print(f"Posting to {p}...")
        try:
            url = posters[p](texts[p], media)
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

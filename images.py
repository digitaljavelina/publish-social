"""
images.py - image handling for publish-social.

One image per post. This module:
  1. Resolves the image referenced in a post's frontmatter (`image:` field).
  2. Validates and sizes it so every platform accepts it (via Pillow).
  3. Uploads it to a public image host so Threads (which needs a public HTTPS
     URL, not a file upload) can fetch it.
  4. Provides per-platform attach helpers (Bluesky, Mastodon, Threads, LinkedIn, X).

Designed to be imported by publish.py, and also runnable standalone for a smoke
test:

    IMAGE_HOST_BASE_URL=... IMAGE_HOST_SSH=... IMAGE_HOST_PATH=... \
        uv run --with Pillow images.py ./media/test.png

Heavy third-party libs (atproto, Mastodon.py, tweepy, Pillow, requests) are
imported lazily inside the function that needs them, so a text-only run does not
pull image or platform deps it will not use.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# Bluesky is the binding constraint: its blob limit is exactly 1,000,000 bytes.
# We target a little under that so the same file passes on every platform.
BLUESKY_MAX_BYTES = 1_000_000
SAFE_MAX_BYTES = 950_000

# Longest-side widths (px) prepare_image() steps through when auto-shrinking an
# over-size image. Ordered largest-first so we keep the most detail that fits.
SHRINK_LADDER = (1600, 1280, 1024, 800)

# JPEG qualities (1-100) the last-rung fallback tries when resizing alone can't
# get a lossless PNG under the byte cap. Ordered best-first.
JPEG_QUALITY_LADDER = (80, 60, 45)

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# LinkedIn's REST API is versioned via a header. Bump this as LinkedIn rotates
# supported versions (format: YYYYMM).
LINKEDIN_API_VERSION = "202506"


class ImageTooLargeError(Exception):
    """Raised when an image cannot be made small enough for the strictest platform."""


@dataclass
class ImageHostConfig:
    """Where hosted images live. Populated from .env."""

    base_url: str  # e.g. https://img.example.com
    ssh_host: str  # e.g. your-image-host (an SSH host alias)
    remote_path: str  # e.g. ~/images (the directory the host serves)

    @classmethod
    def from_env(cls) -> "ImageHostConfig":
        try:
            return cls(
                base_url=os.environ["IMAGE_HOST_BASE_URL"].rstrip("/"),
                ssh_host=os.environ["IMAGE_HOST_SSH"],
                remote_path=os.environ["IMAGE_HOST_PATH"].rstrip("/"),
            )
        except KeyError as exc:
            raise RuntimeError(
                f"Image hosting is not configured: {exc} missing from .env. "
                "Set IMAGE_HOST_BASE_URL, IMAGE_HOST_SSH, IMAGE_HOST_PATH."
            ) from exc


# --------------------------------------------------------------------------- #
# Step 1: resolve the image from frontmatter
# --------------------------------------------------------------------------- #


def resolve_image_path(post_path: Path, image_field: str) -> Path:
    """
    Turn the post's `image:` frontmatter value into an absolute path.

    The field is interpreted relative to the post file (e.g.
    `image: ./media/foo.png` next to the post).
    """
    candidate = (post_path.parent / image_field).expanduser().resolve()
    if not candidate.is_file():
        raise FileNotFoundError(
            f"Post references image '{image_field}' but no file exists at {candidate}"
        )
    return candidate


# --------------------------------------------------------------------------- #
# Step 2: validate and size the image  (decision point - see prepare_image)
# --------------------------------------------------------------------------- #


def _shrink_longest_side(path: Path, max_px: int) -> Path:
    """
    Downscale the longest side to max_px, in place, using Pillow (cross-platform).

    Resizes by pixel dimension, not by byte target, so callers that need a byte
    ceiling step the dimension down and re-check file size.
    """
    from PIL import Image  # lazy

    with Image.open(path) as im:
        fmt = im.format
        im.thumbnail((max_px, max_px))  # preserves aspect; longest side <= max_px
        im.save(path, format=fmt)
    return path


def _reencode_to_jpeg(path: Path, quality: int) -> Path:
    """
    Re-encode an image to JPEG at the given quality (1-100) with Pillow, writing a
    sibling `.jpg` file. Returns the new path.

    JPEG is lossy, so this is what finally shrinks a photographic PNG that
    resizing alone can't get under the byte cap. The original file is left in
    place; callers use the returned path.
    """
    from PIL import Image  # lazy

    out = path.with_suffix(".jpg")
    if out == path:  # source was already .jpg; avoid writing onto its input
        out = path.with_name(f"{path.stem}-c.jpg")
    with Image.open(path) as im:
        im.convert("RGB").save(out, "JPEG", quality=quality, optimize=True)
    return out


def prepare_image(path: Path) -> Path:
    """
    Validate an image and ensure it is under SAFE_MAX_BYTES so every platform
    accepts it. Returns the path to the ready-to-upload file (which may be a new
    `.jpg` sibling if a JPEG re-encode was needed).

    Over-limit policy (auto-shrink, for unattended publishing):
      1. Resize: step the longest side down SHRINK_LADDER, returning as soon as
         the file fits. Fixes screenshots, diagrams, and oversized JPEGs.
      2. JPEG fallback: if a lossless PNG still won't fit at the smallest width
         (photographic content), re-encode down JPEG_QUALITY_LADDER and return
         the first size that fits.
      3. Give up: raise ImageTooLargeError only if even the lowest JPEG quality
         is still over the cap, so a human can crop or replace the image.

    Pillow resizes in place, so the source file is mutated. The JPEG fallback
    writes a new sibling file and leaves the original alone.
    """
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(
            f"Unsupported image type {path.suffix!r}. Allowed: {sorted(ALLOWED_SUFFIXES)}"
        )

    if path.stat().st_size <= SAFE_MAX_BYTES:
        return path

    # 1. Resize down the ladder, returning as soon as it fits.
    for max_px in SHRINK_LADDER:
        _shrink_longest_side(path, max_px)
        if path.stat().st_size <= SAFE_MAX_BYTES:
            return path

    # 2. Still too big at the smallest width: lossless format can't compress
    #    further, so re-encode to JPEG, easing quality down until it fits.
    for quality in JPEG_QUALITY_LADDER:
        jpg = _reencode_to_jpeg(path, quality)
        if jpg.stat().st_size <= SAFE_MAX_BYTES:
            return jpg

    # 3. Even lowest-quality JPEG at the smallest width won't fit: hand it back.
    raise ImageTooLargeError(
        f"{path.name} is still over {SAFE_MAX_BYTES} bytes after shrinking to "
        f"{SHRINK_LADDER[-1]}px and re-encoding to JPEG q{JPEG_QUALITY_LADDER[-1]}. "
        "Crop it or replace it before publishing."
    )


# --------------------------------------------------------------------------- #
# Step 3: host the image so Threads (and anyone) can fetch it by URL
# --------------------------------------------------------------------------- #


def _content_hash(path: Path) -> str:
    """Short content hash, so re-uploading an edited image gets a fresh URL and
    two posts never collide on a shared filename."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest[:8]


def _safe_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "image"


def upload_to_image_host(
    local_path: Path, cfg: ImageHostConfig, slug: str | None = None
) -> str:
    """
    rsync the image to the host's served directory over SSH (Tailscale) and
    return its public URL.

    The remote filename is `<slug>-<hash8><suffix>`, so the same image keeps a
    stable URL while an edited image gets a new one.
    """
    base = _safe_slug(slug or local_path.stem)
    remote_name = f"{base}-{_content_hash(local_path)}{local_path.suffix.lower()}"
    target = f"{cfg.ssh_host}:{cfg.remote_path}/{remote_name}"

    subprocess.run(
        ["rsync", "-az", "--chmod=F644", str(local_path), target],
        check=True,
        capture_output=True,
    )
    return f"{cfg.base_url}/{remote_name}"


# --------------------------------------------------------------------------- #
# Step 4: per-platform attach helpers
# --------------------------------------------------------------------------- #


def build_bluesky_embed(client, image_path: Path, alt: str):
    """
    Upload the image as a blob and return an embed object to pass to
    `client.send_post(text=..., embed=<this>)`.

    Returns an AppBskyEmbedImages.Main with a single image.
    """
    from atproto import models  # lazy

    data = image_path.read_bytes()
    if len(data) > BLUESKY_MAX_BYTES:
        raise ImageTooLargeError(
            f"{image_path.name} is {len(data)} bytes; Bluesky caps blobs at "
            f"{BLUESKY_MAX_BYTES}. Run prepare_image() first."
        )
    upload = client.upload_blob(data)
    image = models.AppBskyEmbedImages.Image(alt=alt or "", image=upload.blob)
    return models.AppBskyEmbedImages.Main(images=[image])


def upload_mastodon_media(client, image_path: Path, alt: str) -> str:
    """
    Upload media to Mastodon and return its media id for `status_post(media_ids=[...])`.

    For images this is effectively synchronous; we still poll briefly in case the
    instance reports the attachment as processing.
    """
    media = client.media_post(str(image_path), description=alt or None)
    media_id = media["id"]

    # Wait out any server-side processing so status_post does not 422.
    for _ in range(10):
        if client.media(media_id).get("url"):
            break
        time.sleep(1)
    return media_id


def upload_x_media(
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
    image_path: Path,
    alt: str,
) -> str:
    """
    Upload an image to X and return its media id string for
    `Client.create_tweet(media_ids=[...])`.

    Media upload and alt-text both go through the v1.1 API (tweepy's `API`),
    while the tweet itself is created on v2 (tweepy's `Client`); this split is
    the standard tweepy pattern. Alt text is set in a second call when present.
    """
    import tweepy  # lazy

    auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret)
    api = tweepy.API(auth)
    media = api.media_upload(filename=str(image_path))
    if alt:
        api.create_media_metadata(media.media_id, alt)
    return media.media_id_string


def post_threads_image(
    user_id: str,
    access_token: str,
    text: str,
    image_url: str,
    alt: str,
    *,
    base_url: str = "https://graph.threads.net/v1.0",
) -> str:
    """
    Post a single-image Thread: create an IMAGE container from the PUBLIC url,
    wait for it to finish processing, then publish it. Returns the post id.

    `image_url` MUST be publicly reachable over HTTPS - Meta fetches it server
    side. A Tailscale-only or LAN URL will fail here.
    """
    import requests  # lazy

    create = requests.post(
        f"{base_url}/{user_id}/threads",
        params={
            "media_type": "IMAGE",
            "image_url": image_url,
            "text": text,
            "alt_text": alt or None,
            "access_token": access_token,
        },
        timeout=30,
    )
    create.raise_for_status()
    creation_id = create.json()["id"]

    # Poll the container until it is FINISHED before publishing.
    for _ in range(20):
        status = requests.get(
            f"{base_url}/{creation_id}",
            params={"fields": "status", "access_token": access_token},
            timeout=30,
        ).json().get("status")
        if status == "FINISHED":
            break
        if status in {"ERROR", "EXPIRED"}:
            raise RuntimeError(f"Threads media container {status}: check the image URL")
        time.sleep(3)

    publish = requests.post(
        f"{base_url}/{user_id}/threads_publish",
        params={"creation_id": creation_id, "access_token": access_token},
        timeout=30,
    )
    publish.raise_for_status()
    return publish.json()["id"]


def register_linkedin_image(
    access_token: str,
    person_urn: str,
    image_path: Path,
    *,
    version: str = LINKEDIN_API_VERSION,
) -> str:
    """
    Register and upload an image via LinkedIn's Images API. Returns the image
    URN (e.g. `urn:li:image:...`) for use in the post body's
    `content.media.id`.

    Two calls: initializeUpload to get a one-time uploadUrl + URN, then a binary
    PUT of the image bytes to that URL.
    """
    import requests  # lazy

    headers = {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": version,
        "X-Restli-Protocol-Version": "2.0.0",
    }

    init = requests.post(
        "https://api.linkedin.com/rest/images?action=initializeUpload",
        headers=headers,
        json={"initializeUploadRequest": {"owner": person_urn}},
        timeout=30,
    )
    init.raise_for_status()
    value = init.json()["value"]
    upload_url, image_urn = value["uploadUrl"], value["image"]

    put = requests.put(
        upload_url,
        headers={"Authorization": f"Bearer {access_token}"},
        data=image_path.read_bytes(),
        timeout=60,
    )
    put.raise_for_status()
    return image_urn


# --------------------------------------------------------------------------- #
# Convenience: resolve + prepare + host in one call (what publish.py will use)
# --------------------------------------------------------------------------- #


@dataclass
class HostedImage:
    local_path: Path
    public_url: str
    alt: str


def resolve_prepare_and_host(
    post_path: Path, frontmatter: dict, cfg: ImageHostConfig
) -> HostedImage | None:
    """
    Full pipeline for a post that has an `image:` field. Returns None for a
    text-only post.

    publish.py calls this once, then hands the result to whichever platform
    helpers it is posting to: local_path for Bluesky/Mastodon/LinkedIn binary
    uploads, public_url for Threads.
    """
    image_field = frontmatter.get("image")
    if not image_field:
        return None

    local_path = resolve_image_path(post_path, str(image_field))
    ready = prepare_image(local_path)
    alt = str(frontmatter.get("image-alt", "")).strip()
    if not alt:
        # Alt text is the one accessibility lever fully under our control. Warn
        # loudly rather than silently shipping an undescribed image.
        print(f"WARNING: {local_path.name} has no image-alt; posting without alt text.")

    public_url = upload_to_image_host(ready, cfg, slug=post_path.stem)
    return HostedImage(local_path=ready, public_url=public_url, alt=alt)


# --------------------------------------------------------------------------- #
# Standalone smoke test: prepare + host a single image, print the URL.
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: python3 images.py <path-to-image>")
        raise SystemExit(2)

    src = Path(sys.argv[1]).expanduser().resolve()
    ready = prepare_image(src)
    cfg = ImageHostConfig.from_env()
    url = upload_to_image_host(ready, cfg, slug="smoke-test")
    print(f"OK hosted at {url}")
    print("Now curl it from off your LAN to confirm Threads can reach it:")
    print(f"  curl -I {url}")

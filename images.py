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
import json
import os
import re
import shutil
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

# Instagram content publishing uses the Meta Graph API with Instagram Login.
INSTAGRAM_API_BASE = "https://graph.instagram.com/v23.0"

# TikTok Content Posting API base.
TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"

# Facebook Page posting uses the Meta Graph API.
FACEBOOK_API_BASE = "https://graph.facebook.com/v23.0"

# --- Video --------------------------------------------------------------------
# Bluesky is the binding constraint again: it caps video at 100,000,000 bytes and
# 180 seconds and requires H.264 MP4. We target just under that so one prepared
# file passes everywhere (Threads <1GB/5min, Mastodon instance-set, X, etc.).
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}
VIDEO_SAFE_MAX_BYTES = 95_000_000
VIDEO_MAX_SECONDS = 180

# ffmpeg transcode ladder: (longest-side px, H.264 CRF). Sharpest/largest first;
# we step down until the re-encoded file fits VIDEO_SAFE_MAX_BYTES.
VIDEO_SHRINK_LADDER = ((1080, 23), (720, 26), (720, 30), (480, 32))


class ImageTooLargeError(Exception):
    """Raised when an image cannot be made small enough for the strictest platform."""


class VideoTooLargeError(Exception):
    """Raised when a video cannot be transcoded under the strictest byte cap."""


class VideoTooLongError(Exception):
    """Raised when a video exceeds the strictest duration cap (we never auto-trim)."""


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
            f"Post references '{image_field}' but no file exists at {candidate}"
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
# Step 2b: validate and size a VIDEO (the same auto-shrink discipline, via ffmpeg)
# --------------------------------------------------------------------------- #


def _ensure_ffmpeg() -> None:
    """Fail early with an install hint if the ffmpeg toolchain is missing."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError(
            "ffmpeg/ffprobe not found, which video posting needs. Install it "
            "(macOS: `brew install ffmpeg`; Debian/Ubuntu: `apt install ffmpeg`)."
        )


def _ffprobe(path: Path) -> dict:
    """Return {duration, width, height, vcodec, acodec, size} for a video file."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    data = json.loads(out)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), {})
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    return {
        "duration": duration,
        "width": int(v.get("width", 0) or 0),
        "height": int(v.get("height", 0) or 0),
        "vcodec": v.get("codec_name", ""),
        "acodec": a.get("codec_name", ""),
        "size": path.stat().st_size,
    }


def _transcode_video(src: Path, max_side: int, crf: int) -> Path:
    """
    Re-encode to H.264/AAC MP4 with the longest side capped at max_side (never
    upscaled), `+faststart` for streaming, writing a new `.mp4` sibling.

    The scale filter caps each dimension with min(), so a video already smaller
    than max_side is left at its native size; force_divisible_by=2 keeps even
    dimensions, which H.264 requires.
    """
    out = src.with_name(f"{src.stem}-x{max_side}.mp4")
    if out == src:
        out = src.with_name(f"{src.stem}-x{max_side}-c.mp4")
    vf = (
        f"scale=w=min(iw\\,{max_side}):h=min(ih\\,{max_side})"
        ":force_original_aspect_ratio=decrease:force_divisible_by=2"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src),
         "-vf", vf,
         "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
         "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart",
         str(out)],
        check=True, capture_output=True,
    )
    return out


def prepare_video(path: Path) -> Path:
    """
    Validate a video and ensure it is H.264/AAC MP4 under VIDEO_SAFE_MAX_BYTES so
    every platform accepts it. Returns the ready-to-upload path (a new `.mp4`
    sibling when a transcode was needed).

    Policy mirrors prepare_image():
      1. Duration over VIDEO_MAX_SECONDS is a hard error - trimming would change
         the content, which is the human's call, not ours.
      2. An MP4 that is already H.264/AAC and under the byte cap passes through.
      3. Otherwise transcode down VIDEO_SHRINK_LADDER (scale + CRF), returning the
         first rung that fits. This also normalizes non-H.264 sources (.mov/HEVC,
         .webm) that Threads/Instagram/TikTok would otherwise reject.
      4. Give up with VideoTooLargeError only if even the smallest rung is over.
    """
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        raise ValueError(
            f"Unsupported video type {path.suffix!r}. Allowed: {sorted(VIDEO_SUFFIXES)}"
        )
    _ensure_ffmpeg()
    info = _ffprobe(path)
    if info["duration"] > VIDEO_MAX_SECONDS + 0.5:
        raise VideoTooLongError(
            f"{path.name} is {info['duration']:.0f}s; the cap is {VIDEO_MAX_SECONDS}s "
            "(Bluesky's limit, the tightest). Trim it before publishing."
        )

    already_ok = (
        path.suffix.lower() == ".mp4"
        and info["vcodec"] == "h264"
        and info["acodec"] in ("aac", "")  # "" = silent video, no audio stream
        and info["size"] <= VIDEO_SAFE_MAX_BYTES
    )
    if already_ok:
        return path

    for max_side, crf in VIDEO_SHRINK_LADDER:
        out = _transcode_video(path, max_side, crf)
        if out.stat().st_size <= VIDEO_SAFE_MAX_BYTES:
            return out
        try:  # this rung is still too big; reclaim the disk before the next try
            out.unlink()
        except OSError:
            pass

    raise VideoTooLargeError(
        f"{path.name} is still over {VIDEO_SAFE_MAX_BYTES} bytes after transcoding to "
        f"{VIDEO_SHRINK_LADDER[-1][0]}px / CRF {VIDEO_SHRINK_LADDER[-1][1]}. "
        "Trim or compress it before publishing."
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
# Step 4b: per-platform VIDEO helpers (parallel to the image helpers above)
# --------------------------------------------------------------------------- #
# Bluesky video is a one-liner in publish.py (atproto's client.send_video reads
# the bytes, uploads to video.bsky.app, polls processing, and posts), so it needs
# no helper here. The rest mirror their image counterparts.


def upload_mastodon_video(client, video_path: Path, alt: str) -> str:
    """
    Upload a video to Mastodon and return its media id for `status_post`.

    Mastodon transcodes video server-side and returns 202, so we poll the media
    endpoint longer than for images before the status can attach it.
    """
    media = client.media_post(str(video_path), description=alt or None)
    media_id = media["id"]
    for _ in range(60):
        if client.media(media_id).get("url"):
            break
        time.sleep(3)
    return media_id


def post_threads_video(
    user_id: str,
    access_token: str,
    text: str,
    video_url: str,
    *,
    base_url: str = "https://graph.threads.net/v1.0",
) -> str:
    """
    Post a single-video Thread: create a VIDEO container from the PUBLIC url, wait
    for it to finish processing, then publish it. Returns the post id.

    `video_url` MUST be publicly reachable over HTTPS - Meta fetches it server
    side. Video containers take longer than images, so the poll budget is bigger.
    """
    import requests  # lazy

    create = requests.post(
        f"{base_url}/{user_id}/threads",
        params={
            "media_type": "VIDEO",
            "video_url": video_url,
            "text": text,
            "access_token": access_token,
        },
        timeout=30,
    )
    create.raise_for_status()
    creation_id = create.json()["id"]

    for _ in range(60):
        status = requests.get(
            f"{base_url}/{creation_id}",
            params={"fields": "status", "access_token": access_token},
            timeout=30,
        ).json().get("status")
        if status == "FINISHED":
            break
        if status in {"ERROR", "EXPIRED"}:
            raise RuntimeError(f"Threads video container {status}: check the video URL/format")
        time.sleep(5)

    publish = requests.post(
        f"{base_url}/{user_id}/threads_publish",
        params={"creation_id": creation_id, "access_token": access_token},
        timeout=30,
    )
    publish.raise_for_status()
    return publish.json()["id"]


def register_linkedin_video(
    access_token: str,
    owner_urn: str,
    video_path: Path,
    *,
    version: str = LINKEDIN_API_VERSION,
) -> str:
    """
    Register and upload a video via LinkedIn's Videos API. Returns the video URN
    (e.g. `urn:li:video:...`) for use in the post body's `content.media.id`.

    Three steps: initializeUpload (declares fileSizeBytes, returns an upload token
    plus one or more byte-range upload URLs), PUT each byte range and collect its
    ETag, then finalizeUpload with the ordered ETags. LinkedIn finishes processing
    the video asynchronously after finalize; the post can reference it immediately.
    """
    import requests  # lazy

    headers = {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": version,
        "X-Restli-Protocol-Version": "2.0.0",
    }
    data = video_path.read_bytes()

    init = requests.post(
        "https://api.linkedin.com/rest/videos?action=initializeUpload",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "initializeUploadRequest": {
                "owner": owner_urn,
                "fileSizeBytes": len(data),
                "uploadCaptions": False,
                "uploadThumbnail": False,
            }
        },
        timeout=30,
    )
    init.raise_for_status()
    value = init.json()["value"]
    video_urn = value["video"]
    upload_token = value.get("uploadToken", "")

    etags: list[str] = []
    for ins in value["uploadInstructions"]:
        first, last = int(ins["firstByte"]), int(ins["lastByte"])
        put = requests.put(
            ins["uploadUrl"],
            headers={"Authorization": f"Bearer {access_token}",
                     "Content-Type": "application/octet-stream"},
            data=data[first : last + 1],
            timeout=300,
        )
        put.raise_for_status()
        etag = put.headers.get("ETag") or put.headers.get("etag", "")
        etags.append(etag.strip('"'))

    fin = requests.post(
        "https://api.linkedin.com/rest/videos?action=finalizeUpload",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "finalizeUploadRequest": {
                "video": video_urn,
                "uploadToken": upload_token,
                "uploadedPartIds": etags,
            }
        },
        timeout=30,
    )
    fin.raise_for_status()
    return video_urn


def upload_x_video(
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
    video_path: Path,
    alt: str,
) -> str:
    """
    Upload a video to X and return its media id string for `create_tweet`.

    Video goes through the v1.1 chunked upload (INIT/APPEND/FINALIZE); tweepy's
    `media_upload(chunked=True, media_category="tweet_video")` runs all three
    steps and, with wait_for_async_finalize, blocks until X finishes transcoding.
    Alt text on video is best-effort, so a failure there does not fail the post.
    """
    import tweepy  # lazy

    auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret)
    api = tweepy.API(auth)
    media = api.media_upload(
        filename=str(video_path),
        chunked=True,
        media_category="tweet_video",
        wait_for_async_finalize=True,
    )
    if alt:
        try:
            api.create_media_metadata(media.media_id, alt)
        except Exception:
            pass
    return media.media_id_string


# --------------------------------------------------------------------------- #
# Step 4c: Instagram + TikTok posters (both fetch media from the PUBLIC url)
# --------------------------------------------------------------------------- #


def _wait_instagram_container(
    base_url: str, creation_id: str, access_token: str, *, tries: int = 60, delay: int = 5
) -> None:
    """Poll an Instagram media container until it reports FINISHED."""
    import requests  # lazy

    for _ in range(tries):
        code = requests.get(
            f"{base_url}/{creation_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=30,
        ).json().get("status_code")
        if code == "FINISHED":
            return
        if code in {"ERROR", "EXPIRED"}:
            raise RuntimeError(f"Instagram media container {code}: check the media URL/format")
        time.sleep(delay)
    raise RuntimeError("Instagram media container did not finish processing in time")


def post_instagram_image(
    user_id: str, access_token: str, caption: str, image_url: str,
    *, base_url: str = INSTAGRAM_API_BASE,
) -> str:
    """Create an image container from the PUBLIC url, wait, publish. Returns media id."""
    import requests  # lazy

    create = requests.post(
        f"{base_url}/{user_id}/media",
        params={"image_url": image_url, "caption": caption, "access_token": access_token},
        timeout=30,
    )
    create.raise_for_status()
    creation_id = create.json()["id"]
    _wait_instagram_container(base_url, creation_id, access_token, tries=20, delay=3)
    publish = requests.post(
        f"{base_url}/{user_id}/media_publish",
        params={"creation_id": creation_id, "access_token": access_token},
        timeout=30,
    )
    publish.raise_for_status()
    return publish.json()["id"]


def post_instagram_video(
    user_id: str, access_token: str, caption: str, video_url: str,
    *, base_url: str = INSTAGRAM_API_BASE,
) -> str:
    """
    Publish a Reel from the PUBLIC video url. Instagram only accepts API video as
    REELS (in-feed video posts go out as Reels). Returns the published media id.
    """
    import requests  # lazy

    create = requests.post(
        f"{base_url}/{user_id}/media",
        params={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": access_token,
        },
        timeout=30,
    )
    create.raise_for_status()
    creation_id = create.json()["id"]
    _wait_instagram_container(base_url, creation_id, access_token)  # Reels processing is slower
    publish = requests.post(
        f"{base_url}/{user_id}/media_publish",
        params={"creation_id": creation_id, "access_token": access_token},
        timeout=30,
    )
    publish.raise_for_status()
    return publish.json()["id"]


def post_tiktok_video(
    access_token: str,
    caption: str,
    video_url: str,
    *,
    privacy_level: str = "SELF_ONLY",
    base_url: str = TIKTOK_API_BASE,
) -> str:
    """
    Direct-post a video to TikTok from the PUBLIC url (PULL_FROM_URL) and poll
    until publishing completes. Returns the publish_id.

    Until the app passes TikTok's Content Posting audit, privacy_level must be
    SELF_ONLY (the post is visible only to the creator). PULL_FROM_URL also
    requires the host domain be verified in the TikTok developer portal.
    """
    import requests  # lazy

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    # Creator info gates the allowed privacy levels and interaction settings.
    info = requests.post(f"{base_url}/post/publish/creator_info/query/", headers=headers, timeout=30)
    info.raise_for_status()

    init = requests.post(
        f"{base_url}/post/publish/video/init/",
        headers=headers,
        json={
            "post_info": {
                "title": caption[:2200],
                "privacy_level": privacy_level,
                "disable_comment": False,
                "disable_duet": False,
                "disable_stitch": False,
            },
            "source_info": {"source": "PULL_FROM_URL", "video_url": video_url},
        },
        timeout=60,
    )
    init.raise_for_status()
    publish_id = init.json()["data"]["publish_id"]

    for _ in range(60):
        st = requests.post(
            f"{base_url}/post/publish/status/fetch/",
            headers=headers,
            json={"publish_id": publish_id},
            timeout=30,
        ).json().get("data", {})
        status = st.get("status")
        if status == "PUBLISH_COMPLETE":
            break
        if status == "FAILED":
            raise RuntimeError(f"TikTok publish failed: {st.get('fail_reason') or st}")
        time.sleep(5)
    return publish_id


def post_facebook_photo(
    page_id: str, access_token: str, message: str, image_url: str,
    *, base_url: str = FACEBOOK_API_BASE,
) -> str:
    """
    Publish a photo to a Facebook Page from the PUBLIC image url. Facebook fetches
    the url itself. Returns the feed post id (post_id) so the caller can build a
    permalink.
    """
    import requests  # lazy

    r = requests.post(
        f"{base_url}/{page_id}/photos",
        params={"url": image_url, "caption": message, "access_token": access_token},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("post_id") or data["id"]


def post_facebook_video(
    page_id: str, access_token: str, message: str, video_url: str,
    *, base_url: str = FACEBOOK_API_BASE,
) -> str:
    """
    Publish a video to a Facebook Page from the PUBLIC video url (file_url).
    Facebook ingests and processes it server-side. Returns the video id.
    """
    import requests  # lazy

    r = requests.post(
        f"{base_url}/{page_id}/videos",
        params={"file_url": video_url, "description": message, "access_token": access_token},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["id"]


# --------------------------------------------------------------------------- #
# Convenience: resolve + prepare + host in one call (what publish.py will use)
# --------------------------------------------------------------------------- #


@dataclass
class HostedImage:
    local_path: Path
    public_url: str
    alt: str
    kind: str = "image"


@dataclass
class HostedVideo:
    local_path: Path
    public_url: str
    alt: str
    duration_s: float = 0.0
    kind: str = "video"


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


def resolve_prepare_and_host_video(
    post_path: Path, frontmatter: dict, cfg: ImageHostConfig
) -> HostedVideo | None:
    """
    Full pipeline for a post that has a `video:` field. Returns None when there
    is no video.

    Mirrors resolve_prepare_and_host: resolve the path, transcode/size it under
    the strictest cap, then host it so Threads, Instagram, and TikTok can fetch it
    by public URL. Bluesky/Mastodon/LinkedIn/X upload the local file directly.
    """
    video_field = frontmatter.get("video")
    if not video_field:
        return None

    local_path = resolve_image_path(post_path, str(video_field))
    ready = prepare_video(local_path)
    alt = str(frontmatter.get("video-alt", "")).strip()
    if not alt:
        print(f"WARNING: {local_path.name} has no video-alt; posting without an alt description.")

    duration = _ffprobe(ready)["duration"]
    public_url = upload_to_image_host(ready, cfg, slug=post_path.stem)
    return HostedVideo(local_path=ready, public_url=public_url, alt=alt, duration_s=duration)


# --------------------------------------------------------------------------- #
# Standalone smoke test: prepare + host a single image or video, print the URL.
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: uv run images.py <path-to-image-or-video>")
        raise SystemExit(2)

    src = Path(sys.argv[1]).expanduser().resolve()
    is_video = src.suffix.lower() in VIDEO_SUFFIXES
    ready = prepare_video(src) if is_video else prepare_image(src)
    cfg = ImageHostConfig.from_env()
    url = upload_to_image_host(ready, cfg, slug="smoke-test")
    print(f"OK hosted {'video' if is_video else 'image'} at {url}")
    print(f"  prepared file: {ready}")
    print("Now curl it from off your network to confirm Threads/IG/TikTok can reach it:")
    print(f"  curl -I {url}")

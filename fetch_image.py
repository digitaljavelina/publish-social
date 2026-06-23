#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "requests",
#   "Pillow",
#   "PyYAML",
#   "python-dotenv",
# ]
# ///
"""
fetch_image.py - find a context-aware image for a social post, from Pexels (free).

Image choice for a public post is a judgment call, so this runs in two phases:

  1. SEARCH (default): given a post (and an optional --query), pull a few
     candidate photos, download small previews to a temp dir, and print each
     one's id, dimensions, photographer, alt text, and local preview path.
     It changes nothing on disk. Review the previews, then pick one.

  2. APPLY (--apply <photo_id>): download the chosen photo at social size into
     the post's media/ folder, size it under the per-platform cap (via
     images.prepare_image), and write `image:`, `image-alt:`, and
     `image-credit:` into the post's frontmatter.

If the post already has an `image:` set, fetching is skipped unless --force, so
an image supplied by hand is never overwritten.

Run with uv:
    uv run fetch_image.py --file <post.md> --query "your topic" --count 4
    uv run fetch_image.py --file <post.md> --apply 1234567

Needs a free Pexels API key in .env: PEXELS_API_KEY (get one at pexels.com/api).
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

# images.py sits next to this file; the script's own directory is on sys.path.
import images

PEXELS_SEARCH = "https://api.pexels.com/v1/search"
PEXELS_PHOTO = "https://api.pexels.com/v1/photos/{id}"
CANDIDATE_DIR = Path(tempfile.gettempdir()) / "publish-social-candidates"


def resolve_env_path() -> Path:
    """Same resolution order as publish.py: env override, stable config dir, legacy."""
    override = os.environ.get("PUBLISH_SOCIAL_ENV")
    if override:
        return Path(override).expanduser()
    stable = Path.home() / ".config" / "publish-social" / ".env"
    if stable.exists():
        return stable
    return Path(__file__).resolve().parent / ".env"


class FetchError(Exception):
    """A user-facing failure, printed without a traceback."""


# --------------------------------------------------------------------------- #
# Frontmatter
# --------------------------------------------------------------------------- #


def split_frontmatter(text: str) -> tuple[dict, str, int]:
    """Return (frontmatter dict, body, index of the closing '---')."""
    if not text.startswith("---"):
        raise FetchError("Post has no YAML frontmatter (expected a leading '---').")
    end = text.find("\n---", 3)
    if end == -1:
        raise FetchError("Frontmatter is not closed with '---'.")
    fm = yaml.safe_load(text[3:end].strip("\n")) or {}
    if not isinstance(fm, dict):
        raise FetchError("Frontmatter did not parse to a mapping.")
    body = text[end + 4 :].lstrip("\n")
    return fm, body, end


def yaml_dq(s: str) -> str:
    """Double-quote a scalar for YAML (alt text and credits contain colons/commas)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_frontmatter_keys(path: Path, updates: dict[str, str]) -> None:
    """Set or replace top-level frontmatter keys, preserving everything else."""
    text = path.read_text(encoding="utf-8")
    _, body, end = split_frontmatter(text)
    lines = text[3:end].strip("\n").splitlines()
    remaining = dict(updates)
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Za-z0-9_-]+):", line)
        if m and m.group(1) in remaining:
            lines[i] = f"{m.group(1)}: {remaining.pop(m.group(1))}"
    for key, val in remaining.items():
        lines.append(f"{key}: {val}")
    path.write_text("---\n" + "\n".join(lines) + "\n---\n\n" + body, encoding="utf-8")


def derive_query(fm: dict, body: str, override: str | None) -> str | None:
    """Pick a search query: explicit flag, then frontmatter hints, then the H1."""
    if override:
        return override
    for key in ("image-query", "topic", "title"):
        val = fm.get(key)
        if val:
            return str(val)
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


# --------------------------------------------------------------------------- #
# Pexels
# --------------------------------------------------------------------------- #


def _search(key: str, query: str, count: int) -> list[dict]:
    r = requests.get(
        PEXELS_SEARCH,
        headers={"Authorization": key},
        params={"query": query, "orientation": "landscape", "per_page": count, "size": "large"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("photos", [])


def _get_photo(key: str, photo_id: int) -> dict:
    r = requests.get(PEXELS_PHOTO.format(id=photo_id), headers={"Authorization": key}, timeout=30)
    r.raise_for_status()
    return r.json()


def _download(url: str, dest: Path) -> Path:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


# --------------------------------------------------------------------------- #
# Phases
# --------------------------------------------------------------------------- #


def _open_files(paths: list[Path]) -> bool:
    """Open files in the OS default image viewer. Best-effort; never fatal."""
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["open", *map(str, paths)], check=False)
        elif system == "Linux":
            for p in paths:
                subprocess.run(["xdg-open", str(p)], check=False)
        elif system == "Windows":
            for p in paths:
                os.startfile(str(p))  # type: ignore[attr-defined]
        else:
            return False
        return True
    except Exception:
        return False


def do_search(key: str, post_path: Path, query: str, count: int, open_previews: bool) -> None:
    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
    for old in CANDIDATE_DIR.glob("*.jpg"):
        old.unlink()

    photos = _search(key, query, count)
    if not photos:
        raise FetchError(f"No Pexels results for query: {query!r}. Try a different --query.")

    previews: list[Path] = []
    print(f"Query: {query!r}  ({len(photos)} candidates)\n")
    for p in photos:
        preview = CANDIDATE_DIR / f"{p['id']}.jpg"
        _download(p["src"]["medium"], preview)
        previews.append(preview)
        alt = (p.get("alt") or "").strip() or "(no alt provided)"
        print(f"  id={p['id']}  {p['width']}x{p['height']}  by {p['photographer']}")
        print(f"    alt: {alt}")
        print(f"    pexels: {p.get('url', '')}")
        print(f"    preview: {preview}")
        print()

    if open_previews and _open_files(previews):
        print("Opened the previews in your image viewer.\n")

    print("Pick the one you want, then apply it by id:")
    print(f'  uv run "{Path(__file__).resolve()}" --file "{post_path}" --apply <id>')


def do_apply(key: str, post_path: Path, photo_id: int) -> None:
    p = _get_photo(key, photo_id)
    media = post_path.parent / "media"
    media.mkdir(parents=True, exist_ok=True)

    src = p["src"].get("large2x") or p["src"].get("large") or p["src"]["original"]
    dest = media / f"{post_path.stem}-pexels-{photo_id}.jpg"
    _download(src, dest)
    ready = images.prepare_image(dest)  # sizes under the strictest platform's cap

    alt = (p.get("alt") or "").strip()
    credit = f"Photo by {p['photographer']} on Pexels ({p.get('url', '')})"
    updates = {"image": f"media/{ready.name}", "image-credit": yaml_dq(credit)}
    if alt:
        updates["image-alt"] = yaml_dq(alt)
    set_frontmatter_keys(post_path, updates)

    print(f"Applied to {post_path.name}:")
    print(f"  image: media/{ready.name}  ({ready.stat().st_size // 1024} KB)")
    print(f"  image-alt: {alt or '(none returned — write one before publishing)'}")
    print(f"  image-credit: {credit}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch a context-aware image for a post from Pexels.")
    ap.add_argument("--file", required=True, help="Path to the post .md file.")
    ap.add_argument("--query", help="Search query. Defaults to the post's topic/title.")
    ap.add_argument("--count", type=int, default=4, help="Number of candidates to preview.")
    ap.add_argument("--apply", type=int, metavar="PHOTO_ID", help="Download and attach this Pexels photo.")
    ap.add_argument("--force", action="store_true", help="Fetch even if image: is already set.")
    ap.add_argument("--no-open", action="store_true", help="Do not open the previews in your image viewer.")
    args = ap.parse_args()

    load_dotenv(resolve_env_path())
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        raise FetchError("PEXELS_API_KEY not set. Get a free key at https://www.pexels.com/api/ and add it to .env.")

    post_path = Path(args.file).expanduser().resolve()
    if not post_path.is_file():
        raise FetchError(f"No such file: {post_path}")

    fm, body, _ = split_frontmatter(post_path.read_text(encoding="utf-8"))
    if fm.get("image") and not args.force:
        print(f"Post already has image: {fm['image']} — skipping fetch (a supplied image is respected). Use --force to override.")
        return 0

    if args.apply:
        do_apply(key, post_path, args.apply)
        return 0

    query = derive_query(fm, body, args.query)
    if not query:
        raise FetchError("No query available: pass --query, or add a `topic`/`title` to the post.")
    do_search(key, post_path, query, args.count, open_previews=not args.no_open)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FetchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except requests.HTTPError as exc:
        body = getattr(exc.response, "text", "")[:200]
        print(f"HTTP error: {exc} {body}", file=sys.stderr)
        raise SystemExit(2)

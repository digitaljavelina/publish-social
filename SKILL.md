---
name: publish-social
description: Publish one Markdown post to Bluesky, Mastodon, Threads, LinkedIn, and X with a single command. Each post is a Markdown file with one fenced code block per platform and an optional image. publish.py dry-runs first, posts only files gated with status:ready and approved:true, then writes the resulting URLs back into the file. Use when someone says "post this", "publish to social", "send this to Bluesky/Mastodon", or points at a post file and wants it live.
---

# publish-social — one Markdown file to Bluesky / Mastodon / Threads / LinkedIn / X

Each social post is a Markdown file: per-platform text in fenced code blocks and
an optional `image:` field. `publish.py` reads a post, extracts each platform's
text, optionally attaches one image, posts to the selected platforms, then marks
the file posted and fills in its Publish Tracking table.

`publish.py` is a PEP 723 script: run it with `uv` so dependencies resolve from
the script header (no venv to manage). It reads credentials from
`~/.config/publish-social/.env` (override with `$PUBLISH_SOCIAL_ENV`). See
README.md for credential setup.

Two gates, because this posts to live public accounts:

1. **Review gate.** A file only posts when its frontmatter has `status: ready`
   **and** `approved: true`. Never flip those just to make a file send; that is
   the human's sign-off.
2. **Dry-run before live.** Always run `--dry-run` first, show the user exactly
   what would post per platform, and only post for real after they confirm.

## Post file format

````markdown
---
status: draft          # set to "ready" (with approved: true) to allow posting
approved: false        # set to true after a human reviews the content
platforms: [bluesky, mastodon, threads, linkedin, x]
image: ./media/example.jpg   # optional; one image, every platform that takes one
image-alt: "Describe the image for screen readers."
---

## Bluesky

```
Post text for Bluesky (<= 300 chars).
```

## Mastodon

```
Post text for Mastodon (<= 500 chars).
```

## Publish Tracking

| Platform | Posted? | Date | URL | Notes |
|---|---|---|---|---|
| Bluesky | ☐ | | | |
| Mastodon | ☐ | | | |
````

- The `## <Platform>` heading is matched by its first word, so `## Bluesky (~270 chars)` works.
- Character limits: Bluesky 300, X 280, Mastodon/Threads 500, LinkedIn 3000 (the dry-run flags overages).
- Hashtags are fine on Bluesky, Mastodon, LinkedIn, and X. Threads turns the first hashtag into a header topic tag, so `publish.py` strips hashtags from Threads text automatically.

## Workflow

1. Dry-run (changes nothing; prints per-platform text, char counts, and whether an image is attached):
   ```bash
   uv run publish.py --file path/to/post.md --dry-run
   uv run publish.py --auto --dry-run --platforms bluesky,mastodon
   ```
   `--platforms` is a comma list from `bluesky,mastodon,threads,linkedin,x`; omit
   it to use the file's `platforms:` frontmatter. `--auto` picks the most recently
   modified `status: ready` file in the posts dir (`SOCIAL_POSTS_DIR`, default `~/social-posts`).
2. Read the dry-run back to the user; flag anything truncated or wrong.
3. After the user confirms and the gates are set, post for real (drop `--dry-run`):
   ```bash
   uv run publish.py --file path/to/post.md
   ```
   Add `-y` to skip the interactive confirmation once the dry-run is approved. On
   success the file is marked `status: posted`, stamped `published-at`, and its
   Publish Tracking table is filled with per-platform URLs. Report those back.

## Notes

- **X costs money** (pay-per-use): about $0.015 per post, $0.20 if the post
  contains a link. The other four platforms are free. Skip X by leaving its creds
  unset or omitting it from `--platforms`.
- **One image max.** Bluesky, Mastodon, LinkedIn, and X take a direct upload.
  Threads needs a public HTTPS URL it can fetch, so images on Threads require the
  optional image host (see README.md). Text-only posting needs no image host.
- If a platform's credentials are missing, that platform is skipped, not fatal.
- The script never bulk-posts: one file per run.

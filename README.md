# publish-social

Publish one Markdown post to **Bluesky, Mastodon, Threads, LinkedIn, and X** with a single command. Write the post once (one fenced block per platform, one optional image), dry-run to see exactly what will go out, then post everywhere and get the URLs written back into the file.

Works both as a **Claude Code skill** and as a **standalone CLI**.

## Platforms

| Platform | Cost | Images | Tokens |
|---|---|---|---|
| Bluesky | Free | Direct upload | App password, no expiry |
| Mastodon | Free | Direct upload | Access token, no expiry |
| Threads | Free | Public URL (Meta fetches it) | 60-day token, auto-rolled |
| LinkedIn | Free | Direct upload | 60-day access / 365-day refresh, auto-renewed |
| X / Twitter | **Paid** (~$0.015/post, $0.20 with a link) | Direct upload | OAuth 1.0a keys, no expiry |

Set up only the platforms you want. Missing credentials mean that platform is skipped, not an error.

## Requirements

- [uv](https://docs.astral.sh/uv/) — runs the scripts and resolves dependencies from each script's header, so there is no venv to manage.
- Python 3.11+ (uv fetches it if needed).
- macOS, Linux, or Windows (image resizing uses Pillow, which is cross-platform).

## Install

### As a CLI

```bash
git clone https://github.com/<owner>/<repo>.git
cd <repo>
# set up credentials (below), then:
uv run publish.py --file examples/example-post.md --dry-run
```

### As a Claude Code skill

Copy this folder into your Claude Code skills directory (for example `~/.claude/skills/publish-social/`), or install it as a plugin. The bundled `SKILL.md` tells Claude how to drive it. Then point Claude at a post file and ask it to publish.

## Credentials

Credentials live in a `.env` file, resolved in this order: `$PUBLISH_SOCIAL_ENV`, then `~/.config/publish-social/.env`, then a `.env` next to the scripts. The stable per-user location is recommended so an update never overwrites it:

```bash
mkdir -p ~/.config/publish-social && chmod 700 ~/.config/publish-social
cp .env.example ~/.config/publish-social/.env
chmod 600 ~/.config/publish-social/.env
```

Fill in only the platforms you use. `.env.example` documents every key. Key points and gotchas per platform (see each provider's developer docs for the full walkthrough):

- **Bluesky** — create an *app password* (Settings → App Passwords), not your account password. Set `BLUESKY_HANDLE` and `BLUESKY_APP_PASSWORD`.
- **Mastodon** — create an application on your instance (Preferences → Development) with the **`write:statuses`** and **`write:media`** scopes, and copy its access token. A correctly write-scoped token returns 403 on read endpoints; that is expected, not a failure.
- **Threads** — create a Meta app for the Threads API with `threads_basic` + `threads_content_publish`, add your account as a tester, run the OAuth authorize flow once, and exchange the returned code for a 60-day token. `publish.py` rolls the token forward automatically near expiry.
- **LinkedIn** — posts as an **organization / Company Page** using the `w_organization_social` scope from the Community Management API (a gated review). You need the org URN (`urn:li:organization:<id>`) and an access token. Set the 365-day refresh token too and `publish.py` auto-renews the access token.
- **X / Twitter** — paid, pay-per-use. Create an app, set it to **Read and write**, then generate the OAuth 1.0a **Access Token + Secret** (generate them *after* enabling write, or posting returns 403). Use the OAuth 1.0a Consumer Keys, not the OAuth 2.0 client/bearer values.

## Writing a post

A post is a Markdown file with frontmatter and one fenced code block per platform. See [`examples/example-post.md`](examples/example-post.md):

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
- Character limits: Bluesky 300, X 280, Mastodon/Threads 500, LinkedIn 3000. The dry-run flags overages.
- Hashtags are fine on Bluesky, Mastodon, LinkedIn, and X. Threads promotes the first hashtag to a header topic tag, so the tool strips hashtags from Threads text.

### The two gates

A file only posts when its frontmatter has both `status: ready` and `approved: true`. `approved: true` is your human sign-off that the content has been reviewed and is cleared to publish. Always dry-run before posting for real.

## Publishing

```bash
# Dry-run (changes nothing): shows per-platform text, char counts, and image
uv run publish.py --file path/to/post.md --dry-run
uv run publish.py --auto --dry-run --platforms bluesky,mastodon

# Post for real (after reviewing the dry-run and setting the gates)
uv run publish.py --file path/to/post.md
uv run publish.py --auto --platforms bluesky,mastodon -y
```

- `--platforms` is a comma list from `bluesky,mastodon,threads,linkedin,x`; omit it to use the file's `platforms:` frontmatter.
- `--auto` picks the most recently modified `status: ready` file in the posts directory (`SOCIAL_POSTS_DIR`, default `~/social-posts`).
- `-y` skips the interactive confirmation; use it only after approving the dry-run.
- On success the file is marked `status: posted`, stamped `published-at`, and its Publish Tracking table is filled with per-platform URLs.

## Images

One image per post, attached to every platform that accepts one. Set `image:` (relative to the post file) and `image-alt:` in frontmatter. The image is auto-sized under the strictest platform cap (about 976 KB, Bluesky's limit) with Pillow.

Bluesky, Mastodon, LinkedIn, and X take a direct upload and need no extra setup. **Threads is the exception**: its API fetches the image from a public HTTPS URL rather than accepting an upload, so images on Threads need a public image host. Any host works (a small static file server, a VPS, object storage, or a tunnel exposing a local server). Point the tool at it:

```
IMAGE_HOST_BASE_URL=https://img.example.com   # public prefix
IMAGE_HOST_SSH=your-image-host                 # SSH host alias the tool rsyncs to
IMAGE_HOST_PATH=~/images                        # directory that host serves
```

The tool rsyncs the image there and hands Threads the resulting URL. Text-only posting, and images on the other four platforms, need none of this.

### Auto-sourcing an image (optional)

`fetch_image.py` finds a context-aware photo from Pexels (free; needs `PEXELS_API_KEY`). It runs in two phases: search (downloads previews, changes nothing) and apply (downloads the chosen photo, sizes it, writes the frontmatter):

```bash
uv run fetch_image.py --file path/to/post.md --query "your topic" --count 4
uv run fetch_image.py --file path/to/post.md --apply <photo_id>
```

## Token renewal

| Platform | Expiry | Renewal |
|---|---|---|
| Bluesky | None | Set and forget |
| Mastodon | None | Set and forget |
| X | None (OAuth 1.0a keys) | Set and forget; regenerate only if you change app permissions or a key leaks |
| Threads | 60 days | Automatic: `publish.py` rolls the token forward near expiry |
| LinkedIn | 60-day access / 365-day refresh | Automatic when `LINKEDIN_REFRESH_TOKEN` is set; re-authorize only when the refresh token expires |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `File status is '...'` / `File does not have approved: true` | Set both `status: ready` and `approved: true` after reviewing the post |
| A platform is silently skipped | Its credentials are missing from `.env`; add them or drop it from `--platforms` |
| Bluesky `Invalid handle or password` | Use an app password, not your account password |
| Mastodon image upload 403 | The token lacks `write:media`; recreate it with `write:statuses` and `write:media` |
| Threads: `media url is not accessible` | The image URL must be publicly reachable HTTPS; a LAN or private URL will not work |
| X posting returns 403 about permissions | The access token was generated before the app was set to Read and write; set write, then regenerate the token |
| `ModuleNotFoundError` | Run the scripts with `uv` so the PEP 723 dependencies resolve |

## License

[MIT](LICENSE).

---
name: publish-social
description: Publish one Markdown post to Bluesky, Mastodon, Threads, LinkedIn, X, Instagram, Facebook, YouTube (Shorts), and Reddit with a single command. Each post is a Markdown file with one fenced code block per platform and an optional image or video. publish.py dry-runs first, posts only files gated with status:ready and approved:true, then writes the resulting URLs back into the file. Use when someone says "post this", "publish to social", "send this to Bluesky/Mastodon", "post this Short to YouTube", "post this to r/...", or points at a post file and wants it live.
---

# publish-social — one Markdown file to Bluesky / Mastodon / Threads / LinkedIn / X / Instagram / Facebook / YouTube / Reddit

Each social post is a Markdown file: per-platform text in fenced code blocks and
an optional `image:` or `video:` field. `publish.py` reads a post, extracts each
platform's text, optionally attaches one image or video, posts to the selected
platforms, then marks the file posted and fills in its Publish Tracking table.

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
image: ./media/example.jpg   # optional; one image (or use `video: ./media/clip.mp4` for one video instead)
image-alt: "Describe the image for screen readers."
# youtube-title: "..."       # required only when posting to youtube (the Short's title, <=100 chars)
# youtube-privacy: public    # optional: public | unlisted | private (default public)
# Reddit only (when posting to reddit):
# reddit-title: "..."        # required; the post title (<=300 chars)
# reddit-flair: "..."        # optional; matched to a subreddit flair by text
# reddit-link: "https://..." # optional; makes it a link post instead of a self post
# reddit-type: text|link|image|video   # optional; force a kind (default: auto from content)
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
- Character limits: Bluesky 300, X 280, Mastodon/Threads 500, LinkedIn 3000, Instagram 2200, Facebook effectively unlimited, YouTube 5000, Reddit body 40,000 (the dry-run flags overages). For YouTube the `## YouTube` block is the video **description** (title is the separate `youtube-title:`, ≤100). For Reddit the `## Reddit` block is the self-post **body** (optional for link/image/video posts; title is the separate `reddit-title:`, ≤300).
- Hashtags are fine on Bluesky, Mastodon, LinkedIn, X, Instagram, Facebook, and YouTube. Threads turns the first hashtag into a header topic tag, so `publish.py` strips hashtags from Threads text automatically.
- A post carries **one image OR one video**, never both. Use `video:` like `image:`; video needs `ffmpeg` installed and is auto-transcoded to fit Bluesky's H.264 / 100 MB / 3-minute cap.

## Workflow

1. **Choose platforms (ask first).** Before the dry-run, get the offerable set
   from the script, then ask the user which platforms to publish to with the
   AskUserQuestion tool:
   ```bash
   uv run publish.py --file path/to/post.md --check   # or --auto --check
   ```
   `--check` prints a per-platform table and a final `OFFER:` line. A platform is
   in `OFFER:` only when its credentials are present in `.env` **and** the post has
   a `## <Platform>` text block. **Only offer the platforms in that `OFFER:` list**;
   one with no credentials (or no text block) is never shown as a choice.

   AskUserQuestion allows at most four options per question, so do it in two steps:
   first a single-select, `All platforms` (everything in `OFFER:`) vs `Let me
   choose`; then, only if they choose to pick, a `multiSelect` of the `OFFER:`
   platforms, split across two questions (each with at least two options) when more
   than four are offerable. Flag that **X spends real money** (~$0.015/post) in its
   option description — but only when X would post via the paid API; if the X
   transport for this run is the browser (see step 2), it is free, so drop the money
   warning. The selection becomes the `--platforms` value below.
2. **Choose the X transport (only if `x` is among the chosen platforms).** X can
   post two ways: the paid **API**, or a **free** logged-in browser (Playwright).
   Figure out which are available, then route accordingly:
   - **API available?** X is in the `OFFER:` line of the plain `--check` (API creds present).
   - **Browser available?** X is in the `OFFER:` line of `--check --x-transport browser`
     (a saved session exists). If it is missing, the user can create one with
     `uv run x_playwright.py login` (a one-time interactive browser sign-in). If
     X rate-limits that automated login, fall back to
     `uv run x_playwright.py import-session` (import cookies from a browser the
     user is already logged into). Text, photos, and video all post this way.
   - **Only one available** → use it with no question: pass nothing for API (the
     default), or `--x-transport browser` for the browser.
   - **Both available** → ask with AskUserQuestion which to use this run:
     - `Free (browser)` — posts through your logged-in browser session, no cost.
     - `Paid API (~$0.015/post, $0.20 with a link)` — uses the X developer API.
   Whatever is chosen, pass it as `--x-transport api|browser` to **both** the
   dry-run and the real post below (so the dry-run reflects the right transport and
   the link/cost guardrail only fires for the API). `--x-transport` overrides
   `X_TRANSPORT` in `.env`, so nothing needs editing for a one-off choice.
3. **Choose the subreddit (only if `reddit` is among the chosen platforms).** A
   Reddit post goes to one subreddit, asked for here. First read the recently-used
   subreddits:
   ```bash
   uv run publish.py --reddit-recent   # prints up to the last 3, one per line (empty if none yet)
   ```
   Then ask with AskUserQuestion: offer those recent subreddits as options (label
   each `r/<name>`); the tool's built-in **Other** choice lets the user type a new
   one. If the list is empty, just have them type the subreddit via Other. Pass the
   result as `--subreddit r/<name>` to **both** the dry-run and the real post below.
   (The chosen subreddit is remembered automatically after a successful post.) The
   post needs a `reddit-title:` in the frontmatter; if it's missing, ask the user
   for a title and add it before the dry-run.
4. **Dry-run** the chosen platforms (changes nothing; prints per-platform text,
   char counts, and whether an image is attached):
   ```bash
   uv run publish.py --file path/to/post.md --dry-run --platforms <chosen> [--x-transport api|browser] [--subreddit r/<name>]
   ```
   `--platforms` is a comma list from `bluesky,mastodon,threads,linkedin,x,instagram,facebook,youtube,reddit`. `--auto`
   picks the most recently modified `status: ready` file in the posts dir
   (`SOCIAL_POSTS_DIR`, default `~/social-posts`).
5. Read the dry-run back to the user; flag anything truncated or wrong.
6. After the user confirms and the gates are set, post for real (drop `--dry-run`):
   ```bash
   uv run publish.py --file path/to/post.md --platforms <chosen> [--x-transport api|browser] [--subreddit r/<name>]
   ```
   Add `-y` to skip the interactive confirmation once the dry-run is approved. On
   success the file is marked `status: posted`, stamped `published-at`, and its
   Publish Tracking table is filled with per-platform URLs. Report those back.

## Notes

- **X costs money** (pay-per-use): about $0.015 per post, $0.20 if the post
  contains a link. The other platforms are free. Skip X by leaving its creds
  unset or omitting it from `--platforms`.
- **Free X via browser.** Setting `X_TRANSPORT=browser` makes X post for free by
  driving a logged-in browser with Playwright (`x_playwright.py`) instead of the
  paid API — no API keys, no per-post cost, no link surcharge. It needs a one-time
  `uv run x_playwright.py login` to save a session; after that X is offerable when
  that session exists. If X rate-limits the automated login, use
  `uv run x_playwright.py import-session` to import cookies from a browser the user
  already logged into (sidesteps X's login-flow bot detection). Handles text, URLs,
  photos, and videos. `x_playwright.py` also runs standalone
  (`login` / `import-session` / `post --text ... [--media ...]`).
- **Instagram has setup gates.** It needs a Business/Creator account and Meta App
  Review, and has no text-only posts (a post must carry an image or video; video
  posts as a Reel). See README.md.
- **Facebook** posts to a Page you administer (text, image, or video) via the
  Graph API with a non-expiring Page token. Posting to your own Page works in the
  app's development mode; App Review for `pages_manage_posts` is only needed to go
  further. See README.md.
- **YouTube is video-only.** It publishes the post's `video:` as a Short via the
  Data API v3, using `youtube-title:` (≤100 chars, required) as the title and the
  `## YouTube` block as the description. A vertical/square clip ≤180s is
  auto-classified as a Short — the dry-run warns if the video is landscape (it
  still posts, just as a regular video). It's only offered when the post has a
  video. `youtube-privacy:` is `public` (default), `unlisted`, or `private`.
  YouTube uploads the file directly, so it needs no media host. Setup is OAuth
  2.0 (Google Cloud project + `youtube.upload` scope); the per-day quota allows
  ~6 uploads. Free (no per-post cost). See README.md.
- **Reddit posts to a subreddit you pick at post time** (asked via AskUserQuestion;
  see the workflow). It's free via Reddit's API. It needs a `reddit-title:`; the
  post kind is auto-chosen from the file — a `reddit-link:` makes a link post, else
  a `video:`/`image:` makes a media post, else the `## Reddit` block is a self-post
  body (force a kind with `reddit-type:`). Optional `reddit-flair:` is matched to a
  subreddit flair by text (a no-match warns and posts without it). Image/video
  upload directly to Reddit, no media host. The last subreddits used are
  remembered; read them with `uv run publish.py --reddit-recent`.
- **One image OR one video** per post, never both. Bluesky, Mastodon, LinkedIn,
  X, and YouTube take a direct upload. Threads, Instagram, and Facebook fetch the
  media by public HTTPS URL, so they require the optional media host (see README.md).
  Text-only posting (where allowed) needs no host; video needs `ffmpeg` installed.
- If a platform's credentials are missing, it is not offered (and `--check` omits
  it); the script never bulk-posts: one file per run.

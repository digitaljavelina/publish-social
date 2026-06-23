# publish-social

Publish one Markdown post to **Bluesky, Mastodon, Threads, LinkedIn, and X** with a single command. Write the post once (one fenced block per platform, one optional image), preview exactly what will go out, then post everywhere and get the links written back into the file.

Works both as a **Claude Code skill** and as a **standalone command-line tool**. This guide assumes you have never done any of this before and walks every step.

## Platforms at a glance

| Platform | Cost | Images | How hard to set up |
|---|---|---|---|
| Bluesky | Free | Direct upload | Easiest (~5 min) |
| Mastodon | Free | Direct upload | Easy |
| LinkedIn | Free | Direct upload | Needs a one-time API approval (days to weeks) |
| Threads | Free | Needs a public image URL | Most involved (a browser OAuth flow) |
| X / Twitter | **Paid** (~$0.015/post, $0.20 if the post has a link) | Direct upload | Moderate, and costs money |

You do not need all five. Set up only the ones you want; the rest are skipped automatically.

## Contents

1. [How it works](#1-how-it-works-read-this-first)
2. [Install the prerequisites](#2-install-the-prerequisites)
3. [Get publish-social](#3-get-publish-social)
4. [Create your credentials file](#4-create-your-credentials-file)
5. [Connect your platforms](#5-connect-your-platforms)
   - [Bluesky](#bluesky) · [Mastodon](#mastodon) · [LinkedIn](#linkedin) · [Threads](#threads) · [X / Twitter](#x--twitter)
6. [Write your first post](#6-write-your-first-post)
7. [Preview with a dry run](#7-preview-with-a-dry-run)
8. [Publish for real](#8-publish-for-real)
9. [Adding an image](#9-adding-an-image)
10. [Keeping tokens alive](#10-keeping-tokens-alive)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. How it works (read this first)

Everything below makes more sense after this.

- **A post is one Markdown file.** Inside it, the text for each platform sits in its own fenced code block under a `## <Platform>` heading. One file holds all platforms.
- **One command sends it.** `publish.py` reads the file, pulls each platform's text, optionally attaches one image, posts to the platforms you pick, then writes the resulting links back into the file.
- **Credentials live in a `.env` file** that you create once. Each platform needs a token or a small set of values there.
- **Two safety gates.** A file only posts when its frontmatter says `status: ready` **and** `approved: true`. And you always do a **dry run** first, which shows exactly what would post and changes nothing.

You run the scripts with `uv`, which installs everything they need automatically. You never have to manage Python versions or virtual environments.

---

## 2. Install the prerequisites

The only thing you need to install is **uv** (a fast Python runner). It pulls in the right Python and all dependencies on its own.

Open a terminal and run the line for your system:

**macOS or Linux**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell)**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close and reopen the terminal, then confirm it works:
```bash
uv --version
```
If that prints a version number, you are ready.

---

## 3. Get publish-social

Pick one of the two ways to use it.

### Option A — as a command-line tool

```bash
git clone https://github.com/digitaljavelina/publish-social.git
cd publish-social
```
You will run commands like `uv run publish.py ...` from inside this folder.

### Option B — as a Claude Code skill

Copy the folder into your Claude Code skills directory so Claude can drive it:
```bash
git clone https://github.com/digitaljavelina/publish-social.git ~/.claude/skills/publish-social
```
Then in Claude Code, point it at a post file and ask it to publish. The included `SKILL.md` tells Claude how. The rest of this guide still applies, since the credential setup is the same.

---

## 4. Create your credentials file

Your tokens go in a file called `.env`. Keep it in a stable spot so updates never overwrite it:

```bash
mkdir -p ~/.config/publish-social
chmod 700 ~/.config/publish-social
cp .env.example ~/.config/publish-social/.env
chmod 600 ~/.config/publish-social/.env
```

The `chmod` commands make the file readable only by you, which matters because it holds live tokens.

Open `~/.config/publish-social/.env` in a text editor. It lists every setting with comments. You will fill in only the platforms you set up in the next step. Leave the rest blank.

> The tool looks for `.env` in this order: the path in the `PUBLISH_SOCIAL_ENV` environment variable, then `~/.config/publish-social/.env`, then a `.env` next to the scripts. The location above is the recommended one.

> **Tip:** close `.env` in your editor while the tool runs. The scripts sometimes write refreshed tokens back to it, and an editor saving at the same time can clobber that.

---

## 5. Connect your platforms

For the OAuth platforms (Threads, LinkedIn, X), `.env` holds two kinds of value, and mixing them up is the most common mistake:

- **App credentials** (an id and a secret) identify your app. They are *inputs* used to get a token.
- **Tokens** are the *result* of finishing the sign-in flow. They stay blank until you complete it.

An app id is not a user token. Do not paste app credentials into the token slots.

Set up whichever platforms you want, then move on. Each section ends with a quick check you can run to confirm it works.

### Bluesky

The quickest. About five minutes.

1. Log in at [bsky.app](https://bsky.app).
2. Click your avatar, then **Settings**.
3. Go to **Privacy and Security** (or **Account**), then **App Passwords**, then **Add App Password**.
4. Name it `publish-social` and create it. **Copy it immediately** — it is shown only once and looks like `abcd-efgh-ijkl-mnop`.
5. In `.env`, set:
   ```
   BLUESKY_HANDLE=yourhandle.bsky.social
   BLUESKY_APP_PASSWORD=the-app-password-you-copied
   ```

Check it:
```bash
uv run --with atproto --with python-dotenv - << 'PYEOF'
import os
from pathlib import Path
from dotenv import load_dotenv
from atproto import Client
env = os.environ.get("PUBLISH_SOCIAL_ENV", str(Path.home() / ".config/publish-social/.env"))
load_dotenv(Path(env).expanduser())
p = Client().login(os.getenv("BLUESKY_HANDLE"), os.getenv("BLUESKY_APP_PASSWORD"))
print(f"OK Bluesky: logged in as {p.handle}")
PYEOF
```

### Mastodon

You need an account on a Mastodon instance (server). Some instances approve new accounts manually, so create the account first.

1. Log in to your instance in a browser.
2. Go to **Preferences**, then **Development** (often at `https://YOUR-INSTANCE/settings/applications`), then **New Application**.
3. Name it `publish-social`. Under **Scopes**, uncheck everything, then check **`write:statuses`** and **`write:media`**.
   - `write:statuses` lets it post text; `write:media` lets it attach images. This is least-privilege: the token can post but cannot read your account or messages.
4. Submit, open the `publish-social` entry, and copy **Your access token**.
5. In `.env`, set:
   ```
   MASTODON_INSTANCE_URL=https://your.instance
   MASTODON_ACCESS_TOKEN=your-access-token
   ```

Check it (this posts a self-only test that auto-deletes, so nothing public is left behind). A write-only token returns 403 on read endpoints, which is why the check uses a write:
```bash
uv run --with Mastodon.py --with python-dotenv - << 'PYEOF'
import os
from pathlib import Path
from dotenv import load_dotenv
from mastodon import Mastodon
env = os.environ.get("PUBLISH_SOCIAL_ENV", str(Path.home() / ".config/publish-social/.env"))
load_dotenv(Path(env).expanduser())
m = Mastodon(access_token=os.getenv("MASTODON_ACCESS_TOKEN"), api_base_url=os.getenv("MASTODON_INSTANCE_URL"))
s = m.status_post("publish-social test (self-only, auto-deleting)", visibility="direct")
print(f"OK Mastodon write: posted id={s['id']}")
m.status_delete(s["id"]); print("OK cleanup: deleted.")
PYEOF
```

### LinkedIn

This one posts as a **LinkedIn organization / Company Page**, not a personal profile. That requires the **Community Management API**, which is a manual review (roughly one to four weeks). Do the app setup now, request the API, and finish once it is approved.

1. You must be an **admin of a LinkedIn Company Page**. If you do not have one, create one first.
2. Go to the [LinkedIn Developers portal](https://www.linkedin.com/developers/apps/new). Name the app `publish-social`, select your Company Page, add a logo, and create it.
3. On the app's **Settings** tab, click **Verify** next to the page and approve it (as page admin you approve your own app).
4. On the **Auth** tab, copy the **Client ID** and **Client Secret** (use the copy button; the secret is longer than the visible field). Add `https://yourdomain.com/` (any URL you control) as an authorized redirect URL. Put these in `.env`:
   ```
   LINKEDIN_CLIENT_ID=your-client-id
   LINKEDIN_CLIENT_SECRET=your-client-secret
   ```
5. On the **Products** tab, request **Community Management API** and fill out the access form. Then wait for approval.
6. **After approval**, mint a token:
   - Open the [token generator](https://www.linkedin.com/developers/tools/oauth/token-generator), select your app, check **`w_organization_social`** (and `r_organization_social` if offered), and request the token.
   - Copy the **access token** into `LINKEDIN_ACCESS_TOKEN`. If a **refresh token** is shown, copy it into `LINKEDIN_REFRESH_TOKEN` (with it set, the tool renews the access token for you).
7. Find your org URN: open your Company Page while logged in as admin. The admin URL contains a number, `linkedin.com/company/<NUMBER>/admin/`. Your value is:
   ```
   LINKEDIN_ORG_URN=urn:li:organization:<NUMBER>
   ```

There is no clean self-only test on LinkedIn, so treat your first dry run and first real post as the verification.

### Threads

The most involved platform; allow about an hour the first time. Every Threads token comes from a full browser sign-in flow tied to a redirect URL, even for your own account. **You need a public website URL you control** (for example a personal site, or even a free GitHub Pages page) to use as the redirect. If you do not have one, skip Threads.

> **Do this on a laptop in a clean browser, not your phone.** Meta's dashboard saves via background requests that ad blockers and mobile browsers silently block, which produces misleading "form can't be saved" errors. Use desktop Chrome or Safari with extensions off, or a private window.

1. Go to [developers.facebook.com](https://developers.facebook.com/), log in, and register as a developer if prompted.
2. **My Apps**, then **Create App**. For the use case, pick **Access the Threads API**. Name it `publish-social`.
3. Open **Use cases**, then **Access the Threads API**, then **Customize**, and add both `threads_basic` and `threads_content_publish`.
4. Add your own Threads account as a tester: in the dashboard under **App roles → Roles → Add People → Threads Tester**, enter your username. Then on Threads (Settings → Account → Website permissions) accept the invite.
5. Register your domain: under **App settings → Basic**, add your bare domain (e.g. `yourdomain.com`, no `https://`) to **App Domains**. If it refuses to save for a missing Privacy Policy URL, add one (any valid page on your domain).
6. Set the three callback URLs (under **Use cases → Access the Threads API → Customize → Settings**). All three must be filled and be live HTTPS URLs on your domain that return 200:
   | Field | Value |
   |---|---|
   | Valid OAuth Redirect URIs | `https://yourdomain.com/` |
   | Deauthorize callback URL | `https://yourdomain.com/?deauth` |
   | Data Deletion Requests URL | `https://yourdomain.com/?delete` |
7. Put your app id and secret in `.env`:
   ```
   THREADS_APP_ID=your-numeric-app-id
   THREADS_APP_SECRET=your-app-secret
   THREADS_USERNAME=your-threads-username
   ```
8. Authorize in the browser (logged in as your Threads account), swapping in your app id:
   ```
   https://threads.net/oauth/authorize?client_id=YOUR_APP_ID&redirect_uri=https%3A%2F%2Fyourdomain.com%2F&scope=threads_basic,threads_content_publish&response_type=code
   ```
   Click **Allow**. The browser redirects to your site with a `code` in the address bar (`...?code=AQB...#_`). Copy that `code` value (drop the trailing `#_`); it is single-use and expires in about an hour.
9. Exchange the code for a 60-day token and print the two values to add to `.env`:
   ```bash
   uv run --with requests --with python-dotenv - << 'PYEOF'
   import os, requests
   from pathlib import Path
   from dotenv import load_dotenv
   CODE = "PASTE_CODE_HERE"               # the ?code= value, without #_
   REDIRECT = "https://yourdomain.com/"   # must match the authorize redirect exactly
   env = os.environ.get("PUBLISH_SOCIAL_ENV", str(Path.home() / ".config/publish-social/.env"))
   load_dotenv(Path(env).expanduser())
   aid, sec = os.environ["THREADS_APP_ID"], os.environ["THREADS_APP_SECRET"]
   short = requests.post("https://graph.threads.net/oauth/access_token", data={
       "client_id": aid, "client_secret": sec, "grant_type": "authorization_code",
       "redirect_uri": REDIRECT, "code": CODE}).json()
   long = requests.get("https://graph.threads.net/access_token", params={
       "grant_type": "th_exchange_token", "client_secret": sec,
       "access_token": short["access_token"]}).json()
   tok = long["access_token"]
   me = requests.get("https://graph.threads.net/v1.0/me",
                     params={"fields": "id,username", "access_token": tok}).json()
   print(f"Add to .env:\nTHREADS_USER_ID={me['id']}\nTHREADS_ACCESS_TOKEN={tok}")
   PYEOF
   ```
   Paste the two printed lines into `.env`. The tool refreshes this token automatically before it expires.

### X / Twitter

X is the only platform that **costs money**. New developers pay per use: about **$0.015 per post**, or **$0.20 if the post contains a link**. There is no free tier. You buy credits up front and can set a spending cap.

1. Go to the [X Developer Portal](https://developer.x.com/), sign in as the posting account, and create a **Project** and an **App** inside it (name it `publish-social`).
2. In the Developer Console, add a payment method and enable pay-per-use. Set a monthly spending limit while you are there.
3. Open the app's **Settings → User authentication settings** and set **App permissions** to **Read and write**. (This must be done *before* the next step.)
4. On the **Keys and tokens** tab:
   - Copy the **API Key** and **API Key Secret** (the Consumer Keys).
   - Under **Authentication Tokens**, click **Generate** for the **Access Token and Secret**. Generate these *after* step 3, or posting fails with a 403.
5. In `.env`, set (use the OAuth 1.0a values above, not any OAuth 2.0 client/bearer values):
   ```
   X_API_KEY=your-api-key
   X_API_SECRET=your-api-key-secret
   X_ACCESS_TOKEN=your-access-token
   X_ACCESS_TOKEN_SECRET=your-access-token-secret
   X_USERNAME=yourhandle
   ```

Check it (a tiny read; write permission is proven by your first real post):
```bash
uv run --with tweepy --with python-dotenv - << 'PYEOF'
import os
from pathlib import Path
from dotenv import load_dotenv
import tweepy
env = os.environ.get("PUBLISH_SOCIAL_ENV", str(Path.home() / ".config/publish-social/.env"))
load_dotenv(Path(env).expanduser())
a = tweepy.OAuth1UserHandler(os.environ["X_API_KEY"], os.environ["X_API_SECRET"],
                             os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_TOKEN_SECRET"])
print(f"OK X: @{tweepy.API(a).verify_credentials().screen_name}")
PYEOF
```

---

## 6. Write your first post

By default the tool looks for posts in `~/social-posts` (you can change this with the `SOCIAL_POSTS_DIR` setting). Create it and copy the example:

```bash
mkdir -p ~/social-posts
cp examples/example-post.md ~/social-posts/my-first-post.md
```

Open `~/social-posts/my-first-post.md`. A post looks like this:

````markdown
---
status: draft          # change to "ready" when you are ready to publish
approved: false        # change to true after you have reviewed the content
platforms: [bluesky, mastodon]
# image: ./media/example.jpg
# image-alt: "Describe the image for screen readers."
---

# A working title (notes, not published)

## Bluesky

```
Your Bluesky text. Keep it under 300 characters.
```

## Mastodon

```
Your Mastodon text. Up to 500 characters.
```

## Publish Tracking

| Platform | Posted? | Date | URL | Notes |
|---|---|---|---|---|
| Bluesky | ☐ | | | |
| Mastodon | ☐ | | | |
````

Rules to know:
- The text that posts is only what is **inside each fenced code block**. Anything else (the title, notes) is ignored.
- The `## <Platform>` heading is matched by its first word, so `## Bluesky (~270 chars)` also works.
- Character limits: Bluesky 300, X 280, Mastodon and Threads 500, LinkedIn 3000.
- Hashtags work on Bluesky, Mastodon, LinkedIn, and X. On Threads the first hashtag becomes a header topic, so the tool removes hashtags from Threads text for you.
- List the platforms you want in `platforms:`, or pass them on the command line (next steps).

Write your text in each block. Leave `status: draft` and `approved: false` for now.

---

## 7. Preview with a dry run

A dry run shows exactly what would post and changes nothing. Always do this first.

```bash
uv run publish.py --file ~/social-posts/my-first-post.md --dry-run
```

It prints each platform's text, the character count against that platform's limit, and whether an image is attached. Read it carefully and fix anything that looks wrong or truncated.

To dry-run only some platforms, add `--platforms`:
```bash
uv run publish.py --file ~/social-posts/my-first-post.md --dry-run --platforms bluesky,mastodon
```

(If you get a message about `status` or `approved`, that is expected — the gates are covered next.)

---

## 8. Publish for real

1. Once the dry run looks right, open the post and set **both** gates in the frontmatter:
   ```
   status: ready
   approved: true
   ```
   `approved: true` is your own sign-off that the content is reviewed and fine to publish. Keep this gate; it is what stops half-finished drafts from going out.
2. Publish (drop `--dry-run`):
   ```bash
   uv run publish.py --file ~/social-posts/my-first-post.md
   ```
   It asks for confirmation before posting. Answer `y`. (Add `-y` to skip that prompt once you have approved the dry run.)
3. On success it sets `status: posted`, stamps the time, and fills the Publish Tracking table with the live links.

Handy variants:
```bash
# Auto-pick the most recently edited "ready" post in your posts folder
uv run publish.py --auto --platforms bluesky,mastodon

# If a platform's credentials are missing, it is skipped, not an error
```

> **First time:** keep your first real post low-stakes, and remember X charges per post if you include it.

---

## 9. Adding an image

One image per post, attached to every platform that takes one. In the frontmatter:

```yaml
image: ./media/my-photo.jpg
image-alt: "A short, plain-language description for screen readers."
```

The path is relative to the post file (put images in a `media/` folder next to it). The tool automatically shrinks the image to fit every platform's size cap, so you do not have to.

- **Bluesky, Mastodon, LinkedIn, and X** take a direct upload. Nothing extra to set up.
- **Threads is different.** Its API does not accept an upload; it fetches the image from a public HTTPS URL. So images on Threads need a public image host. Any host works (a small static file server, a cheap VPS, object storage, or a tunnel that exposes a local folder). Once you have one, set:
  ```
  IMAGE_HOST_BASE_URL=https://img.example.com   # the public address
  IMAGE_HOST_SSH=your-image-host                 # an SSH host alias the tool uploads to
  IMAGE_HOST_PATH=~/images                        # the folder that host serves
  ```
  The tool copies the image there and hands Threads the resulting link. **If you do not post images to Threads, you can ignore this entirely.**

### Optional: auto-find an image

`fetch_image.py` finds a fitting photo from [Pexels](https://www.pexels.com/api/) (free; needs a `PEXELS_API_KEY` in `.env`). It searches first (downloads previews, changes nothing), then applies the one you pick:

```bash
uv run fetch_image.py --file ~/social-posts/my-first-post.md --query "your topic" --count 4
uv run fetch_image.py --file ~/social-posts/my-first-post.md --apply <photo_id>
```

---

## 10. Keeping tokens alive

| Platform | Expires? | What you do |
|---|---|---|
| Bluesky | No | Nothing |
| Mastodon | No | Nothing |
| X | No | Nothing (regenerate only if you change app permissions or a key leaks) |
| Threads | 60 days | Nothing — the tool refreshes it for you near expiry |
| LinkedIn | 60-day access / 365-day refresh | Nothing if you set `LINKEDIN_REFRESH_TOKEN`; otherwise re-mint the token about every 55 days |

If a token ever leaks, revoke it in that platform's app settings, re-issue it, and update `.env`.

---

## 11. Troubleshooting

| Problem | Fix |
|---|---|
| `File status is '...'` or `File does not have approved: true` | Set both `status: ready` and `approved: true` after reviewing the post |
| A platform is skipped silently | Its credentials are missing from `.env`; add them, or remove it from `--platforms` |
| Bluesky: `Invalid handle or password` | Use the app password, not your account password |
| Mastodon: `403 ... outside the authorized scopes` | Not an error by itself — a write-only token cannot call read endpoints. If the 403 is on image upload, the token is missing `write:media`; recreate it with both scopes |
| Threads: `media url is not accessible` | The image URL must be public HTTPS. A LAN or private URL will not work; test it from another network |
| Threads: authorize page shows an access error | The tester invite was not accepted. On Threads: Settings → Account → Website permissions, accept it |
| Threads: code exchange fails | The code is single-use and expires in about an hour. Re-run the authorize URL and drop the trailing `#_` |
| LinkedIn: token generator does not list `w_organization_social` | The Community Management API approval has not come through yet; wait and recheck |
| LinkedIn: `401 INVALID_ACCESS_TOKEN` but the token looks active | You pasted the refresh token into the access-token slot. Put the access token in `LINKEDIN_ACCESS_TOKEN` |
| X: `403` about oauth1 app permissions | The access token was generated before the app was set to Read and write. Set write, then **regenerate** the token |
| X: a post cost $0.20 instead of $0.015 | Expected — X charges more for posts that contain a link |
| `ModuleNotFoundError` | Run the scripts with `uv` (as shown), which installs dependencies automatically |

---

## License

[MIT](LICENSE). Contributions and forks welcome.

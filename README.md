# publish-social

Publish one Markdown post to **Bluesky, Mastodon, Threads, LinkedIn, X, Instagram, Facebook, YouTube (Shorts), and Reddit** with a single command. Write the post once (one fenced block per platform, one optional image or video), preview exactly what will go out, then post everywhere and get the links written back into the file.

Works both as a **Claude Code skill** and as a **standalone command-line tool**. This guide assumes you have never done any of this before and walks every step.

## Platforms at a glance

| Platform | Cost | Media | How hard to set up |
|---|---|---|---|
| Bluesky | Free | Direct upload | Easiest (~5 min) |
| Mastodon | Free | Direct upload | Easy |
| LinkedIn | Free | Direct upload | Needs a one-time API approval (days to weeks) |
| Threads | Free | Fetched from a public URL | Most involved (a browser OAuth flow) |
| X / Twitter | **Paid** API (~$0.015/post, $0.20 with a link) — or **free** via a logged-in browser | Direct upload | Moderate (API), or a one-time browser login (free) |
| Instagram | Free | Fetched from a public URL | Hard: needs a Business account + Meta App Review |
| Facebook | Free | Fetched from a public URL | Moderate: a Page you admin; non-expiring token |
| YouTube | Free | Direct upload (**video required**) | Moderate: a Google Cloud OAuth app |
| Reddit | Free | Direct upload | Easy: a one-time "script" app |

Each post carries **one image or one video** (never both). You do not need all nine platforms; set up only the ones you want, and the rest are skipped automatically. Instagram has an extra gate (App Review) described in its section. **YouTube is video-only** — it publishes the post's video as a Short, so it's only offered when the post has a `video:`. **Reddit** posts to one subreddit you pick at post time (with a required title), and submits a text, link, image, or video post depending on what the file has.

## Contents

1. [How it works](#1-how-it-works-read-this-first)
2. [Install the prerequisites](#2-install-the-prerequisites)
3. [Get publish-social](#3-get-publish-social)
4. [Create your credentials file](#4-create-your-credentials-file)
5. [Connect your platforms](#5-connect-your-platforms)
   - [Bluesky](#bluesky) · [Mastodon](#mastodon) · [LinkedIn](#linkedin) · [Threads](#threads) · [X / Twitter](#x--twitter) · [Instagram](#instagram) · [Facebook](#facebook) · [YouTube](#youtube) · [Reddit](#reddit)
6. [Write your first post](#6-write-your-first-post)
7. [Preview with a dry run](#7-preview-with-a-dry-run)
8. [Publish for real](#8-publish-for-real)
9. [Adding an image or video](#9-adding-an-image-or-video)
10. [Keeping tokens alive](#10-keeping-tokens-alive)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. How it works (read this first)

Everything below makes more sense after this.

- **A post is one Markdown file.** Inside it, the text for each platform sits in its own fenced code block under a `## <Platform>` heading. One file holds all platforms.
- **One command sends it.** `publish.py` reads the file, pulls each platform's text, optionally attaches one image or video, posts to the platforms you pick, then writes the resulting links back into the file.
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

**For video posts, also install ffmpeg** (text-only and image-only posting do not need it):
- macOS: `brew install ffmpeg`
- Debian/Ubuntu: `sudo apt install ffmpeg`
- Windows: `winget install Gyan.FFmpeg`

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

For the OAuth platforms (Threads, LinkedIn, X, Instagram, Facebook, YouTube), `.env` holds two kinds of value, and mixing them up is the most common mistake:

- **App credentials** (an id and a secret) identify your app. They are *inputs* used to get a token.
- **Tokens** are the *result* of finishing the sign-in flow. They stay blank until you complete it.

An app id is not a user token. Do not paste app credentials into the token slots.

> **Use Google Chrome for the browser sign-in steps, not Safari.** Threads, LinkedIn, X, Instagram, Facebook, and YouTube authorize in a browser, and Safari's privacy defaults (tracking prevention, popup blocking, and how it handles `localhost` redirects) quietly break these flows: popups vanish, Meta dashboards fail to save, and redirect sign-ins hang. Do them in Chrome with extensions off (or a private window). Bluesky and Mastodon are plain forms and work anywhere; the YouTube helper opens Chrome for you.

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
   - In **Chrome**, open the [token generator](https://www.linkedin.com/developers/tools/oauth/token-generator), select your app, check **`w_organization_social`** (and `r_organization_social` if offered), and request the token.
   - Copy the **access token** into `LINKEDIN_ACCESS_TOKEN`. If a **refresh token** is shown, copy it into `LINKEDIN_REFRESH_TOKEN` (with it set, the tool renews the access token for you).
7. Find your org URN: open your Company Page while logged in as admin. The admin URL contains a number, `linkedin.com/company/<NUMBER>/admin/`. Your value is:
   ```
   LINKEDIN_ORG_URN=urn:li:organization:<NUMBER>
   ```

There is no clean self-only test on LinkedIn, so treat your first dry run and first real post as the verification.

### Threads

The most involved platform; allow about an hour the first time. Every Threads token comes from a full browser sign-in flow tied to a redirect URL, even for your own account. **You need a public website URL you control** (for example a personal site, or even a free GitHub Pages page) to use as the redirect. If you do not have one, skip Threads.

> **Do this on a laptop in a clean browser, not your phone.** Meta's dashboard saves via background requests that ad blockers and mobile browsers silently block, which produces misleading "form can't be saved" errors. Use desktop **Google Chrome** with extensions off, or a private window (avoid Safari; its tracking protection is one of the blockers).

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
8. Authorize in **Chrome** (logged in as your Threads account), swapping in your app id:
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

X has two ways to post. The **API** (below) is the default but **costs money**: about **$0.015 per post**, or **$0.20 if the post contains a link** — there is no free API tier for posting. Or you can post for **free** by driving a logged-in browser instead of the API — see **[Free posting via a browser](#x-free-posting-via-a-browser-no-api-cost)** at the end of this section. The two are interchangeable; pick one with `X_TRANSPORT`.

The API setup is below; skip to the browser option if you'd rather not pay or deal with developer-portal approval.

**Character limit depends on your subscription level.** A free X account caps a post at **280 characters**; a paid **X Premium** subscription raises that to **25,000**. publish-social treats this as a soft warning only (X enforces the real cap server-side) through `CHAR_LIMITS["x"]` in `publish.py`. It ships set to **25,000** for a Premium account, so if you are on the **free tier, lower it to 280** so the dry run flags overlong posts.

1. In **Chrome**, go to the [X Developer Portal](https://developer.x.com/), sign in as the posting account, and create a **Project** and an **App** inside it (name it `publish-social`).
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

#### X: free posting via a browser (no API cost)

Instead of the paid API, `x_playwright.py` posts to X by driving a real **logged-in browser** with [Playwright](https://playwright.dev/) — it types the text, attaches one photo or video, and clicks **Post**, exactly as you would by hand. No API keys, no per-post charge, and no link surcharge. It handles **text, URLs, photos, and videos** — all verified end to end, including a 36 MB `.mov`.

The trade-off: it depends on X's web UI (a major redesign could require updating the selectors in `x_playwright.py`), and it needs a saved login session. The interactive `login` below is the simplest way to create one, but X aggressively rate-limits automated logins ("We've temporarily limited your login"); if you hit that, use `import-session` (step 2) instead, which sidesteps the login form entirely.

1. **Install the browser** (one time). Playwright resolves automatically through `uv`; install its Chromium once:
   ```bash
   uv run --with playwright playwright install chromium
   ```
   (If your environment already provides a managed Chromium, set `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/path/to/chromium` instead of installing.)

2. **Log in once.** This opens a real browser window — sign in to X normally (including 2FA), and the session is saved to `~/.config/publish-social/x-state.json`:
   ```bash
   uv run x_playwright.py login
   ```
   The saved file holds live login cookies — treat it like a password and never commit it. (`.gitignore` already excludes `.env`; keep this file in `~/.config/publish-social/` too.)

   **If X rate-limits the automated login** ("We've temporarily limited your login"), skip the login form entirely. Log in to x.com in your normal browser, then import those cookies into the same saved session. X defends its login flow against automation but not an already-valid session, so this sidesteps the block:
   ```bash
   # Paste the two cookies that matter (auth_token and ct0). Find them in your
   # browser's DevTools > Application/Storage > Cookies > https://x.com.
   uv run x_playwright.py import-session
   # …or point at a cookies export (Netscape cookies.txt or a JSON array):
   uv run x_playwright.py import-session --cookies-file ~/Downloads/x.com_cookies.txt
   ```

3. **Turn on the browser transport.** Either set the default in `.env`:
   ```
   X_TRANSPORT=browser
   ```
   …or choose it per run with the `--x-transport` flag (which overrides `.env`), e.g. `uv run publish.py --file post.md --platforms x --x-transport browser`. Now `publish.py` routes X through the browser for free. In browser mode X is "configured" once the saved session exists (no API keys needed), the dry run shows a free-posting note instead of the cost warning, and the $0.20 link double-confirm is skipped because nothing is billed.

   Precedence is **`--x-transport` flag → `X_TRANSPORT` in `.env` → default `api`**, so you can keep one as your standing default and override it for a single post. (Running through the Claude skill, you'll simply be asked which to use when both are set up.)

You can also post directly, outside `publish.py`:
```bash
uv run x_playwright.py post --text "hello, posted free from a browser"
uv run x_playwright.py post --text "with a photo" --media ./photo.jpg
uv run x_playwright.py post --text "with a clip"  --media ./clip.mp4
```
Add `--dry-run` to validate without posting, or `--headed` to watch the browser work. If posting ever fails with a "session expired" message, just run `x_playwright.py login` again.

> Switching back to the paid API is just removing `X_TRANSPORT=browser` (or setting it to `api`) — your API creds keep working.

### Instagram

Instagram publishes through the Meta Graph API (the **Instagram API with Instagram Login** product). Its dashboard shows three cards — *Add permissions*, *Generate access tokens*, *Configure webhooks* — but you only need the first two, and there's a one-click way to get a token that skips the OAuth/redirect flow. Three things gate it:

- A **Business or Creator account** (personal accounts cannot use the publishing API). Convert yours in the Instagram app under Settings → Account type.
- The **`instagram_business_content_publish` permission**. Posting to your *own* account works in Development mode (with your account added as a Tester); going public needs **Meta App Review** (2–4 weeks, with a screencast).
- **Media on every post** — Instagram has no text-only posts; video posts go out as Reels.

1. At [developers.facebook.com](https://developers.facebook.com/), **add the Instagram product to the app you already made for Threads, or create a new Business app**, and open **API setup with Instagram Login**. It authenticates straight through Instagram and **needs no Facebook Page and no Pages API** (that's the older Facebook-Login path, which this skill does not use).
2. **Add the publishing permission.** The permissions card lists messaging permissions (`manage_comments`, `manage_messages`) by default — ignore those; they are not for posting. Click **Go to permissions and features** and ensure `instagram_business_basic` and `instagram_business_content_publish` are added. **Skip the *Configure webhooks* card entirely** — its **Callback URL is not the OAuth redirect URI**, and webhooks are not needed to publish.
3. **Add your account as an Instagram Tester, and accept the invite.** Being an admin of the account is not enough, and skipping the accept step causes an **"Insufficient developer role"** error. Add `@yourhandle` under the app's **Roles → Instagram Testers**, then accept the pending invite at `https://www.instagram.com/accounts/manage_access/` (logged in as that account). Role changes take ~5–10 minutes to propagate.
4. **Generate the token the easy way:** on the *Generate access tokens* card, click **Add account**, log in, click **Allow**, then **Generate token** — it opens a one-time popup with the token; copy it immediately (shown once). That is your `INSTAGRAM_ACCESS_TOKEN` (60-day), and the number under the account name is your `INSTAGRAM_USER_ID` — no redirect URI needed. (If Generate token returns you to the dashboard with no popup, an ad blocker or Safari ate it; retry in **Chrome** with extensions off.) Then confirm your user id:
   ```bash
   uv run --with requests - << 'PYEOF'
   import requests
   TOKEN = "PASTE_GENERATED_TOKEN"
   me = requests.get("https://graph.instagram.com/v23.0/me",
                     params={"fields": "user_id,username", "access_token": TOKEN}).json()
   print(me)   # use the user_id value for INSTAGRAM_USER_ID
   PYEOF
   ```
   Put `user_id` in `INSTAGRAM_USER_ID` and the token in `INSTAGRAM_ACCESS_TOKEN`. The tool refreshes the token automatically, and `INSTAGRAM_APP_ID`/`INSTAGRAM_APP_SECRET` are not needed for this path. Instagram fetches media by public URL, so it also needs the media host (see [Adding an image or video](#9-adding-an-image-or-video)).

> **Prefer scripting it?** You can use the manual OAuth flow instead, but then the `redirect_uri` in your authorize URL must exactly match an entry under **Instagram → API setup with Instagram Login → Business login settings → OAuth redirect URIs** (this is a different field from the webhook Callback URL). The one-click token above avoids that entirely.

### Facebook

Posts to a **Facebook Page** (not a personal profile). It handles text, image, and video, and the Page token is **long-lived (non-expiring)**, so there's nothing to renew. You must be an **admin of the Page**, and you can add this to the same Meta app you use for Threads/Instagram or a separate one.

1. At [developers.facebook.com](https://developers.facebook.com/), add the **Facebook Login** product to your app. Copy the **App ID** and **App Secret** (App settings → Basic) into `.env` (`FACEBOOK_APP_ID`, `FACEBOOK_APP_SECRET`).
2. The flow needs `pages_show_list`, `pages_read_engagement`, and `pages_manage_posts`. Posting to a Page **you administer** works while the app is in development; App Review for `pages_manage_posts` is only needed to go beyond your own Pages.
3. In **Chrome**, open the [Graph API Explorer](https://developers.facebook.com/tools/explorer/): select your app, set **User Token**, add those three scopes, click **Generate Access Token → Continue**, and on the **"Choose the Pages…"** screen pick **Opt in to all current and future Pages**. Copy the token (short-lived, ~1–2h). Then exchange it for your Page's non-expiring token: set `USER_TOKEN` below and run it (leave `PAGE_ID` blank the first time).

```bash
uv run --with requests --with python-dotenv - << 'PYEOF'
import os, requests
from pathlib import Path
from dotenv import load_dotenv
USER_TOKEN = "PASTE_SHORT_LIVED_USER_TOKEN"   # from Graph API Explorer; expires ~1-2h
PAGE_ID = ""                                  # set this only if no Page is found (see note)
env = os.environ.get("PUBLISH_SOCIAL_ENV", str(Path.home() / ".config/publish-social/.env"))
load_dotenv(Path(env).expanduser())
G = "https://graph.facebook.com/v23.0"
aid, sec = os.environ.get("FACEBOOK_APP_ID", ""), os.environ.get("FACEBOOK_APP_SECRET", "")
ex = requests.get(f"{G}/oauth/access_token", params={
    "grant_type": "fb_exchange_token", "client_id": aid, "client_secret": sec,
    "fb_exchange_token": USER_TOKEN}).json()
if "access_token" not in ex:
    raise SystemExit(f"Exchange failed: {ex}")
ll = ex["access_token"]
if PAGE_ID:
    p = requests.get(f"{G}/{PAGE_ID}", params={"fields": "name,access_token", "access_token": ll}).json()
    if "access_token" not in p:
        raise SystemExit(f"Couldn't get a token for Page {PAGE_ID}: {p}")
    print(f"\nPage: {p['name']}\nFACEBOOK_PAGE_ID={p['id']}\nFACEBOOK_PAGE_ACCESS_TOKEN={p['access_token']}")
else:
    pages = requests.get(f"{G}/me/accounts", params={"access_token": ll}).json().get("data", [])
    for p in pages:
        print(f"\nPage: {p['name']}\nFACEBOOK_PAGE_ID={p['id']}\nFACEBOOK_PAGE_ACCESS_TOKEN={p['access_token']}")
    if not pages:
        print("No Pages via /me/accounts - set PAGE_ID above and re-run (see note).")
PYEOF
```

Paste the `FACEBOOK_PAGE_ID` and `FACEBOOK_PAGE_ACCESS_TOKEN` for your Page into `.env`. The Page token does not expire. Facebook fetches image/video by public URL, so media posts need the media host (text-only Page posts don't).

> **No Pages returned?** Pages on Meta's **New Pages Experience** (the ones you "switch into") usually don't appear in `/me/accounts`, even with the right permissions. Find your **Page ID** (the Page's **About → Page transparency**, or Meta Business Suite), set it as `PAGE_ID` in the snippet, and re-run. it then queries that Page's token directly. This is the most common Facebook snag.

### Reddit

Reddit is **free** and quick to set up. Unlike the microblog platforms, a Reddit post goes to **one subreddit** (chosen at post time) and always has a **title** plus one of: a text body, a link, an image, or a video.

1. Make sure you're logged in to the Reddit account you'll post from, then go to [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) and click **Create another app…**.
2. Choose type **script**. Give it a name (`publish-social`); set the redirect uri to `http://localhost:8080` (required by the form but unused for script apps). Click **create app**.
3. Read the two values off the app card:
   - The **client id** is the short string just under the app's name (under "personal use script").
   - The **client secret** is the **secret** field.
4. In `.env`, set those plus the posting account's username and password:
   ```
   REDDIT_CLIENT_ID=your-client-id
   REDDIT_CLIENT_SECRET=your-client-secret
   REDDIT_USERNAME=your-reddit-username
   REDDIT_PASSWORD=your-reddit-password
   REDDIT_USER_AGENT=publish-social/1.0 by u/your-reddit-username
   ```
   (`REDDIT_USER_AGENT` is optional but recommended — Reddit asks API clients to identify themselves. A default is used if you leave it blank. If the account uses 2FA, append the 6-digit code to the password as `password:123456` when you post, or use an app password.)

Check it:
```bash
uv run --with praw --with python-dotenv - << 'PYEOF'
import os
from pathlib import Path
from dotenv import load_dotenv
import praw
env = os.environ.get("PUBLISH_SOCIAL_ENV", str(Path.home() / ".config/publish-social/.env"))
load_dotenv(Path(env).expanduser())
r = praw.Reddit(client_id=os.environ["REDDIT_CLIENT_ID"], client_secret=os.environ["REDDIT_CLIENT_SECRET"],
                username=os.environ["REDDIT_USERNAME"], password=os.environ["REDDIT_PASSWORD"],
                user_agent=os.environ.get("REDDIT_USER_AGENT") or "publish-social/1.0")
print(f"OK Reddit: logged in as u/{r.user.me()}")
PYEOF
```

**How a Reddit post is built.** Add `reddit` to the post's `platforms`, set a **`reddit-title:`** in the frontmatter, and choose the subreddit when you post:
- The **subreddit** comes from `--subreddit r/<name>` (the skill asks for it and offers your recent subreddits), or a `reddit-subreddit:` frontmatter field.
- The **post kind** is picked automatically from the file: a `reddit-link:` (or `link:`) makes a **link** post; otherwise a `video:`/`image:` makes a **video/image** post; otherwise the `## Reddit` block is a **self-post** body. Force a kind with `reddit-type: text|link|image|video`.
- Optional **`reddit-flair:`** is matched to one of the subreddit's flairs by text (many subreddits require a flair). If nothing matches, the post still goes out, without flair, and the dry run/post warns.
- The subreddits you post to are **remembered** (most-recent-first) in `~/.config/publish-social/reddit-subreddits.json`; `uv run publish.py --reddit-recent` prints the last few, which is how the skill offers them as quick choices.

```bash
# Post a self-text post to r/test (after a dry run):
uv run publish.py --file path/to/post.md --platforms reddit --subreddit r/test
```

### YouTube

**Video-only.** YouTube publishes the post's `video:` as a **Short** (a vertical or square clip ≤180 seconds is classified as one automatically — there's no separate "Short" switch), uploading the file directly, so it needs **no media host**. The `youtube-title:` frontmatter field is the title; the `## YouTube` block is the description. It's only offered when the post has a video.

Auth is Google OAuth 2.0. The **refresh token** is the durable credential — `publish.py` mints a ~1-hour access token from it on each run (and manages `YOUTUBE_ACCESS_TOKEN` / `YOUTUBE_TOKEN_EXPIRES_AT` itself).

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a **new project** (project picker at the top, then **New Project**), then enable the **YouTube Data API v3** at [console.cloud.google.com/apis/library/youtube.googleapis.com](https://console.cloud.google.com/apis/library/youtube.googleapis.com). This is **not** App Hub (a separate infrastructure product); you only need this one API.
2. Configure the consent screen, now called the **Google Auth Platform** ([console.cloud.google.com/auth/overview](https://console.cloud.google.com/auth/overview)): set **Branding** (app name + support email), **Audience** (user type **External**, add your Google account as a **Test user**), and **Data access** (add the scope `https://www.googleapis.com/auth/youtube.upload`).
3. Create the OAuth client under **Google Auth Platform → Clients** ([console.cloud.google.com/auth/clients](https://console.cloud.google.com/auth/clients)): **Create client**, application type **Desktop app**. Copy the **Client ID** and **Client secret** into `.env` as `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET`.
4. **Create your YouTube channel** (skip if you already have one). Uploads post to a channel, so the account you authorize next needs one. In Chrome, go to [youtube.com](https://www.youtube.com/) signed in as that account, click your profile picture, then **Create a channel** and confirm. It is ready immediately.
5. Mint a refresh token with the helper below. It opens Google's consent page in Chrome, catches the redirect on `localhost`, and prints `YOUTUBE_REFRESH_TOKEN`. Sign in as the channel-owning account. Run it after the client id/secret are in `.env`:

```bash
uv run --with requests --with python-dotenv - << 'PYEOF'
import os, sys, subprocess, webbrowser, urllib.parse, http.server, requests
from pathlib import Path
from dotenv import load_dotenv
env = os.environ.get("PUBLISH_SOCIAL_ENV", str(Path.home() / ".config/publish-social/.env"))
load_dotenv(Path(env).expanduser())
cid, sec = os.environ.get("YOUTUBE_CLIENT_ID", ""), os.environ.get("YOUTUBE_CLIENT_SECRET", "")
if not (cid and sec):
    raise SystemExit("Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in .env first.")
PORT = 8765
redirect = f"http://localhost:{PORT}/"
auth = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": cid, "redirect_uri": redirect, "response_type": "code",
    "scope": "https://www.googleapis.com/auth/youtube.upload",
    "access_type": "offline", "prompt": "consent"})
# Open the consent page in Chrome, not the default browser: Safari's privacy
# settings can block the localhost redirect below. Override with BROWSER_APP.
browser_app = os.environ.get("BROWSER_APP", "Google Chrome")
print(f"Opening {browser_app} to authorize. If it does not open, paste this URL into Chrome:\n{auth}")
try:
    if sys.platform == "darwin":
        subprocess.run(["open", "-a", browser_app, auth], check=True)
    else:
        webbrowser.open(auth)
except Exception:
    webbrowser.open(auth)  # fall back to the default browser
code = {}
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.urlparse(self.path).query
        code["v"] = urllib.parse.parse_qs(q).get("code", [""])[0]
        self.send_response(200); self.end_headers()
        self.wfile.write(b"Authorized. You can close this tab.")
    def log_message(self, *a): pass
http.server.HTTPServer(("localhost", PORT), H).handle_request()
if not code.get("v"):
    raise SystemExit("No authorization code received.")
tok = requests.post("https://oauth2.googleapis.com/token", data={
    "code": code["v"], "client_id": cid, "client_secret": sec,
    "redirect_uri": redirect, "grant_type": "authorization_code"}).json()
if "refresh_token" not in tok:
    raise SystemExit(f"No refresh token returned: {tok}. Remove the app's access at "
                     "https://myaccount.google.com/permissions and re-run (prompt=consent forces one).")
print(f"\nYOUTUBE_REFRESH_TOKEN={tok['refresh_token']}")
PYEOF
```

Paste the `YOUTUBE_REFRESH_TOKEN` into `.env`. Two things to know:

- **"Testing" mode expires refresh tokens after 7 days.** While the **Publishing status** is *Testing* (Google Auth Platform → Audience), Google expires the refresh token weekly. For hands-off posting, set it to **In production** with **Publish app** (no Google review is required for a single-user app using only your own account).
- **Quota.** The default YouTube Data API quota (10,000 units/day) covers about **6 uploads per day** (an upload costs ~1,600 units). That's plenty for one-file-per-run, but it's a real ceiling.

#### Publish a Short — step by step

Once the credentials above are in `.env`, here is the whole flow from clip to live Short. This posts to YouTube alongside any other platforms in the same file.

1. **Have a Short-shaped clip.** Vertical or square, ≤180 seconds. The tool transcodes to H.264 MP4 and fits it under every platform's caps, but it never changes the aspect ratio or trims length — a landscape or >180s clip still uploads, just as a regular video, not a Short.

2. **Write the post file.** Put the video next to it and add the YouTube fields. The `youtube-title:` is the title; the `## YouTube` block is the description:

   ````markdown
   ---
   status: draft
   approved: false
   platforms: [youtube]
   video: ./media/clip.mp4
   video-alt: "What's happening in the clip, for screen readers."
   youtube-title: "My first Short from publish-social"
   youtube-privacy: public        # public | unlisted | private (default public)
   ---

   ## YouTube

   ```
   The description shown under the Short. Hashtags are fine here. #demo
   ```

   ## Publish Tracking

   | Platform | Posted? | Date | URL | Notes |
   |---|---|---|---|---|
   | YouTube | ☐ | | | |
   ````

3. **See what's offerable.** Confirm YouTube has credentials and the file has a video + `## YouTube` block:

   ```bash
   uv run publish.py --file ./media/my-short.md --check
   ```

   YouTube appears in the `OFFER:` line only when both are true. (It is never offered for a post with no `video:`.)

4. **Dry run.** Changes nothing; prints the title, privacy, description, and a warning if the clip is landscape (so you catch a non-Short before it goes out):

   ```bash
   uv run publish.py --file ./media/my-short.md --platforms youtube --dry-run
   ```

5. **Approve the gates.** After reviewing the dry run, set both in the frontmatter — this is the human sign-off that lets the file post:

   ```yaml
   status: ready
   approved: true
   ```

6. **Publish for real.** Drop `--dry-run`; add `-y` to skip the confirm prompt once the dry run looks right:

   ```bash
   uv run publish.py --file ./media/my-short.md --platforms youtube
   ```

   The clip uploads directly to YouTube (no media host needed). On success the file is marked `status: posted`, stamped `published-at`, and the YouTube row in Publish Tracking is filled with the Short's URL (`https://www.youtube.com/shorts/<id>`).

To post the same clip to more networks in one run, list them together — e.g. `platforms: [youtube, bluesky, instagram]` and `--platforms youtube,bluesky,instagram` — and give each its own `## <Platform>` block. The single `video:` is reused everywhere.

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
- Character limits: Bluesky 300, X 280 on the free tier (25,000 with X Premium), Mastodon and Threads 500, LinkedIn 3000, Instagram 2200, Facebook effectively unlimited, YouTube description 5000 (title from `youtube-title:`, capped at 100), Reddit body 40,000 (title from `reddit-title:`, capped at 300).
- Hashtags work on Bluesky, Mastodon, LinkedIn, X, Instagram, Facebook, and YouTube. On Threads the first hashtag becomes a header topic, so the tool removes hashtags from Threads text for you.
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

To see which platforms are actually ready before you post, run `--check`. It lists each platform with whether its credentials are present and whether your post has a text block for it, ending with an `OFFER:` line of the ones good to go:
```bash
uv run publish.py --file ~/social-posts/my-first-post.md --check
```
A platform with no credentials in `.env` is reported as not offerable, so this is the quickest way to confirm your setup. (Running publish-social as a Claude Code skill, the assistant uses this to ask you which of the ready platforms to post to.)

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

## 9. Adding an image or video

A post carries one image **or** one video (never both). For an image, attach it to every platform that takes one. In the frontmatter:

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

### Posting a video instead

Use a `video:` field exactly like `image:` (one or the other, never both):

```yaml
video: ./media/my-clip.mp4
video-alt: "A short description of the video."
```

- Requires **ffmpeg** (see [step 2](#2-install-the-prerequisites)). The tool checks the clip and, if needed, transcodes it to fit the strictest platform — **Bluesky: H.264 MP4, under 100 MB, 3 minutes or less**. A clip over 3 minutes is rejected so you can trim it; other formats (`.mov`, HEVC, `.webm`) are converted automatically.
- **Bluesky, Mastodon, LinkedIn, X, YouTube, and Reddit** upload the video directly. **Threads, Instagram, and Facebook** fetch it from the public media host, so the same `IMAGE_HOST_*` settings apply — and the host must serve video files (`.mp4`, `.mov`, `.m4v`, `.webm`), not only images.
- **YouTube is video-only and posts the clip as a Short.** Add a `youtube-title:` (the title, ≤100 chars) and put the description in the `## YouTube` block; a vertical/square clip ≤180s auto-qualifies as a Short (the dry run warns on landscape). Set visibility with `youtube-privacy:` (`public`, `unlisted`, or `private`; default `public`).
- **Reddit** turns a `video:` or `image:` into a video/image post to your chosen subreddit (with the required `reddit-title:`); a `reddit-link:` makes a link post instead. It uploads media directly, so it needs no media host.
- **Instagram** posts video as a **Reel**, and it requires the media host.

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
| Instagram | 60 days | Nothing — the tool refreshes it for you near expiry |
| Facebook | No (long-lived Page token) | Nothing; re-mint only if you change your password, revoke the app, or the Page admin changes |
| YouTube | Refresh token durable in production (7 days while the app is in "Testing") | Nothing — the tool mints an access token from the refresh token each run; set the OAuth app to "In production" so the refresh token doesn't expire weekly |
| Reddit | No (username + password) | Nothing; update `.env` only if you change your password (or rotate the script app's secret) |

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
| Instagram: post rejected as text-only | Instagram has no text-only posts; add an `image:` or `video:` |
| Reddit: `needs a target subreddit` / `needs a reddit-title:` | Pass `--subreddit r/<name>` (the skill asks) and set `reddit-title:` in the frontmatter |
| Reddit: `SUBMIT_VALIDATION_FLAIR_REQUIRED` or flair error | The subreddit requires a flair; set `reddit-flair:` to one of its flair names (exact text) |
| Reddit: `RATELIMIT` / "you're doing that too much" | Reddit throttles new or low-karma accounts; wait the stated time and retry |
| Reddit: login fails with 2FA on | Append the 6-digit code to the password as `password:123456`, or use an app password |
| Instagram: media URL not accessible, or pull fails | The media host must serve the file over public HTTPS (and serve video extensions, for video). Test the URL from another network |
| YouTube: not offered / `requires a video` | YouTube is video-only — add a `video:` to the post. It's only listed once the post has one |
| YouTube: `youtube needs a youtube-title:` | Add a `youtube-title:` field (the Short's title, ≤100 chars) to the frontmatter |
| YouTube: token refresh fails after ~a week | The OAuth app is still in "Testing", which expires refresh tokens after 7 days. Set it to "In production" (Google Auth Platform → Audience → Publish app) and re-mint the refresh token |
| YouTube: posted as a normal video, not a Short | The clip is landscape or over 180s. Shorts need a vertical/square video ≤180s (the dry run warns about this) |
| YouTube: `quota` / `uploadLimitExceeded` | The daily Data API quota (~6 uploads) is spent; wait for the reset or request more quota in the Cloud Console |
| `ffmpeg/ffprobe not found` | Install ffmpeg (see step 2); it is required for video |
| Video rejected as too long (`... the cap is 180s`) | The clip is over Bluesky's 3-minute limit; trim it (the tool never auto-trims) |
| `Post sets both image: and video:` | A post takes one or the other, not both; remove one |
| `ModuleNotFoundError` | Run the scripts with `uv` (as shown), which installs dependencies automatically |

---

## License

[MIT](LICENSE). Contributions and forks welcome.

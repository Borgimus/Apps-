# Complete Beginner's Guide — Run Trading Sessions From Your Phone

This guide assumes you have never rented a server, used SSH, or used a terminal.
Follow it top to bottom. Total time: about an hour, most of it waiting.

**What you're building, in plain words:** you'll rent a tiny computer ("server")
that lives in a data center and is always on. The trading program runs there,
automatically, every market morning. Your phone becomes a remote control: a
private, secure connection (Tailscale) lets you peek at the server, watch the
session, and press an emergency stop — from anywhere. Cost: about $6/month.

Words you'll see:
- **Server / VPS** — a rented computer in a data center. No screen; you type
  commands into it remotely.
- **SSH** — the app-to-server connection that gives you a text window (a
  "terminal") on the server. You type a command, press Enter, it runs.
- **Tailscale** — a free app that puts your phone and your server on a private
  network only you can access. It's what makes this safe.

---

## Part 1 — Create four accounts (on your phone, ~20 min)

### 1.1 Alpaca — the paper-trading broker (you likely have this)

1. Go to **alpaca.markets**, log in.
2. Top-left corner: make sure the toggle says **Paper** (not Live).
3. On the Paper overview page, right side: **API Keys** → **Generate New Keys**.
4. Two codes appear: **API Key ID** and **Secret Key**. Copy both into a notes
   app *now* — the secret is shown only once. You'll paste them in Part 5.

### 1.2 GitHub — a token so the server can save session results

1. Go to **github.com**, log in as Borgimus.
2. Tap your profile picture → **Settings** → scroll to the bottom →
   **Developer settings** → **Personal access tokens** → **Fine-grained tokens**
   → **Generate new token**.
3. Name: `trader-vps`. Expiration: 90 days.
4. **Repository access**: "Only select repositories" → choose **Borgimus/Apps-**.
5. **Permissions** → Repository permissions → **Contents** → "Read and write".
6. **Generate token**. Copy the code that starts with `github_pat_` into your
   notes. Shown only once.

### 1.3 Tailscale — the private network

1. Go to **tailscale.com** → **Get started** → sign up (using your Google
   account is fine). That's it for now.

### 1.4 DigitalOcean — where you rent the server

1. Go to **digitalocean.com** → sign up (needs a payment card).
   (Hetzner.com is cheaper if you prefer, but these steps use DigitalOcean.)

---

## Part 2 — Rent the server (~5 min)

1. In DigitalOcean, press **Create** (green button, top right) → **Droplets**
   ("droplet" = their word for server).
2. **Region**: pick **New York** (close to the markets; any US region is fine).
3. **OS / Image**: choose **Debian**, version **12 x64**. ← Important; the
   trading system is pinned to the Python version Debian 12 ships.
4. **Size**: **Basic** → **Regular** → the **$6/mo option (1 GB RAM)**. Don't
   take the 512 MB one — it's too small to install the software.
5. **Authentication**: choose **Password** and invent a long root password.
   Save it in your notes (you'll only need it once, in Part 4).
6. Press **Create Droplet**. After ~1 minute it shows an **IP address** — four
   numbers like `164.90.155.20`. Copy it into your notes.

---

## Part 3 — Install two apps on your phone

1. **Tailscale** (Play Store) — log in with the account from step 1.3, and flip
   its VPN switch **on**. Leave it on; it uses no meaningful battery.
2. **Termius** (Play Store) — the SSH app; the free tier is all you need.

---

## Part 4 — Connect to your server for the first time

1. Open Termius → **+ New Host**.
2. **Address**: the IP from Part 2. **Username**: `root`. **Password**: the one
   you invented. Save, then tap it to connect. (Accept the fingerprint prompt.)
3. You now see a black screen with a line ending in `#` — that's the server's
   terminal. When this guide says "type a command", you type it here (or use
   Termius's paste) and press Enter.

---

## Part 5 — Run the setup script (one command, then answer its questions)

Type (or paste) these two lines, pressing Enter after each:

```
curl -fsSL -o vps_bootstrap.sh https://raw.githubusercontent.com/Borgimus/Apps-/claude/apps-repo-investigation-ihekbi/vps_bootstrap.sh
bash vps_bootstrap.sh
```

The script now sets up everything. It pauses to ask you things — here is every
prompt, in order, and what to do:

| The script says | What you do |
|---|---|
| A long **https://login.tailscale.com/a/...** link appears and it waits | Copy the link into your phone browser, log in to Tailscale, press **Connect**. The script then continues by itself. |
| `GitHub personal access token:` | Paste the `github_pat_...` code. (Typing stays invisible — that's normal. Paste, press Enter.) |
| `git user.name for session commits:` | Type `Borgimus` |
| `git user.email:` | The email on your GitHub account |
| `ALPACA_API_KEY:` | Paste the Alpaca **Key ID** (invisible; paste, Enter) |
| `ALPACA_SECRET_KEY:` | Paste the Alpaca **Secret** (invisible; paste, Enter) |
| `Install weekday 09:31 ET auto-launch cron job? [y/N]` | Type `y` — this is what makes sessions start automatically every market morning with no action from you. |

Then it verifies itself. You want to see, at the end:

- `Broker connectivity` … account details printed, no errors
- `All static checks passed — session is eligible for Phase 3 cohort`
- `== DONE`

If instead it stops with a red `ERROR`, read Part 8.

---

## Part 6 — Two small one-time chores

1. **Stop the server from ever falling off your network.** On your phone open
   **login.tailscale.com** → **Machines** → find **trader** → tap the **⋯**
   menu → **Disable key expiry**. (Otherwise the connection silently expires
   after a few months.)
2. **Save your monitoring link.** In the Tailscale phone app, tap the machine
   **trader** — it shows a full name like `trader.tail1a2b3c.ts.net`. Your
   live-status page is:

   `https://trader.<that-name>.ts.net/api/session/pulse`

   Open it in your phone browser and bookmark it. Right now it will say
   `"session_active": false` — correct, nothing is running yet.
3. **Update Termius** to use the private network: edit the host, change the
   Address from the IP to `trader`. From now on it connects through Tailscale
   from anywhere (home, LTE, café) — as long as the Tailscale switch is on.
   If a browser link pops up when connecting, open it — Tailscale is
   double-checking it's you.

---

## Part 7 — What a trading day looks like

**You don't start anything.** Monday–Friday at 09:31 ET the server launches the
session itself, runs 09:31–12:30 ET, trades paper-only, and shuts itself down.

- **~09:35 ET (optional):** open the bookmarked pulse page. You should see
  `"session_active": true` plus cycle count and P&L. Refresh whenever curious.
- **Any time:** Termius → trader → type
  `tail -20 ~/trader/logs/session_$(date +%F).log` to see the last 20 log lines.
- **Emergency stop (blocks NEW trades; open positions still get managed and
  closed):** Termius → trader → type `~/stop_entries.sh`
  Never switch the server off while a position is open.
- **After 12:35 ET, once per session day:** Termius → trader → type
  `~/eod_close.sh`
  This checks the broker is flat (0 positions, 0 orders) and uploads the
  session's logs and results to GitHub. It takes ~15 seconds.
- **Market holidays:** the auto-start doesn't know the holiday calendar. It
  will start, find nothing tradeable, and stop at 12:30. Ignore those days
  (they are voided per protocol), or the night before type
  `crontab -e` … simpler: just ignore them.

That's the whole routine: glance at a webpage, and run one command after lunch.

---

## Part 8 — If something goes wrong

| Symptom | Fix |
|---|---|
| Pulse page won't load at all | Is the Tailscale switch on on your phone? Is the server on? (DigitalOcean dashboard → droplet → power) |
| Pulse says `session_active: false` during market hours | Termius → trader → `tail -40 ~/trader/logs/session_$(date +%F).log` — a Python error at the bottom is the reason. Also check the auto-start ran: `grep start_session /var/log/syslog \| tail -3` |
| Script Part 5 failed at "Broker connectivity" | Alpaca keys mistyped. Type `rm ~/trader/.env`, run `bash vps_bootstrap.sh` again — it skips finished steps and re-asks for keys. |
| Script failed at fingerprint verification | Don't run sessions. This means the code on the server doesn't match the frozen Phase 3 baseline — ask Claude to investigate. |
| `eod_close.sh` says push rejected/authentication failed | Your GitHub token expired (90 days). Make a new one (step 1.2), then in Termius: `cd ~/trader && git remote set-url origin https://NEWTOKEN@github.com/Borgimus/Apps-.git` |
| You're lost | Everything on the server can be rebuilt in 15 minutes: destroy the droplet in DigitalOcean, make a new one (Part 2), rerun Parts 4–6. The trading history is safe on GitHub and at Alpaca. |

---

## Safety facts (unchanged from protocol)

- The system is **paper-only**: `LIVE_TRADING_ENABLED=false` and the Alpaca
  *paper* URL are written into the config by the script. No real money moves.
- Your keys live only in a protected file on the server (`.env`), which is
  never uploaded to GitHub.
- The monitoring page and stop button are reachable **only through your
  Tailscale network** — not the public internet.
- Don't edit files inside `~/trader` — the frozen-baseline check will fail and
  sessions stop counting toward the evaluation.

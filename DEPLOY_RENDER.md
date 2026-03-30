# Deploy to Render.com

> **Note:** Render free tier sleeps after 15 minutes of inactivity.
> First call after sleep will fail (30s cold start). Upgrade to $7/month to avoid this.

---

## Prerequisites

- [Render account](https://render.com) (free, no credit card)
- Code pushed to GitHub (see Step 1 below)
- Your `GOOGLE_API_KEY` from [aistudio.google.com](https://aistudio.google.com)

---

## Step 1 — Push Code to GitHub

If not already done:

```bash
git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

> Make sure `.env.local` is in your `.gitignore` — never push secrets.

---

## Step 2 — Create a New Web Service on Render

1. Go to [dashboard.render.com](https://dashboard.render.com)
2. Click **New +** → **Web Service**
3. Select **Build and deploy from a Git repository**
4. Click **Connect** next to your GitHub repo
   - If GitHub is not connected, click **Connect GitHub** and authorize Render

---

## Step 3 — Configure the Service

Fill in the settings:

| Field | Value |
|---|---|
| **Name** | `trueailab-voice-agent` (or any name) |
| **Region** | Oregon (US West) or closest to you |
| **Branch** | `main` |
| **Runtime** | **Docker** |
| **Dockerfile Path** | `./Dockerfile` |
| **Instance Type** | **Free** |

> Render will auto-detect your `Dockerfile` — no changes needed.

---

## Step 4 — Add Environment Variables

Scroll down to **Environment Variables** on the same page:

Click **Add Environment Variable** and add:

| Key | Value |
|---|---|
| `GOOGLE_API_KEY` | your Google Gemini API key |

---

## Step 5 — Deploy

1. Click **Create Web Service** at the bottom
2. Render starts building — watch the **Logs** tab
3. Wait for:
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```
4. Your public URL appears at the top of the page:
```
https://trueailab-voice-agent.onrender.com
```

---

## Step 6 — Verify It Works

Open your Render URL in browser:

```
https://trueailab-voice-agent.onrender.com/
```

You should see:

```json
{"status": "TrueAILab voice agent running"}
```

---

## Step 7 — Set Twilio Webhook

1. Go to [console.twilio.com](https://console.twilio.com)
2. **Phone Numbers** → **Manage** → **Active Numbers** → click your number
3. Scroll to **Voice Configuration** → **A call comes in**
4. Set webhook URL to:
```
https://trueailab-voice-agent.onrender.com/incoming-call
```
5. Method: **HTTP POST**
6. Click **Save configuration**

---

## Step 8 — Test

Call your Twilio number. In Render → **Logs** tab you should see:

```
[Call started] MZ...
[Lead] {'name': '...', 'email': '...', ...}
[Webhook] {...} → 200
[Call ended]
```

---

## Redeployments

Every push to `main` on GitHub triggers an automatic redeploy:

```bash
git add .
git commit -m "your changes"
git push
```

---

## Fix the Sleep Problem (Optional)

Free tier sleeps after 15 min of no traffic. To keep it awake:

**Option A — Upgrade to Starter ($7/month)**
- Go to your service → **Settings** → **Instance Type** → change to **Starter**

**Option B — Use a free uptime monitor**
- Sign up at [uptimerobot.com](https://uptimerobot.com) (free)
- Add a monitor: HTTP → `https://your-app.onrender.com/`
- Set interval: every **5 minutes**
- This pings your app before it sleeps, keeping it always awake

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Build fails | Check **Logs** tab — usually a missing dependency |
| `ValueError: No API key` | `GOOGLE_API_KEY` not set — go to **Environment** tab and add it |
| Twilio webhook fails | Service is sleeping — wait 30s and call again, or use UptimeRobot |
| Call connects but silent | Verify `GOOGLE_API_KEY` is valid and has Gemini API access |
| App crashes | Check **Logs** tab for the full error message |

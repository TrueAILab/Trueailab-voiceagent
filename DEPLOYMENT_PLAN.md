# TrueAILab Voice Agent — Deployment Guide

> End-to-end setup from your laptop to a live phone number on Google Cloud.

---

## What you are building

```
Someone dials your Twilio number
          ↓
Twilio sends audio stream to your server
          ↓
Server pipes audio to Gemini Live AI
          ↓
Gemini responds with voice, saves lead data
          ↓
Lead posted to your n8n webhook
```

---

## Two modes

| Mode | URL type | Use for |
|------|----------|---------|
| **Local + ngrok** | Temporary (changes every restart) | Development & testing |
| **Google Cloud Run** | Permanent HTTPS URL | Production |

---

---

# PART 1 — LOCAL DEVELOPMENT

---

## Step 1 — Install dependencies

Open a terminal in your project folder and run:

```bash
pip install -r requirements.txt
```

---

## Step 2 — Create your `.env.local` file

Copy the example file:

```bash
cp .env.example .env.local
```

Open `.env.local` and fill in your real values:

```
GOOGLE_API_KEY=AIzaSy...your_key_here

TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_PHONE_NUMBER=+1xxxxxxxxxx

LIVEKIT_URL=wss://your-instance.livekit.cloud
LIVEKIT_API_KEY=your_key
LIVEKIT_API_SECRET=your_secret
```

> **Never commit `.env.local` to git.** It is already in `.gitignore`.

---

## Step 3 — Start the server with ngrok

```bash
python start.py
```

You will see:

```
============================================================
  ngrok tunnel:    https://xxxx-xxxx.ngrok-free.app
  Twilio webhook:  https://xxxx-xxxx.ngrok-free.app/incoming-call
============================================================
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Copy the **Twilio webhook** URL — you will paste it into Twilio.

> **Note:** The ngrok URL changes every time you restart `start.py`.
> For a permanent URL, use Google Cloud Run (Part 2).

---

## Step 4 — Configure Twilio webhook

1. Go to [console.twilio.com](https://console.twilio.com)
2. Left sidebar → **Phone Numbers → Manage → Active numbers**
3. Click your phone number
4. Scroll to **"Voice Configuration"**
5. Under **"A call comes in"**:
   - Set the dropdown to **Webhook**
   - Paste your URL: `https://xxxx-xxxx.ngrok-free.app/incoming-call`
   - Method: **HTTP POST**
6. Click **Save configuration**

---

## Step 5 — Test the call

Call your Twilio phone number.
The AI (Jacqueline) should answer and start the sales conversation.

If there is no answer, check the terminal — error messages appear there.

---

---

# PART 2 — GOOGLE CLOUD RUN (PRODUCTION)

Cloud Run gives you a permanent HTTPS URL. No ngrok. No laptop running.

---

## Step 1 — Create a Google Cloud account

1. Go to [cloud.google.com](https://cloud.google.com)
2. Click **"Get started for free"** — you get $300 free credits
3. Sign in with your Google account
4. Add a credit card (required, but you won't be charged during free trial)

---

## Step 2 — Create a project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. At the top, click the project dropdown → **"New Project"**
3. Name it: `trueailab-voice-agent`
4. Click **Create**
5. Select the new project from the dropdown

---

## Step 3 — Install Google Cloud CLI

**Windows:**
1. Download from: [cloud.google.com/sdk/docs/install](https://cloud.google.com/sdk/docs/install)
2. Run the installer, keep all defaults
3. When it finishes, it will open a terminal and ask you to log in — follow the prompts

**Verify:**
```bash
gcloud --version
```

---

## Step 4 — Log in and set your project

```bash
gcloud auth login
```

A browser window will open. Sign in with your Google account.

Then set your project:
```bash
gcloud config set project trueailab-voice-agent
```

---

## Step 5 — Enable required Google Cloud APIs

Run this once:

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com
```

Wait ~1 minute for this to complete.

---

## Step 6 — Create an Artifact Registry repository

This is where your Docker image will be stored.

```bash
gcloud artifacts repositories create voice-agent-repo \
  --repository-format=docker \
  --location=us-central1 \
  --description="TrueAILab voice agent images"
```

---

## Step 7 — Build and push the Docker image

From your project folder (where the Dockerfile is), run:

```bash
gcloud builds submit \
  --tag us-central1-docker.pkg.dev/trueailab-voice-agent/voice-agent-repo/server:latest
```

This uploads your code to Google, builds the Docker image in the cloud, and pushes it to your repository.

It takes 2-3 minutes. You will see build logs scroll by.

---

## Step 8 — Deploy to Cloud Run

```bash
gcloud run deploy voice-agent \
  --image us-central1-docker.pkg.dev/trueailab-voice-agent/voice-agent-repo/server:latest \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8000 \
  --set-env-vars "GOOGLE_API_KEY=YOUR_KEY_HERE,TWILIO_ACCOUNT_SID=ACxxxx,TWILIO_AUTH_TOKEN=xxxx,TWILIO_PHONE_NUMBER=+1xxxx"
```

> Replace the env var values with your real keys from `.env.local`.

When it finishes you will see:

```
Service [voice-agent] revision [voice-agent-xxxxx] has been deployed
Service URL: https://voice-agent-xxxxxxxxxx-uc.a.run.app
```

**Copy that Service URL — this is your permanent URL.**

---

## Step 9 — Set environment variables (safer method via console)

Instead of putting secrets in the command line, you can set them in the Cloud Run console:

1. Go to [console.cloud.google.com/run](https://console.cloud.google.com/run)
2. Click **voice-agent**
3. Click **Edit & Deploy New Revision**
4. Scroll to **"Variables & Secrets"**
5. Click **"Add Variable"** for each:

| Name | Value |
|------|-------|
| `GOOGLE_API_KEY` | your Gemini API key |
| `TWILIO_ACCOUNT_SID` | your Twilio SID |
| `TWILIO_AUTH_TOKEN` | your Twilio auth token |
| `TWILIO_PHONE_NUMBER` | your Twilio number |
| `LIVEKIT_URL` | your LiveKit URL |
| `LIVEKIT_API_KEY` | your LiveKit key |
| `LIVEKIT_API_SECRET` | your LiveKit secret |

6. Click **Deploy**

---

## Step 10 — Configure Twilio webhook (production URL)

1. Go to [console.twilio.com](https://console.twilio.com)
2. Left sidebar → **Phone Numbers → Manage → Active numbers**
3. Click your phone number
4. Under **"A call comes in"**:
   - Paste: `https://voice-agent-xxxxxxxxxx-uc.a.run.app/incoming-call`
   - Method: **HTTP POST**
5. Click **Save configuration**

> This URL is **permanent** — you never need to update it again unless you change Cloud Run services.

---

## Step 11 — Test production

Call your Twilio phone number.
The AI should answer within 2-3 seconds.

**Check logs if something is wrong:**
```bash
gcloud run logs read voice-agent --region us-central1 --limit 50
```

Or in the console: Cloud Run → voice-agent → **Logs** tab.

---

---

# PART 3 — UPDATING THE DEPLOYED SERVER

When you make code changes and want to redeploy:

```bash
# 1. Rebuild and push the new image
gcloud builds submit \
  --tag us-central1-docker.pkg.dev/trueailab-voice-agent/voice-agent-repo/server:latest

# 2. Deploy the new image
gcloud run deploy voice-agent \
  --image us-central1-docker.pkg.dev/trueailab-voice-agent/voice-agent-repo/server:latest \
  --region us-central1
```

The URL stays the same. Zero downtime — Cloud Run switches traffic automatically.

---

---

# PART 4 — NGROK AUTHTOKEN (optional, for stable local URLs)

By default, ngrok gives you a random URL each restart.
To get a stable subdomain, create a free ngrok account:

1. Sign up at [ngrok.com](https://ngrok.com)
2. Go to **Your Authtoken** in the dashboard
3. Copy your token
4. Open `start.py` and uncomment this line:
   ```python
   # ngrok.set_auth_token("your_ngrok_authtoken")
   ```
   Replace with your actual token.

With a paid ngrok plan you also get a fixed domain (e.g. `trueailab.ngrok.io`) that never changes.

---

---

# QUICK REFERENCE

## Local development
```bash
python start.py
# → prints ngrok URL
# → set that URL in Twilio webhook
# → call your number to test
```

## Redeploy to production
```bash
gcloud builds submit --tag us-central1-docker.pkg.dev/trueailab-voice-agent/voice-agent-repo/server:latest
gcloud run deploy voice-agent --image us-central1-docker.pkg.dev/trueailab-voice-agent/voice-agent-repo/server:latest --region us-central1
```

## View production logs
```bash
gcloud run logs read voice-agent --region us-central1 --limit 50
```

## Check service URL
```bash
gcloud run services describe voice-agent --region us-central1 --format="value(status.url)"
```

---

## Call flow recap

```
You call Twilio number
    ↓
Twilio: POST https://your-url/incoming-call
    ↓
server.py returns TwiML with WebSocket URL
    ↓
Twilio opens: wss://your-url/media-stream
    ↓
server.py bridges audio ↔ Gemini Live
    ↓
When AI collects lead → POST to n8n webhook
```

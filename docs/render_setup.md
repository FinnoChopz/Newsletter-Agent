# Render Setup

This makes Finn-Signal available at a public URL and lets it send daily digests even when your laptop is off.

## 1. Push the repo to GitHub

Render deploys from GitHub. Commit and push the current repo first.

## 2. Prepare the Google env value

Run locally:

```bash
python scripts/write_render_env.py
```

This creates `.render.env`. Copy the `FINN_SIGNAL_GOOGLE_CLIENT_CONFIG_JSON` value into Render later.

Do not commit `.render.env`.

## 3. Create the Render service

1. Go to `https://dashboard.render.com`.
2. Click `New`.
3. Choose `Blueprint`.
4. Connect the GitHub repo.
5. Select this repo.
6. Render should detect `render.yaml`.
7. Click `Apply`.

The blueprint creates:

- a Python web service
- a persistent disk mounted at `/var/data`
- environment-variable placeholders
- `/healthz` health check
- a built-in scheduler loop

If Render asks for a start command manually, use exactly:

```bash
python web_console.py
```

Do not use `uvicorn server:app --host 0.0.0.0 --port $PORT`; this project is not a FastAPI/Uvicorn app.

## 4. Set Render environment variables

In the Render service, open `Environment` and set:

```text
OPENAI_API_KEY=...
OPENAI_MAIN_MODEL=gpt-5.5
OPENAI_CHEAP_MODEL=gpt-5.4-mini
FINN_SIGNAL_PUBLIC_URL=https://your-render-url.onrender.com
FINN_SIGNAL_FEEDBACK_BASE_URL=https://your-render-url.onrender.com
FINN_SIGNAL_GOOGLE_CLIENT_CONFIG_JSON=<value from .render.env>
```

The blueprint already sets:

```text
FINN_SIGNAL_CONSOLE_HOST=0.0.0.0
FINN_SIGNAL_USERS_DIR=/var/data/users
FINN_SIGNAL_ENABLE_HOSTED_SCHEDULER=true
```

## 5. Add the Google callback URL

In Google Cloud Console, open the OAuth client used by `credentials.json`.

Add this authorized redirect URI:

```text
https://your-render-url.onrender.com/oauth2callback
```

Save it.

## 6. Redeploy

Back in Render, click `Manual Deploy` -> `Deploy latest commit`.

## 7. Use the app

Open the Render URL.

Then:

1. Create profile.
2. Connect Gmail.
3. Scan Gmail.
4. Approve newsletters.
5. Set schedule.
6. Send a test digest.

Render now does the daily work. Your laptop can be off.

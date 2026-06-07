# Finn-Signal Setup for Testers

## The Simple Truth

Finn-Signal must run somewhere every day.

- If it runs on your laptop, the laptop cannot send while fully asleep or off.
- If it wakes later that same day after the scheduled time, Finn-Signal catches up and sends once.
- If it runs on Render, your laptop can be off because Render runs it.

For nontechnical family/friend testing, the best long-term setup is hosted on Render.

## Local Setup

Use this if the person is okay running it on their own Mac.

1. Download the repo as a ZIP from GitHub.
2. Unzip it.
3. Open Terminal in the folder.
4. Run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python web_console.py
```

5. Open:

```text
http://127.0.0.1:8787
```

6. Create profile, connect Gmail, scan newsletters, approve sources, set schedule.

The Mac needs to be awake to send. If it was asleep at delivery time but wakes later that same day, it will send then.

## Hosted Setup

Use this if you want it to work while everyone’s computer is off.

1. Deploy this repo to Render as a web service.
2. Add a persistent disk.
3. Set `FINN_SIGNAL_USERS_DIR` to the persistent disk path.
4. Set `FINN_SIGNAL_PUBLIC_URL` to the Render URL.
5. Add the Render `/oauth2callback` URL to the Google OAuth client.
6. Add the OpenAI and Google environment variables in Render.

Then users only visit the Render URL and connect Gmail.

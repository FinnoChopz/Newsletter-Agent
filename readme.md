# Finn-Signal

Finn-Signal is a personal daily intelligence digest agent. It reads approved newsletters from Gmail, extracts atomic stories, scores them by personal relevance and global importance, ranks them, writes a v3 HTML digest, and emails it daily.

The goal is a personal salience engine: it learns what matters to Finn while still surfacing globally important events outside his normal interests.

## Pipeline

```text
Approved newsletter sources
-> Gmail fetch
-> story extraction
-> scoring
-> deterministic ranking
-> v3 HTML digest generation
-> manifest save
-> email delivery
-> feedback processing
-> learned preference updates
```

## Environment

Create `.env` in the project root:

```text
OPENAI_API_KEY=your_api_key_here
OPENAI_MAIN_MODEL=gpt-5.5
OPENAI_CHEAP_MODEL=gpt-5.4-mini
FINN_SIGNAL_RECIPIENTS=you@example.com
FINN_SIGNAL_BCC=
FINN_SIGNAL_FEEDBACK_EMAIL=you@example.com
FINN_SIGNAL_FEEDBACK_BASE_URL=https://your-project.vercel.app
FINN_SIGNAL_DAYS=7
FINN_SIGNAL_MAX_EMAILS=25
FINN_SIGNAL_GMAIL_RETRIES=5
FINN_SIGNAL_GMAIL_RETRY_SECONDS=30
FINN_SIGNAL_PROCESS_FEEDBACK=true
FINN_SIGNAL_FEEDBACK_DAYS=14
FINN_SIGNAL_FEEDBACK_MAX_EMAILS=20
```

`OPENAI_MAIN_MODEL` is used for nuanced scoring. `OPENAI_CHEAP_MODEL` is used for extraction, newsletter-source classification, and feedback parsing.

`FINN_SIGNAL_GMAIL_RETRIES` and `FINN_SIGNAL_GMAIL_RETRY_SECONDS` make the scheduled job tolerant of transient Gmail/API/network timeouts.

`FINN_SIGNAL_PROCESS_FEEDBACK=true` makes each daily run process recent feedback replies before scoring the next digest.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env`, fill in the values, add Google OAuth credentials as `credentials.json`, then run onboarding:

```bash
cp .env.example .env
python onboard_newsletters.py
python scripts/newsletters_smoke.py
```

## Test

```bash
make test
```

## Daily Run

```bash
python run_daily_signal.py
```

To temporarily test the macOS schedule, use:

```bash
python scripts/set_launch_time.py 16:40
```

After the test, put it back to 11:00:

```bash
python scripts/set_launch_time.py 11:00
```

Outputs:

- `outputs/latest_extracted_items.json`
- `outputs/latest_scored_items.json`
- `outputs/latest_digest_manifest.json`
- `outputs/finn_signal_latest.html`

The manifest maps visible digest item numbers to the ranked items, which lets feedback like `2:1` update the right topics and source.

## Ranking

The scorer returns component scores from 0 to 10:

- `finn_relevance`
- `global_importance`
- `novelty`
- `actionability`
- `source_quality`

The Python ranking layer recomputes:

```text
final_score =
0.40 * finn_relevance
+ 0.25 * global_importance
+ 0.15 * novelty
+ 0.10 * actionability
+ 0.10 * source_quality
```

Then it applies bounded learned topic/source multipliers from `data/learned_preferences.yaml`, sorts the items, preserves a global-importance override, assigns visible item numbers, and builds digest sections.

## v3 Digest UX

The HTML digest shows visible item numbers (`#1`, `#2`, etc.) and feedback controls on each item:

- `More like this`
- `Less like this`

When `FINN_SIGNAL_FEEDBACK_BASE_URL` is set, the buttons point to:

```text
https://your-finn-signal-project.vercel.app/api/feedback
```

Each link includes the digest id, item number, and rating. The hosted endpoint opens a confirmation page first, then sends a structured feedback email back to Gmail after confirmation. This avoids accidental ratings from email link scanners.

If `FINN_SIGNAL_FEEDBACK_BASE_URL` is not set, buttons fall back to `mailto:` links for local testing.

The footer also supports manual reply syntax:

```text
1:5, 2:2, 3:4
More AI infra. Less routine market noise.
```

Ratings:

- `1` = bad / less like this
- `2` = weak
- `3` = okay
- `4` = good
- `5` = excellent / more like this

## Feedback Processing

Manual feedback:

```bash
python process_feedback.py --text "1:5, 2:2. More local models. Less routine market updates."
```

Manual feedback for a specific digest:

```bash
python process_feedback.py --digest-id training-broad-20260605-120000 --text "1:5, 2:1"
```

Gmail feedback ingestion:

```bash
python process_feedback.py
```

It searches Gmail with:

```text
subject:"Re: Finn-Signal" newer_than:14d -in:spam -in:trash
```

Already processed Gmail message IDs are stored in `data/processed_feedback_ids.json`.

## Hosted Feedback Endpoint

The Vercel endpoint lives at:

```text
api/feedback.py
```

Deploy this repository to Vercel, then set these Vercel environment variables:

```text
FINN_SIGNAL_FEEDBACK_TO=you@example.com
FINN_SIGNAL_GMAIL_CLIENT_ID=your_google_oauth_client_id
FINN_SIGNAL_GMAIL_CLIENT_SECRET=your_google_oauth_client_secret
FINN_SIGNAL_GMAIL_REFRESH_TOKEN=your_google_oauth_refresh_token
```

The endpoint does not write to local files. It sends a structured email through Gmail with a subject like:

```text
Re: Finn-Signal - 2026-06-05
```

The normal feedback processor then reads that email from Gmail and updates local learned preferences.

Generate the Gmail forwarding env values from local OAuth files:

```bash
python scripts/write_vercel_feedback_env.py
```

Then add the values from `.vercel-feedback.env` to Vercel. The file is gitignored because it contains secrets.

Optional fallback: use Resend instead of Gmail forwarding by setting:

```text
RESEND_API_KEY=your_resend_api_key
FINN_SIGNAL_FEEDBACK_FROM=Finn-Signal <feedback@your-verified-domain.com>
```

If Resend returns `403 Forbidden`, the usual cause is an unverified sender/domain. Gmail forwarding avoids that Resend domain requirement.

## Demo Flow

For a reviewer-friendly local demo that does not require scanning Gmail:

```bash
python send_training_digest.py --scenario broad
python process_feedback.py --digest-id <training-digest-id> --no-model --text "1:5, 2:1. More local models. Less routine market updates."
cat data/learned_preferences.yaml
```

See `docs/demo.md` for the full version.

See `docs/sample_digest_excerpt.md` for a sanitized example of the digest shape.

## Training Digests

Use training digests to test the full loop and initialize preferences with real replies.

List available scenarios:

```bash
python send_training_digest.py --list
```

Write a preview without sending:

```bash
python send_training_digest.py --scenario broad
```

Send yourself a real training email:

```bash
python send_training_digest.py --scenario broad --send
```

Available scenarios:

- `broad`: AI, markets, geopolitics, cognitive science, Apple, data centers, startup hype
- `ai-agents`: agent capability, memory systems, weak AI-agent hype
- `markets`: routine market noise versus systemic-risk market events
- `latest`: reuses the most recent real scored items in `outputs/latest_scored_items.json`

After the training email arrives, reply to it with ratings and notes:

```text
1:5, 2:1, 3:4
More local models.
Less routine market updates.
```

Then process the reply:

```bash
python process_feedback.py
```

The script detects the digest id from the email subject/body, loads the matching file in `outputs/manifests/`, updates `data/learned_preferences.yaml`, and appends to `data/feedback_log.jsonl`.

## Learned Preference Files

Feedback updates data files only. It never edits Python code.

- `data/learned_preferences.yaml`: mutable topic/source weights, rules, and style notes
- `data/feedback_log.jsonl`: append-only audit log of parsed feedback and applied updates
- `data/processed_feedback_ids.json`: Gmail reply IDs already processed
- `outputs/latest_digest_manifest.json`: latest item-number mapping

Weights are updated gradually:

```text
rating target: 1 -> 2, 2 -> 4, 3 -> 6, 4 -> 8, 5 -> 10
error = target_score - original_final_score
delta = 0.03 * error
weight bounds = 0.25 to 2.0
```

Natural-language adjustments are clamped to +/- 0.25 per update. Exact duplicate rules and style notes are ignored.

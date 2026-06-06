# Finn-Signal Demo Flow

This is the fastest reviewer-friendly way to prove the loop works without scanning a real inbox.

1. Generate a calibration digest:

```bash
python send_training_digest.py --scenario broad
```

2. Inspect the generated preview in `outputs/training/`.

3. Apply sample feedback against that digest:

```bash
python process_feedback.py --digest-id <training-digest-id> --no-model --text "1:5, 2:1. More local models. Less routine market updates."
```

4. Inspect the learned preference update:

```bash
cat data/learned_preferences.yaml
tail -1 data/feedback_log.jsonl
```

For the real email loop, send a training digest:

```bash
python send_training_digest.py --scenario broad --send
```

Reply to the email with ratings, then run:

```bash
python process_feedback.py
```

import json
import re
from pathlib import Path
from typing import Any


LATEST_MANIFEST_PATH = Path("outputs/latest_digest_manifest.json")
MANIFEST_DIR = Path("outputs/manifests")


def safe_manifest_name(digest_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", digest_id).strip("_")


def manifest_path_for_digest_id(digest_id: str) -> Path:
    return MANIFEST_DIR / f"{safe_manifest_name(digest_id)}.json"


def save_manifest(manifest: dict[str, Any]) -> Path:
    digest_id = str(manifest["digest_id"])
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    path = manifest_path_for_digest_id(digest_id)
    text = json.dumps(manifest, indent=2)
    path.write_text(text, encoding="utf-8")
    LATEST_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_MANIFEST_PATH.write_text(text, encoding="utf-8")
    return path


def load_manifest(digest_id: str | None = None) -> dict[str, Any]:
    if digest_id:
        path = manifest_path_for_digest_id(digest_id)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

    if LATEST_MANIFEST_PATH.exists():
        return json.loads(LATEST_MANIFEST_PATH.read_text(encoding="utf-8"))

    raise FileNotFoundError(
        "No digest manifest found. Run python run_daily_signal.py or python send_training_digest.py first."
    )


def extract_digest_id(text: str) -> str | None:
    patterns = [
        r"\bdigest_id:\s*([A-Za-z0-9_.:-]+)",
        r"\bFinn-Signal(?:\s+Training)?\s+-\s*([A-Za-z0-9_.:-]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1).strip()

    return None

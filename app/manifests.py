import json
import re
from pathlib import Path
from typing import Any

from app.profiles import profile_paths


LATEST_MANIFEST_PATH = Path("outputs/latest_digest_manifest.json")
MANIFEST_DIR = Path("outputs/manifests")


def safe_manifest_name(digest_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", digest_id).strip("_")


def manifest_path_for_digest_id(digest_id: str) -> Path:
    return MANIFEST_DIR / f"{safe_manifest_name(digest_id)}.json"


def profile_id_from_digest_id(digest_id: str) -> str | None:
    parts = digest_id.rsplit("-", 3)
    if len(parts) == 4 and all(part.isdigit() for part in parts[-3:]):
        return parts[0]
    return None


def profile_manifest_path_for_digest_id(digest_id: str) -> Path | None:
    profile_id = profile_id_from_digest_id(digest_id)
    if not profile_id:
        return None
    return profile_paths(profile_id).root / "outputs" / "manifests" / f"{safe_manifest_name(digest_id)}.json"


def profile_manifest_candidate_paths(digest_id: str) -> list[Path]:
    profile_id = profile_id_from_digest_id(digest_id)
    if not profile_id:
        return []
    output_dir = profile_paths(profile_id).root / "outputs"
    return [
        output_dir / "manifests" / f"{safe_manifest_name(digest_id)}.json",
        output_dir / "latest_digest_manifest.json",
    ]


def save_manifest(manifest: dict[str, Any]) -> Path:
    digest_id = str(manifest["digest_id"])
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    path = manifest_path_for_digest_id(digest_id)
    text = json.dumps(manifest, indent=2)
    path.write_text(text, encoding="utf-8")
    LATEST_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_MANIFEST_PATH.write_text(text, encoding="utf-8")
    profile_path = profile_manifest_path_for_digest_id(digest_id)
    if profile_path is not None:
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(text, encoding="utf-8")
    return path


def load_manifest(digest_id: str | None = None) -> dict[str, Any]:
    if digest_id:
        paths = [
            *profile_manifest_candidate_paths(digest_id),
            manifest_path_for_digest_id(digest_id),
            LATEST_MANIFEST_PATH,
        ]
        for path in paths:
            if path.exists():
                manifest = json.loads(path.read_text(encoding="utf-8"))
                if str(manifest.get("digest_id") or "") == digest_id:
                    return manifest
        raise FileNotFoundError(f"No digest manifest found for {digest_id}.")

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

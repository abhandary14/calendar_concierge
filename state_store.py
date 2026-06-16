import json
import os
from datetime import datetime

STATE_FILE = "state/processed_ids.json"
MAX_IDS = 500


def _load() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"processed_ids": [], "last_success": None}
    with open(STATE_FILE) as f:
        return json.load(f)


def get_processed_ids() -> set[str]:
    return set(_load()["processed_ids"])


def get_last_success() -> datetime | None:
    raw = _load()["last_success"]
    return datetime.fromisoformat(raw) if raw else None


def mark_processed(new_ids: list[str]) -> None:
    """Append new_ids to the processed set and record the current time as last_success."""
    state = _load()
    merged = list(set(state["processed_ids"]) | set(new_ids))
    # Keep only the most recent MAX_IDS to prevent unbounded file growth
    state["processed_ids"] = merged[-MAX_IDS:]
    state["last_success"] = datetime.now().isoformat()
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

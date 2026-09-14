from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

CACHE_FILE = Path("/tmp/kturtle_candidate_finance_cache.json")

def _load_all() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_all(data: dict) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def save_candidate_finance(stock_code: str, payload: dict) -> None:
    data = _load_all()
    item = dict(payload)
    item["saved_at"] = datetime.now(timezone.utc).isoformat()
    data[str(stock_code).zfill(6)] = item
    _save_all(data)

def get_candidate_finance(stock_code: str) -> tuple[dict | None, float | None]:
    item = _load_all().get(str(stock_code).zfill(6))
    if not item:
        return None, None
    try:
        saved = datetime.fromisoformat(item["saved_at"])
        age_hours = (datetime.now(timezone.utc) - saved).total_seconds() / 3600.0
    except Exception:
        age_hours = None
    return item, age_hours

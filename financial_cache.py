from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

CACHE_FILE = Path("/tmp/kturtle_financial_gate_cache.json")

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

def save_financial_gate(stock_code: str, payload: dict) -> None:
    data = _load_all()
    item = dict(payload)
    item["saved_at"] = datetime.now(timezone.utc).isoformat()
    data[str(stock_code).zfill(6)] = item
    _save_all(data)

def load_financial_gate(stock_code: str) -> dict | None:
    return _load_all().get(str(stock_code).zfill(6))

def cache_age_hours(item: dict | None) -> float | None:
    if not item or not item.get("saved_at"):
        return None
    try:
        saved = datetime.fromisoformat(item["saved_at"])
        return (datetime.now(timezone.utc) - saved).total_seconds() / 3600
    except Exception:
        return None

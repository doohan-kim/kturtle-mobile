from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "financial_db.json"

def load_db() -> dict:
    if not DB_PATH.exists():
        return {"generated_at": None, "stocks": {}}
    try:
        return json.loads(DB_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"generated_at": None, "stocks": {}}

def get_stock_finance(stock_code: str):
    db = load_db()
    item = db.get("stocks", {}).get(str(stock_code).zfill(6))
    generated_at = db.get("generated_at")
    age_hours = None
    if generated_at:
        try:
            dt = datetime.fromisoformat(generated_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0
        except Exception:
            pass
    return item, age_hours

def db_meta():
    db = load_db()
    return {
        "generated_at": db.get("generated_at"),
        "stock_count": len(db.get("stocks", {})),
        "source": db.get("source", "OpenDART"),
    }

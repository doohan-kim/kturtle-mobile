from __future__ import annotations
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JOURNAL_FILE = Path("/tmp/kturtle_trade_journal.json")

FIELDS = [
    "trade_id","status","stock_code","name","sector","breakout_type",
    "sector_score","rs_score","entry_date","entry_price","qty",
    "atr_n","initial_stop","add_05n","add_10n","risk_krw","unit_count","initial_entry_price",
    "exit_date","exit_price","exit_reason","pnl_krw","return_pct",
    "r_multiple","n_multiple","holding_days","memo"
]

def _now_iso():
    return datetime.now(timezone.utc).isoformat()

def _load() -> list[dict]:
    if not JOURNAL_FILE.exists():
        return []
    try:
        data = json.loads(JOURNAL_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []

def _save(rows: list[dict]) -> None:
    JOURNAL_FILE.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def list_trades() -> list[dict]:
    return _load()

def record_entry(payload: dict[str, Any]) -> dict:
    rows = _load()
    stock_code = str(payload.get("stock_code","")).zfill(6)

    # Prevent duplicate open position for same stock.
    for r in rows:
        if r.get("stock_code") == stock_code and r.get("status") == "OPEN":
            raise ValueError("이미 OPEN 상태의 동일 종목이 있습니다.")

    trade_id = f"{stock_code}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    row = {k: None for k in FIELDS}
    row.update({
        "trade_id": trade_id,
        "status": "OPEN",
        "stock_code": stock_code,
        "name": payload.get("name"),
        "sector": payload.get("sector"),
        "breakout_type": payload.get("breakout_type"),
        "sector_score": payload.get("sector_score"),
        "rs_score": payload.get("rs_score"),
        "entry_date": payload.get("entry_date") or datetime.now().date().isoformat(),
        "entry_price": float(payload.get("entry_price",0)),
        "qty": int(payload.get("qty",0)),
        "atr_n": float(payload.get("atr_n",0)),
        "initial_stop": float(payload.get("initial_stop",0)),
        "add_05n": float(payload.get("add_05n",0)),
        "add_10n": float(payload.get("add_10n",0)),
        "risk_krw": float(payload.get("risk_krw",0)),
        "unit_count": 1,
        "initial_entry_price": float(payload.get("entry_price",0)),
        "memo": payload.get("memo",""),
    })
    rows.append(row)
    _save(rows)
    return row

def close_trade(trade_id: str, exit_price: float, exit_reason: str, exit_date: str | None = None, memo: str = "") -> dict:
    rows = _load()
    target = None
    for r in rows:
        if r.get("trade_id") == trade_id:
            target = r
            break
    if target is None:
        raise ValueError("거래를 찾을 수 없습니다.")
    if target.get("status") != "OPEN":
        raise ValueError("이미 종료된 거래입니다.")

    entry = float(target.get("entry_price") or 0)
    qty = int(target.get("qty") or 0)
    atr = float(target.get("atr_n") or 0)
    risk = float(target.get("risk_krw") or 0)
    exit_price = float(exit_price)

    pnl = (exit_price - entry) * qty
    ret = ((exit_price / entry) - 1) * 100 if entry > 0 else None
    r_mult = pnl / risk if risk > 0 else None
    n_mult = (exit_price - entry) / atr if atr > 0 else None

    ed = exit_date or datetime.now().date().isoformat()
    holding_days = None
    try:
        holding_days = (datetime.fromisoformat(ed).date() - datetime.fromisoformat(str(target["entry_date"])).date()).days
    except Exception:
        pass

    target.update({
        "status":"CLOSED",
        "exit_date":ed,
        "exit_price":exit_price,
        "exit_reason":exit_reason,
        "pnl_krw":pnl,
        "return_pct":ret,
        "r_multiple":r_mult,
        "n_multiple":n_mult,
        "holding_days":holding_days,
        "memo": (str(target.get("memo") or "") + (" | " + memo if memo else "")).strip(" |"),
    })
    _save(rows)
    return target

def delete_trade(trade_id: str) -> None:
    rows = [r for r in _load() if r.get("trade_id") != trade_id]
    _save(rows)

def import_csv_bytes(data: bytes) -> int:
    text = data.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for r in reader:
        row = {k: r.get(k) for k in FIELDS}
        # Convert numeric fields
        for k in ["sector_score","rs_score","entry_price","initial_entry_price","atr_n","initial_stop","add_05n","add_10n",
                  "risk_krw","exit_price","pnl_krw","return_pct","r_multiple","n_multiple"]:
            v = row.get(k)
            if v in (None,""):
                row[k] = None
            else:
                try: row[k] = float(v)
                except Exception: row[k] = None
        for k in ["qty","holding_days","unit_count"]:
            v = row.get(k)
            if v in (None,""):
                row[k] = None
            else:
                try: row[k] = int(float(v))
                except Exception: row[k] = None
        rows.append(row)
    _save(rows)
    return len(rows)

def export_csv_bytes() -> bytes:
    rows = _load()
    sio = io.StringIO()
    writer = csv.DictWriter(sio, fieldnames=FIELDS)
    writer.writeheader()
    for r in rows:
        writer.writerow({k:r.get(k) for k in FIELDS})
    return sio.getvalue().encode("utf-8-sig")

def performance_summary() -> dict:
    rows = _load()
    closed = [r for r in rows if r.get("status") == "CLOSED"]
    open_rows = [r for r in rows if r.get("status") == "OPEN"]

    if not closed:
        return {
            "closed_count":0, "open_count":len(open_rows), "wins":0, "losses":0,
            "win_rate":None, "total_pnl":0, "avg_return":None, "avg_r":None,
            "avg_n":None, "profit_factor":None, "max_loss":None
        }

    pnls = [float(r.get("pnl_krw") or 0) for r in closed]
    rets = [float(r.get("return_pct") or 0) for r in closed if r.get("return_pct") is not None]
    rs = [float(r.get("r_multiple") or 0) for r in closed if r.get("r_multiple") is not None]
    ns = [float(r.get("n_multiple") or 0) for r in closed if r.get("n_multiple") is not None]

    wins = sum(1 for x in pnls if x > 0)
    losses = sum(1 for x in pnls if x < 0)
    gross_profit = sum(x for x in pnls if x > 0)
    gross_loss = abs(sum(x for x in pnls if x < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else None

    return {
        "closed_count":len(closed),
        "open_count":len(open_rows),
        "wins":wins,
        "losses":losses,
        "win_rate": wins/len(closed)*100,
        "total_pnl":sum(pnls),
        "avg_return": sum(rets)/len(rets) if rets else None,
        "avg_r": sum(rs)/len(rs) if rs else None,
        "avg_n": sum(ns)/len(ns) if ns else None,
        "profit_factor":pf,
        "max_loss":min(pnls) if pnls else None,
    }

def grouped_performance(key: str) -> list[dict]:
    rows = [r for r in _load() if r.get("status")=="CLOSED"]
    groups = {}
    for r in rows:
        g = str(r.get(key) or "미분류")
        groups.setdefault(g, []).append(r)

    out = []
    for g, items in groups.items():
        pnls = [float(x.get("pnl_krw") or 0) for x in items]
        rets = [float(x.get("return_pct") or 0) for x in items if x.get("return_pct") is not None]
        wins = sum(1 for x in pnls if x > 0)
        out.append({
            key:g,
            "trades":len(items),
            "win_rate":wins/len(items)*100 if items else None,
            "pnl_krw":sum(pnls),
            "avg_return":sum(rets)/len(rets) if rets else None,
        })
    return sorted(out, key=lambda x:x["pnl_krw"], reverse=True)

def get_open_trade(stock_code: str) -> dict | None:
    code = str(stock_code).zfill(6)
    for r in _load():
        if r.get("stock_code") == code and r.get("status") == "OPEN":
            return r
    return None

def record_add_unit(trade_id: str, fill_price: float, fill_qty: int, unit_risk_krw: float, memo: str = "") -> dict:
    """기존 OPEN 거래에 +0.5N 단위 피라미딩을 기록. 최대 3 Units."""
    rows = _load()
    target = next((r for r in rows if r.get("trade_id") == trade_id), None)
    if target is None or target.get("status") != "OPEN":
        raise ValueError("OPEN 거래를 찾을 수 없습니다.")
    units = int(target.get("unit_count") or 1)
    if units >= 3:
        raise ValueError("K-TURTLE 최대 3 Units에 도달했습니다.")
    old_qty = int(target.get("qty") or 0)
    fill_qty = int(fill_qty)
    fill_price = float(fill_price)
    if fill_qty < 1 or fill_price <= 0:
        raise ValueError("추가 체결가/수량을 확인하세요.")
    initial_entry = float(target.get("initial_entry_price") or target.get("entry_price") or 0)
    atr = float(target.get("atr_n") or 0)
    trigger = initial_entry + (0.5 * atr * units)
    if fill_price < trigger:
        raise ValueError(f"추가매수 신호 미도달: {units+1} Unit 기준 {trigger:,.0f}원")
    old_avg = float(target.get("entry_price") or 0)
    new_qty = old_qty + fill_qty
    new_avg = ((old_avg * old_qty) + (fill_price * fill_qty)) / new_qty
    target["initial_entry_price"] = initial_entry
    target["entry_price"] = new_avg
    target["qty"] = new_qty
    target["unit_count"] = units + 1
    target["risk_krw"] = float(target.get("risk_krw") or 0) + float(unit_risk_krw)
    target["memo"] = (str(target.get("memo") or "") + f" | Unit {units+1} 추가 {fill_price:,.0f}원×{fill_qty}주" + (f" ({memo})" if memo else "")).strip(" |")
    _save(rows)
    return target

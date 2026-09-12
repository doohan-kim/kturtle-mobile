from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import json
from datetime import datetime

@dataclass
class Fill:
    symbol: str
    side: str
    qty: int
    price: float
    timestamp: str

class PaperBroker:
    """실제 주문을 절대로 보내지 않는 모의 브로커."""
    def __init__(self, state_path: str = "paper_state.json"):
        self.state_path = Path(state_path)
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            self.state = {"fills": [], "positions": {}}

    def _save(self):
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def place_buy_order(self, symbol: str, qty: int, price: float) -> Fill:
        fill = Fill(
            symbol=symbol,
            side="BUY",
            qty=int(qty),
            price=float(price),
            timestamp=datetime.now().isoformat(timespec="seconds")
        )
        self.state["fills"].append(asdict(fill))
        pos = self.state["positions"].setdefault(symbol, {"qty": 0, "avg_price": 0.0})
        old_qty = int(pos["qty"])
        new_qty = old_qty + qty
        pos["avg_price"] = (
            (old_qty * float(pos["avg_price"]) + qty * price) / new_qty
            if new_qty else 0.0
        )
        pos["qty"] = new_qty
        self._save()
        return fill

    def place_sell_order(self, symbol: str, qty: int, price: float) -> Fill:
        pos = self.state["positions"].get(symbol, {"qty": 0, "avg_price": 0.0})
        if qty > int(pos["qty"]):
            raise ValueError("보유수량보다 많은 매도 요청입니다.")
        fill = Fill(
            symbol=symbol,
            side="SELL",
            qty=int(qty),
            price=float(price),
            timestamp=datetime.now().isoformat(timespec="seconds")
        )
        self.state["fills"].append(asdict(fill))
        pos["qty"] -= qty
        if pos["qty"] == 0:
            pos["avg_price"] = 0.0
        self._save()
        return fill

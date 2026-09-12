from __future__ import annotations
from dataclasses import dataclass
import math
import pandas as pd

@dataclass
class Signal:
    symbol: str
    breakout_type: str
    entry_price: float
    atr_n: float
    unit_qty: int
    initial_stop: float
    next_add_price: float
    risk_krw: float
    volume_ratio: float
    twenty_day_high: float
    fiftyfive_day_high: float

def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    a = df["high"] - df["low"]
    b = (df["high"] - prev_close).abs()
    c = (df["low"] - prev_close).abs()
    return pd.concat([a, b, c], axis=1).max(axis=1)

def turtle_n(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    고전 터틀 방식의 N:
    첫 N은 최초 period개 TR의 단순평균,
    이후 N_t=(period-1)/period*N_{t-1}+1/period*TR_t
    """
    tr = true_range(df)
    out = pd.Series(index=df.index, dtype="float64")
    if len(df) < period:
        return out
    first_idx = df.index[period - 1]
    out.loc[first_idx] = tr.iloc[:period].mean()
    for i in range(period, len(df)):
        prev = out.iloc[i - 1]
        out.iloc[i] = ((period - 1) * prev + tr.iloc[i]) / period
    return out

def prior_channel_high(df: pd.DataFrame, lookback: int) -> pd.Series:
    # 오늘을 제외한 직전 lookback 거래일 최고가
    return df["high"].shift(1).rolling(lookback).max()

def prior_avg_volume(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    # 오늘을 제외한 직전 20거래일 단순평균
    return df["volume"].shift(1).rolling(lookback).mean()

def check_manual_gates(row: pd.Series) -> tuple[bool, list[str]]:
    cols = ["liquidity_ok", "financial_ok", "weekly_trend_ok", "relative_strength_ok"]
    failed = []
    for c in cols:
        v = str(row[c]).strip().lower()
        if v not in ("true", "1", "yes", "y"):
            failed.append(c)
    return len(failed) == 0, failed

def evaluate_entry(
    symbol: str,
    df: pd.DataFrame,
    gate_row: pd.Series,
    config: dict,
    current_portfolio_heat_krw: float = 0.0,
    current_sector_heat_krw: float = 0.0,
) -> tuple[Signal | None, str]:

    required = {"date","open","high","low","close","volume"}
    missing = required - set(df.columns)
    if missing:
        return None, f"데이터 열 부족: {sorted(missing)}"

    if len(df) < 60:
        return None, "최소 60거래일 이상의 데이터가 필요합니다."

    gates_ok, failed = check_manual_gates(gate_row)
    if not gates_ok:
        return None, f"수동 게이트 미통과: {', '.join(failed)}"

    x = df.copy()
    x["N"] = turtle_n(x, int(config["atr_period"]))
    x["H20"] = prior_channel_high(x, 20)
    x["H55"] = prior_channel_high(x, 55)
    x["V20"] = prior_avg_volume(x, 20)

    last = x.iloc[-1]
    if pd.isna(last["N"]) or pd.isna(last["H55"]) or pd.isna(last["V20"]):
        return None, "ATR/채널/거래량 평균 계산 데이터가 부족합니다."

    # 실제 돌파 여부: 당일 고가가 직전 채널을 초과
    break55 = float(last["high"]) > float(last["H55"])
    break20 = float(last["high"]) > float(last["H20"])

    if not (break20 or break55):
        return None, "20일/55일 신고가 돌파 없음"

    vol_ratio = float(last["volume"]) / float(last["V20"]) if float(last["V20"]) > 0 else 0
    if vol_ratio < float(config["volume_multiple"]):
        return None, f"거래량 미달: {vol_ratio:.2f}배"

    breakout_type = "55일 강한 돌파" if break55 else "20일 약한 돌파"

    # 프로토타입에서는 사용자가 확인하는 시점의 종가를 제안가격으로 사용.
    # 실시간 API 도입 시 이 값을 실시간 최우선매도/현재가로 교체한다.
    entry = float(last["close"])
    N = float(last["N"])

    account = float(config["account_equity_krw"])
    risk_budget = account * float(config["risk_per_unit_pct"])
    stop_distance = N * float(config["stop_n"])

    if stop_distance <= 0:
        return None, "ATR(N)이 0 이하입니다."

    qty = math.floor(risk_budget / stop_distance)
    if qty < 1:
        return None, "1 Unit 계산 결과 1주 미만입니다."

    trade_risk = qty * stop_distance
    portfolio_limit = account * float(config["portfolio_heat_limit_pct"])
    sector_limit = account * float(config["sector_heat_limit_pct"])

    if current_portfolio_heat_krw + trade_risk > portfolio_limit + 1e-9:
        return None, "Portfolio Heat 5% 상한 초과"

    if current_sector_heat_krw + trade_risk > sector_limit + 1e-9:
        return None, "Sector Heat 2.5% 상한 초과"

    return Signal(
        symbol=symbol,
        breakout_type=breakout_type,
        entry_price=entry,
        atr_n=N,
        unit_qty=qty,
        initial_stop=entry - float(config["stop_n"]) * N,
        next_add_price=entry + float(config["pyramid_step_n"]) * N,
        risk_krw=trade_risk,
        volume_ratio=vol_ratio,
        twenty_day_high=float(last["H20"]),
        fiftyfive_day_high=float(last["H55"]),
    ), "OK"

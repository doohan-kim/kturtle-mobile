from __future__ import annotations
import math
import pandas as pd

def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift(1)
    return pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs()
    ], axis=1).max(axis=1)

def turtle_n(df: pd.DataFrame, period: int = 20) -> pd.Series:
    tr = true_range(df)
    out = pd.Series(index=df.index, dtype="float64")
    if len(df) < period:
        return out
    out.iloc[period-1] = tr.iloc[:period].mean()
    for i in range(period, len(df)):
        out.iloc[i] = ((period-1) * out.iloc[i-1] + tr.iloc[i]) / period
    return out

def evaluate_price_breakout(
    df: pd.DataFrame,
    volume_multiple: float = 2.0,
):
    need = {"Open","High","Low","Close","Volume"}
    if not need.issubset(df.columns):
        return {"status":"ERROR","reason":"가격 데이터 필수 열 부족"}

    x = df.copy().dropna(subset=["High","Low","Close","Volume"]).sort_index()
    if len(x) < 60:
        return {"status":"WATCH","reason":"최소 60거래일 데이터 필요"}

    x["N"] = turtle_n(x,20)
    x["H20"] = x["High"].shift(1).rolling(20).max()
    x["H55"] = x["High"].shift(1).rolling(55).max()
    x["V20"] = x["Volume"].shift(1).rolling(20).mean()

    last = x.iloc[-1]
    if pd.isna(last["H55"]) or pd.isna(last["N"]) or pd.isna(last["V20"]):
        return {"status":"WATCH","reason":"채널/ATR 계산 데이터 부족"}

    break55 = float(last["High"]) > float(last["H55"])
    break20 = float(last["High"]) > float(last["H20"])
    vol_ratio = float(last["Volume"]) / float(last["V20"]) if float(last["V20"]) > 0 else 0.0

    result = {
        "status":"WATCH",
        "breakout_type":"없음",
        "close":float(last["Close"]),
        "high":float(last["High"]),
        "h20":float(last["H20"]),
        "h55":float(last["H55"]),
        "volume_ratio":vol_ratio,
        "atr_n":float(last["N"]),
    }

    if not (break20 or break55):
        result["reason"] = "20일/55일 신고가 돌파 없음"
        return result

    result["breakout_type"] = "55일 강한 돌파" if break55 else "20일 약한 돌파"

    if vol_ratio < volume_multiple:
        result["reason"] = f"거래량 미달 ({vol_ratio:.2f}배)"
        return result

    result["status"] = "PRICE_PASS"
    result["reason"] = "돌파 + 거래량 조건 충족"
    return result

def calculate_trade_plan(
    price_result: dict,
    account_equity: float = 10_000_000,
    risk_per_unit_pct: float = 0.01,
):
    if price_result.get("status") != "PRICE_PASS":
        return {"status":"NO_PLAN","reason":"가격 Gate 미통과"}

    entry = float(price_result["close"])
    N = float(price_result["atr_n"])
    risk_budget = account_equity * risk_per_unit_pct
    stop_distance = 2.0 * N
    qty = math.floor(risk_budget / stop_distance) if stop_distance > 0 else 0

    if qty < 1:
        return {"status":"NO_PLAN","reason":"1 Unit 계산 결과 1주 미만"}

    return {
        "status":"PLAN_READY",
        "entry_price":entry,
        "unit_qty":qty,
        "risk_budget":risk_budget,
        "risk_krw":qty*stop_distance,
        "initial_stop":entry - 2.0*N,
        "next_add_price":entry + 0.5*N,
        "next_add2_price":entry + 1.0*N,
        "breakout_type":price_result["breakout_type"],
        "atr_n":N,
    }

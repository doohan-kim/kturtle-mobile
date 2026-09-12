from __future__ import annotations
import math
import time
import pandas as pd
import yfinance as yf
import FinanceDataReader as fdr

from price_engine import evaluate_price_breakout, calculate_trade_plan


def get_kr_universe() -> pd.DataFrame:
    """
    FinanceDataReader KRX 상장목록에서 KOSPI/KOSDAQ 종목만 사용.
    ETF/ETN 등이 섞이는 경우를 줄이기 위해 Code 6자리 + Market 필터 적용.
    """
    df = fdr.StockListing("KRX").copy()
    df["Code"] = df["Code"].astype(str).str.zfill(6)
    if "Market" not in df.columns:
        raise RuntimeError("KRX 상장목록에 Market 열이 없습니다.")

    df = df[df["Market"].isin(["KOSPI", "KOSDAQ"])].copy()
    df = df[df["Code"].str.fullmatch(r"\d{6}")].copy()

    keep = [c for c in ["Code","Name","Market","Close","Marcap","Volume"] if c in df.columns]
    return df[keep].drop_duplicates("Code").reset_index(drop=True)


def _yf_symbol(code: str, market: str) -> str:
    return code + (".KS" if market == "KOSPI" else ".KQ")


def _extract_one(batch_df: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    if batch_df is None or batch_df.empty:
        return None

    # 여러 ticker를 한 번에 받은 경우 MultiIndex
    if isinstance(batch_df.columns, pd.MultiIndex):
        # yfinance 버전에 따라 level 순서가 다를 수 있어 양쪽을 시도
        try:
            if ticker in batch_df.columns.get_level_values(1):
                one = batch_df.xs(ticker, axis=1, level=1)
                return one.dropna(how="all")
        except Exception:
            pass
        try:
            if ticker in batch_df.columns.get_level_values(0):
                one = batch_df.xs(ticker, axis=1, level=0)
                return one.dropna(how="all")
        except Exception:
            pass
        return None

    # 단일 ticker
    return batch_df.copy()


def scan_market(
    universe: pd.DataFrame,
    *,
    account_equity: float = 10_000_000,
    risk_per_unit_pct: float = 0.01,
    volume_multiple: float = 2.0,
    chunk_size: int = 80,
    max_candidates: int = 50,
    progress_callback=None,
) -> tuple[pd.DataFrame, dict]:
    """
    전 종목 가격 스캔. DART 호출 없음.
    반환:
      candidates DataFrame
      stats dict
    """
    rows = universe.copy()
    rows["Ticker"] = rows.apply(lambda r: _yf_symbol(r["Code"], r["Market"]), axis=1)

    results = []
    total = len(rows)
    chunks = math.ceil(total / chunk_size)

    ok_price = 0
    errors = 0

    for ci in range(chunks):
        part = rows.iloc[ci*chunk_size:(ci+1)*chunk_size].copy()
        tickers = part["Ticker"].tolist()

        try:
            batch = yf.download(
                tickers=tickers,
                period="6mo",
                interval="1d",
                auto_adjust=False,
                progress=False,
                group_by="column",
                threads=True,
            )
        except Exception:
            batch = None

        for _, meta in part.iterrows():
            ticker = meta["Ticker"]
            try:
                one = _extract_one(batch, ticker)
                if one is None or one.empty:
                    errors += 1
                    continue

                needed = ["Open","High","Low","Close","Volume"]
                if not all(c in one.columns for c in needed):
                    errors += 1
                    continue

                one = one[needed].copy()
                sig = evaluate_price_breakout(one, volume_multiple=volume_multiple)
                ok_price += 1

                if sig.get("status") != "PRICE_PASS":
                    continue

                plan = calculate_trade_plan(
                    sig,
                    account_equity=account_equity,
                    risk_per_unit_pct=risk_per_unit_pct,
                )
                if plan.get("status") != "PLAN_READY":
                    continue

                results.append({
                    "Code": meta["Code"],
                    "Name": meta["Name"],
                    "Market": meta["Market"],
                    "Ticker": ticker,
                    "Breakout": sig["breakout_type"],
                    "Close": round(sig["close"], 2),
                    "H20": round(sig["h20"], 2),
                    "H55": round(sig["h55"], 2),
                    "VolumeRatio": round(sig["volume_ratio"], 2),
                    "ATR_N": round(sig["atr_n"], 2),
                    "UnitQty": int(plan["unit_qty"]),
                    "Stop2N": round(plan["initial_stop"], 2),
                    "Add0_5N": round(plan["next_add_price"], 2),
                    "RiskKRW": round(plan["risk_krw"], 0),
                })
            except Exception:
                errors += 1

        if progress_callback:
            progress_callback(min((ci+1)/chunks, 1.0), ci+1, chunks, len(results))

        # 네트워크 과부하 완화
        time.sleep(0.15)

    out = pd.DataFrame(results)
    if not out.empty:
        strength = out["Breakout"].map({"55일 강한 돌파":2, "20일 약한 돌파":1}).fillna(0)
        out["_strength"] = strength
        out = out.sort_values(
            ["_strength","VolumeRatio"],
            ascending=[False,False]
        ).drop(columns="_strength").head(max_candidates).reset_index(drop=True)

    stats = {
        "universe": total,
        "price_ok": ok_price,
        "errors": errors,
        "candidates": len(out),
    }
    return out, stats

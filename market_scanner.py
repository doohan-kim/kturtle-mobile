from __future__ import annotations
import math
import time
import pandas as pd
import yfinance as yf
import FinanceDataReader as fdr

from price_engine import evaluate_price_breakout, calculate_trade_plan


def get_kr_universe() -> pd.DataFrame:
    base = fdr.StockListing("KRX").copy()
    base["Code"] = base["Code"].astype(str).str.zfill(6)
    base = base[base["Market"].isin(["KOSPI", "KOSDAQ"])].copy()

    try:
        desc = fdr.StockListing("KRX-DESC").copy()
        if "Code" in desc.columns:
            desc["Code"] = desc["Code"].astype(str).str.zfill(6)
            cols = [c for c in ["Code","Sector","Industry"] if c in desc.columns]
            if cols:
                base = base.merge(desc[cols].drop_duplicates("Code"), on="Code", how="left")
    except Exception:
        pass

    if "Sector" in base.columns:
        base["SectorLabel"] = base["Sector"]
    else:
        base["SectorLabel"] = None

    if "Industry" in base.columns:
        base["SectorLabel"] = base["SectorLabel"].fillna(base["Industry"])

    base["SectorLabel"] = base["SectorLabel"].fillna(base["Market"] + " 기타")
    base["SectorLabel"] = base["SectorLabel"].astype(str).str.strip()
    base.loc[base["SectorLabel"].isin(["", "nan", "None"]), "SectorLabel"] = base["Market"] + " 기타"

    keep = [c for c in ["Code","Name","Market","SectorLabel","Close","Marcap","Volume"] if c in base.columns]
    return base[keep].drop_duplicates("Code").reset_index(drop=True)


def _yf_symbol(code: str, market: str) -> str:
    return code + (".KS" if market == "KOSPI" else ".KQ")


def _extract_one(batch_df: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    if batch_df is None or batch_df.empty:
        return None
    if isinstance(batch_df.columns, pd.MultiIndex):
        try:
            if ticker in batch_df.columns.get_level_values(1):
                return batch_df.xs(ticker, axis=1, level=1).dropna(how="all")
        except Exception:
            pass
        try:
            if ticker in batch_df.columns.get_level_values(0):
                return batch_df.xs(ticker, axis=1, level=0).dropna(how="all")
        except Exception:
            pass
        return None
    return batch_df.copy()


def _period_return(close: pd.Series, lookback: int) -> float | None:
    s = close.dropna()
    if len(s) <= lookback:
        return None
    start = float(s.iloc[-lookback-1])
    end = float(s.iloc[-1])
    if start <= 0:
        return None
    return (end / start - 1.0) * 100.0


def add_sector_relative_strength(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return candidates.copy()

    x = candidates.copy()
    x["RS20"] = pd.to_numeric(x["RS20"], errors="coerce")
    x["RS60"] = pd.to_numeric(x["RS60"], errors="coerce")

    x["RS20Rank"] = x.groupby("Sector")["RS20"].rank(pct=True, method="average").mul(100)
    x["RS60Rank"] = x.groupby("Sector")["RS60"].rank(pct=True, method="average").mul(100)
    x["RSScore"] = x[["RS20Rank","RS60Rank"]].mean(axis=1)
    return x


def select_sector_leaders(
    candidates: pd.DataFrame,
    leaders_per_sector: int = 1,
    max_total: int = 30,
) -> pd.DataFrame:
    """
    같은 섹터 내 상대강도 최강 종목을 대장주로 선정.

    우선순위:
    1) RSScore = 섹터 내 20일/60일 수익률 백분위 평균
    2) RS60
    3) RS20
    4) 거래량배수
    """
    if candidates is None or candidates.empty:
        return candidates.copy()

    x = add_sector_relative_strength(candidates)
    x = x.sort_values(
        ["Sector","RSScore","RS60","RS20","VolumeRatio"],
        ascending=[True,False,False,False,False]
    )

    picked = x.groupby("Sector", group_keys=False).head(int(leaders_per_sector)).copy()
    picked = picked.sort_values(
        ["RSScore","RS60","RS20","VolumeRatio"],
        ascending=[False,False,False,False]
    ).head(int(max_total))

    return picked.reset_index(drop=True)


def scan_market(
    universe: pd.DataFrame,
    *,
    account_equity: float = 10_000_000,
    risk_per_unit_pct: float = 0.01,
    volume_multiple: float = 2.0,
    chunk_size: int = 80,
    max_candidates: int = 100,
    progress_callback=None,
) -> tuple[pd.DataFrame, dict]:
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

                rs20 = _period_return(one["Close"], 20)
                rs60 = _period_return(one["Close"], 60)

                results.append({
                    "Code": meta["Code"],
                    "Name": meta["Name"],
                    "Market": meta["Market"],
                    "Sector": meta.get("SectorLabel", f'{meta["Market"]} 기타'),
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
                    "RS20": round(rs20, 2) if rs20 is not None else None,
                    "RS60": round(rs60, 2) if rs60 is not None else None,
                })
            except Exception:
                errors += 1

        if progress_callback:
            progress_callback(min((ci+1)/chunks,1.0), ci+1, chunks, len(results))
        time.sleep(0.15)

    out = pd.DataFrame(results)
    if not out.empty:
        out = add_sector_relative_strength(out)
        out = out.sort_values(
            ["RSScore","RS60","RS20","VolumeRatio"],
            ascending=[False,False,False,False]
        ).head(max_candidates).reset_index(drop=True)

    stats = {
        "universe": total,
        "price_ok": ok_price,
        "errors": errors,
        "raw_candidates": len(out),
    }
    return out, stats

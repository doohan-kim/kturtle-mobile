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
            cols = [c for c in ["Code", "Sector", "Industry"] if c in desc.columns]
            if cols:
                base = base.merge(
                    desc[cols].drop_duplicates("Code"),
                    on="Code",
                    how="left"
                )
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
    base.loc[
        base["SectorLabel"].isin(["", "nan", "None"]),
        "SectorLabel"
    ] = base["Market"] + " 기타"

    keep = [
        c for c in
        ["Code", "Name", "Market", "SectorLabel", "Close", "Marcap", "Volume"]
        if c in base.columns
    ]
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

    start = float(s.iloc[-lookback - 1])
    end = float(s.iloc[-1])

    if start <= 0:
        return None

    return (end / start - 1.0) * 100.0


def add_sectorwide_relative_strength(all_strength: pd.DataFrame) -> pd.DataFrame:
    """
    섹터 전체 종목을 기준으로 상대강도 계산.

    RS20Rank = 같은 섹터 전체 종목 중 최근 20거래일 수익률 백분위
    RS60Rank = 같은 섹터 전체 종목 중 최근 60거래일 수익률 백분위
    RSScore  = (RS20Rank + RS60Rank) / 2

    0~100점. 높을수록 섹터 전체에서 상대적으로 강함.
    """
    if all_strength is None or all_strength.empty:
        return all_strength.copy()

    x = all_strength.copy()
    x["RS20"] = pd.to_numeric(x["RS20"], errors="coerce")
    x["RS60"] = pd.to_numeric(x["RS60"], errors="coerce")

    x["RS20Rank"] = (
        x.groupby("Sector")["RS20"]
        .rank(pct=True, method="average")
        .mul(100)
    )

    x["RS60Rank"] = (
        x.groupby("Sector")["RS60"]
        .rank(pct=True, method="average")
        .mul(100)
    )

    x["RSScore"] = x[["RS20Rank", "RS60Rank"]].mean(axis=1)
    return x


def select_sector_leaders(
    candidates: pd.DataFrame,
    leaders_per_sector: int = 1,
    max_total: int = 30,
) -> pd.DataFrame:
    """
    후보군에는 이미 섹터 전체 기준 RSScore가 붙어 있다는 전제.

    대장주 우선순위:
    1) 섹터 전체 기준 RSScore
    2) RS60
    3) RS20
    4) 거래량 배수
    """
    if candidates is None or candidates.empty:
        return candidates.copy()

    x = candidates.copy()

    for col in ["RSScore", "RS60", "RS20", "VolumeRatio"]:
        x[col] = pd.to_numeric(x[col], errors="coerce")

    x = x.sort_values(
        ["Sector", "RSScore", "RS60", "RS20", "VolumeRatio"],
        ascending=[True, False, False, False, False]
    )

    picked = (
        x.groupby("Sector", group_keys=False)
        .head(int(leaders_per_sector))
        .copy()
    )

    picked = picked.sort_values(
        ["RSScore", "RS60", "RS20", "VolumeRatio"],
        ascending=[False, False, False, False]
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
    """
    1) KOSPI/KOSDAQ 전체 종목의 RS20/RS60 계산
    2) 섹터 전체 기준 RSScore 계산
    3) 가격 Gate 통과 후보에 RSScore 결합
    """

    rows = universe.copy()
    rows["Ticker"] = rows.apply(
        lambda r: _yf_symbol(r["Code"], r["Market"]),
        axis=1
    )

    total = len(rows)
    chunks = math.ceil(total / chunk_size)

    all_strength_rows = []
    price_candidates = []

    ok_price = 0
    errors = 0

    for ci in range(chunks):
        part = rows.iloc[
            ci * chunk_size:(ci + 1) * chunk_size
        ].copy()

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

                needed = ["Open", "High", "Low", "Close", "Volume"]
                if not all(c in one.columns for c in needed):
                    errors += 1
                    continue

                one = one[needed].copy()

                rs20 = _period_return(one["Close"], 20)
                rs60 = _period_return(one["Close"], 60)

                # 섹터 전체 RS 계산용 데이터는 가격 Gate 통과 여부와 무관하게 저장
                all_strength_rows.append({
                    "Code": meta["Code"],
                    "Name": meta["Name"],
                    "Market": meta["Market"],
                    "Sector": meta.get(
                        "SectorLabel",
                        f'{meta["Market"]} 기타'
                    ),
                    "RS20": round(rs20, 4) if rs20 is not None else None,
                    "RS60": round(rs60, 4) if rs60 is not None else None,
                })

                sig = evaluate_price_breakout(
                    one,
                    volume_multiple=volume_multiple
                )

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

                price_candidates.append({
                    "Code": meta["Code"],
                    "Name": meta["Name"],
                    "Market": meta["Market"],
                    "Sector": meta.get(
                        "SectorLabel",
                        f'{meta["Market"]} 기타'
                    ),
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
            progress_callback(
                min((ci + 1) / chunks, 1.0),
                ci + 1,
                chunks,
                len(price_candidates)
            )

        time.sleep(0.15)

    strength_df = pd.DataFrame(all_strength_rows)
    strength_df = add_sectorwide_relative_strength(strength_df)
    sector_strength = calculate_sector_strength(strength_df)

    candidate_df = pd.DataFrame(price_candidates)

    if not candidate_df.empty and not strength_df.empty:
        candidate_df = candidate_df.merge(
            strength_df[
                [
                    "Code",
                    "RS20",
                    "RS60",
                    "RS20Rank",
                    "RS60Rank",
                    "RSScore"
                ]
            ],
            on="Code",
            how="left"
        )

        if not sector_strength.empty:
            candidate_df = candidate_df.merge(
                sector_strength[[
                    "Sector","SectorRank","SectorScore",
                    "SectorRet20","SectorRet60"
                ]],
                on="Sector",
                how="left"
            )

        candidate_df = candidate_df.sort_values(
            ["SectorRank","RSScore","RS60","RS20","VolumeRatio"],
            ascending=[True,False,False,False,False]
        ).head(max_candidates).reset_index(drop=True)

    stats = {
        "universe": total,
        "price_ok": ok_price,
        "errors": errors,
        "raw_candidates": len(candidate_df),
        "rs_universe": len(strength_df),
        "sector_count": len(sector_strength),
    }

    # sector_strength is also returned so the app can show top sectors
    return candidate_df, stats, sector_strength


def calculate_sector_strength(all_strength: pd.DataFrame) -> pd.DataFrame:
    """
    전체 섹터끼리 비교하는 섹터 상대강도.

    각 섹터:
      SectorRet20 = 구성종목 20일 수익률의 중앙값
      SectorRet60 = 구성종목 60일 수익률의 중앙값

    그리고 전체 섹터 내 백분위:
      Sector20Rank, Sector60Rank
      SectorScore = (Sector20Rank + Sector60Rank) / 2

    0~100점. 높을수록 전체 시장에서 강한 섹터.
    """
    if all_strength is None or all_strength.empty:
        return pd.DataFrame()

    x = all_strength.copy()
    x["RS20"] = pd.to_numeric(x["RS20"], errors="coerce")
    x["RS60"] = pd.to_numeric(x["RS60"], errors="coerce")

    sec = (
        x.groupby("Sector", as_index=False)
         .agg(
             SectorRet20=("RS20", "median"),
             SectorRet60=("RS60", "median"),
             SectorCount=("Code", "count"),
         )
    )

    sec["Sector20Rank"] = sec["SectorRet20"].rank(pct=True, method="average") * 100
    sec["Sector60Rank"] = sec["SectorRet60"].rank(pct=True, method="average") * 100
    sec["SectorScore"] = sec[["Sector20Rank","Sector60Rank"]].mean(axis=1)

    sec = sec.sort_values(
        ["SectorScore","SectorRet60","SectorRet20"],
        ascending=[False,False,False]
    ).reset_index(drop=True)

    sec["SectorRank"] = range(1, len(sec)+1)
    return sec


def select_top_sector_leaders(
    candidates: pd.DataFrame,
    sector_strength: pd.DataFrame,
    top_n_sectors: int = 3,
) -> pd.DataFrame:
    """
    전체 섹터 상위 N개만 선택하고,
    각 섹터의 가격 Gate 후보 중 RSScore가 가장 높은 1종목을 대장주로 선택.
    """
    if candidates is None or candidates.empty or sector_strength is None or sector_strength.empty:
        return pd.DataFrame()

    top_sec = sector_strength.head(int(top_n_sectors)).copy()
    x = candidates.merge(
        top_sec[[
            "Sector","SectorRank","SectorScore",
            "SectorRet20","SectorRet60"
        ]],
        on="Sector",
        how="inner"
    )

    if x.empty:
        return x

    x = x.sort_values(
        ["SectorRank","RSScore","RS60","RS20","VolumeRatio"],
        ascending=[True,False,False,False,False]
    )

    leaders = (
        x.groupby("Sector", group_keys=False)
         .head(1)
         .copy()
    )

    return leaders.sort_values("SectorRank").reset_index(drop=True)

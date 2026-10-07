from __future__ import annotations
import math
import time
import pandas as pd
import yfinance as yf
import FinanceDataReader as fdr

from price_engine import evaluate_price_breakout, calculate_trade_plan


JP_SECTOR_KO = {
    "水産・農林業":"수산·농림업", "鉱業":"광업", "建設業":"건설업", "食料品":"식료품",
    "繊維製品":"섬유제품", "パルプ・紙":"펄프·종이", "化学":"화학", "医薬品":"의약품",
    "石油・石炭製品":"석유·석탄제품", "ゴム製品":"고무제품", "ガラス・土石製品":"유리·토석제품",
    "鉄鋼":"철강", "非鉄金属":"비철금속", "金属製品":"금속제품", "機械":"기계",
    "電気機器":"전기기기", "輸送用機器":"수송용기기", "精密機器":"정밀기기", "その他製品":"기타제품",
    "電気・ガス業":"전기·가스업", "陸運業":"육상운송업", "海運業":"해운업", "空運業":"항공운송업",
    "倉庫・運輸関連業":"창고·운송관련업", "情報・通信業":"정보·통신업", "卸売業":"도매업",
    "小売業":"소매업", "銀行業":"은행업", "証券、商品先物取引業":"증권·상품선물업",
    "保険業":"보험업", "その他金融業":"기타금융업", "不動産業":"부동산업", "サービス業":"서비스업",
    "プライム（内国株式）":"프라임(일본 국내주식)", "スタンダード（内国株式）":"스탠다드(일본 국내주식)",
    "グロース（内国株式）":"그로스(일본 국내주식)", "プライム":"프라임", "スタンダード":"스탠다드", "グロース":"그로스",
}

# JPX 종목명은 고유명사라 기계 번역 대신 주요 종목의 공식/통용 한국어명을 우선 사용한다.
# 미등록 종목은 일본어 원문을 유지해 오역을 방지한다.
JP_NAME_KO = {
    "古野電気":"후루노전기", "タムロン":"탐론", "牧野フライス製作所":"마키노프라이스제작소",
    "トヨタ自動車":"도요타자동차", "ソニーグループ":"소니그룹", "日立製作所":"히타치제작소",
    "三菱重工業":"미쓰비시중공업", "東京エレクトロン":"도쿄일렉트론", "三菱商事":"미쓰비시상사",
    "三菱ＵＦＪフィナンシャル・グループ":"미쓰비시UFJ파이낸셜그룹", "みずほフィナンシャルグループ":"미즈호파이낸셜그룹",
}

def _jp_ko_label(value, mapping):
    x = str(value).strip()
    return mapping.get(x, x)


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



def get_jp_universe() -> pd.DataFrame:
    """일본 TSE 유니버스. JPX 공식 목록 우선, 실패 시 FDR TSE로 안전 폴백.

    반환 attrs: universe_source, universe_warning
    """
    import io, re
    from urllib.parse import urljoin
    import requests

    errors = []
    try:
        page_url = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
        headers = {"User-Agent": "Mozilla/5.0 (K-TURTLE Mobile; +https://www.jpx.co.jp/)"}
        r = requests.get(page_url, headers=headers, timeout=12)
        r.raise_for_status()
        links = re.findall(r'href=["\\\']([^"\\\']+\.(?:xlsx?|xls)(?:\?[^"\\\']*)?)["\\\']', r.text, flags=re.I)
        if not links:
            links = [x for x in re.findall(r'href=["\\\']([^"\\\']+)["\\\']', r.text, flags=re.I) if '.xls' in x.lower()]
        if not links:
            raise RuntimeError("JPX Excel 링크 없음")
        file_url = urljoin(page_url, links[0])
        fr = requests.get(file_url, headers=headers, timeout=20)
        fr.raise_for_status()
        base = pd.read_excel(io.BytesIO(fr.content))
        def pick(names):
            return next((n for n in names if n in base.columns), None)
        code_col = pick(["コード", "Code", "Local Code"])
        name_col = pick(["銘柄名", "Name", "Issue Name"])
        market_col = pick(["市場・商品区分", "市場区分", "Market Segment", "Market/Products"])
        sector_col = pick(["33業種区分", "33業種区分名", "33 Sector(name)", "Sector"])
        if code_col is None or name_col is None:
            raise RuntimeError("JPX Excel 열 구조 인식 실패")
        out = pd.DataFrame({
            "Code": base[code_col].astype(str).str.replace(r"\.0$", "", regex=True).str.strip(),
            "Name": base[name_col].astype(str).str.strip(),
            "Market": base[market_col].astype(str).str.strip() if market_col else "TSE",
            "SectorLabel": base[sector_col].astype(str).str.strip() if sector_col else "TSE 기타",
        })
        if market_col:
            m=out["Market"]
            good=m.str.contains("プライム|スタンダード|グロース|Prime|Standard|Growth",case=False,regex=True,na=False)
            bad=m.str.contains("ETF|ETN|REIT|投資|PRO|外国|Foreign|出資|優先",case=False,regex=True,na=False)
            out=out[good & ~bad].copy()
        out=out[out["Code"].str.match(r"^[0-9A-Z]{4}$",na=False)].drop_duplicates("Code").reset_index(drop=True)
        out["Name"] = out["Name"].map(lambda x: _jp_ko_label(x, JP_NAME_KO))
        out["Market"] = out["Market"].map(lambda x: _jp_ko_label(x, JP_SECTOR_KO))
        out["SectorLabel"] = out["SectorLabel"].map(lambda x: _jp_ko_label(x, JP_SECTOR_KO))
        if len(out) < 1000:
            raise RuntimeError(f"JPX 응답 종목수 비정상({len(out)})")
        out.attrs["universe_source"]="JPX 공식 TSE 목록"
        out.attrs["universe_warning"]=""
        return out
    except Exception as e:
        errors.append(str(e))

    # JPX가 일시적으로 차단/지연돼도 앱 전체를 죽이지 않는다.
    try:
        base = fdr.StockListing("TSE").copy()
        if base.empty:
            raise RuntimeError("FDR TSE 목록 비어 있음")
        code_col = next((c for c in ["Code","Symbol"] if c in base.columns), None)
        name_col = next((c for c in ["Name","Company"] if c in base.columns), None)
        market_col = next((c for c in ["Market","MarketId","Exchange"] if c in base.columns), None)
        sector_col = next((c for c in ["Sector","Industry"] if c in base.columns), None)
        if not code_col:
            raise RuntimeError(f"FDR TSE 코드 열 없음: {list(base.columns)}")
        out=pd.DataFrame()
        out["Code"]=base[code_col].astype(str).str.replace(r"\.0$","",regex=True).str.strip()
        out["Name"]=base[name_col].astype(str).str.strip() if name_col else out["Code"]
        out["Market"]=base[market_col].astype(str).str.strip() if market_col else "TSE"
        out["SectorLabel"]=base[sector_col].astype(str).str.strip() if sector_col else "TSE 기타"
        out=out[out["Code"].str.match(r"^[0-9A-Z]{4}$",na=False)].drop_duplicates("Code").reset_index(drop=True)
        out["Name"] = out["Name"].map(lambda x: _jp_ko_label(x, JP_NAME_KO))
        out["Market"] = out["Market"].map(lambda x: _jp_ko_label(x, JP_SECTOR_KO))
        out["SectorLabel"] = out["SectorLabel"].map(lambda x: _jp_ko_label(x, JP_SECTOR_KO))
        out.attrs["universe_source"]="FinanceDataReader TSE 폴백"
        out.attrs["universe_warning"]=("JPX 공식 목록 연결 실패로 대체 목록을 사용합니다. "
            f"현재 {len(out):,}종목이며 전체시장 여부를 보장하지 않습니다. JPX 오류: {' / '.join(errors)}")
        return out
    except Exception as e:
        errors.append(str(e))
        raise RuntimeError("일본 종목목록을 불러오지 못했습니다: " + " / ".join(errors))

def _yf_symbol(code: str, market: str, country: str = "KR") -> str:
    if country == "JP":
        return str(code) + ".T"
    return str(code) + (".KS" if market == "KOSPI" else ".KQ")


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
    country: str = "KR",
    adtv_min_local: float = 2_000_000_000,
) -> tuple[pd.DataFrame, dict]:
    """
    1) KOSPI/KOSDAQ 전체 종목의 RS20/RS60 계산
    2) 섹터 전체 기준 RSScore 계산
    3) 가격 Gate 통과 후보에 RSScore 결합
    """

    rows = universe.copy()
    rows["Ticker"] = rows.apply(
        lambda r: _yf_symbol(r["Code"], r["Market"], country),
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
                period="3y",
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

                # 유동성 Gate: 직전 20일 평균 거래대금 >= 20억원
                # 그리고 1 Unit 주문금액 <= 평균 거래대금의 0.5%
                adtv20 = float(sig.get("adtv20") or 0.0)
                unit_order_krw = float(plan["entry_price"]) * int(plan["unit_qty"])
                order_adtv_pct = (unit_order_krw / adtv20 * 100.0) if adtv20 > 0 else None
                liquidity_pass = (
                    adtv20 >= float(adtv_min_local)
                    and order_adtv_pct is not None
                    and order_adtv_pct <= 0.5
                )
                if not liquidity_pass:
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
                    "TodayHigh": round(sig.get("high", sig["close"]), 2),
                    "H20": round(sig["h20"], 2),
                    "H55": round(sig["h55"], 2),
                    "Break20Pct": round(sig.get("breakout20_pct") or 0.0, 4),
                    "Break55Pct": round(sig.get("breakout55_pct") or 0.0, 4),
                    "VolumeRatio": round(sig["volume_ratio"], 2),
                    "ATR_N": round(sig["atr_n"], 2),
                    "UnitQty": int(plan["unit_qty"]),
                    "Stop2N": round(plan["initial_stop"], 2),
                    "Add0_5N": round(plan["next_add_price"], 2),
                    "RiskKRW": round(plan["risk_krw"], 0),
                    "WeeklyMA120": round(sig.get("weekly_ma120", 0), 2),
                    "WeeklyTrendPass": bool(sig.get("weekly_trend_pass", False)),
                    "ADTV20": round(adtv20, 0),
                    "UnitOrderKRW": round(unit_order_krw, 0),
                    "OrderADTVPct": round(order_adtv_pct, 4),
                    "LiquidityPass": True,
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
    전체 섹터 상대강도 + Breadth.

    SectorRet20 / SectorRet60:
      섹터 구성종목의 20일/60일 수익률 중앙값.

    Breadth:
      Up20Ratio = RS20 > 0 인 종목 비율
      Up60Ratio = RS60 > 0 인 종목 비율

    SectorScore:
      20일/60일 중앙값의 전체 섹터 백분위 평균.
    """
    if all_strength is None or all_strength.empty:
        return pd.DataFrame()

    x = all_strength.copy()
    x["RS20"] = pd.to_numeric(x["RS20"], errors="coerce")
    x["RS60"] = pd.to_numeric(x["RS60"], errors="coerce")

    rows = []
    for sector, g in x.groupby("Sector"):
        valid20 = g["RS20"].dropna()
        valid60 = g["RS60"].dropna()
        rows.append({
            "Sector": sector,
            "SectorRet20": valid20.median() if not valid20.empty else None,
            "SectorRet60": valid60.median() if not valid60.empty else None,
            "SectorCount": len(g),
            "Valid20Count": len(valid20),
            "Valid60Count": len(valid60),
            "Up20Count": int((valid20 > 0).sum()),
            "Up60Count": int((valid60 > 0).sum()),
            "Up20Ratio": float((valid20 > 0).mean() * 100) if len(valid20) else None,
            "Up60Ratio": float((valid60 > 0).mean() * 100) if len(valid60) else None,
        })

    sec = pd.DataFrame(rows)

    sec["Sector20Rank"] = sec["SectorRet20"].rank(pct=True, method="average") * 100
    sec["Sector60Rank"] = sec["SectorRet60"].rank(pct=True, method="average") * 100
    sec["SectorScore"] = sec[["Sector20Rank", "Sector60Rank"]].mean(axis=1)

    sec = sec.sort_values(
        ["SectorScore", "SectorRet60", "SectorRet20"],
        ascending=[False, False, False]
    ).reset_index(drop=True)

    sec["SectorRank"] = range(1, len(sec) + 1)
    return sec


def select_top_sector_leaders(
    candidates: pd.DataFrame,
    sector_strength: pd.DataFrame,
    top_n_sectors: int = 3,
) -> pd.DataFrame:
    """
    v2.1:
    1) 가격 Gate 통과 후보를 먼저 확정
    2) 그 후보들이 속한 섹터만 전체시장 섹터강도표에서 조회
    3) 해당 섹터 중 SectorScore 상위 3개 선택
    4) 각 선택 섹터에서 RSScore 최상위 돌파주 1개 선정
    """
    if candidates is None or candidates.empty or sector_strength is None or sector_strength.empty:
        return pd.DataFrame()

    active_sectors = candidates["Sector"].dropna().astype(str).unique().tolist()
    eligible = sector_strength[
        sector_strength["Sector"].astype(str).isin(active_sectors)
    ].copy()

    if eligible.empty:
        return pd.DataFrame()

    eligible = eligible.sort_values(
        ["SectorScore", "SectorRet60", "SectorRet20"],
        ascending=[False, False, False]
    ).head(int(top_n_sectors)).reset_index(drop=True)

    # Rank is now among sectors that actually have a breakout candidate.
    eligible["ActiveSectorRank"] = range(1, len(eligible) + 1)

    x = candidates.merge(
        eligible[[
            "Sector", "ActiveSectorRank", "SectorScore",
            "SectorRet20", "SectorRet60"
        ]],
        on="Sector",
        how="inner",
        suffixes=("", "_eligible")
    )

    if x.empty:
        return x

    # Avoid duplicated score columns from earlier scan merge.
    for col in ["SectorScore", "SectorRet20", "SectorRet60"]:
        alt = col + "_eligible"
        if alt in x.columns:
            x[col] = x[alt]
            x = x.drop(columns=[alt])

    x = x.sort_values(
        ["ActiveSectorRank", "RSScore", "RS60", "RS20", "VolumeRatio"],
        ascending=[True, False, False, False, False]
    )

    leaders = x.groupby("Sector", group_keys=False).head(1).copy()
    leaders["SectorRank"] = leaders["ActiveSectorRank"]
    return leaders.sort_values("ActiveSectorRank").reset_index(drop=True)


def breakout_sector_top3(
    candidates: pd.DataFrame,
    sector_strength: pd.DataFrame,
    top_n: int = 3
) -> pd.DataFrame:
    """
    가격 Gate 통과 종목이 존재하는 섹터만 대상으로 강도 TOP N.
    각 섹터의 돌파 후보 개수도 함께 반환.
    """
    if candidates is None or candidates.empty or sector_strength is None or sector_strength.empty:
        return pd.DataFrame()

    active = candidates["Sector"].dropna().astype(str).unique()

    breakout_counts = (
        candidates.groupby("Sector")
        .size()
        .rename("BreakoutCount")
        .reset_index()
    )

    out = sector_strength[
        sector_strength["Sector"].astype(str).isin(active)
    ].copy()

    out = out.merge(
        breakout_counts,
        on="Sector",
        how="left"
    )
    out["BreakoutCount"] = out["BreakoutCount"].fillna(0).astype(int)

    out = out.sort_values(
        ["SectorScore", "SectorRet60", "SectorRet20"],
        ascending=[False, False, False]
    ).head(int(top_n)).reset_index(drop=True)

    out["SectorRank"] = range(1, len(out) + 1)
    return out

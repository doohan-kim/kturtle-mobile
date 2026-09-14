from __future__ import annotations
import os
from datetime import date, timedelta
import pandas as pd
import streamlit as st

from opendart_client import OpenDartClient
from dart_financials import build_quarter_history, financing_summary, financial_gate_v07
from price_source import fetch_kr_stock
from price_engine import evaluate_price_breakout, calculate_trade_plan
from heat_engine import evaluate_heat
from financial_cache import save_financial_gate, load_financial_gate, cache_age_hours
from candidate_finance_cache import save_candidate_finance, get_candidate_finance
from trade_journal import record_entry, close_trade, list_trades, delete_trade, performance_summary, grouped_performance, export_csv_bytes, import_csv_bytes
from market_scanner import get_kr_universe, scan_market, select_top_sector_leaders, breakout_sector_top3

st.set_page_config(page_title="K-TURTLE Mobile", page_icon="🐢", layout="centered")

st.markdown("""
<style>
.block-container {
    max-width: 760px;
    padding-top: 0.8rem;
    padding-left: 0.8rem;
    padding-right: 0.8rem;
    padding-bottom: 4rem;
}
h1 { font-size: 2rem !important; margin-bottom: .25rem !important; }
h2, h3 { margin-top: .8rem !important; }
div[data-testid="stMetric"] {
    border: 1px solid rgba(120,120,120,.18);
    border-radius: 14px;
    padding: 10px 12px;
}
.stButton > button {
    width: 100%;
    min-height: 46px;
    border-radius: 12px;
    font-weight: 700;
}
.kt-card {
    border: 1px solid rgba(120,120,120,.22);
    border-radius: 16px;
    padding: 14px;
    margin: 10px 0;
}
.kt-title {
    font-size: 1.08rem;
    font-weight: 800;
    margin-bottom: 4px;
}
.kt-sub {
    opacity: .72;
    font-size: .86rem;
    margin-bottom: 8px;
}
.kt-grid {
    display:grid;
    grid-template-columns: repeat(2, minmax(0,1fr));
    gap:8px 12px;
    font-size:.92rem;
}
.kt-label {opacity:.65;}
.kt-val {font-weight:700;}
@media (max-width: 600px) {
    .block-container {padding-left:.55rem;padding-right:.55rem;}
    h1 {font-size:1.65rem !important;}
    .kt-grid {grid-template-columns:1fr 1fr;}
}
</style>
""", unsafe_allow_html=True)


def get_key():
    try:
        if "OPENDART_API_KEY" in st.secrets:
            return str(st.secrets["OPENDART_API_KEY"])
    except Exception:
        pass
    return os.getenv("OPENDART_API_KEY")

@st.cache_resource(show_spinner=False)
def client(k):
    return OpenDartClient(api_key=k)

@st.cache_data(ttl=21600, show_spinner=False)
def stock_map(k):
    cs = client(k).corp_codes(allow_stale_cache=True)
    return {c.stock_code:(c.corp_code,c.corp_name) for c in cs if c.stock_code}

@st.cache_data(ttl=86400, show_spinner=False)
def universe_cached():
    return get_kr_universe()

def dart_gate(stock_code: str):
    k = get_key()
    if not k:
        raise RuntimeError("OpenDART 인증키 없음")

    mp = stock_map(k)
    s = stock_code.zfill(6)
    if s not in mp:
        raise RuntimeError("DART 종목코드 매핑 실패")

    corp, name = mp[s]
    c = client(k)
    hist = build_quarter_history(c, corp, 2)
    end = date.today()
    begin = end - timedelta(days=365)
    events = c.financing_events(corp, begin.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    counts, total = financing_summary(events)
    gate, reasons = financial_gate_v07(hist, total)

    save_financial_gate(s, {
        "gate": gate,
        "reasons": reasons,
        "counts": counts,
        "corp": corp,
        "name": name,
    })
    return gate, reasons, counts, corp, name, "LIVE", 0.0


st.title("🐢 K‑TURTLE Mobile v2.6")
st.caption("가격 원자료 검증 → 매매계획 → Heat → 재무 Gate → 삼성증권 주문 준비")

tab1, tab2, tab3, tab4 = st.tabs(["🌐 전체시장","🔎 단일종목","📒 매매일지","📊 성적표"])

with tab1:
    account_equity = st.number_input("한국 계좌자산(원)", min_value=100000, value=10000000, step=100000)
    max_candidates = st.number_input("최대 후보 표시", min_value=5, max_value=100, value=30, step=5)

    if st.button("한국시장 전체 스캔", type="primary"):
        try:
            universe = universe_cached()
            bar = st.progress(0)
            status = st.empty()

            def cb(frac, ci, chunks, found):
                bar.progress(frac)
                status.caption(f"가격 스캔 {ci}/{chunks} · 후보 {found}개")

            candidates, stats, sector_strength = scan_market(
                universe,
                account_equity=float(account_equity),
                risk_per_unit_pct=0.01,
                volume_multiple=2.0,
                max_candidates=int(max_candidates),
                progress_callback=cb,
            )
            leaders = select_top_sector_leaders(
                candidates,
                sector_strength,
                top_n_sectors=3,
            )
            st.session_state["kt_raw_candidates"] = candidates
            st.session_state["kt_sector_strength"] = breakout_sector_top3(
                candidates, sector_strength, top_n=3
            )
            st.session_state["kt_candidates"] = leaders
            stats["sector_leaders"] = len(leaders)
            st.session_state["kt_stats"] = stats
        except Exception as e:
            st.error(str(e))

    if "kt_sector_strength" in st.session_state:
        sec = st.session_state["kt_sector_strength"].head(3).copy()
        if not sec.empty:
            st.subheader("🔥 돌파 발생 섹터 중 강세 TOP 3 · Breadth")
            for _, r in sec.iterrows():
                sector_text = (
                    f'**{int(r["SectorRank"])}위 {r["Sector"]}**  \n'
                    f'섹터점수 **{r["SectorScore"]:.1f}** · '
                    f'20일 **{r["SectorRet20"]:.1f}%** / '
                    f'60일 **{r["SectorRet60"]:.1f}%**  \n'
                    f'구성 **{int(r.get("SectorCount", 0))}종목** · '
                    f'20일 상승 **{r.get("Up20Ratio", 0):.0f}%** · '
                    f'60일 상승 **{r.get("Up60Ratio", 0):.0f}%** · '
                    f'가격Gate 돌파 **{int(r.get("BreakoutCount", 0))}종목**'
                )
                st.markdown(sector_text)

    if "kt_candidates" in st.session_state:
        cand = st.session_state["kt_candidates"]
        if cand.empty:
            st.warning("가격 Gate 통과 후보 없음")
        else:
            st.caption("가격 Gate를 먼저 통과한 종목들의 섹터만 추린 뒤, 그 섹터들을 전체시장 상대강도로 비교해 TOP3를 고르고 각 섹터의 최강 돌파주 1개를 표시합니다.")

            for idx, row in cand.head(int(max_candidates)).iterrows():
                code = row.get("Code","")
                name = row.get("Name","")
                market = row.get("Market","")
                breakout = row.get("Breakout","")
                close = row.get("Close")
                high = row.get("TodayHigh")
                h20 = row.get("H20")
                h55 = row.get("H55")
                vr = row.get("VolumeRatio")
                atr = row.get("ATR_N")
                qty = row.get("UnitQty")
                stop = row.get("Stop2N")
                add = row.get("Add0_5N")
                b20 = row.get("Break20Pct", 0) or 0
                b55 = row.get("Break55Pct", 0) or 0

                st.markdown(
                    f"""
                    <div class="kt-card">
                      <div class="kt-title">{name} <span style="opacity:.6;font-size:.9rem">({code})</span></div>
                      <div class="kt-sub">섹터 {int(row.get("SectorRank",0))}위 · {market} · {row.get("Sector","기타")} · {breakout}</div>
                      <div class="kt-grid">
                        <div><span class="kt-label">섹터 RS 점수</span><br><span class="kt-val">{row.get("RSScore",0):.1f}</span></div>
                        <div><span class="kt-label">20일 / 60일 수익률</span><br><span class="kt-val">{row.get("RS20",0):.1f}% / {row.get("RS60",0):.1f}%</span></div>
                        <div><span class="kt-label">종가</span><br><span class="kt-val">{close:,.0f}원</span></div>
                        <div><span class="kt-label">거래량배수</span><br><span class="kt-val">{vr:.2f}배</span></div>
                        <div><span class="kt-label">20일 돌파율</span><br><span class="kt-val">{b20:.2f}%</span></div>
                        <div><span class="kt-label">55일 돌파율</span><br><span class="kt-val">{b55:.2f}%</span></div>
                        <div><span class="kt-label">ATR(N)</span><br><span class="kt-val">{atr:,.0f}원</span></div>
                        <div><span class="kt-label">1 Unit</span><br><span class="kt-val">{int(qty):,}주</span></div>
                        <div><span class="kt-label">2N 손절</span><br><span class="kt-val">{stop:,.0f}원</span></div>
                        <div><span class="kt-label">+0.5N</span><br><span class="kt-val">{add:,.0f}원</span></div>
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

            with st.expander("📊 원자료 표 보기"):
                diag_cols = [
                    "Code","Name","Market","Sector","Breakout",
                    "Close","TodayHigh","H20","H55",
                    "TodayVolume","Avg20Volume","VolumeRatio",
                    "Break20Pct","Break55Pct",
                    "ATR_N","UnitQty","Stop2N","Add0_5N"
                ]
                existing = [c for c in diag_cols if c in cand.columns]
                show = cand[existing].copy()
                rename = {
                    "Code":"코드","Name":"종목","Market":"시장","Sector":"섹터","Breakout":"돌파",
                    "Close":"종가","TodayHigh":"오늘고가","H20":"20일고점","H55":"55일고점",
                    "TodayVolume":"오늘거래량","Avg20Volume":"직전20일평균거래량","VolumeRatio":"거래량배수",
                    "Break20Pct":"20일돌파율(%)","Break55Pct":"55일돌파율(%)",
                    "ATR_N":"ATR(N)","UnitQty":"1 Unit","Stop2N":"2N손절","Add0_5N":"+0.5N"
                }
                show = show.rename(columns=rename)
                st.dataframe(show, use_container_width=True, hide_index=True)

            with st.expander("검증 공식 보기"):
                st.code(
                    "거래량배수 = 오늘 거래량 ÷ 직전 20거래일 평균 거래량\n"
                    "20일돌파율 = (오늘 고가 ÷ 직전 20일 고가 - 1) × 100\n"
                    "55일돌파율 = (오늘 고가 ÷ 직전 55일 고가 - 1) × 100"
                )

            st.divider()
            st.subheader("최종 검증")

            candidate_labels = (
                cand["Code"].astype(str)
                + " · "
                + cand["Name"].astype(str)
                + " · RS "
                + cand["RSScore"].round(1).astype(str)
            )

            selected = st.selectbox(
                "검증할 섹터 대장주",
                candidate_labels.tolist()
            )

            selected_code = selected.split(" · ")[0]
            selected_row = cand[cand["Code"].astype(str) == selected_code].iloc[0]

            st.caption(
                f'{selected_row["Name"]} · {selected_row["Sector"]} · '
                f'RS {selected_row["RSScore"]:.1f} · {selected_row["Breakout"]}'
            )

            force_finance_refresh = st.checkbox(
                "재무 새로고침",
                value=False,
                help="체크하면 24시간 캐시를 무시하고 OpenDART에서 다시 조회합니다."
            )

            h1, h2 = st.columns(2)
            current_portfolio_heat = h1.number_input(
                "현재 Portfolio Heat(원)",
                min_value=0,
                value=0,
                step=10000,
                key="market_pf_heat"
            )
            current_sector_heat = h2.number_input(
                "현재 Sector Heat(원)",
                min_value=0,
                value=0,
                step=10000,
                key="market_sector_heat"
            )

            if st.button("선택 후보 최종 검증", type="primary"):
                st.subheader("1. Heat 검사")

                heat = evaluate_heat(
                    new_trade_risk_krw=float(selected_row["RiskKRW"]),
                    account_equity=float(account_equity),
                    current_portfolio_heat_krw=float(current_portfolio_heat),
                    current_sector_heat_krw=float(current_sector_heat),
                )

                st.write(
                    f'Portfolio Heat: **{heat["portfolio_heat_krw"]:,.0f} / '
                    f'{heat["portfolio_limit_krw"]:,.0f}원**'
                )
                st.write(
                    f'Sector Heat: **{heat["sector_heat_krw"]:,.0f} / '
                    f'{heat["sector_limit_krw"]:,.0f}원**'
                )

                if heat["status"] != "PASS":
                    st.error(f'🔴 {heat["reason"]}')
                    st.info("Heat 미통과 → 재무 Gate 생략")
                    st.stop()

                st.success("🟢 Heat PASS")

                st.subheader("2. 재무·공시 Gate")

                CACHE_TTL_HOURS = 24.0
                cached, cache_hours = get_candidate_finance(selected_code)

                use_cache = (
                    cached is not None
                    and cache_hours is not None
                    and cache_hours <= CACHE_TTL_HOURS
                    and not force_finance_refresh
                )

                if use_cache:
                    gate = cached.get("gate", "WATCH")
                    reasons = cached.get("reasons", [])
                    counts = cached.get("counts", {})
                    corp = cached.get("corp")
                    name = cached.get("name", selected_row.get("Name", selected_code))
                    source = "CACHE"
                    st.success(f"⚡ 재무 캐시 사용 · {cache_hours:.1f}시간 전 조회")
                else:
                    try:
                        with st.spinner("최종 후보 재무를 OpenDART에서 확인하는 중..."):
                            gate, reasons, counts, corp, name, _, _ = dart_gate(selected_code)

                        save_candidate_finance(
                            selected_code,
                            {
                                "gate": gate,
                                "reasons": reasons,
                                "counts": counts,
                                "corp": corp,
                                "name": name,
                            }
                        )
                        source = "LIVE"
                        st.success("🌐 OpenDART 조회 완료 · 결과를 24시간 캐시에 저장")
                    except Exception as e:
                        if cached:
                            gate = "WATCH"
                            reasons = list(cached.get("reasons", [])) + [
                                "OpenDART 실시간 연결 실패 — 오래된 캐시는 참고용"
                            ]
                            counts = cached.get("counts", {})
                            corp = cached.get("corp")
                            name = cached.get("name", selected_row.get("Name", selected_code))
                            source = "STALE_CACHE"
                            st.warning("🟡 실시간 DART 실패 · 오래된 캐시 참고 · 신규 진입 보류")
                            st.caption(str(e))
                        else:
                            gate = "WATCH"
                            reasons = ["OpenDART 연결 실패 + 저장된 후보 재무 캐시 없음"]
                            counts = {}
                            corp = None
                            name = selected_row.get("Name", selected_code)
                            source = "NONE"
                            st.warning("🟡 재무 Gate 미확정 — 신규 진입 보류")
                            st.caption(str(e))

                if gate == "PASS":
                    st.success(f"🟢 재무 Gate PASS · {source}")
                elif gate == "WATCH":
                    st.warning(f"🟡 재무 Gate WATCH · {source} — 신규 진입 보류")
                elif gate == "FAIL":
                    st.error(f"🔴 재무 Gate FAIL · {source} — 신규 진입 금지")
                else:
                    gate = "WATCH"
                    st.warning("🟡 재무 Gate 미확정 — 신규 진입 보류")

                for reason in reasons:
                    st.write("•", reason)

                if counts:
                    cols = st.columns(4)
                    for col, key, label in zip(
                        cols,
                        ["RIGHTS_ISSUE","CB","BW","EB"],
                        ["유증","CB","BW","EB"]
                    ):
                        col.metric(label, counts.get(key, 0))

                if gate != "PASS":
                    st.stop()

                st.subheader("3. 삼성증권 주문 준비")
                st.success("🟢 최종 진입 후보")

                st.markdown(
                    f"""
**종목:** {name} ({selected_code})  
**섹터:** {selected_row["Sector"]}  
**섹터 RS:** {selected_row["RSScore"]:.1f}  
**돌파:** {selected_row["Breakout"]}  
**진입 기준가:** {selected_row["Close"]:,.0f}원  
**1 Unit:** {int(selected_row["UnitQty"]):,}주  
**ATR(N):** {selected_row["ATR_N"]:,.0f}원  
**2N 손절:** {selected_row["Stop2N"]:,.0f}원  
**+0.5N 추가매수:** {selected_row["Add0_5N"]:,.0f}원  
**거래량 배수:** {selected_row["VolumeRatio"]:.2f}배  
"""
                )

                st.warning(
                    "아직 삼성증권 계좌로 주문을 전송하지 않습니다. "
                    "위 값을 mPOP에 입력해 사용자가 최종 주문합니다."
                )

                st.divider()
                st.subheader("4. 실제 체결 기록")
                st.caption("삼성증권에서 실제 체결된 가격과 수량을 입력하면 매매일지에 저장됩니다.")

                fill_c1, fill_c2 = st.columns(2)
                actual_entry = fill_c1.number_input(
                    "실제 체결가(원)",
                    min_value=0.0,
                    value=float(selected_row["Close"]),
                    step=10.0,
                    key=f"actual_entry_{selected_code}"
                )
                actual_qty = fill_c2.number_input(
                    "실제 체결수량(주)",
                    min_value=1,
                    value=int(selected_row["UnitQty"]),
                    step=1,
                    key=f"actual_qty_{selected_code}"
                )
                trade_memo = st.text_input(
                    "메모(선택)",
                    value="",
                    key=f"entry_memo_{selected_code}"
                )

                if st.button("✅ 체결 기록 저장", key=f"save_trade_{selected_code}"):
                    actual_risk = max(
                        0.0,
                        (float(actual_entry) - float(selected_row["Stop2N"])) * int(actual_qty)
                    )
                    try:
                        trade = record_entry({
                            "stock_code": selected_code,
                            "name": name,
                            "sector": selected_row.get("Sector"),
                            "breakout_type": selected_row.get("Breakout"),
                            "sector_score": selected_row.get("SectorScore"),
                            "rs_score": selected_row.get("RSScore"),
                            "entry_price": actual_entry,
                            "qty": actual_qty,
                            "atr_n": selected_row.get("ATR_N"),
                            "initial_stop": selected_row.get("Stop2N"),
                            "add_05n": selected_row.get("Add0_5N"),
                            "add_10n": float(selected_row.get("Close",0)) + float(selected_row.get("ATR_N",0)),
                            "risk_krw": actual_risk,
                            "memo": trade_memo,
                        })
                        st.success(f"📒 매매일지 저장 완료 · {trade['name']} {trade['qty']}주")
                    except Exception as e:
                        st.error(str(e))

with tab2:
    account_equity = st.number_input("계좌자산(원)", min_value=100000, value=10000000, step=100000, key="eq")
    current_portfolio_heat = st.number_input("현재 Portfolio Heat(원)", min_value=0, value=0, step=10000)
    current_sector_heat = st.number_input("현재 Sector Heat(원)", min_value=0, value=0, step=10000)
    stock = st.text_input("종목코드", value="005930", max_chars=6)

    if st.button("K‑TURTLE 최종 검증", type="primary"):
        s = stock.strip().zfill(6)

        # 1) Price Gate
        st.subheader("1. 가격 Gate")
        try:
            df,ticker = fetch_kr_stock(s, "6mo")
            price = evaluate_price_breakout(df, 2.0)
        except Exception as e:
            st.error(f"가격 데이터 오류: {e}")
            st.stop()

        if price.get("status") != "PRICE_PASS":
            st.warning(price.get("reason","가격 조건 미충족"))
            st.stop()

        st.success(f'🟢 {price["breakout_type"]}')
        st.write(f'거래량 배수: **{price["volume_ratio"]:.2f}배** / ATR(N): **{price["atr_n"]:,.0f}원**')

        # 2) Trade plan
        st.subheader("2. 매매 가격 계산")
        plan = calculate_trade_plan(price, float(account_equity), 0.01)
        if plan.get("status") != "PLAN_READY":
            st.warning(plan.get("reason","매매계획 계산 실패"))
            st.stop()

        st.write(f'진입가 **{plan["entry_price"]:,.0f}원**')
        st.write(f'1 Unit **{plan["unit_qty"]:,}주**')
        st.write(f'2N 손절 **{plan["initial_stop"]:,.0f}원**')
        st.write(f'+0.5N **{plan["next_add_price"]:,.0f}원**')

        # 3) Heat
        st.subheader("3. Heat 검사")
        heat = evaluate_heat(
            new_trade_risk_krw=plan["risk_krw"],
            account_equity=float(account_equity),
            current_portfolio_heat_krw=float(current_portfolio_heat),
            current_sector_heat_krw=float(current_sector_heat),
        )

        st.write(f'Portfolio Heat: **{heat["portfolio_heat_krw"]:,.0f} / {heat["portfolio_limit_krw"]:,.0f}원**')
        st.write(f'Sector Heat: **{heat["sector_heat_krw"]:,.0f} / {heat["sector_limit_krw"]:,.0f}원**')

        if heat["status"] != "PASS":
            st.error(f'🔴 {heat["reason"]}')
            st.info("Heat 미통과 → DART 재무검증 생략")
            st.stop()

        st.success("🟢 Heat PASS")

        # 4) Finance gate
        st.subheader("4. 재무·공시 Gate")
        try:
            gate,reasons,counts,corp,name = dart_gate(s)
        except Exception as e:
            st.error(f"DART 연결 실패: {e}")
            st.stop()

        if gate=="PASS":
            st.success("🟢 재무 Gate PASS")
        elif gate=="WATCH":
            st.warning("🟡 재무 Gate WATCH — 신규 진입 보류")
        else:
            st.error("🔴 재무 Gate FAIL — 신규 진입 금지")

        for r in reasons:
            st.write("•",r)

        if counts:
            cols=st.columns(4)
            for col,key,label in zip(cols,["RIGHTS_ISSUE","CB","BW","EB"],["유증","CB","BW","EB"]):
                col.metric(label, counts.get(key,0))

        if gate != "PASS":
            st.stop()

        # 5) Samsung order prep
        st.subheader("5. 삼성증권 주문 준비")
        st.success("🟢 최종 진입 후보")
        st.markdown(f"""
**종목코드:** {s}  
**종목명:** {name or '확인됨'}  
**진입 기준가:** {plan["entry_price"]:,.0f}원  
**1 Unit:** {plan["unit_qty"]:,}주  
**ATR(N):** {plan["atr_n"]:,.0f}원  
**2N 손절:** {plan["initial_stop"]:,.0f}원  
**+0.5N 추가매수:** {plan["next_add_price"]:,.0f}원  
**+1.0N 추가매수:** {plan["next_add2_price"]:,.0f}원  
""")
        st.warning("아직 삼성증권 계좌에 주문을 전송하지 않습니다. 이 값을 mPOP에 입력해 최종 주문하세요.")

st.caption("모바일 최적화 v1.4 · 가격 Gate → 매매가격 계산 → Heat → 재무 Gate → 삼성증권 주문 준비")

# v1.3 diagnostic price gate


st.caption(
    "RSScore가 가장 높은 돌파 종목을 대장주로 선정합니다."
)
st.caption(
    "각 강세 섹터에서 RSScore가 가장 높은 돌파 종목 1개를 최종 대장주로 표시합니다."
)
st.caption(
    "매매계획 → Heat → DART 재무 Gate → 삼성증권 주문 준비"
)
st.caption(
    "섹터 순위는 기존처럼 20일·60일 수익률 중앙값의 상대강도로 계산합니다."
)


with tab3:
    st.subheader("📒 K-TURTLE 매매일지")

    trades = list_trades()
    open_trades = [t for t in trades if t.get("status") == "OPEN"]
    closed_trades = [t for t in trades if t.get("status") == "CLOSED"]

    st.caption(f"보유 중 {len(open_trades)}건 · 종료 {len(closed_trades)}건")

    if open_trades:
        st.markdown("### 보유 중")
        for t in open_trades:
            with st.expander(
                f'🟢 {t.get("name")} ({t.get("stock_code")}) · '
                f'{t.get("entry_price",0):,.0f}원 × {int(t.get("qty") or 0)}주'
            ):
                c1,c2 = st.columns(2)
                c1.metric("초기 손절", f'{float(t.get("initial_stop") or 0):,.0f}원')
                c2.metric("ATR(N)", f'{float(t.get("atr_n") or 0):,.0f}원')
                st.write(
                    f'돌파: **{t.get("breakout_type")}** · '
                    f'섹터: **{t.get("sector")}** · '
                    f'RS: **{float(t.get("rs_score") or 0):.1f}**'
                )

                exit_price = st.number_input(
                    "실제 청산가",
                    min_value=0.0,
                    value=float(t.get("entry_price") or 0),
                    step=10.0,
                    key=f'exit_price_{t["trade_id"]}'
                )
                exit_reason = st.selectbox(
                    "청산 사유",
                    ["10일/20일 청산채널","2N 손절","추세 훼손","수동 청산","기타"],
                    key=f'exit_reason_{t["trade_id"]}'
                )
                exit_memo = st.text_input(
                    "청산 메모",
                    key=f'exit_memo_{t["trade_id"]}'
                )

                c3,c4 = st.columns(2)
                if c3.button("🔴 청산 기록", key=f'close_{t["trade_id"]}'):
                    try:
                        result = close_trade(
                            t["trade_id"],
                            exit_price,
                            exit_reason,
                            memo=exit_memo
                        )
                        st.success(
                            f'청산 저장 · 손익 {result["pnl_krw"]:,.0f}원 · '
                            f'{result["return_pct"]:.2f}% · {result["r_multiple"]:.2f}R'
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

                if c4.button("🗑️ 기록 삭제", key=f'del_{t["trade_id"]}'):
                    delete_trade(t["trade_id"])
                    st.rerun()
    else:
        st.info("현재 OPEN 거래가 없습니다.")

    if closed_trades:
        st.markdown("### 최근 종료 거래")
        df_closed = pd.DataFrame(closed_trades)
        cols = [
            "name","stock_code","breakout_type","entry_date","entry_price",
            "exit_date","exit_price","pnl_krw","return_pct","r_multiple","n_multiple"
        ]
        st.dataframe(
            df_closed[[c for c in cols if c in df_closed.columns]].tail(30),
            use_container_width=True,
            hide_index=True
        )

    st.markdown("### 백업 / 복원")
    st.download_button(
        "⬇️ 매매일지 CSV 백업",
        data=export_csv_bytes(),
        file_name="kturtle_trade_journal.csv",
        mime="text/csv"
    )
    upload = st.file_uploader("CSV 매매일지 복원", type=["csv"])
    if upload is not None and st.button("CSV 복원 실행"):
        try:
            count = import_csv_bytes(upload.getvalue())
            st.success(f"{count}건 복원 완료")
            st.rerun()
        except Exception as e:
            st.error(str(e))


with tab4:
    st.subheader("📊 K-TURTLE 실제 성적표")

    perf = performance_summary()
    m1,m2,m3 = st.columns(3)
    m1.metric("종료 거래", f'{perf["closed_count"]}건')
    m2.metric("승률", "-" if perf["win_rate"] is None else f'{perf["win_rate"]:.1f}%')
    m3.metric("누적 손익", f'{perf["total_pnl"]:,.0f}원')

    m4,m5,m6 = st.columns(3)
    m4.metric("평균 수익률", "-" if perf["avg_return"] is None else f'{perf["avg_return"]:.2f}%')
    m5.metric("평균 R", "-" if perf["avg_r"] is None else f'{perf["avg_r"]:.2f}R')
    m6.metric("Profit Factor", "-" if perf["profit_factor"] is None else f'{perf["profit_factor"]:.2f}')

    m7,m8,m9 = st.columns(3)
    m7.metric("평균 N", "-" if perf["avg_n"] is None else f'{perf["avg_n"]:.2f}N')
    m8.metric("최대 단일손실", "-" if perf["max_loss"] is None else f'{perf["max_loss"]:,.0f}원')
    m9.metric("현재 OPEN", f'{perf["open_count"]}건')

    if perf["closed_count"] == 0:
        st.info("청산 완료 거래가 쌓이면 실제 성적표가 자동으로 계산됩니다.")
    else:
        st.markdown("### 20일 vs 55일 돌파 성과")
        by_breakout = grouped_performance("breakout_type")
        if by_breakout:
            st.dataframe(pd.DataFrame(by_breakout), use_container_width=True, hide_index=True)

        st.markdown("### 섹터별 성과")
        by_sector = grouped_performance("sector")
        if by_sector:
            st.dataframe(pd.DataFrame(by_sector), use_container_width=True, hide_index=True)

        trades = [t for t in list_trades() if t.get("status") == "CLOSED"]
        if trades:
            curve = []
            total = 0.0
            for t in sorted(trades, key=lambda x: str(x.get("exit_date") or "")):
                total += float(t.get("pnl_krw") or 0)
                curve.append({
                    "exit_date": t.get("exit_date"),
                    "누적손익": total
                })
            if curve:
                st.markdown("### 누적 손익")
                st.line_chart(
                    pd.DataFrame(curve).set_index("exit_date")["누적손익"]
                )

st.caption(
    "v2.6: 최종 후보 재무 24시간 캐시 + 실제 체결 매매일지 + K-TURTLE 실제 성적표. "
    "같은 종목은 다시 DART를 호출하지 않으며, 필요할 때만 '재무 새로고침'으로 갱신합니다."
)

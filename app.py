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
from market_scanner import get_kr_universe, scan_market, select_sector_leaders

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
        return None, ["OpenDART 인증키 없음"], {}, None, None
    mp = stock_map(k)
    s = stock_code.zfill(6)
    if s not in mp:
        return None, ["DART 종목코드 매핑 실패"], {}, None, None

    corp, name = mp[s]
    c = client(k)
    hist = build_quarter_history(c, corp, 2)
    end = date.today()
    begin = end - timedelta(days=365)
    events = c.financing_events(
        corp,
        begin.strftime("%Y%m%d"),
        end.strftime("%Y%m%d")
    )
    counts,total = financing_summary(events)
    gate,reasons = financial_gate_v07(hist,total)
    return gate,reasons,counts,corp,name

st.title("🐢 K‑TURTLE Mobile v1.7.1")
st.caption("가격 원자료 검증 → 매매계획 → Heat → 재무 Gate → 삼성증권 주문 준비")

tab1, tab2 = st.tabs(["🌐 전체시장 스캔","🔎 단일종목"])

with tab1:
    account_equity = st.number_input("한국 계좌자산(원)", min_value=100000, value=10000000, step=100000)
    max_candidates = st.number_input("최대 후보 표시", min_value=5, max_value=100, value=30, step=5)
    leaders_per_sector = st.number_input(
        "섹터당 대장주 수",
        min_value=1,
        max_value=3,
        value=1,
        step=1,
        help="같은 섹터에서 상대강도(RS)가 가장 높은 순서대로 표시합니다."
    )

    if st.button("한국시장 전체 스캔", type="primary"):
        try:
            universe = universe_cached()
            bar = st.progress(0)
            status = st.empty()

            def cb(frac, ci, chunks, found):
                bar.progress(frac)
                status.caption(f"가격 스캔 {ci}/{chunks} · 후보 {found}개")

            candidates, stats = scan_market(
                universe,
                account_equity=float(account_equity),
                risk_per_unit_pct=0.01,
                volume_multiple=2.0,
                max_candidates=int(max_candidates),
                progress_callback=cb,
            )
            leaders = select_sector_leaders(
                candidates,
                leaders_per_sector=int(leaders_per_sector),
                max_total=int(max_candidates),
            )
            st.session_state["kt_raw_candidates"] = candidates
            st.session_state["kt_candidates"] = leaders
            stats["sector_leaders"] = len(leaders)
            st.session_state["kt_stats"] = stats
        except Exception as e:
            st.error(str(e))

    if "kt_candidates" in st.session_state:
        cand = st.session_state["kt_candidates"]
        if cand.empty:
            st.warning("가격 Gate 통과 후보 없음")
        else:
            st.caption("가격 Gate 원후보를 섹터별로 압축했습니다. 기본은 같은 섹터에서 상대강도(RS)가 가장 강한 1종목입니다.")

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
                      <div class="kt-sub">{market} · {row.get("Sector","기타")} · {breakout}</div>
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

st.caption("v1.6 대장주 선정: 섹터별 55일 강한 돌파 우선 → 시가총액 → 거래량배수 → 돌파율 순. 거래량 계산은 v1.4 기준을 그대로 유지합니다.")

st.caption("v1.7.1: 같은 섹터 내 RSScore(20일·60일 수익률 백분위 평균) 최상위 종목을 대장주로 선정합니다.")

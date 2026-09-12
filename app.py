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
from market_scanner import get_kr_universe, scan_market

st.set_page_config(page_title="K-TURTLE Mobile", page_icon="🐢", layout="centered")

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

st.title("🐢 K‑TURTLE Mobile v1.2")
st.caption("가격 → 매매계획 → Heat → 재무 Gate → 삼성증권 주문 준비")

tab1, tab2 = st.tabs(["🌐 전체시장 스캔","🔎 단일종목"])

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

            candidates, stats = scan_market(
                universe,
                account_equity=float(account_equity),
                risk_per_unit_pct=0.01,
                volume_multiple=2.0,
                max_candidates=int(max_candidates),
                progress_callback=cb,
            )
            st.session_state["kt_candidates"] = candidates
            st.session_state["kt_stats"] = stats
        except Exception as e:
            st.error(str(e))

    if "kt_candidates" in st.session_state:
        cand = st.session_state["kt_candidates"]
        if cand.empty:
            st.warning("가격 Gate 통과 후보 없음")
        else:
            st.dataframe(cand, use_container_width=True, hide_index=True)

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

st.caption("검증순서: 가격 Gate → 매매가격 계산 → Heat → 재무 Gate → 삼성증권 주문 준비")

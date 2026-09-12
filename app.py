from __future__ import annotations
import os
from datetime import date, timedelta
import pandas as pd
import streamlit as st

from opendart_client import OpenDartClient
from dart_financials import build_quarter_history, financing_summary, financial_gate_v07
from price_source import fetch_kr_stock
from price_engine import evaluate_price_breakout, calculate_trade_plan

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

st.title("🐢 K‑TURTLE Mobile v1.0")
st.caption("가격 돌파 → 매매계획 → 재무/공시 Gate · 실주문 없음")

stock = st.text_input("종목코드", value="005930", max_chars=6)

if st.button("K‑TURTLE 종합 검사", type="primary"):
    s = stock.strip().zfill(6)

    # 1) PRICE FIRST
    st.subheader("1. 가격·돌파 검사")
    try:
        with st.spinner("최근 주가 데이터를 불러오는 중..."):
            df,ticker = fetch_kr_stock(s, "6mo")
        st.caption(f"가격 데이터 심볼: {ticker}")

        price = evaluate_price_breakout(df, volume_multiple=2.0)

        c1,c2 = st.columns(2)
        c1.metric("20일 돌파선", f'{price.get("h20",0):,.0f}원' if price.get("h20") else "미확인")
        c2.metric("55일 돌파선", f'{price.get("h55",0):,.0f}원' if price.get("h55") else "미확인")
        c3,c4 = st.columns(2)
        c3.metric("거래량 / 20일 평균", f'{price.get("volume_ratio",0):.2f}배')
        c4.metric("ATR(N)", f'{price.get("atr_n",0):,.0f}원' if price.get("atr_n") else "미확인")

        if price["status"] != "PRICE_PASS":
            st.warning(f'🟡 가격 Gate: {price.get("reason","조건 미충족")}')
            st.info("가격 Gate 미통과 → DART 재무 검증 생략")
            st.stop()

        st.success(f'🟢 가격 Gate PASS — {price["breakout_type"]}')

    except Exception as e:
        st.error("가격 데이터 조회에 실패했습니다.")
        st.code(str(e))
        st.stop()

    # 2) TRADE PLAN BEFORE FINANCE
    st.subheader("2. 매매 가격 계산")
    plan = calculate_trade_plan(
        price,
        account_equity=10_000_000,
        risk_per_unit_pct=0.01,
    )
    if plan["status"] != "PLAN_READY":
        st.warning(plan.get("reason","매매계획 계산 실패"))
        st.stop()

    st.metric("제안 진입가", f'{plan["entry_price"]:,.0f}원')
    st.metric("1 Unit 수량", f'{plan["unit_qty"]:,}주')
    c5,c6 = st.columns(2)
    c5.metric("2N 손절", f'{plan["initial_stop"]:,.0f}원')
    c6.metric("+0.5N 추가매수", f'{plan["next_add_price"]:,.0f}원')

    # 3) ONLY NOW DART FINANCE
    st.subheader("3. 재무·공시 Gate")
    k = get_key()
    if not k:
        st.warning("OpenDART 인증키가 없어 재무 Gate를 확인하지 못했습니다.")
        st.error("재무 Gate 미확정 → 신규 진입 금지")
        st.stop()

    try:
        with st.spinner("돌파 종목만 DART 재무·공시를 검증하는 중..."):
            mp = stock_map(k)
            if s not in mp:
                st.error("DART 상장종목 목록에서 종목코드를 찾지 못했습니다.")
                st.stop()

            corp,name = mp[s]
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

        st.write(f"**{name} ({s})**")
        st.caption(f"DART 고유번호 {corp}")

        if gate=="PASS":
            st.success("🟢 재무 Gate: PASS")
        elif gate=="WATCH":
            st.warning("🟡 재무 Gate: WATCH — 신규 진입 보류")
        else:
            st.error("🔴 재무 Gate: FAIL — 신규 진입 금지")

        for r in reasons:
            st.write("•",r)

        cols = st.columns(4)
        for col,key,label in zip(cols,["RIGHTS_ISSUE","CB","BW","EB"],["유증","CB","BW","EB"]):
            col.metric(label, counts.get(key,0))

        if gate != "PASS":
            st.error("최종 판정: 매수 금지")
            st.stop()

    except Exception as e:
        st.error("OpenDART 연결 실패로 재무 Gate를 확정하지 못했습니다.")
        st.warning("재무 Gate 미확정 → 신규 진입 금지")
        st.code(str(e))
        st.stop()

    # 4) FINAL CANDIDATE
    st.subheader("4. 최종 판정")
    st.success(f'🟢 최종 진입 후보 — {plan["breakout_type"]}')
    st.write(f'진입가 **{plan["entry_price"]:,.0f}원** / 1 Unit **{plan["unit_qty"]:,}주**')
    st.write(f'손절 **{plan["initial_stop"]:,.0f}원** / 다음 추가매수 **{plan["next_add_price"]:,.0f}원**')
    st.warning("아직 실제 주문은 전송되지 않습니다. 최종 주문은 사용자가 증권사 앱에서 직접 실행합니다.")

st.caption("v1.0 검증순서: 가격 → 돌파/거래량 → ATR/Unit/손절 계산 → 재무·공시 → 최종 후보")

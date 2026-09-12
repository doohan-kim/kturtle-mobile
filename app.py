from __future__ import annotations
import os
from datetime import date, timedelta
import pandas as pd
import streamlit as st

from opendart_client import OpenDartClient, DartError
from dart_financials import build_quarter_history, financing_summary, financial_gate_v07
from price_source import fetch_kr_stock
from price_engine import evaluate_price_signal

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

st.title("🐢 K‑TURTLE Mobile v0.9")
st.caption("OpenDART 재시도·캐시 안정화 + 재무 Gate + 가격 신호 · 실주문 없음")

k = get_key()
if not k:
    st.error("OpenDART 인증키가 Secrets에 없습니다.")
    st.stop()

stock = st.text_input("종목코드", value="005930", max_chars=6)

if st.button("K‑TURTLE 종합 검사", type="primary"):
    s = stock.strip().zfill(6)

    try:
        with st.spinner("기업코드 목록을 확인하는 중..."):
            mp = stock_map(k)
    except Exception as e:
        st.error("OpenDART 기업코드 목록 연결에 실패했습니다.")
        st.warning("잠시 후 다시 시도해 주세요. 앱 전체는 종료되지 않습니다.")
        st.code(str(e))
        if st.button("기업코드 다시 시도"):
            stock_map.clear()
            st.rerun()
        st.stop()

    if s not in mp:
        st.error("상장종목코드를 찾지 못했습니다.")
        st.stop()

    corp,name = mp[s]
    c = client(k)

    try:
        with st.spinner("재무·공시를 검사하는 중..."):
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
    except Exception as e:
        st.error("OpenDART 재무/공시 조회 중 연결 오류가 발생했습니다.")
        st.warning("잠시 후 다시 검사하면 됩니다. 매수 신호는 생성하지 않았습니다.")
        st.code(str(e))
        st.stop()

    st.subheader(f"{name} ({s})")
    st.caption(f"DART 고유번호 {corp}")

    if gate=="PASS":
        st.success("🟢 재무 Gate: PASS")
    elif gate=="WATCH":
        st.warning("🟡 재무 Gate: WATCH — 신규 진입 보류")
    else:
        st.error("🔴 재무 Gate: FAIL — 신규 진입 금지")

    for r in reasons:
        st.write("•",r)

    if hist:
        st.subheader("최근 분기 추세")
        table = pd.DataFrame([{
            "분기":f'{r["year"]}-{r["quarter"]}',
            "매출(억원)":None if r["revenue"] is None else round(r["revenue"]/1e8,1),
            "영업이익(억원)":None if r["operating_profit"] is None else round(r["operating_profit"]/1e8,1),
            "영업이익률(%)":None if r["operating_margin_pct"] is None else round(r["operating_margin_pct"],2),
            "검증":"OK" if not r.get("validation_flags") else "CHECK",
        } for r in hist])
        st.dataframe(table, use_container_width=True, hide_index=True)

    st.subheader("최근 1년 자금조달 공시")
    cols = st.columns(4)
    for col,key,label in zip(cols,["RIGHTS_ISSUE","CB","BW","EB"],["유증","CB","BW","EB"]):
        col.metric(label, counts.get(key,0))

    if gate != "PASS":
        st.info("재무 Gate가 PASS가 아니므로 가격 돌파를 매수신호로 사용하지 않습니다.")
        st.stop()

    st.divider()
    st.subheader("가격·돌파 검사")

    try:
        with st.spinner("최근 주가 데이터를 불러오는 중..."):
            df,ticker = fetch_kr_stock(s, "6mo")
        st.caption(f"가격 데이터 심볼: {ticker}")

        sig = evaluate_price_signal(
            df,
            account_equity=10_000_000,
            risk_per_unit_pct=0.01,
            volume_multiple=2.0,
        )

        c1,c2 = st.columns(2)
        c1.metric("20일 돌파선", f'{sig.get("h20",0):,.0f}원' if sig.get("h20") else "미확인")
        c2.metric("55일 돌파선", f'{sig.get("h55",0):,.0f}원' if sig.get("h55") else "미확인")
        c3,c4 = st.columns(2)
        c3.metric("거래량 / 20일 평균", f'{sig.get("volume_ratio",0):.2f}배')
        c4.metric("ATR(N)", f'{sig.get("atr_n",0):,.0f}원' if sig.get("atr_n") else "미확인")

        if sig["status"] == "ENTRY":
            st.success(f'🟢 {sig["breakout_type"]} — 1 Unit 진입 후보')
            st.metric("제안 진입가", f'{sig["entry_price"]:,.0f}원')
            st.metric("1 Unit 수량", f'{sig["unit_qty"]:,}주')
            c5,c6 = st.columns(2)
            c5.metric("2N 손절", f'{sig["initial_stop"]:,.0f}원')
            c6.metric("+0.5N 추가매수", f'{sig["next_add_price"]:,.0f}원')
            st.warning("실제 주문은 전송되지 않습니다.")
        else:
            st.warning(f'🟡 가격 Gate: {sig.get("reason","조건 미충족")}')

    except Exception as e:
        st.error("가격 데이터 조회에 실패했습니다.")
        st.warning("재무 PASS와 별개로 가격 신호는 생성하지 않았습니다.")
        st.code(str(e))

st.caption("v0.9: OpenDART 지연 시 재시도하고, 성공했던 기업코드 목록은 서버 캐시를 재사용합니다.")

from __future__ import annotations
import os
from datetime import date, timedelta
import pandas as pd
import streamlit as st
from opendart_client import OpenDartClient
from dart_financials import build_quarter_history, financing_summary, financial_gate_v06

st.set_page_config(page_title="K-TURTLE Mobile", page_icon="🐢", layout="centered")

def get_key():
    try:
        if "OPENDART_API_KEY" in st.secrets:
            return str(st.secrets["OPENDART_API_KEY"])
    except Exception:
        pass
    return os.getenv("OPENDART_API_KEY")

@st.cache_resource(show_spinner=False)
def client(k): return OpenDartClient(api_key=k)

@st.cache_data(ttl=86400, show_spinner=False)
def stock_map(k):
    return {c.stock_code:(c.corp_code,c.corp_name) for c in client(k).corp_codes() if c.stock_code}

def won(v):
    return "미확인" if v is None else f"{v/1e8:,.1f}억원"

def pct(v):
    return "미확인" if v is None else f"{v:.2f}%"

st.title("🐢 K‑TURTLE Mobile v0.6")
st.caption("OpenDART 누적값 역산 + 주요계정 API 교차검증 · 실주문 없음")

k = get_key()
if not k:
    st.error("OpenDART 인증키가 Secrets에 없습니다.")
    st.stop()

tabs = st.tabs(["🔎 종목 검사","⚙️ 연결상태"])

with tabs[0]:
    stock = st.text_input("종목코드", value="005930", max_chars=6)
    if st.button("K‑TURTLE 재무 Gate 검사", type="primary"):
        s = stock.strip().zfill(6)
        mp = stock_map(k)
        if s not in mp:
            st.error("상장종목코드를 찾지 못했습니다.")
            st.stop()

        corp,name = mp[s]
        c = client(k)

        with st.spinner("재무제표와 주요계정을 교차검증하는 중..."):
            hist = build_quarter_history(c, corp, 2)
            end = date.today()
            begin = end - timedelta(days=365)
            events = c.financing_events(corp, begin.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
            counts,total = financing_summary(events)
            gate,reasons = financial_gate_v06(hist,total)

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

            last = hist[-1]
            c1,c2 = st.columns(2)
            c1.metric("최근 분기 영업이익", won(last["operating_profit"]))
            c2.metric("최근 분기 영업이익률", pct(last["operating_margin_pct"]))
            c3,c4 = st.columns(2)
            c3.metric("현금성자산", won(last["cash_and_equivalents"]))
            dr = None
            if last["equity"] not in (None,0) and last["total_liabilities"] is not None:
                dr = last["total_liabilities"]/last["equity"]*100
            c4.metric("부채비율", pct(dr))

            with st.expander("데이터 검증 상세"):
                cc = last.get("cross_check",{})
                st.write("매출 계정:", last.get("revenue_account"))
                st.write("영업이익 계정:", last.get("op_account"))
                st.write("주요계정 API 교차검증:", cc.get("status"))
                st.write("매출 누적값 차이(%):", cc.get("revenue_gap_pct"))
                st.write("영업이익 누적값 차이(%):", cc.get("op_gap_pct"))
                st.write("검증 플래그:", last.get("validation_flags") or "없음")

        st.subheader("최근 1년 자금조달 공시")
        cols=st.columns(4)
        for col,key,label in zip(cols,["RIGHTS_ISSUE","CB","BW","EB"],["유증","CB","BW","EB"]):
            col.metric(label, counts.get(key,0))

        st.info("v0.6부터 분기 손익은 누적값을 역산하고, 주요계정 API와 1% 이내로 일치하지 않으면 PASS를 금지합니다.")

with tabs[1]:
    st.write("OpenDART Secrets: ✅ 등록됨")
    if st.button("연결 테스트"):
        st.success(f"정상 연결 · 상장종목 매핑 {len(stock_map(k)):,}개")

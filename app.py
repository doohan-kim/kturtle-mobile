from __future__ import annotations
import os
from datetime import date, timedelta
import pandas as pd
import streamlit as st

from opendart_client import OpenDartClient, DartError

st.set_page_config(
    page_title="K-TURTLE Mobile",
    page_icon="🐢",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.block-container {padding-top: 1rem; padding-bottom: 4rem; max-width: 760px;}
div[data-testid="stMetric"] {border: 1px solid rgba(128,128,128,.25); padding: 10px; border-radius: 12px;}
.stButton > button {width:100%; min-height:48px; font-weight:700; border-radius:12px;}
.kcard {padding:14px;border:1px solid rgba(128,128,128,.25);border-radius:14px;margin-bottom:10px;}
.pass {font-size:1.35rem;font-weight:800;}
.small {opacity:.75;font-size:.9rem;}
</style>
""", unsafe_allow_html=True)

def get_api_key():
    # Streamlit Cloud secrets 우선, 그 다음 환경변수
    try:
        if "OPENDART_API_KEY" in st.secrets:
            return str(st.secrets["OPENDART_API_KEY"])
    except Exception:
        pass
    return os.getenv("OPENDART_API_KEY")

@st.cache_resource(show_spinner=False)
def get_client(api_key: str):
    return OpenDartClient(api_key=api_key)

@st.cache_data(ttl=86400, show_spinner=False)
def corp_map(api_key: str):
    c = get_client(api_key)
    rows = c.corp_codes()
    return {x.stock_code: (x.corp_code, x.corp_name) for x in rows if x.stock_code}

def clean_num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "None"):
        return None
    try:
        return float(s)
    except:
        return None

def pick_account(rows, names):
    # 정확명 우선, 부분일치 후순위
    for nm in names:
        for r in rows:
            if str(r.get("account_nm","")).strip() == nm:
                return r
    for nm in names:
        for r in rows:
            if nm in str(r.get("account_nm","")):
                return r
    return None

def latest_financial_snapshot(c: OpenDartClient, corp_code: str):
    # 최신 공개 가능성이 높은 순서로 시도
    today = date.today()
    years = [today.year, today.year-1]
    reports = [
        ("11014", "3분기"),
        ("11012", "반기"),
        ("11013", "1분기"),
        ("11011", "사업보고서"),
    ]
    for y in years:
        for code, label in reports:
            rows = c.full_financials(corp_code, y, code, "CFS")
            if not rows:
                rows = c.full_financials(corp_code, y, code, "OFS")
            if rows:
                return y, code, label, rows
    return None

def account_amount(row):
    if not row:
        return None
    for key in ["thstrm_amount","thstrm_add_amount"]:
        v = clean_num(row.get(key))
        if v is not None:
            return v
    return None

def ratio(a,b):
    if a is None or b in (None,0):
        return None
    return a/b

st.title("🐢 K‑TURTLE Mobile")
st.caption("한국주식 · OpenDART 재무/공시 확인 · 주문 전 사용자 승인 원칙")

api_key = get_api_key()
if not api_key:
    st.error("OpenDART 인증키가 서버 Secrets에 등록되지 않았습니다.")
    st.info("Streamlit Cloud → App settings → Secrets에 OPENDART_API_KEY를 등록하세요.")
    st.stop()

tabs = st.tabs(["🔎 종목 검사", "📊 가격 CSV", "⚙️ 연결상태"])

with tabs[0]:
    stock = st.text_input("종목코드", value="005930", max_chars=6, placeholder="예: 005930")
    run = st.button("DART 재무·공시 검사", type="primary")

    if run:
        stock = stock.strip().zfill(6)
        try:
            mp = corp_map(api_key)
            if stock not in mp:
                st.error("DART 상장종목 목록에서 종목코드를 찾지 못했습니다.")
                st.stop()
            corp_code, corp_name = mp[stock]
            c = get_client(api_key)

            st.subheader(f"{corp_name} ({stock})")
            st.caption(f"DART 고유번호 {corp_code}")

            snap = latest_financial_snapshot(c, corp_code)
            if not snap:
                st.warning("최근 재무제표를 찾지 못했습니다.")
            else:
                y, rep_code, rep_label, rows = snap

                sales_r = pick_account(rows, ["매출액","수익(매출액)","영업수익"])
                op_r = pick_account(rows, ["영업이익","영업이익(손실)"])
                cash_r = pick_account(rows, ["현금및현금성자산","현금 및 현금성자산"])
                liab_r = pick_account(rows, ["부채총계"])
                eq_r = pick_account(rows, ["자본총계"])

                sales = account_amount(sales_r)
                op = account_amount(op_r)
                cash = account_amount(cash_r)
                liab = account_amount(liab_r)
                eq = account_amount(eq_r)

                op_margin = ratio(op, sales)
                debt_ratio = ratio(liab, eq)

                st.markdown(f'<div class="kcard"><b>최근 재무자료</b><br>{y}년 {rep_label}</div>', unsafe_allow_html=True)

                c1,c2 = st.columns(2)
                with c1:
                    st.metric("영업이익", "확인" if op is not None else "미확인")
                    if op is not None:
                        st.caption(f"{op/1e8:,.1f}억원")
                with c2:
                    st.metric("영업이익률", f"{op_margin*100:.2f}%" if op_margin is not None else "미확인")

                c3,c4 = st.columns(2)
                with c3:
                    st.metric("현금성자산", f"{cash/1e8:,.1f}억원" if cash is not None else "미확인")
                with c4:
                    st.metric("부채비율", f"{debt_ratio*100:.1f}%" if debt_ratio is not None else "미확인")

            end = date.today()
            begin = end - timedelta(days=365)
            events = c.financing_events(
                corp_code,
                begin.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"),
            )
            counts = {k: len(v) for k,v in events.items()}
            total = sum(counts.values())

            st.subheader("최근 1년 자금조달 공시")
            cols = st.columns(4)
            labels = [("RIGHTS_ISSUE","유증"),("CB","CB"),("BW","BW"),("EB","EB")]
            for col,(key,label) in zip(cols, labels):
                col.metric(label, counts.get(key,0))

            if total == 0:
                st.success("최근 1년 유상증자·CB·BW·EB 조회 건수 0건")
            elif total == 1:
                st.warning("최근 1년 자금조달 관련 공시 1건 → K‑TURTLE WATCH 후보")
            else:
                st.error("최근 1년 자금조달 관련 공시 2건 이상 → K‑TURTLE 재무 Gate FAIL 후보")

            with st.expander("공시 상세 보기"):
                any_rows = False
                for kind, items in events.items():
                    if items:
                        any_rows = True
                        st.write(f"**{kind}**")
                        st.dataframe(pd.DataFrame(items), use_container_width=True, hide_index=True)
                if not any_rows:
                    st.caption("해당 기간 조회된 유증/CB/BW/EB 공시가 없습니다.")

            st.info(
                "현재 모바일 v0.4는 DART 실제 데이터 연결과 최근 자금조달 공시 자동 확인까지 수행합니다. "
                "4~8개 분기 추세와 잠재희석률의 완전 자동 판정은 다음 단계에서 연결합니다."
            )

        except DartError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"검사 중 오류: {e}")

with tabs[1]:
    st.subheader("가격 데이터 검사")
    st.caption("현재는 CSV 업로드 방식입니다. 실시간 주가 API 연결 전 검증용입니다.")
    up = st.file_uploader("가격 CSV 업로드", type=["csv"])
    if up:
        df = pd.read_csv(up)
        need = {"date","open","high","low","close","volume"}
        if not need.issubset(df.columns):
            st.error(f"필수 열: {sorted(need)}")
        else:
            st.success(f"{len(df):,}개 거래일 데이터 확인")
            st.dataframe(df.tail(10), use_container_width=True, hide_index=True)

with tabs[2]:
    st.subheader("연결 상태")
    st.write("OpenDART 인증키:", "✅ 서버 Secrets 등록됨")
    if st.button("OpenDART 연결 테스트"):
        try:
            mp = corp_map(api_key)
            st.success(f"정상 연결 · 상장종목 매핑 {len(mp):,}개")
        except Exception as e:
            st.error(f"연결 실패: {e}")

    st.warning("이 앱은 아직 실제 증권사 주문을 전송하지 않습니다.")

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
from trade_journal import record_entry, close_trade, list_trades, delete_trade, performance_summary, grouped_performance, export_csv_bytes, import_csv_bytes, record_add_unit
from market_scanner import get_kr_universe, get_jp_universe, scan_market, select_top_sector_leaders, breakout_sector_top3
from manual_dart_upload import parse_manual_report, manual_gate_from_reports

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

@st.cache_data(ttl=86400, show_spinner=False)
def jp_universe_cached():
    return get_jp_universe()

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


st.title("🐢 K‑TURTLE Mobile v2.7.5 · KR/JP")
st.caption("가격 원자료 검증 → 매매계획 → Heat → 국가별 재무 Gate → 주문 준비")

tab1, tab2, tab3, tab4 = st.tabs(["🌐 전체시장","🔎 단일종목","📒 매매일지","📊 성적표"])

with tab1:
    market_choice = st.radio("시장", ["🇰🇷 한국", "🇯🇵 일본"], horizontal=True)
    is_jp = market_choice.startswith("🇯🇵")
    country = "JP" if is_jp else "KR"
    currency = "엔" if is_jp else "원"
    account_equity = st.number_input(f'{"일본" if is_jp else "한국"} 계좌자산({currency})', min_value=100000, value=(1487000 if is_jp else 10000000), step=(10000 if is_jp else 100000), key=f"eq_{country}")
    max_candidates = st.number_input("최대 후보 표시", min_value=5, max_value=100, value=30, step=5, key=f"max_{country}")
    if is_jp:
        krw_per_jpy = st.number_input("유동성 환산용 1엔당 원화", 5.0, 20.0, 9.30, 0.01, format="%.2f", help="20억원 ADTV 기준을 엔화로 환산합니다.")
        adtv_min_local = 2_000_000_000 / float(krw_per_jpy)
        st.caption(f"일본 유동성 Gate: 20일 평균 거래대금 ≥ 약 {adtv_min_local/100_000_000:.2f}억엔 (20억원 환산)")
    else:
        adtv_min_local = 2_000_000_000.0
    pref=f"kt_{country.lower()}_"
    try:
        _u_preview = jp_universe_cached() if is_jp else universe_cached()
        if is_jp:
            _src = _u_preview.attrs.get("universe_source", "TSE")
            _warn = _u_preview.attrs.get("universe_warning", "")
            st.caption(f"스캔 유니버스: {len(_u_preview):,}종목 · {_src}")
            if _warn: st.warning(_warn)
        else:
            st.caption(f"스캔 유니버스: {len(_u_preview):,}종목 · KRX KOSPI/KOSDAQ")
    except Exception as _ue:
        _u_preview = None
        st.error(f"종목 유니버스 확인 실패: {_ue}")

    if st.button(f'{"일본" if is_jp else "한국"}시장 전체 스캔', type="primary", key=f"scan_{country}", disabled=(_u_preview is None)):
        try:
            universe = _u_preview.copy()
            bar=st.progress(0); status=st.empty()
            def cb(frac,ci,chunks,found):
                bar.progress(frac); status.caption(f"가격 스캔 {ci}/{chunks} · 후보 {found}개")
            candidates,stats,sector_strength=scan_market(universe, account_equity=float(account_equity), risk_per_unit_pct=0.01, volume_multiple=2.0, max_candidates=int(max_candidates), progress_callback=cb, country=country, adtv_min_local=float(adtv_min_local))
            leaders=select_top_sector_leaders(candidates,sector_strength,top_n_sectors=3)
            st.session_state[pref+"sector_strength"]=breakout_sector_top3(candidates,sector_strength,top_n=3)
            st.session_state[pref+"market_sector_top3"]=sector_strength.head(3).copy()
            st.session_state[pref+"candidates"]=leaders
        except Exception as e: st.error(str(e))
    msec=st.session_state.get(pref+"market_sector_top3")
    if msec is not None and not msec.empty:
        brief=" · ".join(f'{int(r["SectorRank"])}위 {r["Sector"]} ({r["SectorScore"]:.1f})' for _,r in msec.head(3).iterrows())
        st.caption("📊 시장 전체 강세섹터 TOP3 · 참고용: "+brief)
    sec=st.session_state.get(pref+"sector_strength")
    if sec is not None and not sec.empty:
        st.subheader("🔥 돌파 발생 섹터 중 강세 TOP3")
        for _,r in sec.head(3).iterrows(): st.markdown(f'**{int(r["SectorRank"])}위 {r["Sector"]}** · 점수 **{r["SectorScore"]:.1f}** · 20일 **{r["SectorRet20"]:.1f}%** / 60일 **{r["SectorRet60"]:.1f}%** · 돌파 **{int(r.get("BreakoutCount",0))}종목**')
    cand=st.session_state.get(pref+"candidates")
    if cand is not None:
        if cand.empty: st.warning(f'{"일본" if is_jp else "한국"}시장 신규 가격 Gate 후보 없음')
        else:
            st.caption("v2.7.1 동결 가격규칙 + 유동성 Gate 통과 후 돌파섹터 TOP3의 RSScore 1위만 표시")
            for _,row in cand.head(int(max_candidates)).iterrows():
                close=float(row.get("Close",0)); atr=float(row.get("ATR_N",0)); qty=int(row.get("UnitQty",0)); adtv=float(row.get("ADTV20",0))
                st.markdown(f'''<div class="kt-card"><div class="kt-title">{row.get("Name","")} <span style="opacity:.6">({row.get("Code","")})</span></div><div class="kt-sub">섹터 {int(row.get("SectorRank",0))}위 · {row.get("Market","")} · {row.get("Sector","기타")} · {row.get("Breakout","")}</div><div class="kt-grid"><div><span class="kt-label">RSScore</span><br><span class="kt-val">{row.get("RSScore",0):.1f}</span></div><div><span class="kt-label">종가</span><br><span class="kt-val">{close:,.0f}{currency}</span></div><div><span class="kt-label">거래량배수</span><br><span class="kt-val">{row.get("VolumeRatio",0):.2f}배</span></div><div><span class="kt-label">ATR(N)</span><br><span class="kt-val">{atr:,.0f}{currency}</span></div><div><span class="kt-label">1 Unit</span><br><span class="kt-val">{qty:,}주</span></div><div><span class="kt-label">2N 손절</span><br><span class="kt-val">{row.get("Stop2N",0):,.0f}{currency}</span></div><div><span class="kt-label">+0.5N / +1.0N</span><br><span class="kt-val">{row.get("Add0_5N",0):,.0f} / {close+atr:,.0f}{currency}</span></div><div><span class="kt-label">120주선</span><br><span class="kt-val">{row.get("WeeklyMA120",0):,.0f}{currency} · PASS</span></div><div><span class="kt-label">20일 평균 거래대금</span><br><span class="kt-val">{adtv/100_000_000:.2f}억{currency}</span></div><div><span class="kt-label">주문/ADTV</span><br><span class="kt-val">{row.get("OrderADTVPct",0):.3f}%</span></div></div></div>''', unsafe_allow_html=True)
            st.divider(); st.subheader("최종 검증")
            labels=(cand["Code"].astype(str)+" · "+cand["Name"].astype(str)+" · RS "+cand["RSScore"].round(1).astype(str)).tolist()
            selected=st.selectbox("검증할 섹터 대장주",labels,key=f"leader_{country}"); selected_code=selected.split(" · ")[0]; selected_row=cand[cand["Code"].astype(str)==selected_code].iloc[0]
            h1,h2=st.columns(2); pf=h1.number_input(f"현재 Portfolio Heat({currency})",min_value=0,value=0,step=10000,key=f"pf_{country}"); sh=h2.number_input(f"현재 Sector Heat({currency})",min_value=0,value=0,step=10000,key=f"sh_{country}")
            if st.button("선택 후보 Heat/재무 확인",type="primary",key=f"verify_{country}"):
                heat=evaluate_heat(float(selected_row["RiskKRW"]),float(account_equity),float(pf),float(sh))
                if heat["status"]!="PASS": st.error(f'🔴 Heat FAIL — {heat["reason"]}')
                elif is_jp:
                    st.success("🟢 Portfolio/Sector Heat PASS"); st.warning("🟡 일본 재무 Gate WATCH — TDnet/EDINET/기업 IR 검증 전 신규 진입 금지. 이 가격 후보를 ChatGPT에 보내 재무검증하세요.")
                else:
                    st.success("🟢 Portfolio/Sector Heat PASS")
                    try:
                        with st.spinner("OpenDART 최신 재무·공시 확인 중..."): gate,reasons,counts,corp,name,_,_=dart_gate(selected_code)
                    except Exception as e: gate="WATCH"; reasons=[f"OpenDART 확인 실패: {e}"]
                    if gate=="PASS": st.success("🟢 재무 Gate PASS → 신규 진입 가능")
                    elif gate=="FAIL": st.error("🔴 재무 Gate FAIL → 신규 진입 금지")
                    else: st.warning("🟡 재무 Gate WATCH → 신규 진입 보류")
                    for reason in reasons[:6]: st.caption("• "+str(reason))

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
            df,ticker = fetch_kr_stock(s, "3y")
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

        adtv20 = float(price.get("adtv20") or 0)
        unit_order_krw = float(plan["entry_price"]) * int(plan["unit_qty"])
        order_adtv_pct = (unit_order_krw / adtv20 * 100) if adtv20 > 0 else 999.0
        if adtv20 < 2_000_000_000 or order_adtv_pct > 0.5:
            st.error(f"🔴 유동성 Gate FAIL · 20일 평균 거래대금 {adtv20/100000000:.1f}억원 · 주문/ADTV {order_adtv_pct:.3f}%")
            st.stop()
        st.success(f"🟢 주봉/유동성 PASS · 120주선 {price['weekly_ma120']:,.0f}원 · ADTV {adtv20/100000000:.1f}억원 · 주문/ADTV {order_adtv_pct:.3f}%")

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

st.caption("v2.7.6 · v2.7.1 매매규칙 동결 · JPX 우선 + 안전 폴백 · 국가별 재무검증")

# v1.3 diagnostic price gate


st.caption(
    "RSScore가 가장 높은 돌파 종목을 대장주로 선정합니다."
)
st.caption(
    "각 강세 섹터에서 RSScore가 가장 높은 돌파 종목 1개를 최종 대장주로 표시합니다."
)
st.caption(
    "한국: DART 재무 Gate · 일본: TDnet/EDINET/기업 IR 재무검증 · 매수 전 Heat 재검사"
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

                # +0.5N 피라미딩: 최대 3 Units, 추가 전 Heat 자동 재검사
                units = int(t.get("unit_count") or 1)
                initial_entry = float(t.get("initial_entry_price") or t.get("entry_price") or 0)
                atr = float(t.get("atr_n") or 0)
                st.write(f'현재 보유: **{units} / 3 Units**')
                if units < 3:
                    next_trigger = initial_entry + (0.5 * atr * units)
                    st.caption(f'다음 추가매수 신호: {next_trigger:,.0f}원 (+0.5N 단계)')
                    add_c1, add_c2 = st.columns(2)
                    add_fill = add_c1.number_input(
                        "추가 실제 체결가", min_value=0.0, value=float(next_trigger), step=10.0,
                        key=f'add_fill_{t["trade_id"]}'
                    )
                    base_unit_qty = max(1, int(round((float(t.get("risk_krw") or 0) / max(units,1)) / max(2*atr,1))))
                    add_qty = add_c2.number_input(
                        "추가 체결수량", min_value=1, value=base_unit_qty, step=1,
                        key=f'add_qty_{t["trade_id"]}'
                    )
                    add_risk = float(add_qty) * 2.0 * atr
                    auto_pf_heat = sum(float(x.get("risk_krw") or 0) for x in open_trades)
                    auto_sec_heat = sum(float(x.get("risk_krw") or 0) for x in open_trades if x.get("sector") == t.get("sector"))
                    add_heat = evaluate_heat(add_risk, 10_000_000, auto_pf_heat, auto_sec_heat)
                    st.caption(
                        f'추가 후 Portfolio Heat {add_heat["portfolio_heat_krw"]:,.0f}/{add_heat["portfolio_limit_krw"]:,.0f}원 · '
                        f'Sector Heat {add_heat["sector_heat_krw"]:,.0f}/{add_heat["sector_limit_krw"]:,.0f}원'
                    )
                    if st.button(f"➕ {units+1} Unit 추가 기록", key=f'add_unit_{t["trade_id"]}'):
                        if add_heat["status"] != "PASS":
                            st.error("🔴 Heat 상한 초과 — 추가매수 금지")
                        else:
                            try:
                                result = record_add_unit(t["trade_id"], add_fill, add_qty, add_risk)
                                st.success(f'추가매수 저장 · 현재 {result.get("unit_count",1)} Units')
                                st.rerun()
                            except Exception as e:
                                st.error(str(e))
                else:
                    st.info("최대 3 Units 도달 · 추가매수 금지")

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
    "v2.7.1 동결: 확정 종가 돌파 + 거래량 2배 + 120주선 + 유동성 Gate + 최대 3 Units. 한국 재무는 DART, 일본 재무는 TDnet/EDINET/기업 IR 검증 전 WATCH입니다."
)

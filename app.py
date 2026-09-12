from __future__ import annotations
import os
from datetime import date,timedelta
import pandas as pd
import streamlit as st
from opendart_client import OpenDartClient
from dart_financials import build_quarter_history,financing_summary,financial_gate_v05

st.set_page_config(page_title='K-TURTLE Mobile',page_icon='🐢',layout='centered')
st.markdown("<style>.block-container{padding-top:1rem;padding-bottom:4rem;max-width:820px}.stButton>button{width:100%;min-height:48px;font-weight:700;border-radius:12px}</style>",unsafe_allow_html=True)

def get_key():
    try:
        if 'OPENDART_API_KEY' in st.secrets:return str(st.secrets['OPENDART_API_KEY'])
    except Exception:pass
    return os.getenv('OPENDART_API_KEY')

@st.cache_resource(show_spinner=False)
def get_client(k):return OpenDartClient(api_key=k)

@st.cache_data(ttl=86400,show_spinner=False)
def stock_map(k):
    return {c.stock_code:(c.corp_code,c.corp_name) for c in get_client(k).corp_codes() if c.stock_code}

def won(v):return '미확인' if v is None else f'{v/1e8:,.1f}억원'
def pct(v):return '미확인' if v is None else f'{v:.2f}%'

st.title('🐢 K‑TURTLE Mobile v0.5')
st.caption('한국주식 · OpenDART 재무/공시 자동검사 · 실주문 없음')

k=get_key()
if not k:
    st.error('OpenDART 인증키가 Secrets에 없습니다.')
    st.stop()

tabs=st.tabs(['🔎 종목 검사','⚙️ 연결상태'])

with tabs[0]:
    stock=st.text_input('종목코드',value='005930',max_chars=6)
    if st.button('K‑TURTLE 재무 Gate 검사',type='primary'):
        s=stock.strip().zfill(6)
        try:
            mp=stock_map(k)
            if s not in mp:
                st.error('상장종목코드를 찾지 못했습니다.')
                st.stop()
            corp,name=mp[s]
            c=get_client(k)
            with st.spinner('최근 분기 재무와 공시를 확인하는 중...'):
                hist=build_quarter_history(c,corp,years_back=2)
                end=date.today();begin=end-timedelta(days=365)
                events=c.financing_events(corp,begin.strftime('%Y%m%d'),end.strftime('%Y%m%d'))
                counts,total=financing_summary(events)
                gate,reasons=financial_gate_v05(hist,total)

            st.subheader(f'{name} ({s})')
            st.caption(f'DART 고유번호 {corp}')

            if gate=='PASS':
                st.success('🟢 재무 Gate: PASS')
            elif gate=='WATCH':
                st.warning('🟡 재무 Gate: WATCH — 신규 진입 보류')
            else:
                st.error('🔴 재무 Gate: FAIL — 신규 진입 금지')

            for r in reasons:
                st.write('•',r)

            if hist:
                st.subheader('최근 분기 추세')
                table=pd.DataFrame([{
                    '분기':f"{r['year']}-{r['quarter']}",
                    '매출(억원)':None if r['revenue'] is None else round(r['revenue']/1e8,1),
                    '영업이익(억원)':None if r['operating_profit'] is None else round(r['operating_profit']/1e8,1),
                    '영업이익률(%)':None if r['operating_margin_pct'] is None else round(r['operating_margin_pct'],2),
                    '연결/별도':r['fs_div']
                } for r in hist[-8:]])
                st.dataframe(table,use_container_width=True,hide_index=True)

                last=hist[-1]
                c1,c2=st.columns(2)
                c1.metric('최근 분기 영업이익',won(last['operating_profit']))
                c2.metric('최근 분기 영업이익률',pct(last['operating_margin_pct']))
                c3,c4=st.columns(2)
                c3.metric('현금성자산',won(last['cash_and_equivalents']))
                dr=None
                if last['equity'] not in (None,0) and last['total_liabilities'] is not None:
                    dr=last['total_liabilities']/last['equity']*100
                c4.metric('부채비율',pct(dr))

                with st.expander('사용된 계정 확인'):
                    st.write('매출 계정:',last.get('revenue_account') or '미확인')
                    st.write('영업이익 계정:',last.get('op_account') or '미확인')
                    st.caption('account_id + 재무제표 구분 + 계정명 순으로 선택합니다.')

            st.subheader('최근 1년 자금조달 공시')
            cols=st.columns(4)
            for col,key,label in zip(cols,['RIGHTS_ISSUE','CB','BW','EB'],['유증','CB','BW','EB']):
                col.metric(label,counts.get(key,0))

            if total==0:
                st.success('최근 1년 유증·CB·BW·EB 0건')
            elif total==1:
                st.warning('최근 1년 자금조달 공시 1건')
            else:
                st.error('최근 1년 자금조달 공시 2건 이상')

            st.info('PASS만 다음 단계의 20일/55일 돌파 검사 대상으로 사용합니다. WATCH/FAIL은 신규 진입을 차단합니다.')

        except Exception as e:
            st.error(f'검사 오류: {e}')

with tabs[1]:
    st.write('OpenDART Secrets:','✅ 등록됨')
    if st.button('연결 테스트'):
        try:
            st.success(f'정상 연결 · 상장종목 매핑 {len(stock_map(k)):,}개')
        except Exception as e:
            st.error(str(e))
    st.warning('v0.5는 실제 증권사 주문을 전송하지 않습니다.')

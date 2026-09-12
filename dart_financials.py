from __future__ import annotations
from datetime import date

REPORTS={"11013":("Q1",1),"11012":("Q2",2),"11014":("Q3",3),"11011":("Q4",4)}

def num(v):
    if v is None: return None
    s=str(v).strip().replace(",","")
    if s in ("","-","None","nan"): return None
    try: return float(s)
    except Exception: return None

def norm_name(s): return "".join(str(s or "").replace(" ","").split()).lower()

ACCOUNT_RULES={
 "revenue":{"sj":{"IS","CIS"},"ids":{"ifrs-full_Revenue","ifrs_Revenue"},"names":["매출액","수익(매출액)","영업수익","매출"]},
 "operating_profit":{"sj":{"IS","CIS"},"ids":{"dart_OperatingIncomeLoss","ifrs-full_ProfitLossFromOperatingActivities"},"names":["영업이익","영업이익(손실)","영업손익"]},
 "cash":{"sj":{"BS"},"ids":{"ifrs-full_CashAndCashEquivalents"},"names":["현금및현금성자산","현금및현금성자산등"]},
 "liabilities":{"sj":{"BS"},"ids":{"ifrs-full_Liabilities"},"names":["부채총계"]},
 "equity":{"sj":{"BS"},"ids":{"ifrs-full_Equity"},"names":["자본총계"]},
 "short_borrowings":{"sj":{"BS"},"ids":set(),"names":["단기차입금","단기차입금및유동성장기부채","단기차입부채"]},
 "interest_expense":{"sj":{"IS","CIS"},"ids":set(),"names":["이자비용","금융비용"]},
 "ocf":{"sj":{"CF"},"ids":{"ifrs-full_CashFlowsFromUsedInOperatingActivities"},"names":["영업활동으로인한현금흐름","영업활동현금흐름","영업활동으로부터의현금흐름"]},
}

def pick(rows,key):
    rule=ACCOUNT_RULES[key]
    cand=[r for r in rows if str(r.get('sj_div','')).strip() in rule['sj']]
    for r in cand:
        if str(r.get('account_id','')).strip() in rule['ids']: return r
    names={norm_name(n) for n in rule['names']}
    for r in cand:
        if norm_name(r.get('account_nm')) in names: return r
    hits=[]
    for r in cand:
        rn=norm_name(r.get('account_nm'))
        if rn and any(n in rn or rn in n for n in names): hits.append(r)
    if hits:
        hits.sort(key=lambda r:len(norm_name(r.get('account_nm'))))
        return hits[0]
    return None

def amount_for_period(row,report_code):
    if not row: return None
    sj=str(row.get('sj_div','')).strip()
    if sj=='BS': return num(row.get('thstrm_amount'))
    if sj in {'IS','CIS'}: return num(row.get('thstrm_amount'))
    if sj=='CF': return num(row.get('thstrm_add_amount')) or num(row.get('thstrm_amount'))
    return num(row.get('thstrm_amount'))

def extract_snapshot(rows,year,report_code,fs_div):
    rev_r=pick(rows,'revenue'); op_r=pick(rows,'operating_profit'); cash_r=pick(rows,'cash')
    liab_r=pick(rows,'liabilities'); eq_r=pick(rows,'equity'); short_r=pick(rows,'short_borrowings')
    int_r=pick(rows,'interest_expense'); ocf_r=pick(rows,'ocf')
    revenue=amount_for_period(rev_r,report_code); op=amount_for_period(op_r,report_code)
    cash=amount_for_period(cash_r,report_code); liab=amount_for_period(liab_r,report_code); equity=amount_for_period(eq_r,report_code)
    short=amount_for_period(short_r,report_code); intexp=amount_for_period(int_r,report_code)
    ocf=(num(ocf_r.get('thstrm_add_amount')) or num(ocf_r.get('thstrm_amount'))) if ocf_r else None
    margin=op/revenue*100 if revenue not in (None,0) and op is not None else None
    return {'year':int(year),'report_code':report_code,'quarter':REPORTS[report_code][0],'quarter_no':REPORTS[report_code][1],
            'fs_div':fs_div,'revenue':revenue,'operating_profit':op,'operating_margin_pct':margin,'cash_and_equivalents':cash,
            'total_liabilities':liab,'equity':equity,'short_term_borrowings':short,'interest_expense':intexp,
            'operating_cash_flow_cumulative':ocf,'revenue_account':rev_r.get('account_nm') if rev_r else None,
            'op_account':op_r.get('account_nm') if op_r else None}

def build_quarter_history(client,corp_code,years_back=2):
    today=date.today(); out=[]
    for year in range(today.year-years_back,today.year+1):
        for code in ['11013','11012','11014','11011']:
            rows=client.full_financials(corp_code,year,code,'CFS'); fs='CFS'
            if not rows:
                rows=client.full_financials(corp_code,year,code,'OFS'); fs='OFS'
            if not rows: continue
            s=extract_snapshot(rows,year,code,fs)
            if s['revenue'] is None and s['operating_profit'] is None: continue
            out.append(s)
    uniq={(r['year'],r['quarter_no']):r for r in out}
    hist=sorted(uniq.values(),key=lambda r:(r['year'],r['quarter_no']))
    by={}
    for r in hist: by.setdefault(r['year'],{})[r['quarter_no']]=r
    for y,qs in by.items():
        if 4 in qs and all(q in qs for q in [1,2,3]):
            q4=qs[4]
            for key in ['revenue','operating_profit','interest_expense']:
                vals=[qs[q].get(key) for q in [1,2,3,4]]
                if all(v is not None for v in vals): q4[key]=vals[3]-vals[0]-vals[1]-vals[2]
            if q4['revenue'] not in (None,0) and q4['operating_profit'] is not None:
                q4['operating_margin_pct']=q4['operating_profit']/q4['revenue']*100
    return hist[-8:]

def financing_summary(events):
    counts={k:len(v) for k,v in events.items()}; return counts,sum(counts.values())

def financial_gate_v05(hist,financing_count):
    if len(hist)<4: return 'WATCH',['최근 4개 분기 재무데이터 부족']
    h=hist[-4:]; profits=[r.get('operating_profit') for r in h]; margins=[r.get('operating_margin_pct') for r in h]
    if any(v is None for v in profits+margins): return 'WATCH',['영업이익/영업이익률 일부 미확인']
    profitable=sum(v>0 for v in profits); last2=sum(margins[-2:])/2; prev2=sum(margins[:2])/2
    margin_steps=sum(1 for i in range(1,4) if margins[i]>margins[i-1]); op_steps=sum(1 for i in range(1,4) if profits[i]>profits[i-1])
    normal=profitable>=3 and profits[-1]>0 and last2>prev2 and margin_steps>=2
    turnaround=profits[-1]>0 and any(v<=0 for v in profits[:-1]) and op_steps>=2
    reasons=[]
    if normal: reasons.append('정상 성장형 조건 통과')
    elif turnaround: reasons.append('턴어라운드형 조건 통과')
    else: return 'FAIL',['영업이익/영업이익률 개선 조건 미충족']
    latest=h[-1]; cash=latest.get('cash_and_equivalents'); short=latest.get('short_term_borrowings'); liab=latest.get('total_liabilities'); equity=latest.get('equity')
    watch=False
    if cash is not None and short not in (None,0):
        c2d=cash/short
        if c2d<0.5: return 'FAIL',reasons+['현금/단기차입금 0.5배 미만']
        elif c2d<1.0: reasons.append('현금/단기차입금 1.0배 미만'); watch=True
    if liab is not None and equity not in (None,0) and liab/equity*100>200:
        reasons.append('부채비율 200% 초과'); watch=True
    if financing_count>=2: return 'FAIL',reasons+['최근 1년 유증/CB/BW/EB 2건 이상']
    if financing_count==1: return 'WATCH',reasons+['최근 1년 유증/CB/BW/EB 1건']
    return ('WATCH' if watch else 'PASS'),reasons

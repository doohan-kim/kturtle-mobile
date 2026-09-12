from __future__ import annotations
from datetime import date

REPORTS = {
    "11013": ("Q1", 1),
    "11012": ("Q2", 2),
    "11014": ("Q3", 3),
    "11011": ("Q4", 4),
}

def num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "None", "nan"):
        return None
    try:
        return float(s)
    except Exception:
        return None

def norm(s):
    return "".join(str(s or "").replace(" ", "").split()).lower()

REV_NAMES = {"매출액","수익(매출액)","영업수익"}
OP_NAMES = {"영업이익","영업이익(손실)","영업손익"}

def _major_candidates(rows, fs_div, names):
    nset = {norm(x) for x in names}
    out = []
    for r in rows:
        if str(r.get("fs_div","")).strip() != fs_div:
            continue
        sj = str(r.get("sj_div","")).strip()
        if sj not in {"IS","CIS"}:
            continue
        if norm(r.get("account_nm")) in nset:
            out.append(r)
    return out

def _pick_major(rows, fs_div, names):
    cands = _major_candidates(rows, fs_div, names)
    if not cands:
        return None
    # IS 우선, 그 다음 ord
    cands.sort(key=lambda r: (
        0 if str(r.get("sj_div","")).strip()=="IS" else 1,
        int(str(r.get("ord","999999")).replace(",","") or 999999)
    ))
    return cands[0]

def _full_candidates(rows, names, ids=None):
    nset = {norm(x) for x in names}
    ids = set(ids or [])
    out = []
    for r in rows:
        sj = str(r.get("sj_div","")).strip()
        if sj not in {"IS","CIS"}:
            continue
        name_ok = norm(r.get("account_nm")) in nset
        id_ok = (not ids) or str(r.get("account_id","")).strip() in ids
        if name_ok and id_ok:
            out.append(r)
    return out

def _pick_full(rows, names, ids=None):
    cands = _full_candidates(rows, names, ids)
    if not cands:
        # 계정ID가 회사별로 다를 수 있으므로 이름 exact만 fallback
        cands = _full_candidates(rows, names, None)
    if not cands:
        return None
    cands.sort(key=lambda r: (
        0 if str(r.get("sj_div","")).strip()=="IS" else 1,
        int(str(r.get("ord","999999")).replace(",","") or 999999)
    ))
    return cands[0]

def _period_amount(row, report_code):
    if not row:
        return None
    # OpenDART 공식 정의:
    # 분/반기 (포괄)손익계산서 thstrm_amount = 3개월 금액
    # 사업보고서 thstrm_amount = 연간 금액
    return num(row.get("thstrm_amount"))

def _balance_pick(rows, account_id, names):
    nset = {norm(x) for x in names}
    cands = []
    for r in rows:
        if str(r.get("sj_div","")).strip() != "BS":
            continue
        aid = str(r.get("account_id","")).strip()
        nm = norm(r.get("account_nm"))
        if aid == account_id or nm in nset:
            cands.append(r)
    if not cands:
        return None
    cands.sort(key=lambda r:int(str(r.get("ord","999999")).replace(",","") or 999999))
    return cands[0]

def _gap_pct(a,b):
    if a in (None,0) or b is None:
        return None
    return abs(a-b)/abs(a)*100

def extract_period(client, corp_code, year, report_code):
    # 주요계정 API에서 연결(CFS) 우선, 없으면 별도(OFS)
    major = client.major_accounts(corp_code, year, report_code)

    fs_div = "CFS"
    rev_m = _pick_major(major, fs_div, REV_NAMES)
    op_m = _pick_major(major, fs_div, OP_NAMES)
    if not rev_m or not op_m:
        fs_div = "OFS"
        rev_m = _pick_major(major, fs_div, REV_NAMES)
        op_m = _pick_major(major, fs_div, OP_NAMES)

    if not rev_m and not op_m:
        return None

    full = client.full_financials(corp_code, year, report_code, fs_div)

    rev_f = _pick_full(
        full, REV_NAMES,
        ids={"ifrs-full_Revenue","ifrs_Revenue"}
    )
    op_f = _pick_full(
        full, OP_NAMES,
        ids={"dart_OperatingIncomeLoss","ifrs-full_ProfitLossFromOperatingActivities"}
    )

    revenue = _period_amount(rev_m, report_code)
    op = _period_amount(op_m, report_code)

    # Q4는 사업보고서의 연간값이므로 Q1~Q3를 나중에 차감
    margin = (op/revenue*100) if revenue not in (None,0) and op is not None else None

    rev_full = _period_amount(rev_f, report_code)
    op_full = _period_amount(op_f, report_code)

    cash_r = _balance_pick(
        full, "ifrs-full_CashAndCashEquivalents",
        ["현금및현금성자산","현금및현금성자산등"]
    )
    liab_r = _balance_pick(full, "ifrs-full_Liabilities", ["부채총계"])
    eq_r = _balance_pick(full, "ifrs-full_Equity", ["자본총계"])

    flags = []
    rg = _gap_pct(revenue, rev_full)
    og = _gap_pct(op, op_full)

    if rg is not None and rg > 1.0:
        flags.append("매출 주요계정/전체재무제표 불일치")
    if og is not None and og > 1.0:
        flags.append("영업이익 주요계정/전체재무제표 불일치")
    if margin is not None and abs(margin) > 40:
        flags.append("영업이익률 절대값 40% 초과")

    return {
        "year":int(year),
        "report_code":report_code,
        "quarter":REPORTS[report_code][0],
        "quarter_no":REPORTS[report_code][1],
        "fs_div":fs_div,
        "revenue":revenue,
        "operating_profit":op,
        "operating_margin_pct":margin,
        "cash_and_equivalents":num(cash_r.get("thstrm_amount")) if cash_r else None,
        "total_liabilities":num(liab_r.get("thstrm_amount")) if liab_r else None,
        "equity":num(eq_r.get("thstrm_amount")) if eq_r else None,
        "validation_flags":flags,
        "major_revenue_row":rev_m,
        "major_op_row":op_m,
        "full_revenue_row":rev_f,
        "full_op_row":op_f,
        "revenue_gap_pct":rg,
        "op_gap_pct":og,
    }

def build_quarter_history(client, corp_code, years_back=2):
    today = date.today()
    rows = []
    for year in range(today.year-years_back, today.year+1):
        for code in ["11013","11012","11014","11011"]:
            r = extract_period(client, corp_code, year, code)
            if r:
                rows.append(r)

    rows.sort(key=lambda r:(r["year"],r["quarter_no"]))

    # Q4 연간값 -> 단독 4분기
    by_year = {}
    for r in rows:
        by_year.setdefault(r["year"],{})[r["quarter_no"]] = r

    out = []
    for year in sorted(by_year):
        qs = by_year[year]
        for q in [1,2,3]:
            if q in qs:
                out.append(qs[q])
        if 4 in qs:
            q4 = qs[4].copy()
            if all(q in qs for q in [1,2,3]):
                annual_rev = q4["revenue"]
                annual_op = q4["operating_profit"]
                if annual_rev is not None and all(qs[q]["revenue"] is not None for q in [1,2,3]):
                    q4["revenue"] = annual_rev - sum(qs[q]["revenue"] for q in [1,2,3])
                if annual_op is not None and all(qs[q]["operating_profit"] is not None for q in [1,2,3]):
                    q4["operating_profit"] = annual_op - sum(qs[q]["operating_profit"] for q in [1,2,3])
                if q4["revenue"] not in (None,0) and q4["operating_profit"] is not None:
                    q4["operating_margin_pct"] = q4["operating_profit"]/q4["revenue"]*100
                if q4["operating_margin_pct"] is not None and abs(q4["operating_margin_pct"]) > 40:
                    q4["validation_flags"] = list(q4.get("validation_flags",[])) + ["Q4 영업이익률 절대값 40% 초과"]
            out.append(q4)

    return out[-8:]

def financing_summary(events):
    counts = {k:len(v) for k,v in events.items()}
    return counts, sum(counts.values())

def financial_gate_v07(hist, financing_count):
    if len(hist) < 4:
        return "WATCH", ["최근 4개 분기 데이터 부족"]

    h = hist[-4:]
    bad = []
    for r in h:
        bad.extend(r.get("validation_flags",[]))
    if bad:
        return "WATCH", ["재무데이터 검증 필요: " + ", ".join(sorted(set(bad)))]

    profits = [r.get("operating_profit") for r in h]
    margins = [r.get("operating_margin_pct") for r in h]
    if any(v is None for v in profits+margins):
        return "WATCH", ["영업이익/영업이익률 일부 미확인"]

    profitable = sum(v > 0 for v in profits)
    last2 = sum(margins[-2:])/2
    prev2 = sum(margins[:2])/2
    margin_up_steps = sum(1 for i in range(1,4) if margins[i] > margins[i-1])
    op_up_steps = sum(1 for i in range(1,4) if profits[i] > profits[i-1])

    normal = profitable >= 3 and profits[-1] > 0 and last2 > prev2 and margin_up_steps >= 2
    turnaround = profits[-1] > 0 and any(v <= 0 for v in profits[:-1]) and op_up_steps >= 2

    if normal:
        reasons = ["정상 성장형 조건 통과"]
    elif turnaround:
        reasons = ["턴어라운드형 조건 통과"]
    else:
        return "FAIL", ["영업이익/영업이익률 개선 조건 미충족"]

    if financing_count >= 2:
        return "FAIL", reasons + ["최근 1년 유증/CB/BW/EB 2건 이상"]
    if financing_count == 1:
        return "WATCH", reasons + ["최근 1년 유증/CB/BW/EB 1건"]

    return "PASS", reasons

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

RULES = {
    "revenue": {
        "sj": {"IS","CIS"},
        "ids": {"ifrs-full_Revenue","ifrs_Revenue"},
        "names": ["매출액","수익(매출액)","영업수익"],
    },
    "operating_profit": {
        "sj": {"IS","CIS"},
        "ids": {"dart_OperatingIncomeLoss","ifrs-full_ProfitLossFromOperatingActivities"},
        "names": ["영업이익","영업이익(손실)","영업손익"],
    },
    "cash": {
        "sj": {"BS"},
        "ids": {"ifrs-full_CashAndCashEquivalents"},
        "names": ["현금및현금성자산","현금및현금성자산등"],
    },
    "liabilities": {
        "sj": {"BS"},
        "ids": {"ifrs-full_Liabilities"},
        "names": ["부채총계"],
    },
    "equity": {
        "sj": {"BS"},
        "ids": {"ifrs-full_Equity"},
        "names": ["자본총계"],
    },
    "short_borrowings": {
        "sj": {"BS"},
        "ids": set(),
        "names": ["단기차입금","단기차입금및유동성장기부채","단기차입부채"],
    },
}

def _ord(r):
    try:
        return int(str(r.get("ord","999999")).replace(",",""))
    except Exception:
        return 999999

def pick(rows, key):
    rule = RULES[key]
    candidates = [r for r in rows if str(r.get("sj_div","")).strip() in rule["sj"]]

    exact = [r for r in candidates if str(r.get("account_id","")).strip() in rule["ids"]]
    if exact:
        exact.sort(key=_ord)
        return exact[0]

    names = {norm(x) for x in rule["names"]}
    exactn = [r for r in candidates if norm(r.get("account_nm")) in names]
    if exactn:
        exactn.sort(key=_ord)
        return exactn[0]

    return None

def cumulative_amount(row):
    if not row:
        return None
    sj = str(row.get("sj_div","")).strip()
    if sj in {"IS","CIS"}:
        add = num(row.get("thstrm_add_amount"))
        return add if add is not None else num(row.get("thstrm_amount"))
    if sj == "BS":
        return num(row.get("thstrm_amount"))
    return num(row.get("thstrm_amount"))

def extract_cumulative_snapshot(rows, year, report_code, fs_div):
    rev = pick(rows, "revenue")
    op = pick(rows, "operating_profit")
    cash = pick(rows, "cash")
    liab = pick(rows, "liabilities")
    eq = pick(rows, "equity")
    short = pick(rows, "short_borrowings")

    return {
        "year": int(year),
        "report_code": report_code,
        "quarter": REPORTS[report_code][0],
        "quarter_no": REPORTS[report_code][1],
        "fs_div": fs_div,
        "revenue_cum": cumulative_amount(rev),
        "operating_profit_cum": cumulative_amount(op),
        "cash_and_equivalents": cumulative_amount(cash),
        "total_liabilities": cumulative_amount(liab),
        "equity": cumulative_amount(eq),
        "short_term_borrowings": cumulative_amount(short),
        "revenue_account": rev.get("account_nm") if rev else None,
        "revenue_account_id": rev.get("account_id") if rev else None,
        "op_account": op.get("account_nm") if op else None,
        "op_account_id": op.get("account_id") if op else None,
    }

def _major_pick(rows, names):
    nset = {norm(x) for x in names}
    for r in rows:
        if norm(r.get("account_nm")) in nset:
            return r
    return None

def cross_check_major_accounts(client, corp_code, year, report_code, snap):
    rows = client.major_accounts(corp_code, year, report_code)
    if not rows:
        return {"status":"NO_DATA","revenue_gap_pct":None,"op_gap_pct":None}

    rows = [r for r in rows if str(r.get("fs_div","")).strip() == snap["fs_div"]]
    rev_r = _major_pick(rows, ["매출액","수익(매출액)","영업수익"])
    op_r = _major_pick(rows, ["영업이익","영업이익(손실)"])

    def mval(r):
        if not r:
            return None
        add = num(r.get("thstrm_add_amount"))
        return add if add is not None else num(r.get("thstrm_amount"))

    rev_m = mval(rev_r)
    op_m = mval(op_r)

    def gap(ref, got):
        if ref in (None,0) or got is None:
            return None
        return abs(ref-got)/abs(ref)*100

    rg = gap(rev_m, snap.get("revenue_cum"))
    og = gap(op_m, snap.get("operating_profit_cum"))
    ok = (rg is None or rg <= 1.0) and (og is None or og <= 1.0)

    return {
        "status":"PASS" if ok else "FAIL",
        "revenue_gap_pct":rg,
        "op_gap_pct":og,
    }

def build_quarter_history(client, corp_code, years_back=2):
    today = date.today()
    cumulative = []

    for year in range(today.year-years_back, today.year+1):
        for code in ["11013","11012","11014","11011"]:
            rows = client.full_financials(corp_code, year, code, "CFS")
            fs_div = "CFS"
            if not rows:
                rows = client.full_financials(corp_code, year, code, "OFS")
                fs_div = "OFS"
            if not rows:
                continue

            snap = extract_cumulative_snapshot(rows, year, code, fs_div)
            if snap["revenue_cum"] is None and snap["operating_profit_cum"] is None:
                continue
            snap["cross_check"] = cross_check_major_accounts(client, corp_code, year, code, snap)
            cumulative.append(snap)

    uniq = {(r["year"], r["quarter_no"]): r for r in cumulative}
    cumulative = sorted(uniq.values(), key=lambda r:(r["year"],r["quarter_no"]))

    out = []
    by_year = {}
    for r in cumulative:
        by_year.setdefault(r["year"], {})[r["quarter_no"]] = r

    for year in sorted(by_year):
        qs = by_year[year]
        prev_rev = 0.0
        prev_op = 0.0

        for q in [1,2,3,4]:
            if q not in qs:
                continue
            r = qs[q].copy()
            rc = r.get("revenue_cum")
            oc = r.get("operating_profit_cum")

            revenue = None if rc is None else rc - prev_rev
            op = None if oc is None else oc - prev_op

            if rc is not None:
                prev_rev = rc
            if oc is not None:
                prev_op = oc

            margin = (op/revenue*100) if revenue not in (None,0) and op is not None else None

            flags = []
            if revenue is not None and revenue <= 0:
                flags.append("매출 분기값 비정상")
            if margin is not None and abs(margin) > 40:
                flags.append("영업이익률 절대값 40% 초과")
            if r.get("cross_check",{}).get("status") == "FAIL":
                flags.append("주요계정 API 교차검증 실패")

            r["revenue"] = revenue
            r["operating_profit"] = op
            r["operating_margin_pct"] = margin
            r["validation_flags"] = flags
            out.append(r)

    return out[-8:]

def financing_summary(events):
    counts = {k: len(v) for k,v in events.items()}
    return counts, sum(counts.values())

def financial_gate_v06(hist, financing_count):
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

    latest = h[-1]
    cash = latest.get("cash_and_equivalents")
    short = latest.get("short_term_borrowings")
    liab = latest.get("total_liabilities")
    eq = latest.get("equity")

    watch = False
    if cash is not None and short not in (None,0):
        c2d = cash/short
        if c2d < 0.5:
            return "FAIL", reasons + ["현금/단기차입금 0.5배 미만"]
        if c2d < 1.0:
            reasons.append("현금/단기차입금 1.0배 미만")
            watch = True

    if liab is not None and eq not in (None,0) and liab/eq*100 > 200:
        reasons.append("부채비율 200% 초과")
        watch = True

    if financing_count >= 2:
        return "FAIL", reasons + ["최근 1년 유증/CB/BW/EB 2건 이상"]
    if financing_count == 1:
        return "WATCH", reasons + ["최근 1년 유증/CB/BW/EB 1건"]

    return ("WATCH" if watch else "PASS"), reasons

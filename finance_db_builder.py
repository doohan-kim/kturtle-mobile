from __future__ import annotations
import io, json, os, time, zipfile, requests, xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = "https://opendart.fss.or.kr/api"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "K-TURTLE-FinanceDB/2.4"})

REPORTS = [
    ("11013", "Q1"),
    ("11012", "Q2"),
    ("11014", "Q3"),
    ("11011", "Q4"),
]
REV_NAMES = {"매출액","수익(매출액)","영업수익"}
OP_NAMES = {"영업이익","영업이익(손실)","영업손익"}

def norm(s):
    return "".join(str(s or "").split()).lower()

def num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",","")
    if s in ("","-","None"):
        return None
    try:
        return float(s)
    except Exception:
        return None

def get(url, params, attempts=4, timeout=(10,90)):
    last = None
    for i in range(attempts):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            if i < attempts-1:
                time.sleep(1.5*(i+1))
    raise RuntimeError(f"OpenDART 연결 실패: {last}")

def api_json(endpoint, api_key, **params):
    params["crtfc_key"] = api_key
    data = get(f"{BASE}/{endpoint}", params).json()
    status = data.get("status")
    if status not in (None, "000", "013"):
        raise RuntimeError(f"DART {status}: {data.get('message')}")
    return data

def corp_codes(api_key):
    r = get(f"{BASE}/corpCode.xml", {"crtfc_key": api_key})
    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read(z.namelist()[0]))
    out = []
    for item in root.findall("list"):
        stock = (item.findtext("stock_code") or "").strip()
        if not stock:
            continue
        out.append({
            "corp_code": (item.findtext("corp_code") or "").strip(),
            "stock_code": stock.zfill(6),
            "corp_name": (item.findtext("corp_name") or "").strip(),
        })
    return out

def pick(rows, names):
    ns = {norm(x) for x in names}
    cands = [
        r for r in rows
        if str(r.get("sj_div","")).strip() in {"IS","CIS"}
        and norm(r.get("account_nm")) in ns
    ]
    if not cands:
        return None
    cands.sort(key=lambda r: (
        0 if str(r.get("sj_div","")).strip()=="IS" else 1,
        int(str(r.get("ord","999999")).replace(",","") or 999999)
    ))
    return cands[0]

def latest_report_periods():
    y = date.today().year
    periods = []
    for year in range(y-2, y+1):
        for code, qname in REPORTS:
            periods.append((year, code, qname))
    return periods[-8:]

def fetch_company_quarters(api_key, corp_code):
    rows_by_period = {}
    for year, reprt_code, qname in latest_report_periods():
        data = api_json(
            "fnlttSinglAcnt.json",
            api_key,
            corp_code=corp_code,
            bsns_year=str(year),
            reprt_code=reprt_code
        )
        rows = data.get("list", []) or []
        if not rows:
            continue

        cfs = [r for r in rows if r.get("fs_div")=="CFS"]
        use = cfs if cfs else [r for r in rows if r.get("fs_div")=="OFS"]
        if not use:
            continue

        rev = pick(use, REV_NAMES)
        op = pick(use, OP_NAMES)
        if not rev and not op:
            continue

        rows_by_period[(year,qname)] = {
            "year": year,
            "quarter": qname,
            "fs_div": "CFS" if cfs else "OFS",
            "revenue_raw": num(rev.get("thstrm_amount")) if rev else None,
            "operating_profit_raw": num(op.get("thstrm_amount")) if op else None,
        }
        time.sleep(0.03)

    # Convert Q4 annual to standalone quarter when possible.
    out = []
    years = sorted({y for y,_ in rows_by_period})
    for y in years:
        qs = {q:rows_by_period[(y,q)] for q in ("Q1","Q2","Q3","Q4") if (y,q) in rows_by_period}
        for q in ("Q1","Q2","Q3"):
            if q in qs:
                r = dict(qs[q])
                r["revenue"] = r.pop("revenue_raw")
                r["operating_profit"] = r.pop("operating_profit_raw")
                rv, op = r["revenue"], r["operating_profit"]
                r["operating_margin_pct"] = (op/rv*100) if rv not in (None,0) and op is not None else None
                out.append(r)
        if "Q4" in qs:
            r = dict(qs["Q4"])
            ann_rev, ann_op = r.pop("revenue_raw"), r.pop("operating_profit_raw")
            prev_rev = [qs[q].get("revenue_raw") for q in ("Q1","Q2","Q3") if q in qs]
            prev_op = [qs[q].get("operating_profit_raw") for q in ("Q1","Q2","Q3") if q in qs]
            r["revenue"] = ann_rev - sum(prev_rev) if ann_rev is not None and len(prev_rev)==3 and all(v is not None for v in prev_rev) else None
            r["operating_profit"] = ann_op - sum(prev_op) if ann_op is not None and len(prev_op)==3 and all(v is not None for v in prev_op) else None
            rv, op = r["revenue"], r["operating_profit"]
            r["operating_margin_pct"] = (op/rv*100) if rv not in (None,0) and op is not None else None
            out.append(r)

    out.sort(key=lambda r:(r["year"], r["quarter"]))
    return out[-8:]

def financial_gate(quarters):
    if len(quarters) < 4:
        return "WATCH", ["최근 4개 분기 데이터 부족"]
    h = quarters[-4:]
    profits = [r.get("operating_profit") for r in h]
    margins = [r.get("operating_margin_pct") for r in h]
    if any(v is None for v in profits+margins):
        return "WATCH", ["영업이익/영업이익률 일부 미확인"]

    profitable = sum(v > 0 for v in profits)
    last2 = sum(margins[-2:]) / 2
    prev2 = sum(margins[:2]) / 2
    margin_up_steps = sum(1 for i in range(1,4) if margins[i] > margins[i-1])
    op_up_steps = sum(1 for i in range(1,4) if profits[i] > profits[i-1])

    normal = profitable >= 3 and profits[-1] > 0 and last2 > prev2 and margin_up_steps >= 2
    turnaround = profits[-1] > 0 and any(v <= 0 for v in profits[:-1]) and op_up_steps >= 2

    if normal:
        return "PASS", ["정상 성장형 조건 통과"]
    if turnaround:
        return "PASS", ["턴어라운드형 조건 통과"]
    return "FAIL", ["영업이익/영업이익률 개선 조건 미충족"]

def financing_counts(api_key, corp_code):
    end = date.today()
    begin = end - timedelta(days=365)
    endpoints = {
        "RIGHTS_ISSUE":"piicDecsn.json",
        "CB":"cvbdIsDecsn.json",
        "BW":"bdwtIsDecsn.json",
        "EB":"exbdIsDecsn.json",
    }
    out = {}
    for k, ep in endpoints.items():
        data = api_json(
            ep, api_key,
            corp_code=corp_code,
            bgn_de=begin.strftime("%Y%m%d"),
            end_de=end.strftime("%Y%m%d")
        )
        out[k] = len(data.get("list", []) or [])
        time.sleep(0.03)
    return out

def apply_financing(gate, reasons, counts):
    total = sum(counts.values())
    if total >= 2:
        return "FAIL", reasons + ["최근 1년 유증/CB/BW/EB 2건 이상"]
    if total == 1:
        return "WATCH", reasons + ["최근 1년 유증/CB/BW/EB 1건"]
    return gate, reasons

def main():
    api_key = os.environ.get("OPENDART_API_KEY")
    if not api_key:
        raise SystemExit("OPENDART_API_KEY 환경변수가 필요합니다.")

    companies = corp_codes(api_key)
    stocks = {}

    for i, c in enumerate(companies, start=1):
        stock = c["stock_code"]
        try:
            qs = fetch_company_quarters(api_key, c["corp_code"])
            gate, reasons = financial_gate(qs)
            counts = financing_counts(api_key, c["corp_code"])
            gate, reasons = apply_financing(gate, reasons, counts)
            stocks[stock] = {
                "name": c["corp_name"],
                "corp_code": c["corp_code"],
                "gate": gate,
                "reasons": reasons,
                "financing": counts,
                "quarters": qs[-4:],
            }
        except Exception as e:
            stocks[stock] = {
                "name": c["corp_name"],
                "corp_code": c["corp_code"],
                "gate": "WATCH",
                "reasons": [f"DB 갱신 실패: {e}"],
                "financing": {},
                "quarters": [],
            }

        if i % 50 == 0:
            print(f"{i}/{len(companies)}")
        time.sleep(0.02)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "OpenDART daily finance DB",
        "stocks": stocks,
    }

    out = Path("data/financial_db.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print("saved:", out, "stocks:", len(stocks))

if __name__ == "__main__":
    main()

from __future__ import annotations
import io, re, zipfile
import xml.etree.ElementTree as ET
from datetime import date

NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
REV_TAGS = {"revenue", "sales", "operatingrevenue"}
OP_TAGS = {"operatingincomeloss", "profitlossfromoperatingactivities"}


def _local(tag):
    return tag.split("}")[-1].split(":")[-1]

def _norm_tag(tag):
    return re.sub(r"[^a-z0-9]", "", _local(tag).lower())

def _num(v):
    s = str(v or "").replace(",", "").strip()
    if not s or s in {"-", "—"}: return None
    try: return float(s)
    except Exception:
        m = NUM_RE.search(s)
        return float(m.group(0).replace(",", "")) if m else None

def _date(v):
    try: return date.fromisoformat((v or "")[:10])
    except Exception: return None

def _read_instance_files(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if name.lower().endswith((".xbrl", ".xml")):
                try:
                    blob = z.read(name)
                    root = ET.fromstring(blob)
                except Exception:
                    continue
                # taxonomy/linkbase XMLs have no xbrli:context; ignore them
                if not any(_local(e.tag).lower() == "context" for e in root.iter()):
                    continue
                yield root

def _contexts(root):
    out = {}
    for e in root.iter():
        if _local(e.tag).lower() != "context": continue
        cid = e.attrib.get("id")
        start = end = instant = None
        dimensional = False
        for c in e.iter():
            t = _local(c.tag).lower()
            if t == "startdate": start = _date(c.text)
            elif t == "enddate": end = _date(c.text)
            elif t == "instant": instant = _date(c.text)
            elif t in {"segment", "scenario", "explicitmember", "typedmember"}:
                dimensional = True
        if cid:
            out[cid] = {"start": start, "end": end or instant, "dimensional": dimensional}
    return out

def _facts(root):
    ctxs = _contexts(root)
    out = []
    for e in root.iter():
        if len(e): continue
        ctxid = e.attrib.get("contextRef") or e.attrib.get("contextref")
        if not ctxid or ctxid not in ctxs: continue
        val = _num(e.text)
        if val is None: continue
        c = ctxs[ctxid]
        if not c.get("start") or not c.get("end"): continue
        out.append({"tag": _norm_tag(e.tag), "value": val, **c})
    return out

def _concept_ok(tag, kind):
    tags = REV_TAGS if kind == "revenue" else OP_TAGS
    # exact IFRS/DART local concept names first; company extension only if it ends in a canonical concept
    return tag in tags or any(tag.endswith(x) for x in tags)

def _period_days(f):
    return (f["end"] - f["start"]).days + 1

def _pick_quarter_fact(facts, kind, end_date):
    c = [f for f in facts if _concept_ok(f["tag"], kind) and f["end"] == end_date]
    if not c: return None
    # Prefer non-dimensional consolidated/total facts to segment facts.
    nd = [f for f in c if not f.get("dimensional")]
    if nd: c = nd
    month = end_date.month
    # Q1/Q2/Q3: use standalone 3-month IS/CIS amount, not cumulative YTD.
    if month in (3, 6, 9):
        q = [f for f in c if 70 <= _period_days(f) <= 110]
        if q: c = q
    elif month == 12:
        # Annual report has annual amount; Q4 cannot be derived safely without Q1-Q3 files.
        a = [f for f in c if 330 <= _period_days(f) <= 380]
        if a: c = a
    # duplicates can remain across IS/CIS; same concept/value is fine. Prefer canonical and largest abs nonzero.
    c.sort(key=lambda f: (0 if f["tag"] in (REV_TAGS if kind=="revenue" else OP_TAGS) else 1,
                          abs(_period_days(f) - (90 if month in (3,6,9) else 365)),
                          -abs(f["value"])))
    return c[0]

def parse_manual_report(uploaded_file):
    name = uploaded_file.name
    result = {"file": name, "source_type": None, "status": "WATCH", "reasons": [], "quarter": None,
              "revenue": None, "operating_profit": None, "operating_margin_pct": None}
    if not name.lower().endswith(".zip"):
        result["source_type"] = "PDF/OTHER"
        result["reasons"] = ["자동 판정은 DART IFRS 원문 XBRL ZIP만 사용합니다."]
        return result
    result["source_type"] = "XBRL ZIP"
    facts = []
    try:
        for root in _read_instance_files(uploaded_file.getvalue()):
            facts.extend(_facts(root))
    except Exception as e:
        result["reasons"] = [f"XBRL ZIP 읽기 실패: {e}"]
        return result
    if not facts:
        result["reasons"] = ["XBRL 재무 인스턴스를 찾지 못했습니다."]
        return result
    ends = [f["end"] for f in facts if f.get("end")]
    end = max(ends) if ends else None
    if not end:
        result["reasons"] = ["보고기간을 확인하지 못했습니다."]
        return result
    rev = _pick_quarter_fact(facts, "revenue", end)
    op = _pick_quarter_fact(facts, "op", end)
    if not rev or not op:
        result["reasons"] = ["매출 또는 영업이익 계정을 확정하지 못했습니다."]
        return result
    revenue, operating_profit = rev["value"], op["value"]
    if revenue == 0:
        result["reasons"] = ["매출이 0으로 추출되어 자동 판정을 중단했습니다."]
        return result
    result.update({
        "quarter": end.isoformat(),
        "revenue": revenue,
        "operating_profit": operating_profit,
        "operating_margin_pct": operating_profit / revenue * 100,
        "status": "PARSED",
        "reasons": []
    })
    return result

def manual_gate_from_reports(parsed_reports):
    valid = [r for r in parsed_reports if r.get("status") == "PARSED"]
    # dedupe by report end date
    by_q = {}
    for r in valid:
        by_q[r["quarter"]] = r
    rows = [by_q[k] for k in sorted(by_q)]
    if len(rows) < 4:
        bad = [r for r in parsed_reports if r.get("status") != "PARSED"]
        reasons = [f"최근 4개 분기 XBRL이 필요합니다. 현재 {len(rows)}개 분기 확인."]
        if bad:
            reasons.append("읽지 못한 파일: " + ", ".join(r.get("file", "") for r in bad[:2]))
        return "WATCH", reasons, rows[-1] if rows else None
    h = rows[-4:]
    profits = [r["operating_profit"] for r in h]
    margins = [r["operating_margin_pct"] for r in h]
    profitable = sum(v > 0 for v in profits)
    last2 = sum(margins[-2:]) / 2
    prev2 = sum(margins[:2]) / 2
    margin_up_steps = sum(1 for i in range(1,4) if margins[i] > margins[i-1])
    op_up_steps = sum(1 for i in range(1,4) if profits[i] > profits[i-1])
    normal = profitable >= 3 and profits[-1] > 0 and last2 > prev2 and margin_up_steps >= 2
    turnaround = profits[-1] > 0 and any(v <= 0 for v in profits[:-1]) and op_up_steps >= 2
    latest = h[-1]
    if normal:
        return "PASS", ["최근 4분기 정상 성장형 재무 Gate 통과"], latest
    if turnaround:
        return "PASS", ["최근 4분기 턴어라운드형 재무 Gate 통과"], latest
    return "FAIL", ["최근 4분기 영업이익/영업이익률 개선 조건 미충족"], latest

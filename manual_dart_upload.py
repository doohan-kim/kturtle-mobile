from __future__ import annotations
import io, re, zipfile
import xml.etree.ElementTree as ET
from datetime import date

NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
REV_KEYS = ("Revenue", "Sales", "OperatingRevenue", "영업수익", "매출액", "수익매출액")
OP_KEYS = ("OperatingIncomeLoss", "ProfitLossFromOperatingActivities", "영업이익", "영업손익")
FINANCE_TERMS = ("유상증자", "전환사채", "신주인수권부사채", "교환사채", "CB", "BW", "EB")


def _num(v):
    if v is None: return None
    s = str(v).replace(",", "").replace(" ", "").strip()
    if not s or s in {"-", "—"}: return None
    m = NUM_RE.search(s)
    if not m: return None
    try: return float(m.group(0).replace(",", ""))
    except Exception: return None


def _local(tag): return tag.split("}")[-1]


def _parse_date(s):
    try: return date.fromisoformat((s or "")[:10])
    except Exception: return None


def _xml_docs(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            if n.lower().endswith((".xml", ".xbrl")):
                try: yield n, z.read(n)
                except Exception: pass


def _contexts(root):
    out = {}
    for e in root.iter():
        if _local(e.tag).lower() != "context": continue
        cid = e.attrib.get("id")
        start = end = instant = None
        for c in e.iter():
            t = _local(c.tag).lower()
            if t == "startdate": start = _parse_date(c.text)
            elif t == "enddate": end = _parse_date(c.text)
            elif t == "instant": instant = _parse_date(c.text)
        if cid: out[cid] = {"start": start, "end": end or instant}
    return out


def _facts_from_xml(blob: bytes):
    try: root = ET.fromstring(blob)
    except Exception: return []
    ctxs = _contexts(root); facts=[]
    for e in root.iter():
        if len(e): continue
        txt=(e.text or "").strip()
        if not txt or not NUM_RE.search(txt): continue
        ctx=e.attrib.get("contextRef") or e.attrib.get("contextref")
        info=ctxs.get(ctx, {})
        facts.append({"tag":_local(e.tag), "ctx":ctx, "value":_num(txt), "start":info.get("start"), "end":info.get("end")})
    return [x for x in facts if x["value"] is not None]


def _matches(tag, keys):
    nt=re.sub(r"[^A-Za-z0-9가-힣]", "", tag).lower()
    return any(re.sub(r"[^A-Za-z0-9가-힣]", "", k).lower() in nt for k in keys)


def _pick_period_pair(facts, keys):
    c=[x for x in facts if _matches(x["tag"], keys) and x.get("start") and x.get("end")]
    if not c: return None, None
    # duration facts only; current = latest end date, prefer YTD/longer duration and plausible magnitude
    latest=max(x["end"] for x in c)
    cur=[x for x in c if x["end"]==latest]
    cur.sort(key=lambda x: (-(x["end"]-x["start"]).days, -abs(x["value"])))
    current=cur[0]
    dur=(current["end"]-current["start"]).days
    # prior same-period: about one year earlier, similar duration
    prior=[x for x in c if 330 <= (current["end"]-x["end"]).days <= 400 and abs((x["end"]-x["start"]).days-dur) <= 10]
    prior.sort(key=lambda x: abs((current["end"]-x["end"]).days-365))
    return current, (prior[0] if prior else None)


def _text_from_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    r=PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in r.pages)


def parse_manual_report(uploaded_file):
    name=uploaded_file.name; data=uploaded_file.getvalue(); low=name.lower()
    result={"file":name,"source_type":None,"status":"WATCH","reasons":[],"metrics":{},"financing_terms":[]}
    if low.endswith(".zip"):
        result["source_type"]="XBRL ZIP"; facts=[]; all_text=[]
        try:
            for _, blob in _xml_docs(data):
                facts.extend(_facts_from_xml(blob))
                try: all_text.append(blob.decode("utf-8", errors="ignore"))
                except Exception: pass
        except Exception as e:
            result["reasons"].append(f"XBRL 읽기 실패: {e}"); return result
        cr, pr=_pick_period_pair(facts, REV_KEYS); co, po=_pick_period_pair(facts, OP_KEYS)
        if cr: result["metrics"]["revenue_current"]=cr["value"]
        if pr: result["metrics"]["revenue_prior"]=pr["value"]
        if co: result["metrics"]["op_current"]=co["value"]
        if po: result["metrics"]["op_prior"]=po["value"]
        text=" ".join(all_text)
        result["financing_terms"]=[t for t in FINANCE_TERMS if t in text]
    elif low.endswith(".pdf"):
        result["source_type"]="PDF"
        try: text=_text_from_pdf(data)
        except Exception as e:
            result["reasons"].append(f"PDF 읽기 실패: {e}"); return result
        result["financing_terms"]=[t for t in FINANCE_TERMS if t in text]
        # PDF tables are not trusted for automatic numeric PASS/FAIL.
        result["reasons"].append("PDF는 표 숫자를 안전하게 구조화하기 어려워 자동 PASS 판정에는 사용하지 않습니다. XBRL ZIP을 올려주세요.")
        return result
    else:
        result["reasons"].append("지원 형식은 DART XBRL ZIP 또는 PDF입니다."); return result

    m=result["metrics"]
    required=("revenue_current","revenue_prior","op_current","op_prior")
    if not all(k in m for k in required):
        result["reasons"].append("현재기간과 전년동기 매출·영업이익을 모두 확정하지 못했습니다.")
        result["reasons"].append("같은 DART 보고서의 XBRL 원문 ZIP 또는 비교기간이 포함된 보고서를 추가해주세요.")
        return result

    rc,rp,oc,op=(m[k] for k in required)
    if rc <= 0 or rp <= 0:
        result["status"]="FAIL"; result["reasons"].append("매출 데이터가 0 이하로 확인됩니다."); return result
    mc=oc/rc*100; mp=op/rp*100
    m["margin_current"]=mc; m["margin_prior"]=mp
    m["revenue_growth_pct"]=(rc/rp-1)*100

    # v2.7.1 financial gate spirit: positive operating profit + non-declining revenue + improving margin.
    if oc <= 0:
        result["status"]="FAIL"; result["reasons"].append("최근 보고기간 영업이익이 적자입니다.")
    elif rc < rp:
        result["status"]="FAIL"; result["reasons"].append("매출이 전년동기보다 감소했습니다.")
    elif mc <= mp:
        result["status"]="FAIL"; result["reasons"].append("영업이익률이 전년동기보다 개선되지 않았습니다.")
    else:
        result["status"]="PASS"
        result["reasons"].append("매출 비감소 · 영업이익 흑자 · 영업이익률 개선 확인")
    if result["financing_terms"]:
        result["reasons"].append("보고서에 자금조달 관련 용어가 있어 원문 공시 추가 확인 필요")
        if result["status"]=="PASS": result["status"]="WATCH"
    return result


def manual_gate_from_reports(parsed_reports):
    if not parsed_reports:
        return "WATCH", ["수동 DART 보고서가 없습니다."], None
    # Prefer XBRL. PASS only when at least one XBRL report itself contains a reliable comparable period.
    ranked=sorted(parsed_reports, key=lambda x: (x.get("source_type")!="XBRL ZIP", {"PASS":0,"FAIL":1,"WATCH":2}.get(x.get("status"),3)))
    best=ranked[0]
    return best.get("status","WATCH"), best.get("reasons",[]), best

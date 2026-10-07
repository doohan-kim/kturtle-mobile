from __future__ import annotations
import io, re, zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")

REV_KEYS = ("Revenue", "Sales", "OperatingRevenue", "영업수익", "매출액", "수익매출액")
OP_KEYS = ("OperatingIncomeLoss", "ProfitLossFromOperatingActivities", "영업이익", "영업손익")


def _num(v):
    if v is None: return None
    s = str(v).replace(",", "").replace(" ", "").strip()
    if not s or s in {"-", "—"}: return None
    m = NUM_RE.search(s)
    if not m: return None
    try: return float(m.group(0).replace(",", ""))
    except Exception: return None


def _text_from_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in r.pages)


def _xml_files_from_zip(data: bytes):
    z = zipfile.ZipFile(io.BytesIO(data))
    for n in z.namelist():
        if n.lower().endswith((".xml", ".xbrl")):
            try:
                yield n, z.read(n)
            except Exception:
                pass


def _facts_from_xml(blob: bytes):
    try: root = ET.fromstring(blob)
    except Exception: return []
    facts=[]
    for e in root.iter():
        if len(e):
            continue
        txt=(e.text or "").strip()
        if not txt or not NUM_RE.search(txt):
            continue
        tag=e.tag.split("}")[-1]
        ctx=e.attrib.get("contextRef") or e.attrib.get("contextref")
        facts.append((tag,ctx,txt))
    return facts


def _best_fact(facts, keys):
    c=[]
    for tag,ctx,txt in facts:
        nt=re.sub(r"[^A-Za-z0-9가-힣]", "", tag).lower()
        if any(re.sub(r"[^A-Za-z0-9가-힣]", "", k).lower() in nt for k in keys):
            v=_num(txt)
            if v is not None:
                c.append((tag,ctx,v))
    # prefer common current-quarter contexts, then non-zero magnitude
    c.sort(key=lambda x:(0 if x[1] and any(k in x[1].lower() for k in ("current", "instant", "d3", "q")) else 1, -abs(x[2])))
    return c[0] if c else None


def _extract_pdf_metric(text, labels):
    flat=re.sub(r"[ \t]+", " ", text)
    for lab in labels:
        # first plausible amount on same/next short span
        m=re.search(re.escape(lab)+r"[^\n]{0,100}\n?[^\n]{0,100}", flat, re.I)
        if m:
            nums=NUM_RE.findall(m.group(0))
            vals=[_num(x) for x in nums]
            vals=[v for v in vals if v is not None]
            if vals: return vals[0]
    return None


def parse_manual_report(uploaded_file):
    name=uploaded_file.name
    data=uploaded_file.getvalue()
    result={"file":name,"revenue":None,"operating_profit":None,"source_type":None,"warnings":[]}
    low=name.lower()
    if low.endswith(".zip"):
        result["source_type"]="XBRL ZIP"
        facts=[]
        try:
            for _,blob in _xml_files_from_zip(data): facts.extend(_facts_from_xml(blob))
        except Exception as e:
            result["warnings"].append(f"ZIP/XBRL 읽기 실패: {e}")
        r=_best_fact(facts, REV_KEYS); o=_best_fact(facts, OP_KEYS)
        result["revenue"]=r[2] if r else None
        result["operating_profit"]=o[2] if o else None
    elif low.endswith(".pdf"):
        result["source_type"]="PDF"
        try:
            text=_text_from_pdf(data)
            result["revenue"]=_extract_pdf_metric(text,["매출액","수익(매출액)","영업수익"])
            result["operating_profit"]=_extract_pdf_metric(text,["영업이익(손실)","영업이익","영업손익"])
            result["warnings"].append("PDF는 표 구조에 따라 자동 추출 오차가 있을 수 있어 원문 숫자 확인이 필요합니다.")
        except Exception as e:
            result["warnings"].append(f"PDF 읽기 실패: {e}")
    else:
        result["warnings"].append("지원 형식은 DART XBRL ZIP 또는 PDF입니다.")
    if result["revenue"] not in (None,0) and result["operating_profit"] is not None:
        result["operating_margin_pct"]=result["operating_profit"]/result["revenue"]*100
    else:
        result["operating_margin_pct"]=None
    return result


def manual_gate_from_reports(parsed_reports, financing_confirmed: bool):
    good=[x for x in parsed_reports if x.get("revenue") not in (None,0) and x.get("operating_profit") is not None]
    if len(good) < 4:
        return "WATCH", [f"자동 확인 가능한 분기 재무가 {len(good)}개입니다. 최근 4개 분기 보고서가 필요합니다."], good
    h=good[-4:]
    profits=[x["operating_profit"] for x in h]
    margins=[x["operating_margin_pct"] for x in h]
    profitable=sum(v>0 for v in profits)
    last2=sum(margins[-2:])/2; prev2=sum(margins[:2])/2
    margin_up=sum(1 for i in range(1,4) if margins[i]>margins[i-1])
    op_up=sum(1 for i in range(1,4) if profits[i]>profits[i-1])
    normal=profitable>=3 and profits[-1]>0 and last2>prev2 and margin_up>=2
    turnaround=profits[-1]>0 and any(v<=0 for v in profits[:-1]) and op_up>=2
    if not (normal or turnaround):
        return "FAIL", ["영업이익/영업이익률 개선 조건 미충족"], h
    if not financing_confirmed:
        return "WATCH", ["영업 추세 조건 통과", "최근 1년 유증/CB/BW/EB 여부가 확인되지 않아 WATCH"], h
    return "PASS", ["정상 성장형 조건 통과" if normal else "턴어라운드형 조건 통과", "사용자가 DART 원문에서 최근 1년 유증/CB/BW/EB 없음 확인"], h

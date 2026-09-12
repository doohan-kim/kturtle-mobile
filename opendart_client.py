from __future__ import annotations
import os, io, zipfile, requests, xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

BASE = "https://opendart.fss.or.kr/api"

class DartError(RuntimeError):
    pass

@dataclass
class Corp:
    corp_code: str
    corp_name: str
    stock_code: str
    modify_date: str

class OpenDartClient:
    def __init__(self, api_key: str | None = None, session=None):
        self.api_key = api_key or os.getenv("OPENDART_API_KEY")
        if not self.api_key:
            raise DartError("OPENDART_API_KEY 환경변수가 없습니다.")
        self.s = session or requests.Session()

    def _json(self, endpoint: str, **params) -> dict[str, Any]:
        params["crtfc_key"] = self.api_key
        r = self.s.get(f"{BASE}/{endpoint}", params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        status = data.get("status")
        if status not in (None, "000", "013"):
            raise DartError(f"DART 오류 {status}: {data.get('message')}")
        return data

    def corp_codes(self) -> list[Corp]:
        r = self.s.get(f"{BASE}/corpCode.xml", params={"crtfc_key": self.api_key}, timeout=30)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        root = ET.fromstring(z.read(z.namelist()[0]))
        out = []
        for item in root.findall("list"):
            out.append(Corp(
                corp_code=(item.findtext("corp_code") or "").strip(),
                corp_name=(item.findtext("corp_name") or "").strip(),
                stock_code=(item.findtext("stock_code") or "").strip(),
                modify_date=(item.findtext("modify_date") or "").strip(),
            ))
        return out

    def full_financials(self, corp_code: str, year: int, reprt_code: str, fs_div="CFS") -> list[dict]:
        data = self._json(
            "fnlttSinglAcntAll.json",
            corp_code=corp_code,
            bsns_year=str(year),
            reprt_code=reprt_code,
            fs_div=fs_div,
        )
        return data.get("list", []) or []

    def major_accounts(self, corp_code: str, year: int, reprt_code: str) -> list[dict]:
        data = self._json(
            "fnlttSinglAcnt.json",
            corp_code=corp_code,
            bsns_year=str(year),
            reprt_code=reprt_code,
        )
        return data.get("list", []) or []

    def financing_events(self, corp_code: str, bgn_de: str, end_de: str) -> dict[str, list[dict]]:
        endpoints = {
            "RIGHTS_ISSUE": "piicDecsn.json",
            "CB": "cvbdIsDecsn.json",
            "BW": "bdwtIsDecsn.json",
            "EB": "exbdIsDecsn.json",
        }
        out = {}
        for kind, ep in endpoints.items():
            data = self._json(ep, corp_code=corp_code, bgn_de=bgn_de, end_de=end_de)
            out[kind] = data.get("list", []) or []
        return out

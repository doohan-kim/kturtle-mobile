from __future__ import annotations
import os, io, zipfile, time, json, requests, xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

BASE = "https://opendart.fss.or.kr/api"
CACHE_PATH = Path("/tmp/kturtle_corp_codes_cache.json")

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
        self.s.headers.update({
            "User-Agent": "K-TURTLE-Mobile/0.9",
            "Accept": "*/*",
        })

    def _get_with_retry(self, url: str, params: dict, attempts: int = 3, timeout=(10, 60)):
        last = None
        for i in range(attempts):
            try:
                r = self.s.get(url, params=params, timeout=timeout)
                r.raise_for_status()
                return r
            except requests.RequestException as e:
                last = e
                if i < attempts - 1:
                    time.sleep(1.5 * (i + 1))
        raise DartError(f"OpenDART 연결 실패: {last}")

    def _json(self, endpoint: str, **params) -> dict[str, Any]:
        params["crtfc_key"] = self.api_key
        r = self._get_with_retry(f"{BASE}/{endpoint}", params, attempts=3, timeout=(10, 75))
        try:
            data = r.json()
        except Exception as e:
            raise DartError(f"OpenDART JSON 응답 해석 실패: {e}")
        status = data.get("status")
        if status not in (None, "000", "013"):
            raise DartError(f"DART 오류 {status}: {data.get('message')}")
        return data

    def _load_cached_corps(self) -> list[Corp]:
        if not CACHE_PATH.exists():
            return []
        try:
            raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            return [Corp(**x) for x in raw]
        except Exception:
            return []

    def _save_cached_corps(self, corps: list[Corp]) -> None:
        try:
            CACHE_PATH.write_text(
                json.dumps([asdict(c) for c in corps], ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception:
            pass

    def corp_codes(self, allow_stale_cache: bool = True) -> list[Corp]:
        cached = self._load_cached_corps()

        try:
            r = self._get_with_retry(
                f"{BASE}/corpCode.xml",
                {"crtfc_key": self.api_key},
                attempts=4,
                timeout=(10, 90),
            )
            try:
                z = zipfile.ZipFile(io.BytesIO(r.content))
                root = ET.fromstring(z.read(z.namelist()[0]))
            except Exception as e:
                raise DartError(f"기업코드 파일 해석 실패: {e}")

            out = []
            for item in root.findall("list"):
                out.append(Corp(
                    corp_code=(item.findtext("corp_code") or "").strip(),
                    corp_name=(item.findtext("corp_name") or "").strip(),
                    stock_code=(item.findtext("stock_code") or "").strip(),
                    modify_date=(item.findtext("modify_date") or "").strip(),
                ))
            if out:
                self._save_cached_corps(out)
                return out
            if cached and allow_stale_cache:
                return cached
            raise DartError("OpenDART 기업코드 목록이 비어 있습니다.")
        except Exception as e:
            if cached and allow_stale_cache:
                return cached
            if isinstance(e, DartError):
                raise
            raise DartError(f"기업코드 조회 실패: {e}")

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

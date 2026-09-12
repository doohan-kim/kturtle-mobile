from __future__ import annotations
from dataclasses import dataclass, asdict
import math
import pandas as pd


@dataclass
class FinancialGateResult:
    status: str                # PASS / WATCH / FAIL
    track: str                 # NORMAL / TURNAROUND / NONE
    reasons: list[str]
    metrics: dict

    @property
    def is_pass(self) -> bool:
        return self.status == "PASS"


def _safe_div(a: float, b: float) -> float | None:
    if b is None or b == 0 or pd.isna(b):
        return None
    return a / b


def _trend_improving(values: list[float], min_positive_steps: int = 2) -> bool:
    """연속 값에서 증가한 구간 수가 min_positive_steps 이상인지."""
    if len(values) < 2:
        return False
    steps = sum(1 for i in range(1, len(values)) if values[i] > values[i-1])
    return steps >= min_positive_steps


def evaluate_financial_gate(
    q: pd.DataFrame,
    events: pd.DataFrame | None = None,
    *,
    dilution_fail_pct: float = 0.10,
    dilution_watch_pct: float = 0.05,
    min_cash_to_short_debt: float = 1.0,
    min_interest_coverage: float = 1.5,
    max_debt_ratio_pct: float = 200.0,
    recent_event_months: int = 12,
) -> FinancialGateResult:
    """
    분기 재무자료 기반 K-TURTLE 재무/자금조달 게이트.

    q 필수 열:
      quarter, revenue, operating_profit, operating_margin_pct,
      operating_cash_flow, cash_and_equivalents,
      short_term_borrowings, total_borrowings, interest_expense,
      equity, total_liabilities

    events 선택 열:
      date, type, amount, dilution_pct, outstanding_balance
      type 예: RIGHTS_ISSUE, CB, BW, EB

    설계 철학:
    - 정상 성장형과 턴어라운드형 분리
    - 단 한 분기의 흔들림보다 최근 4개 분기 방향성 우선
    - '당장 유증/CB 필요성'을 현금/차입/영업현금흐름/이자상환능력으로 함께 판단
    - 최근 실제 자금조달 이력과 잠재희석률은 별도 강한 감점
    """
    required = [
        "quarter","revenue","operating_profit","operating_margin_pct",
        "operating_cash_flow","cash_and_equivalents",
        "short_term_borrowings","total_borrowings","interest_expense",
        "equity","total_liabilities"
    ]
    missing = [c for c in required if c not in q.columns]
    if missing:
        return FinancialGateResult("FAIL", "NONE", [f"재무 데이터 열 부족: {missing}"], {})

    if len(q) < 4:
        return FinancialGateResult("WATCH", "NONE", ["최근 4개 분기 데이터가 필요합니다."], {})

    x = q.copy().tail(8).reset_index(drop=True)
    last4 = x.tail(4).copy()
    last2 = x.tail(2).copy()
    prev2 = x.iloc[-4:-2].copy()

    # 핵심 계산
    profitable_quarters = int((last4["operating_profit"] > 0).sum())
    latest_op = float(last4.iloc[-1]["operating_profit"])
    latest_margin = float(last4.iloc[-1]["operating_margin_pct"])
    avg_last2_margin = float(last2["operating_margin_pct"].mean())
    avg_prev2_margin = float(prev2["operating_margin_pct"].mean())
    op_values = [float(v) for v in last4["operating_profit"].tolist()]
    margin_values = [float(v) for v in last4["operating_margin_pct"].tolist()]

    latest = last4.iloc[-1]
    cash = float(latest["cash_and_equivalents"])
    short_debt = float(latest["short_term_borrowings"])
    total_debt = float(latest["total_borrowings"])
    interest = float(latest["interest_expense"])
    equity = float(latest["equity"])
    liabilities = float(latest["total_liabilities"])
    ocf_ttm = float(last4["operating_cash_flow"].sum())
    op_ttm = float(last4["operating_profit"].sum())

    cash_to_short_debt = _safe_div(cash, short_debt) if short_debt > 0 else math.inf
    interest_coverage = _safe_div(op_ttm, abs(float(last4["interest_expense"].sum())))
    debt_ratio = _safe_div(liabilities, equity)
    debt_ratio_pct = debt_ratio * 100 if debt_ratio is not None else None

    reasons = []
    hard_fail = False
    watch = False

    # 1) 정상 성장형
    normal_track = (
        profitable_quarters >= 3
        and latest_op > 0
        and avg_last2_margin > avg_prev2_margin
        and _trend_improving(margin_values, min_positive_steps=2)
    )

    # 2) 턴어라운드형
    # 최근 분기 흑자 + 최근 4분기 영업이익 방향성이 뚜렷한 개선
    # 또는 최근 4분기 중 적자->흑자 전환
    turnaround_track = (
        latest_op > 0
        and _trend_improving(op_values, min_positive_steps=2)
        and any(v <= 0 for v in op_values[:-1])
    )

    if normal_track:
        track = "NORMAL"
        reasons.append("정상 성장형: 최근 4분기 중 3분기 이상 영업흑자 + 최근 2분기 평균 영업이익률 개선")
    elif turnaround_track:
        track = "TURNAROUND"
        reasons.append("턴어라운드형: 최근 분기 흑자전환 + 영업이익 개선 추세")
    else:
        track = "NONE"
        hard_fail = True
        reasons.append("영업이익/영업이익률 개선 조건 미충족")

    # 3) 영업현금흐름
    if ocf_ttm <= 0:
        if track == "TURNAROUND":
            watch = True
            reasons.append("최근 4분기 누적 영업현금흐름 음수 → 턴어라운드 WATCH")
        else:
            hard_fail = True
            reasons.append("최근 4분기 누적 영업현금흐름 음수")

    # 4) 단기차입금 대비 현금
    if short_debt > 0 and cash_to_short_debt is not None:
        if cash_to_short_debt < 0.5:
            hard_fail = True
            reasons.append("현금/단기차입금 < 0.5배")
        elif cash_to_short_debt < min_cash_to_short_debt:
            watch = True
            reasons.append("현금/단기차입금 < 1.0배")

    # 5) 이자보상능력
    if interest_coverage is not None:
        if interest_coverage < 1.0:
            hard_fail = True
            reasons.append("이자보상배율 < 1.0배")
        elif interest_coverage < min_interest_coverage:
            watch = True
            reasons.append("이자보상배율 < 1.5배")

    # 6) 부채비율
    if debt_ratio_pct is not None:
        if debt_ratio_pct > max_debt_ratio_pct:
            watch = True
            reasons.append("부채비율 > 200%")

    # 7) 최근 유증/CB/BW/EB 및 잠재희석률
    max_dilution = 0.0
    recent_financing_count = 0
    outstanding_balance = 0.0

    if events is not None and not events.empty:
        ev = events.copy()
        if "dilution_pct" in ev.columns:
            ev["dilution_pct"] = pd.to_numeric(ev["dilution_pct"], errors="coerce").fillna(0.0)
            max_dilution = float(ev["dilution_pct"].max())
        if "outstanding_balance" in ev.columns:
            outstanding_balance = float(pd.to_numeric(ev["outstanding_balance"], errors="coerce").fillna(0.0).sum())

        if "date" in ev.columns:
            ev["date"] = pd.to_datetime(ev["date"], errors="coerce")
            latest_q_date = pd.to_datetime(str(last4.iloc[-1]["quarter"]), errors="coerce")
            if pd.notna(latest_q_date):
                cutoff = latest_q_date - pd.DateOffset(months=recent_event_months)
                recent = ev[ev["date"] >= cutoff]
            else:
                recent = ev
        else:
            recent = ev

        if "type" in recent.columns:
            recent = recent[recent["type"].isin(["RIGHTS_ISSUE","CB","BW","EB"])]
        recent_financing_count = len(recent)

        if max_dilution >= dilution_fail_pct:
            hard_fail = True
            reasons.append(f"잠재희석률 {max_dilution*100:.1f}% ≥ 10%")
        elif max_dilution >= dilution_watch_pct:
            watch = True
            reasons.append(f"잠재희석률 {max_dilution*100:.1f}% ≥ 5%")

        if recent_financing_count >= 2:
            hard_fail = True
            reasons.append("최근 12개월 유증/CB/BW/EB 2회 이상")
        elif recent_financing_count == 1:
            watch = True
            reasons.append("최근 12개월 유증/CB/BW/EB 1회")

    # 8) Funding Risk 보조 판단:
    # 영업현금흐름 음수 + 현금 부족이 동시에 발생하면 강한 FAIL
    if ocf_ttm <= 0 and short_debt > 0 and cash_to_short_debt < 1.0:
        hard_fail = True
        reasons.append("Funding Risk: 영업현금흐름 음수 + 단기차입금 대비 현금 부족")

    status = "FAIL" if hard_fail else ("WATCH" if watch else "PASS")

    metrics = {
        "profitable_quarters_last4": profitable_quarters,
        "latest_operating_profit": latest_op,
        "latest_operating_margin_pct": latest_margin,
        "avg_last2_margin_pct": avg_last2_margin,
        "avg_prev2_margin_pct": avg_prev2_margin,
        "ocf_ttm": ocf_ttm,
        "cash_to_short_debt": None if cash_to_short_debt == math.inf else cash_to_short_debt,
        "interest_coverage": interest_coverage,
        "debt_ratio_pct": debt_ratio_pct,
        "max_dilution_pct": max_dilution * 100,
        "recent_financing_count_12m": recent_financing_count,
        "outstanding_cb_bw_eb_balance": outstanding_balance,
    }

    return FinancialGateResult(status, track, reasons, metrics)

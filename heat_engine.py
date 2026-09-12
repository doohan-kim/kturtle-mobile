from __future__ import annotations

def evaluate_heat(
    new_trade_risk_krw: float,
    account_equity: float,
    current_portfolio_heat_krw: float = 0.0,
    current_sector_heat_krw: float = 0.0,
    portfolio_limit_pct: float = 0.05,
    sector_limit_pct: float = 0.025,
):
    portfolio_limit = account_equity * portfolio_limit_pct
    sector_limit = account_equity * sector_limit_pct

    new_portfolio_heat = current_portfolio_heat_krw + new_trade_risk_krw
    new_sector_heat = current_sector_heat_krw + new_trade_risk_krw

    if new_portfolio_heat > portfolio_limit:
        return {
            "status":"FAIL",
            "reason":"Portfolio Heat 5% 상한 초과",
            "portfolio_heat_krw":new_portfolio_heat,
            "sector_heat_krw":new_sector_heat,
            "portfolio_limit_krw":portfolio_limit,
            "sector_limit_krw":sector_limit,
        }

    if new_sector_heat > sector_limit:
        return {
            "status":"FAIL",
            "reason":"Sector Heat 2.5% 상한 초과",
            "portfolio_heat_krw":new_portfolio_heat,
            "sector_heat_krw":new_sector_heat,
            "portfolio_limit_krw":portfolio_limit,
            "sector_limit_krw":sector_limit,
        }

    return {
        "status":"PASS",
        "reason":"Heat 통과",
        "portfolio_heat_krw":new_portfolio_heat,
        "sector_heat_krw":new_sector_heat,
        "portfolio_limit_krw":portfolio_limit,
        "sector_limit_krw":sector_limit,
    }

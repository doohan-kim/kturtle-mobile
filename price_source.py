from __future__ import annotations
import pandas as pd
import yfinance as yf

def fetch_kr_stock(stock_code: str, period="6mo") -> tuple[pd.DataFrame, str]:
    code = stock_code.zfill(6)
    last_err = None
    for suffix in [".KS", ".KQ"]:
        ticker = code + suffix
        try:
            df = yf.download(
                ticker,
                period=period,
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0] for c in df.columns]
                return df, ticker
        except Exception as e:
            last_err = e
    raise RuntimeError(f"주가 데이터를 가져오지 못했습니다: {last_err or '데이터 없음'}")

"""
Indicators and rule engine for TA signals
Compute from a DataFrame with columns Open, High, Low, Close, Volume (index: datetime)
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass

@dataclass
class IndicatorResult:
    rsi: float = None
    ema20: float = None
    ema50: float = None
    sma10: float = None
    macd: float = None
    macd_signal: float = None
    macd_hist: float = None
    vol_mean: float = None
    vol_last: float = None

@dataclass
class CandleInfo:
    is_bullish_engulfing: bool = False
    is_hammer: bool = False
    last_close: float = None
    last_open: float = None
    last_vol: float = None

    @staticmethod
    def from_df(df: pd.DataFrame):
        info = CandleInfo()
        if df.empty:
            return info
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        info.last_close = float(last["Close"])
        info.last_open = float(last["Open"])
        info.last_vol = float(last["Volume"]) if "Volume" in last else None
        prev_body = abs(prev["Close"] - prev["Open"])
        last_body = abs(last["Close"] - last["Open"])
        if prev["Close"] < prev["Open"] and last["Close"] > last["Open"] and last_body > prev_body:
            info.is_bullish_engulfing = True
        body = abs(last["Close"] - last["Open"])
        lower_wick = min(last["Open"], last["Close"]) - last["Low"]
        upper_wick = last["High"] - max(last["Open"], last["Close"])
        if lower_wick > 2 * body and upper_wick < body:
            info.is_hammer = True
        return info

def compute_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff().dropna()
    if delta.empty:
        return None
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ma_up = up.ewm(alpha=1/period, adjust=False).mean()
    ma_down = down.ewm(alpha=1/period, adjust=False).mean()
    rs = ma_up / ma_down
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not rsi.empty else None

def compute_macd(series: pd.Series):
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    if macd.empty:
        return None, None, None
    return float(macd.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1])

def compute_indicators(df: pd.DataFrame) -> IndicatorResult:
    res = IndicatorResult()
    if "Close" not in df.columns:
        if "close" in df.columns:
            df = df.rename(columns={"close":"Close"})
        else:
            return res
    close = df["Close"].astype(float)
    res.rsi = compute_rsi(close, period=14)
    res.ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1]) if len(close)>=20 else None
    res.ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if len(close)>=50 else None
    res.sma10 = float(close.rolling(window=10).mean().iloc[-1]) if len(close)>=10 else None
    macd, macd_sig, macd_hist = compute_macd(close)
    res.macd = macd
    res.macd_signal = macd_sig
    res.macd_hist = macd_hist
    if "Volume" in df.columns and not df["Volume"].empty:
        res.vol_mean = float(df["Volume"].tail(50).mean())
        res.vol_last = float(df["Volume"].iloc[-1])
    return res

def decide_signal(ind: IndicatorResult, candle: CandleInfo) -> str:
    score = 0.0
    if ind is None:
        return ""
    if ind.rsi is not None:
        if ind.rsi < 30:
            score += 2.0
        elif ind.rsi < 40:
            score += 0.5
        elif ind.rsi > 70:
            score -= 1.5
        elif ind.rsi > 80:
            score -= 2.5
    if ind.macd_hist is not None:
        if ind.macd_hist > 0:
            score += 1.0
        elif ind.macd_hist < 0:
            score -= 0.8
    if ind.ema20 and ind.ema50:
        if ind.ema20 > ind.ema50:
            score += 1.0
        else:
            score -= 0.8
    if ind.sma10 and ind.sma10 > 0 and ind.sma10 < (ind.ema20 or float("inf")):
        score += 0.2
    if ind.vol_mean and ind.vol_last is not None and ind.vol_mean > 0:
        if ind.vol_last > 2 * ind.vol_mean:
            score += 1.2
    if candle.is_bullish_engulfing:
        score += 1.5
    if candle.is_hammer:
        score += 1.0
    if score >= 4.0:
        return "STRONG BUY"
    if score >= 1.5:
        return "BUY"
    if score <= -3.0:
        return "STRONG SELL"
    if score <= -1.0:
        return "SELL"
    return ""
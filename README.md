# Crypto Price Alert Bot - CoinLore + Yahoo Finance (1m candles)

This repository upgrades the basic CoinLore Telegram alert bot to include technical analysis using Yahoo Finance candles (via yfinance).

## Features
- 1-minute candles (1 day) from Yahoo Finance for TA indicators
- Indicators: RSI(14), EMA(20/50), SMA(10), MACD(12,26,9)
- Candlestick detectors: bullish engulfing, hammer
- Volume spike detection (relative)
- Pump/Dump detection (CoinLore real-time)
- Weighted rule engine outputs: STRONG BUY / BUY / SELL / STRONG SELL
- Alerts are concise (no long explanation) to Telegram chats
- Price history and alerts log stored in `signals.db` (SQLite)

## Files
- `bot.py` - main worker
- `indicators.py` - TA computation and rule engine
- `coinlist.csv` - CoinLore IDs to monitor
- `yahoo_mapping.csv` - mapping coinlore_id -> Yahoo symbol (edit to add more)
- `requirements.txt`, `Procfile`, `runtime.txt` - Railway deployment files

## Deployment
1. Commit to GitHub (do NOT commit real .env)
2. On Railway set variables in project settings (Variables):
   - TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, SCAN_INTERVAL_SECONDS, COIN_LIST_PATH, DATABASE_PATH, THRESHOLD_PERCENT, COINLORE_BATCH_SIZE, YAHOO_MAP_PATH, ALERT_COOLDOWN_SECONDS, YF_INTERVAL, YF_PERIOD
3. Deploy - Railway will run `worker: python bot.py`

## Notes
- The TA is computed from Yahoo 1m candles sampled by `yfinance`. This is not exchange-native OHLC but it usually provides usable intraday candles for many coins.
- You can expand `yahoo_mapping.csv` with more CoinLore IDs and symbols. For coins without Yahoo mapping the bot will fallback to computing simple indicators from its stored price history (less accurate).
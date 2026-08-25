#!/bin/bash
# 給 macOS launchd 排程用的執行腳本。
# 會自動切到腳本所在資料夾、確保套件已安裝、再執行增量更新。
cd "$(dirname "$0")"
mkdir -p logs

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"

python3 -m pip install -r requirements.txt -q 2>/dev/null \
  || python3 -m pip install -r requirements.txt -q --break-system-packages 2>/dev/null \
  || true

python3 update_finmind.py
python3 update_news.py
python3 update_us_stock.py
python3 update_us_news.py
python3 update_market_news.py
python3 update_pocket_news.py
python3 validate_prices.py
python3 send_news_email.py

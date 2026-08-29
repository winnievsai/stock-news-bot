#!/usr/bin/env python3
"""
股價交叉比對工具
================================
用途：把 data/（台股）、us_data/（美股）裡每支股票「最新一筆收盤價」拿去跟
     Yahoo Finance 的公開報價 API（不需要API key）核對，抓出兩邊價格差異過大
     的股票，寫進 price_validation_report.txt，讓 send_news_email.py 可以在
     每日信件裡提醒異常。

     這是抓資料錯誤/異常用的健檢工具，不是用來判斷「哪個數字才正確」——兩邊有
     落差時，可能是 FinMind 資料有誤，也可能是 Yahoo 那邊還沒更新、或除權息造成
     短暫價格落差，需要人工判斷，程式只負責標示「這裡有落差，建議看一下」。

使用方式：
    python3 validate_prices.py

設定（.env.local，跟其他腳本共用同一份）：
    OUTPUT_DIR=data                         (選填，跟 update_finmind.py 一致)
    US_OUTPUT_DIR=us_data                   (選填，跟 update_us_stock.py 一致)
    STOCK_LIST_FILE=stock_list.txt          (選填)
    US_STOCK_LIST_FILE=us_stock_list.txt    (選填)
    PRICE_DIFF_THRESHOLD_PCT=1.5            (選填，預設 1.5，超過這個百分比落差才算異常)
"""

import csv
import os
import sys
import time
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
DEFAULT_THRESHOLD_PCT = 1.5
REPORT_PATH = SCRIPT_DIR / "price_validation_report.txt"

# FinMind 代碼 -> Yahoo Finance 代碼的已知例外對應（少數美股在兩邊代碼不同）
US_SYMBOL_OVERRIDES = {
    "BRK.B": "BRK-B",
    "GOOG": "GOOG",
}


def load_env_file(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_config():
    env = {**load_env_file(SCRIPT_DIR / ".env.local"), **os.environ}
    output_dir = SCRIPT_DIR / env.get("OUTPUT_DIR", "data").strip()
    us_output_dir = SCRIPT_DIR / env.get("US_OUTPUT_DIR", "us_data").strip()
    stock_list_file = SCRIPT_DIR / env.get("STOCK_LIST_FILE", "stock_list.txt").strip()
    us_stock_list_file = SCRIPT_DIR / env.get("US_STOCK_LIST_FILE", "us_stock_list.txt").strip()
    try:
        threshold = float(env.get("PRICE_DIFF_THRESHOLD_PCT", DEFAULT_THRESHOLD_PCT))
    except ValueError:
        threshold = DEFAULT_THRESHOLD_PCT
    return output_dir, us_output_dir, stock_list_file, us_stock_list_file, threshold


def load_stock_list(path: Path):
    if not path.exists():
        return []
    stocks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        stocks.append(line.split()[0])
    return stocks


def read_latest_close(csv_path: Path):
    """讀 CSV 第一列資料（新到舊排序，第一列就是最新）；回傳 (date, close) 或 None"""
    if not csv_path.exists():
        return None
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date = row.get("日期")
            close = row.get("收盤價")
            if date and close:
                try:
                    return date, float(close)
                except ValueError:
                    return None
            return None
    return None


def fetch_yahoo_close(symbol: str):
    """回傳 (date, close) 或 None；查不到/發生錯誤都回傳 None，不中斷整個流程"""
    try:
        resp = requests.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params={"range": "5d", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
        result = payload.get("chart", {}).get("result")
        if not result:
            return None
        r = result[0]
        timestamps = r.get("timestamp") or []
        closes = r.get("indicators", {}).get("quote", [{}])[0].get("close") or []
        for ts, close in zip(reversed(timestamps), reversed(closes)):
            if close is not None:
                from datetime import datetime, timezone
                date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                return date, float(close)
        return None
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None


def check_stock(stock_id: str, csv_path: Path, yahoo_symbol: str, threshold: float):
    """回傳 None（沒問題）、或一筆 dict，type 是 'stale'（兩邊資料日期對不上，
    無法比較，可能是本地資料落後）或 'mismatch'（同一天但價格落差超過門檻）"""
    local = read_latest_close(csv_path)
    if not local:
        return None
    local_date, local_close = local

    yahoo = fetch_yahoo_close(yahoo_symbol)
    if not yahoo:
        return None
    yahoo_date, yahoo_close = yahoo

    if local_date != yahoo_date:
        # 日期對不上，不是同一天的價格，不能直接拿來比較誰對誰錯
        return {
            "type": "stale",
            "stock_id": stock_id,
            "local_date": local_date,
            "yahoo_date": yahoo_date,
        }

    if local_close == 0:
        return None
    diff_pct = abs(local_close - yahoo_close) / local_close * 100
    if diff_pct < threshold:
        return None

    return {
        "type": "mismatch",
        "stock_id": stock_id,
        "local_date": local_date,
        "local_close": local_close,
        "yahoo_date": yahoo_date,
        "yahoo_close": yahoo_close,
        "diff_pct": diff_pct,
    }


def main():
    output_dir, us_output_dir, stock_list_file, us_stock_list_file, threshold = load_config()
    tw_stocks = load_stock_list(stock_list_file)
    us_stocks = load_stock_list(us_stock_list_file)

    findings = []

    print(f"核對 {len(tw_stocks)} 檔台股價格（門檻 {threshold}%）...")
    for stock_id in tw_stocks:
        result = check_stock(stock_id, output_dir / f"{stock_id}.csv", f"{stock_id}.TW", threshold)
        if result:
            findings.append(result)
        time.sleep(0.5)

    print(f"核對 {len(us_stocks)} 檔美股價格（門檻 {threshold}%）...")
    for stock_id in us_stocks:
        yahoo_symbol = US_SYMBOL_OVERRIDES.get(stock_id, stock_id)
        result = check_stock(stock_id, us_output_dir / f"{stock_id}.csv", yahoo_symbol, threshold)
        if result:
            findings.append(result)
        time.sleep(0.5)

    mismatches = [f for f in findings if f["type"] == "mismatch"]
    stale = [f for f in findings if f["type"] == "stale"]

    lines = []
    if mismatches:
        lines.append("以下股票「同一天」的收盤價跟 Yahoo Finance 核對後落差較大，建議人工確認：")
        lines.append("")
        for f in mismatches:
            lines.append(
                f"【{f['stock_id']}】{f['local_date']} 本地收盤 {f['local_close']}，"
                f"Yahoo 收盤 {f['yahoo_close']}，差異 {f['diff_pct']:.1f}%"
            )
        lines.append("")
    if stale:
        lines.append("以下股票本地資料日期跟 Yahoo Finance 對不上（可能是本地資料還沒更新到最新）：")
        lines.append("")
        for f in stale:
            lines.append(f"【{f['stock_id']}】本地最新 {f['local_date']}，Yahoo 最新 {f['yahoo_date']}")

    if lines:
        REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n發現 {len(mismatches)} 檔價格異常、{len(stale)} 檔資料不同步，已寫入 {REPORT_PATH.name}")
    else:
        if REPORT_PATH.exists():
            REPORT_PATH.unlink()
        print("\n核對完成，沒有發現異常。")


if __name__ == "__main__":
    main()

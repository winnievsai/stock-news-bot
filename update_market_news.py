#!/usr/bin/env python3
"""
大盤／市場整體新聞 抓取工具（Google News RSS）
================================
用途：跟 update_us_news.py 架構相同，差別是這支不綁定特定股票代碼，抓的是
     「台股大盤」「美股大盤」整體市場新聞（不限於 stock_list.txt / us_stock_list.txt
     裡的股票），存到 market_news/tw.csv、market_news/us.csv。

使用方式：
    python3 update_market_news.py

設定（.env.local，跟其他腳本共用同一份）：
    MARKET_NEWS_OUTPUT_DIR=market_news          (選填，預設 ./market_news)
    MARKET_NEWS_REQUEST_INTERVAL_SEC=2          (選填，預設 2 秒)

已知限制：
    - 跟 update_us_news.py 一樣，Google News RSS 沒有真正的歷史區間查詢，
      每次只抓「最近1天」，靠每天執行、用連結去重，慢慢累積歷史記錄
    - `link` 是 Google News 轉址連結，不是新聞原始網址
"""

import csv
import os
import sys
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
RSS_URL = "https://news.google.com/rss/search"
DEFAULT_REQUEST_INTERVAL_SEC = 2
CSV_FIELDS = ["date", "market", "title", "source", "link"]

# market 代號 -> (Google News 查詢字串, hl, gl, ceid)
MARKETS = {
    "TW": ("台股 大盤 when:1d", "zh-TW", "TW", "TW:zh-Hant"),
    "US": ("US stock market when:1d", "en-US", "US", "US:en"),
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
    output_dir = SCRIPT_DIR / env.get("MARKET_NEWS_OUTPUT_DIR", "market_news").strip()
    try:
        request_interval = float(env.get("MARKET_NEWS_REQUEST_INTERVAL_SEC", DEFAULT_REQUEST_INTERVAL_SEC))
    except ValueError:
        request_interval = DEFAULT_REQUEST_INTERVAL_SEC
    return output_dir, request_interval


def fetch_market_news(market: str, query: str, hl: str, gl: str, ceid: str):
    """回傳某個市場最近的新聞 list；發生錯誤時回傳空 list（不中斷整個流程）"""
    url = f"{RSS_URL}?q={quote(query)}&hl={hl}&gl={gl}&ceid={ceid}"
    try:
        resp = requests.get(url, timeout=30)
    except requests.RequestException as e:
        print(f"    [錯誤] {market} 網路錯誤：{e}")
        return []

    if resp.status_code != 200:
        print(f"    [錯誤] {market} 回應 {resp.status_code}")
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        print(f"    [錯誤] {market} RSS 解析失敗：{e}")
        return []

    rows = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date_raw = (item.findtext("pubDate") or "").strip()
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""

        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()

        if not title or not link:
            continue

        try:
            dt = parsedate_to_datetime(pub_date_raw)
            date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            date_str = pub_date_raw

        rows.append({"date": date_str, "market": market, "title": title, "source": source, "link": link})
    return rows


def upsert_csv(csv_path: Path, new_rows: list):
    """合併新資料到CSV，依新聞連結(link)去重、依日期新到舊排序後整份改寫"""
    existing = {}
    if csv_path.exists():
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = row.get("link") or row.get("date")
                existing[key] = row

    for row in new_rows:
        key = row.get("link") or row.get("date")
        existing[key] = {k: row.get(k, "") for k in CSV_FIELDS}

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for key in sorted(existing.keys(), key=lambda k: existing[k].get("date", ""), reverse=True):
            writer.writerow(existing[key])


def main():
    output_dir, request_interval = load_config()
    print(f"輸出資料夾：{output_dir}（資料來源：Google News RSS）\n")

    markets = list(MARKETS.items())
    for i, (market, (query, hl, gl, ceid)) in enumerate(markets, 1):
        print(f"[{i}/{len(markets)}] {market}：抓取大盤新聞")
        rows = fetch_market_news(market, query, hl, gl, ceid)
        if rows:
            csv_path = output_dir / f"{market.lower()}.csv"
            upsert_csv(csv_path, rows)
            print(f"    抓到 {len(rows)} 筆（含新舊，已用連結去重合併）")
        else:
            print("    無資料")

        if i < len(markets):
            time.sleep(request_interval)

    print("\n全部完成。")


if __name__ == "__main__":
    main()

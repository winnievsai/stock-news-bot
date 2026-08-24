#!/usr/bin/env python3
"""
FinMind 台股新聞 增量抓取工具
================================
用途：讀取 stock_list.txt 中的股票代碼，透過 FinMind API 抓取「個股新聞」資料
     (dataset = TaiwanStockNews)，每支股票各自存成一個 CSV。

     這個資料集跟股價不同：FinMind 一次只回傳「一天」的新聞（不接受
     start_date ~ end_date 區間查詢），所以本程式會逐日呼叫 API，從
     CSV 裡最後一筆新聞的日期，一天一天抓到今天為止。

使用方式：
    pip install -r requirements.txt
    python3 update_news.py

設定（.env.local，跟 update_finmind.py 共用同一份）：
    FINMIND_TOKEN=你的token                 (必填，與股價抓取共用同一組token/額度)
    NEWS_OUTPUT_DIR=news                    (選填，預設 ./news)
    NEWS_BACKFILL_DAYS=7                    (選填，預設 7；只在該股票第一次建立CSV時，
                                              回補「今天往前 N 天」的新聞。因為新聞資料
                                              量大、且API逐日呼叫，不適合像股價一樣回補
                                              到很久以前)
    STOCK_LIST_FILE=stock_list.txt          (選填，預設 ./stock_list.txt，與股價抓取共用)
    REQUEST_INTERVAL_SEC=6.5                (選填，預設 6.5 秒，避免超過 FinMind 每小時請求上限)

注意：FinMind 的請求額度是「整個 token」共用的（約600次/小時），如果跟
update_finmind.py 排在同一時段執行，兩者呼叫次數會加總計算。

FinMind API 文件：https://finmindtrade.com/analysis/#/data/api
"""

import csv
import os
import sys
import time
from datetime import datetime, timedelta, date
from pathlib import Path

import requests

# ---------- 基本設定 ----------
SCRIPT_DIR = Path(__file__).resolve().parent
API_URL = "https://api.finmindtrade.com/api/v4/data"
DATASET = "TaiwanStockNews"
DEFAULT_BACKFILL_DAYS = 7
DEFAULT_REQUEST_INTERVAL_SEC = 6.5  # 保守估計，600 requests/hour 上限下留一些餘裕
CSV_FIELDS = ["date", "stock_id", "title", "source", "link"]
MAX_RETRIES = 3


def load_env_file(path: Path) -> dict:
    """簡易 .env 解析器（不依賴 python-dotenv）"""
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
    token = env.get("FINMIND_TOKEN", "").strip()
    if not token:
        sys.exit("[錯誤] 找不到 FINMIND_TOKEN，請在 .env.local 內設定 FINMIND_TOKEN=你的token")
    output_dir = SCRIPT_DIR / env.get("NEWS_OUTPUT_DIR", "news").strip()
    stock_list_file = SCRIPT_DIR / env.get("STOCK_LIST_FILE", "stock_list.txt").strip()
    try:
        backfill_days = int(env.get("NEWS_BACKFILL_DAYS", DEFAULT_BACKFILL_DAYS))
    except ValueError:
        backfill_days = DEFAULT_BACKFILL_DAYS
    try:
        request_interval = float(env.get("REQUEST_INTERVAL_SEC", DEFAULT_REQUEST_INTERVAL_SEC))
    except ValueError:
        request_interval = DEFAULT_REQUEST_INTERVAL_SEC
    return token, backfill_days, output_dir, stock_list_file, request_interval


def load_stock_list(path: Path):
    if not path.exists():
        sys.exit(f"[錯誤] 找不到股票清單檔案：{path}")
    stocks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        code = line.split()[0]
        stocks.append(code)
    if not stocks:
        sys.exit(f"[錯誤] {path} 內沒有任何股票代碼")
    return stocks


def read_last_date(csv_path: Path):
    """回傳 CSV 中最後（最大）一筆新聞的日期（只取日期部分，YYYY-MM-DD）；
    檔案不存在或沒有資料列則回傳 None"""
    if not csv_path.exists():
        return None
    last = None
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = (row.get("date") or "")[:10]
            if d and (last is None or d > last):
                last = d
    return last


def fetch_news_day(token: str, stock_id: str, day: str):
    """抓取指定股票某一天的新聞；回傳資料 list；若觸發流量限制回傳 None（呼叫端應停止本次執行）"""
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "dataset": DATASET,
        "data_id": stock_id,
        "start_date": day,
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(API_URL, headers=headers, params=params, timeout=30)
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                print(f"    [錯誤] {stock_id} {day} 網路錯誤：{e}")
                return []
            print(f"    網路錯誤（第{attempt}次）：{e}，3秒後重試")
            time.sleep(3)
            continue

        if resp.status_code == 402:
            print(f"    [警告] {stock_id} 觸發 FinMind 流量限制 (402)，停止本次執行")
            return None
        if resp.status_code != 200:
            if attempt == MAX_RETRIES:
                print(f"    [錯誤] {stock_id} {day} API 回應 {resp.status_code}：{resp.text[:200]}")
                return []
            time.sleep(2)
            continue

        payload = resp.json()
        if payload.get("status") != 200:
            print(f"    [錯誤] {stock_id} {day} API 訊息：{payload.get('msg')}")
            return []
        return payload.get("data", [])
    return []


def upsert_csv(csv_path: Path, new_rows: list):
    """合併新資料到CSV，依新聞連結(link)去重、依日期排序後整份改寫"""
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


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main():
    token, backfill_days, output_dir, stock_list_file, request_interval = load_config()
    stocks = load_stock_list(stock_list_file)
    today = date.today()

    print(f"共 {len(stocks)} 檔股票，輸出資料夾：{output_dir}")
    print(f"首次建立CSV時回補天數：{backfill_days} 天\n")

    stopped_due_to_limit = False
    for i, stock_id in enumerate(stocks, 1):
        csv_path = output_dir / f"{stock_id}.csv"
        last_date = read_last_date(csv_path)

        if last_date is None:
            start_day = today - timedelta(days=backfill_days)
        else:
            start_day = datetime.strptime(last_date, "%Y-%m-%d").date()

        if start_day > today:
            print(f"[{i}/{len(stocks)}] {stock_id}：已是最新，略過")
            continue

        print(f"[{i}/{len(stocks)}] {stock_id}：抓取 {start_day} ~ {today}")
        collected = []
        for day in daterange(start_day, today):
            data = fetch_news_day(token, stock_id, day.isoformat())
            if data is None:
                stopped_due_to_limit = True
                break
            collected.extend(data)
            if day != today:
                time.sleep(request_interval)

        if collected:
            upsert_csv(csv_path, collected)
            print(f"    新增/更新 {len(collected)} 筆")
        else:
            print("    無新資料")

        if stopped_due_to_limit:
            break

        if i < len(stocks):
            time.sleep(request_interval)

    if stopped_due_to_limit:
        print("\n因觸發 FinMind 流量限制而提前結束，請稍後（約一小時後）再執行一次以補齊剩餘股票。")
    else:
        print("\n全部完成。")


if __name__ == "__main__":
    main()

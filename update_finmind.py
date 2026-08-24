#!/usr/bin/env python3
"""
FinMind 台股日K 增量抓取工具
================================
用途：讀取 stock_list.txt 中的股票代碼，透過 FinMind API 抓取「台股日K」資料
     (dataset = TaiwanStockPrice)，每支股票各自存成一個 CSV。
     每次執行只會抓「CSV 裡還沒有的日期」到「今天」，達到增量更新效果。

使用方式：
    pip install -r requirements.txt
    python3 update_finmind.py

設定（.env.local，放在腳本同一個資料夾）：
    FINMIND_TOKEN=你的token                 (必填)
    BACKFILL_START_DATE=2024-01-01          (選填，預設 2024-01-01；只在該股票第一次建立CSV時使用)
    OUTPUT_DIR=data                         (選填，預設 ./data)
    STOCK_LIST_FILE=stock_list.txt          (選填，預設 ./stock_list.txt)
    REQUEST_INTERVAL_SEC=6.5                (選填，預設 6.5 秒，避免超過 FinMind 每小時請求上限)

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
DATASET = "TaiwanStockPrice"
DEFAULT_BACKFILL_START = "2024-01-01"
DEFAULT_REQUEST_INTERVAL_SEC = 6.5  # 保守估計，600 requests/hour 上限下留一些餘裕
CSV_FIELDS = [
    "date", "stock_id", "Trading_Volume", "Trading_money",
    "open", "max", "min", "close", "spread", "Trading_turnover",
]
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
    backfill_start = env.get("BACKFILL_START_DATE", DEFAULT_BACKFILL_START).strip()
    output_dir = SCRIPT_DIR / env.get("OUTPUT_DIR", "data").strip()
    stock_list_file = SCRIPT_DIR / env.get("STOCK_LIST_FILE", "stock_list.txt").strip()
    try:
        request_interval = float(env.get("REQUEST_INTERVAL_SEC", DEFAULT_REQUEST_INTERVAL_SEC))
    except ValueError:
        request_interval = DEFAULT_REQUEST_INTERVAL_SEC
    return token, backfill_start, output_dir, stock_list_file, request_interval


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
    """回傳 CSV 中最後（最大）的日期字串；檔案不存在或沒有資料列則回傳 None"""
    if not csv_path.exists():
        return None
    last = None
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = row.get("date")
            if d and (last is None or d > last):
                last = d
    return last


def fetch_finmind(token: str, stock_id: str, start_date: str, end_date: str):
    """回傳資料 list；若觸發流量限制回傳 None（呼叫端應停止本次執行）"""
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "dataset": DATASET,
        "data_id": stock_id,
        "start_date": start_date,
        "end_date": end_date,
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(API_URL, headers=headers, params=params, timeout=30)
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                print(f"    [錯誤] {stock_id} 網路錯誤：{e}")
                return []
            print(f"    網路錯誤（第{attempt}次）：{e}，3秒後重試")
            time.sleep(3)
            continue

        if resp.status_code == 402:
            print(f"    [警告] {stock_id} 觸發 FinMind 流量限制 (402)，停止本次執行")
            return None
        if resp.status_code != 200:
            if attempt == MAX_RETRIES:
                print(f"    [錯誤] {stock_id} API 回應 {resp.status_code}：{resp.text[:200]}")
                return []
            time.sleep(2)
            continue

        payload = resp.json()
        if payload.get("status") != 200:
            print(f"    [錯誤] {stock_id} API 訊息：{payload.get('msg')}")
            return []
        return payload.get("data", [])
    return []


def upsert_csv(csv_path: Path, new_rows: list):
    """合併新資料到CSV，依日期去重、排序後整份改寫"""
    existing = {}
    if csv_path.exists():
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing[row["date"]] = row

    for row in new_rows:
        existing[row["date"]] = {k: row.get(k, "") for k in CSV_FIELDS}

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for d in sorted(existing.keys(), reverse=True):
            writer.writerow(existing[d])


def main():
    token, backfill_start, output_dir, stock_list_file, request_interval = load_config()
    stocks = load_stock_list(stock_list_file)
    today = date.today().isoformat()

    print(f"共 {len(stocks)} 檔股票，輸出資料夾：{output_dir}")
    print(f"預設回補起始日：{backfill_start}，抓取到：{today}\n")

    stopped_due_to_limit = False
    for i, stock_id in enumerate(stocks, 1):
        csv_path = output_dir / f"{stock_id}.csv"
        last_date = read_last_date(csv_path)

        if last_date is None:
            start_date = backfill_start
        else:
            next_day = (datetime.strptime(last_date, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
            start_date = next_day

        if start_date > today:
            print(f"[{i}/{len(stocks)}] {stock_id}：已是最新，略過")
            continue

        print(f"[{i}/{len(stocks)}] {stock_id}：抓取 {start_date} ~ {today}")
        data = fetch_finmind(token, stock_id, start_date, today)

        if data is None:
            stopped_due_to_limit = True
            break

        if data:
            upsert_csv(csv_path, data)
            print(f"    新增/更新 {len(data)} 筆")
        else:
            print("    無新資料")

        if i < len(stocks):
            time.sleep(request_interval)

    if stopped_due_to_limit:
        print("\n因觸發 FinMind 流量限制而提前結束，請稍後（約一小時後）再執行一次以補齊剩餘股票。")
    else:
        print("\n全部完成。")


if __name__ == "__main__":
    main()

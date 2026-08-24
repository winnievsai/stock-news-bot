#!/usr/bin/env python3
"""
美股新聞 抓取工具（Google News RSS）
================================
用途：讀取 us_stock_list.txt 中的股票代碼，透過 Google News RSS 搜尋每支美股的
     最新新聞，各自存成一個 CSV。

     FinMind 沒有美股新聞資料集，所以改用 Google News RSS（不需要申請任何 API
     key）。這個來源沒有真正的歷史區間查詢，只能查「最近幾天」，所以每次執行都是
     抓「最近 2 天」，靠每天執行、用新聞連結去重，慢慢累積出歷史記錄。

使用方式：
    python3 update_us_news.py

設定（.env.local，跟其他腳本共用同一份）：
    US_NEWS_OUTPUT_DIR=us_news              (選填，預設 ./us_news)
    US_STOCK_LIST_FILE=us_stock_list.txt    (選填，預設 ./us_stock_list.txt，與
                                              update_us_stock.py 共用)
    US_NEWS_REQUEST_INTERVAL_SEC=2          (選填，預設 2 秒；沒有API額度限制，
                                              但避免對 Google 發太密集的請求)

已知限制：
    - `link` 欄位是 Google News 的轉址連結，不是新聞原始網址，點開會先經過
      Google News 頁面再跳轉到原文，這是 Google News RSS 的正常行為
    - Google News 對「代碼+stock」查詢的比對很鬆散，常常混進完全不相關的新聞。
      程式會用「標題是否包含股票代碼或公司名稱關鍵字」過濾掉無關新聞（見
      relevance_keywords() / is_relevant()），這是關鍵字比對的心探法，不是完美的
      語意判斷——公司名稱抽出來的關鍵字太通用時可能漏掉少數只用全名報導的新聞，
      但不會誤放行明顯無關的新聞
"""

import csv
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
API_URL = "https://api.finmindtrade.com/api/v4/data"
RSS_URL = "https://news.google.com/rss/search"
DEFAULT_REQUEST_INTERVAL_SEC = 2
CSV_FIELDS = ["date", "stock_id", "title", "source", "link"]

GENERIC_NAME_WORDS = {
    "inc", "incorporated", "corp", "corporation", "co", "ltd", "limited", "llc",
    "plc", "trust", "holdings", "holding", "group", "class", "common", "stock",
    "shares", "share", "ordinary", "american", "depositary", "receipt", "receipts",
    "adr", "ads", "sponsored", "series", "fund", "etf", "the", "and", "of",
    "technology", "technologies", "systems", "solutions", "energy", "capital",
    "management", "advisors", "partners", "financial", "global", "international",
    "national", "industries", "industrial", "resources", "communications",
    "networks", "network",
}


def load_env_file(path: Path) -> dict:
    """簡易 .env 解析器（不依賴 python-dotenv），與其他腳本共用同一份 .env.local"""
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
    output_dir = SCRIPT_DIR / env.get("US_NEWS_OUTPUT_DIR", "us_news").strip()
    stock_list_file = SCRIPT_DIR / env.get("US_STOCK_LIST_FILE", "us_stock_list.txt").strip()
    try:
        request_interval = float(env.get("US_NEWS_REQUEST_INTERVAL_SEC", DEFAULT_REQUEST_INTERVAL_SEC))
    except ValueError:
        request_interval = DEFAULT_REQUEST_INTERVAL_SEC
    return token, output_dir, stock_list_file, request_interval


def load_stock_list(path: Path):
    if not path.exists():
        sys.exit(f"[錯誤] 找不到股票清單檔案：{path}")
    stocks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        code = line.split()[0].upper()
        stocks.append(code)
    if not stocks:
        sys.exit(f"[錯誤] {path} 內沒有任何股票代碼")
    return stocks


def fetch_company_names(token: str, stock_ids: list) -> dict:
    """一次呼叫 FinMind USStockInfo，回傳 {代碼: 公司全名}；查不到或發生錯誤的代碼
    對應到空字串，不中斷整個流程"""
    wanted = set(stock_ids)
    names = {sid: "" for sid in stock_ids}
    try:
        resp = requests.get(
            API_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"dataset": "USStockInfo"},
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[警告] 查詢公司名稱失敗（{e}），新聞相關性過濾將只用股票代碼比對")
        return names

    for row in payload.get("data", []):
        sid = row.get("stock_id")
        if sid in wanted and row.get("stock_name"):
            names[sid] = row["stock_name"]
    return names


def extract_keywords(company_name: str) -> list:
    """從公司全名抽出顯著關鍵字（去掉常見的公司類型/類股泛用字），用來輔助判斷
    新聞標題是否真的跟這支股票有關"""
    words = re.findall(r"[A-Za-z]+", company_name)
    return [w for w in words if len(w) >= 3 and w.lower() not in GENERIC_NAME_WORDS]


def is_relevant(title: str, stock_id: str, keywords: list) -> bool:
    """標題用單字邊界比對，包含股票代碼或任一公司關鍵字才算相關"""
    if re.search(rf"\b{re.escape(stock_id)}\b", title, re.IGNORECASE):
        return True
    return any(re.search(rf"\b{re.escape(kw)}\b", title, re.IGNORECASE) for kw in keywords)


def fetch_us_news(stock_id: str, keywords: list):
    """回傳某支美股最近幾天、且跟該股票相關的新聞 list；發生錯誤時回傳空 list（不
    中斷整個流程）"""
    query = quote(f"{stock_id} stock when:2d")
    url = f"{RSS_URL}?q={query}&hl=en-US&gl=US&ceid=US:en"
    try:
        resp = requests.get(url, timeout=30)
    except requests.RequestException as e:
        print(f"    [錯誤] {stock_id} 網路錯誤：{e}")
        return []

    if resp.status_code != 200:
        print(f"    [錯誤] {stock_id} 回應 {resp.status_code}")
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        print(f"    [錯誤] {stock_id} RSS 解析失敗：{e}")
        return []

    rows = []
    skipped = 0
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
        if not is_relevant(title, stock_id, keywords):
            skipped += 1
            continue

        try:
            dt = parsedate_to_datetime(pub_date_raw)
            date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            date_str = pub_date_raw

        rows.append({"date": date_str, "stock_id": stock_id, "title": title, "source": source, "link": link})
    if skipped:
        print(f"    （過濾掉 {skipped} 則不相關新聞）")
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
    token, output_dir, stock_list_file, request_interval = load_config()
    stocks = load_stock_list(stock_list_file)
    today = date.today().isoformat()

    print(f"共 {len(stocks)} 檔美股，輸出資料夾：{output_dir}（資料來源：Google News RSS）")
    print("查詢公司名稱以過濾不相關新聞...")
    company_names = fetch_company_names(token, stocks)
    keywords_by_stock = {sid: extract_keywords(company_names.get(sid, "")) for sid in stocks}
    print()

    for i, stock_id in enumerate(stocks, 1):
        keywords = keywords_by_stock.get(stock_id, [])
        name_hint = company_names.get(stock_id, "")
        print(f"[{i}/{len(stocks)}] {stock_id}（{name_hint or '公司名稱未知'}）：抓取最近新聞")
        rows = fetch_us_news(stock_id, keywords)
        if rows:
            csv_path = output_dir / f"{stock_id}.csv"
            upsert_csv(csv_path, rows)
            print(f"    抓到 {len(rows)} 筆（含新舊，已用連結去重合併）")
        else:
            print("    無資料")

        if i < len(stocks):
            time.sleep(request_interval)

    print("\n全部完成。")


if __name__ == "__main__":
    main()

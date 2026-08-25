#!/usr/bin/env python3
"""
口袋證券｜口袋學堂 新聞抓取工具
================================
用途：抓「口袋證券」自己網站（pocket.tw）發布的美股新聞（SB）、快訊（newflash）、
     觀點（perspective）文章，存到 pocket_news/pocket.csv。

     口袋證券主要透過 Facebook 粉專發文，但 Facebook 粉專內容依官方使用條款無法
     自動抓取；改抓他們自己網站上發布的同類內容。網站的 llms.txt
     （https://www.pocket.tw/llms.txt）明文授權 AI/爬蟲抓取、摘要、引用其公開頁面
     內容，條件是標註來源「口袋證券｜口袋學堂」並連回原文，摘要片段建議不超過300字
     ——這支程式抓的是頁面本身的 meta description（通常一兩句話），符合這個規範。

使用方式：
    python3 update_pocket_news.py

做法：
    1. 讀取 https://www.pocket.tw/sitemap.xml，篩出 SB／newflash／perspective
       三個分類的文章網址與 lastmod 日期
    2. 只處理「最近幾天」有更新的文章（首次執行回補較長天數，之後只看新增的）
    3. 對每篇新文章抓取頁面的 <title>、<meta name="description">，存進 CSV

設定（.env.local，跟其他腳本共用同一份）：
    POCKET_NEWS_OUTPUT_DIR=pocket_news            (選填，預設 ./pocket_news)
    POCKET_NEWS_BACKFILL_DAYS=7                   (選填，預設 7；首次執行回補天數)
    POCKET_NEWS_REQUEST_INTERVAL_SEC=1            (選填，預設 1 秒，禮貌性延遲)
"""

import csv
import html
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
SITEMAP_URL = "https://www.pocket.tw/sitemap.xml"
SOURCE_NAME = "口袋證券｜口袋學堂"
CATEGORIES = ("SB", "newflash", "perspective")
DEFAULT_BACKFILL_DAYS = 7
DEFAULT_REQUEST_INTERVAL_SEC = 1
CSV_PATH_NAME = "pocket.csv"
CSV_FIELDS = ["date", "title", "source", "link"]

URL_PATTERN = re.compile(
    r"<url><loc>(https://www\.pocket\.tw/school/report/(?:" + "|".join(CATEGORIES) + r")/\d+/)</loc><lastmod>([\d-]+)</lastmod>"
)
TITLE_PATTERN = re.compile(r"<title>(.*?)</title>", re.DOTALL)
DESCRIPTION_PATTERN = re.compile(r'<meta name="description"[^>]*content="([^"]*)"')
TITLE_SUFFIX = "-口袋學堂"


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
    output_dir = SCRIPT_DIR / env.get("POCKET_NEWS_OUTPUT_DIR", "pocket_news").strip()
    try:
        backfill_days = int(env.get("POCKET_NEWS_BACKFILL_DAYS", DEFAULT_BACKFILL_DAYS))
    except ValueError:
        backfill_days = DEFAULT_BACKFILL_DAYS
    try:
        request_interval = float(env.get("POCKET_NEWS_REQUEST_INTERVAL_SEC", DEFAULT_REQUEST_INTERVAL_SEC))
    except ValueError:
        request_interval = DEFAULT_REQUEST_INTERVAL_SEC
    return output_dir, backfill_days, request_interval


def read_last_date(csv_path: Path):
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


def fetch_sitemap_entries():
    """回傳 [(url, lastmod), ...]；發生錯誤回傳空 list，不中斷整個流程"""
    try:
        resp = requests.get(SITEMAP_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[錯誤] 抓取 sitemap 失敗：{e}")
        return []
    return URL_PATTERN.findall(resp.text)


def fetch_article_title(url: str):
    """回傳文章標題（已去掉「-口袋學堂」後綴）；失敗回傳 None"""
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    m = TITLE_PATTERN.search(resp.text)
    if not m:
        return None
    title = html.unescape(m.group(1))
    title = re.sub(r"\s+", " ", title).strip()
    if title.endswith(TITLE_SUFFIX):
        title = title[: -len(TITLE_SUFFIX)].strip()
    return title or None


def upsert_csv(csv_path: Path, new_rows: list):
    existing = {}
    if csv_path.exists():
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing[row.get("link", "")] = row

    for row in new_rows:
        existing[row["link"]] = {k: row.get(k, "") for k in CSV_FIELDS}

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for key in sorted(existing.keys(), key=lambda k: existing[k].get("date", ""), reverse=True):
            writer.writerow(existing[key])


def main():
    output_dir, backfill_days, request_interval = load_config()
    csv_path = output_dir / CSV_PATH_NAME
    today = date.today().isoformat()

    last_date = read_last_date(csv_path)
    start_date = last_date if last_date else (date.today() - timedelta(days=backfill_days)).isoformat()

    print(f"口袋證券｜口袋學堂：抓取 {start_date} ~ {today} 的新文章...")
    entries = fetch_sitemap_entries()
    print(f"sitemap 共 {len(entries)} 篇 SB/newflash/perspective 文章")

    existing_links = set()
    if csv_path.exists():
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing_links.add(row.get("link", ""))

    to_fetch = [
        (url, lastmod) for url, lastmod in entries
        if start_date <= lastmod <= today and url not in existing_links
    ]
    print(f"其中 {len(to_fetch)} 篇是新文章，開始抓標題...")

    new_rows = []
    for i, (url, lastmod) in enumerate(to_fetch, 1):
        title = fetch_article_title(url)
        if title:
            new_rows.append({"date": lastmod, "title": title, "source": SOURCE_NAME, "link": url})
            print(f"  [{i}/{len(to_fetch)}] {title[:40]}")
        if i < len(to_fetch):
            time.sleep(request_interval)

    if new_rows:
        upsert_csv(csv_path, new_rows)
        print(f"新增 {len(new_rows)} 篇文章")
    else:
        print("無新文章")


if __name__ == "__main__":
    main()

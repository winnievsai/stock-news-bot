#!/usr/bin/env python3
"""
每日新聞 Email 摘要（GitHub Actions / Resend 版）
================================
用途：跟 send_news_email.py 幾乎一樣（讀 news/、us_news/ 整理成一封信），差別是
     這支改用 Resend 的 HTTP API 寄信，不依賴 macOS 的「郵件」App，可以在 GitHub
     Actions 這種 Linux 雲端環境裡執行，不需要本機 Mac 開機。

使用方式：
    python3 send_news_email_resend.py

設定（GitHub Actions 用 repository 的 Secrets 注入環境變數；本機測試可以放在
.env.local，跟其他腳本共用同一份）：
    RESEND_API_KEY=你的Resend API Key      (必填，在 resend.com 申請帳號後取得)
    EMAIL_FROM=onboarding@resend.dev        (選填，預設用 Resend 提供的測試寄件地址；
                                              之後想用自己網域寄信可以另外設定)
    EMAIL_TO=收件人地址                      (必填；要同時寄給多人，用逗號分隔，
                                              例如 a@gmail.com,b@company.com)
    NEWS_OUTPUT_DIR=news                    (選填，預設 ./news)
    STOCK_LIST_FILE=stock_list.txt          (選填，預設 ./stock_list.txt，台股清單)
    US_NEWS_OUTPUT_DIR=us_news              (選填，預設 ./us_news)
    US_STOCK_LIST_FILE=us_stock_list.txt    (選填，預設 ./us_stock_list.txt，美股清單)
    MAX_NEWS_PER_STOCK=8                    (選填，預設 8)
    MARKET_NEWS_OUTPUT_DIR=market_news      (選填，預設 ./market_news，需跟
                                              update_market_news.py 一致)
    MAX_MARKET_NEWS=10                      (選填，預設 10；大盤新聞信件裡最多列幾則)

其餘邏輯（可信來源白名單、標題相似新聞統合、美股標題翻譯）都跟 send_news_email.py
完全相同，說明請參考該檔案開頭的註解。
"""

import csv
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

SIMILARITY_THRESHOLD = 0.5
DEFAULT_MAX_NEWS_PER_STOCK = 8
DEFAULT_MAX_MARKET_NEWS = 10
DEFAULT_EMAIL_FROM = "onboarding@resend.dev"

TW_CREDIBLE_SOURCES = {
    "UDN", "udn.com", "money.udn.com",
    "經濟日報", "工商時報", "chinatimes.com", "wantrich.chinatimes.com",
    "自由時報", "自由財經", "ec.ltn.com.tw", "stock.ltn.com.tw",
    "ETtoday財經雲", "finance.ettoday.net",
    "FTNN 新聞網", "ftnn.com.tw",
    "TVBS新聞", "TVBS新聞網", "news.tvbs.com.tw",
    "三立新聞", "三立新聞網SETN.com",
    "民視新聞網", "民視財經網",
    "東森新聞", "fnc.ebc.net.tw",
    "今周刊", "businesstoday.com.tw",
    "財訊", "理財周刊", "moneyweekly.com.tw",
    "風傳媒", "storm.mg",
    "壹蘋新聞網", "Newtalk新聞",
    "MoneyDJ", "news.cnyes.com",
    "TechNews 科技新報", "technews.tw",
    "Yahoo股市", "tw.stock.yahoo.com", "Yahoo新聞", "tw.news.yahoo.com",
    "口袋證券｜口袋學堂",
}

US_CREDIBLE_SOURCES = {
    "Reuters", "Bloomberg", "CNBC", "MarketWatch",
    "Barron's", "Barrons", "The Wall Street Journal", "WSJ",
    "Financial Times", "Associated Press", "AP", "Nasdaq",
    "Yahoo Finance", "Yahoo! Finance", "Yahoo Finance UK", "Yahoo! Finance Canada",
    "Forbes", "Business Insider", "Fortune", "Fox Business",
    "The Motley Fool", "Investor's Business Daily", "Zacks Investment Research",
    "TheStreet", "Barchart.com", "Investing.com", "Benzinga",
    "The Globe and Mail", "USA Today", "CBS News", "NBC News", "ABC News",
}

SCRIPT_DIR = Path(__file__).resolve().parent


def load_env_file(path: Path) -> dict:
    """簡易 .env 解析器（不依賴 python-dotenv）；GitHub Actions 上這個檔案不存在，
    設定會全部改由 os.environ（也就是 repository 的 Secrets）提供"""
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

    resend_api_key = env.get("RESEND_API_KEY", "").strip()
    email_to = env.get("EMAIL_TO", "").strip()
    missing = [
        name
        for name, value in [("RESEND_API_KEY", resend_api_key), ("EMAIL_TO", email_to)]
        if not value
    ]
    if missing:
        sys.exit(
            "[錯誤] 缺少以下設定：" + ", ".join(missing) +
            "\n請參考 send_news_email_resend.py 開頭的說明填入（GitHub Actions 請用 Secrets）。"
        )

    email_from = env.get("EMAIL_FROM", DEFAULT_EMAIL_FROM).strip()
    news_dir = SCRIPT_DIR / env.get("NEWS_OUTPUT_DIR", "news").strip()
    stock_list_file = SCRIPT_DIR / env.get("STOCK_LIST_FILE", "stock_list.txt").strip()
    us_news_dir = SCRIPT_DIR / env.get("US_NEWS_OUTPUT_DIR", "us_news").strip()
    us_stock_list_file = SCRIPT_DIR / env.get("US_STOCK_LIST_FILE", "us_stock_list.txt").strip()
    market_news_dir = SCRIPT_DIR / env.get("MARKET_NEWS_OUTPUT_DIR", "market_news").strip()
    pocket_news_dir = SCRIPT_DIR / env.get("POCKET_NEWS_OUTPUT_DIR", "pocket_news").strip()
    try:
        max_per_stock = int(env.get("MAX_NEWS_PER_STOCK", DEFAULT_MAX_NEWS_PER_STOCK))
    except ValueError:
        max_per_stock = DEFAULT_MAX_NEWS_PER_STOCK
    try:
        max_market_news = int(env.get("MAX_MARKET_NEWS", DEFAULT_MAX_MARKET_NEWS))
    except ValueError:
        max_market_news = DEFAULT_MAX_MARKET_NEWS

    return (
        resend_api_key, email_from, email_to,
        news_dir, stock_list_file, us_news_dir, us_stock_list_file,
        market_news_dir, pocket_news_dir, max_per_stock, max_market_news,
    )


def load_stock_list(path: Path):
    """讀股票清單；找不到檔案時回傳空清單（美股清單缺少不應該讓台股新聞信寄不出去）"""
    if not path.exists():
        return []
    stocks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        code = line.split()[0]
        stocks.append(code)
    return stocks


def load_today_news(csv_path: Path, today: str):
    """回傳某支股票 CSV 裡「今天」的新聞列表（CSV本身已是新到舊排序）"""
    if not csv_path.exists():
        return []
    rows = []
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("日期") or "").startswith(today):
                rows.append(row)
    return rows


def title_words(title: str) -> set:
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return set(re.sub(r"\s+", " ", t).strip().split())


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cluster_similar_news(rows: list, threshold: float = SIMILARITY_THRESHOLD) -> list:
    """把標題相似的新聞歸成同一組（同一事件的不同轉載），用標題斷詞後的重疊比例
    （Jaccard）比對。回傳 [[row, row, ...], ...]，群組內、群組間都維持原本的先後順序"""
    clusters = []  # list of {"words": set, "rows": [row, ...]}
    for row in rows:
        words = title_words(row.get("標題", ""))
        match = next(
            (c for c in clusters if jaccard(words, c["words"]) >= threshold),
            None,
        )
        if match:
            match["rows"].append(row)
        else:
            clusters.append({"words": words, "rows": [row]})
    return [c["rows"] for c in clusters]


TRANSLATE_QUOTA_EXHAUSTED = False  # 一旦MyMemory回傳429（今日額度用完），本次執行
                                    # 就不再浪費時間呼叫API，標題先保留英文，之後
                                    # 額度重置後下次執行會自動繼續翻


def translate_to_zh_tw(text: str) -> str:
    """用 MyMemory 免費翻譯 API（不需要API key）把英文標題翻成繁體中文；
    翻譯失敗就回傳原文，不中斷整個流程。
    （這個API每個IP每天有免費字數額度，用完要等隔天重置；試過改用Google Translate
    的公開端點當替代，但那個端點對自動化查詢的IP封鎖更快更不穩定，所以維持用這個）"""
    global TRANSLATE_QUOTA_EXHAUSTED
    if not text or not re.search(r"[A-Za-z]", text):
        return text  # 已經是中文或空字串，不用翻
    if TRANSLATE_QUOTA_EXHAUSTED:
        return text  # 本次執行已知額度用完，先保留英文，不用再等一次逾時/被拒
    try:
        resp = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": "en|zh-TW"},
            timeout=15,
        )
        if resp.status_code == 429:
            TRANSLATE_QUOTA_EXHAUSTED = True
            return text
        resp.raise_for_status()
        payload = resp.json()
        translated = (payload.get("responseData") or {}).get("translatedText") or ""
        translated = translated.strip()
        return translated or text
    except (requests.RequestException, ValueError, AttributeError):
        return text


def build_news_section(section_title: str, stocks, news_dir: Path, today: str, max_per_stock: int = 0, credible_sources: set = None, translate: bool = False):
    lines = [f"== {section_title} ==", ""]
    total = 0
    for stock_id in stocks:
        csv_path = news_dir / f"{stock_id}.csv"
        rows = load_today_news(csv_path, today)
        if credible_sources is not None:
            rows = [r for r in rows if r.get("來源", "").strip() in credible_sources]
        lines.append(f"【{stock_id}】")
        if not rows:
            lines.append("  （今日無新聞）")
        else:
            groups = cluster_similar_news(rows)
            shown_groups = groups[:max_per_stock] if max_per_stock else groups
            for group in shown_groups:
                rep = group[0]
                title = rep.get("標題", "").strip()
                link = rep.get("連結", "").strip()
                if translate:
                    title = translate_to_zh_tw(title)
                    time.sleep(0.3)  # 對免費翻譯API禮貌性地放慢速度
                if len(group) > 1:
                    sources = list(dict.fromkeys(r.get("來源", "").strip() for r in group if r.get("來源", "").strip()))
                    shown = "、".join(sources[:5]) + ("等" if len(sources) > 5 else "")
                    lines.append(f"  - {title}（共{len(group)}篇報導：{shown}）")
                else:
                    lines.append(f"  - {title}（{rep.get('來源', '').strip()}）")
                lines.append(f"    {link}")
                total += 1
            hidden = len(groups) - len(shown_groups)
            if hidden > 0:
                lines.append(f"  …還有 {hidden} 則較舊的新聞未列出（完整記錄在 {csv_path.name}）")
        lines.append("")
    return "\n".join(lines), total


def build_market_news_section(section_title: str, csv_path: Path, today: str, max_items: int = 0, credible_sources: set = None, translate: bool = False):
    lines = [f"== {section_title} ==", ""]
    total = 0
    rows = load_today_news(csv_path, today)
    if credible_sources is not None:
        rows = [r for r in rows if r.get("來源", "").strip() in credible_sources]
    if not rows:
        lines.append("（今日無新聞）")
    else:
        groups = cluster_similar_news(rows)
        shown_groups = groups[:max_items] if max_items else groups
        for group in shown_groups:
            rep = group[0]
            title = rep.get("標題", "").strip()
            link = rep.get("連結", "").strip()
            if translate:
                title = translate_to_zh_tw(title)
                time.sleep(0.3)
            if len(group) > 1:
                sources = list(dict.fromkeys(r.get("來源", "").strip() for r in group if r.get("來源", "").strip()))
                shown = "、".join(sources[:5]) + ("等" if len(sources) > 5 else "")
                lines.append(f"- {title}（共{len(group)}篇報導：{shown}）")
            else:
                lines.append(f"- {title}（{rep.get('來源', '').strip()}）")
            lines.append(f"  {link}")
            total += 1
        hidden = len(groups) - len(shown_groups)
        if hidden > 0:
            lines.append(f"…還有 {hidden} 則較舊的新聞未列出（完整記錄在 {csv_path.name}）")
    lines.append("")
    return "\n".join(lines), total


def send_via_resend(api_key: str, email_from: str, email_to_list: list, subject: str, body: str):
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"from": email_from, "to": email_to_list, "subject": subject, "text": body},
        timeout=30,
    )
    if resp.status_code >= 300:
        sys.exit(f"[錯誤] Resend 寄信失敗（{resp.status_code}）：{resp.text[:300]}")


def load_price_validation_warning() -> str:
    """如果 validate_prices.py 有發現價格異常/資料不同步，讀出來放進信件開頭"""
    report_path = SCRIPT_DIR / "price_validation_report.txt"
    if not report_path.exists():
        return ""
    content = report_path.read_text(encoding="utf-8").strip()
    return f"⚠️ 股價核對提醒\n{content}\n\n" if content else ""


def main():
    (
        resend_api_key, email_from, email_to,
        news_dir, stock_list_file, us_news_dir, us_stock_list_file,
        market_news_dir, pocket_news_dir, max_per_stock, max_market_news,
    ) = load_config()
    stocks = load_stock_list(stock_list_file)
    us_stocks = load_stock_list(us_stock_list_file)
    today = date.today().isoformat()

    price_warning = load_price_validation_warning()
    tw_market_body, tw_market_total = build_market_news_section(
        "台股大盤新聞", market_news_dir / "tw.csv", today, max_market_news, TW_CREDIBLE_SOURCES, translate=False
    )
    # 美股標題已經在 update_us_news.py / update_market_news.py 抓取時翻譯好存進CSV，
    # 這裡不用再翻一次（translate=False），避免重複呼叫翻譯API
    us_market_body, us_market_total = build_market_news_section(
        "美股大盤新聞", market_news_dir / "us.csv", today, max_market_news, US_CREDIBLE_SOURCES, translate=False
    )
    pocket_body, pocket_total = build_market_news_section(
        "口袋證券新聞", pocket_news_dir / "pocket.csv", today, max_market_news, TW_CREDIBLE_SOURCES, translate=False
    )
    tw_body, tw_total = build_news_section("台股個股新聞", stocks, news_dir, today, max_per_stock, TW_CREDIBLE_SOURCES, translate=False)
    us_body, us_total = build_news_section("美股個股新聞", us_stocks, us_news_dir, today, max_per_stock, US_CREDIBLE_SOURCES, translate=False)

    body = (
        f"股票新聞日報 - {today}\n\n{price_warning}"
        f"{tw_market_body}\n{us_market_body}\n{pocket_body}\n{tw_body}\n{us_body}"
    )
    subject = (
        f"股票新聞日報 {today}（大盤 {tw_market_total + us_market_total} 則 / "
        f"口袋證券 {pocket_total} 則 / 台股 {tw_total} 則 / 美股 {us_total} 則）"
    )

    email_to_list = [addr.strip() for addr in email_to.split(",") if addr.strip()]
    print(
        f"寄送對象：{', '.join(email_to_list)}，"
        f"大盤 {tw_market_total + us_market_total} 則、口袋證券 {pocket_total} 則、"
        f"台股 {tw_total} 則、美股 {us_total} 則"
    )
    send_via_resend(resend_api_key, email_from, email_to_list, subject, body)
    print("已寄出。")


if __name__ == "__main__":
    main()

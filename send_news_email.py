#!/usr/bin/env python3
"""
每日新聞 Email 摘要
================================
用途：讀取 news/（台股）、us_news/（美股）資料夾內每支股票的新聞 CSV，把「今天新增」
     的新聞整理成一封信，分「台股新聞」「美股新聞」兩段，透過 Mac 內建的「郵件」App
     （Mail.app）寄出。
     設計上接在 update_news.py、update_us_news.py 之後執行（run.sh 已經串好順序）。

     信件只會列出來自 TW_CREDIBLE_SOURCES / US_CREDIBLE_SOURCES 白名單裡的新聞來源
     （例如 UDN、經濟日報、Reuters、CNBC 等主流媒體），投資論壇/個人部落格/自動產生
     的機構持股公告等來源不會列入信件（CSV 裡的原始資料不受影響，只是不寄進信）。
     白名單直接寫在檔案開頭，覺得漏掉哪個可信來源可以直接編輯調整。

     美股新聞標題會用 MyMemory 免費翻譯 API（不需要API key）自動翻成繁體中文再放進
     信裡；台股新聞本來就是中文，不會另外翻譯。翻譯是機器翻譯，僅供快速瀏覽參考，
     連結還是連到英文原文。

使用方式：
    python3 send_news_email.py

事前準備：
    1. 在 Mac 的「系統設定」→「網際網路帳號」加入寄件用的 Gmail 帳號，並確認
       「郵件」項目是打開的（不需要 App Password，用一般帳號登入即可）
    2. 在 .env.local 設定：
       EMAIL_FROM=你的Gmail地址（要跟上面加入「郵件」App 的帳號一致）
       EMAIL_TO=收件人地址（可以跟 EMAIL_FROM 相同，自寄自收）

    第一次執行時，macOS 可能會跳出「終端機／Python 想要控制『郵件』」的權限詢問，
    需要按「允許」，之後就不會再問。

設定（.env.local，跟其他腳本共用同一份）：
    EMAIL_FROM=hsunweiai@gmail.com          (必填，寄件人；需已加入 Mail.app 帳號)
    EMAIL_TO=hsunweiai@gmail.com             (必填，收件人；要同時寄給多人，用逗號
                                              分隔，例如 a@gmail.com,b@company.com)
    NEWS_OUTPUT_DIR=news                    (選填，預設 ./news，需跟 update_news.py 一致)
    STOCK_LIST_FILE=stock_list.txt          (選填，預設 ./stock_list.txt，台股清單)
    US_NEWS_OUTPUT_DIR=us_news              (選填，預設 ./us_news，需跟 update_us_news.py 一致)
    US_STOCK_LIST_FILE=us_stock_list.txt    (選填，預設 ./us_stock_list.txt，美股清單)
    MAX_NEWS_PER_STOCK=8                    (選填，預設 8；每檔股票信件裡最多列幾則
                                              「統合後」的新聞，避免熱門股新聞量太大時
                                              信件過長，CSV 裡的完整記錄不受影響)
    MARKET_NEWS_OUTPUT_DIR=market_news      (選填，預設 ./market_news，需跟
                                              update_market_news.py 一致)
    MAX_MARKET_NEWS=10                      (選填，預設 10；大盤新聞信件裡最多列幾則)

如果當天所有股票都沒有新新聞，仍會寄出一封信，內容註明「今日無新聞」。
"""

import csv
import os
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import requests

SIMILARITY_THRESHOLD = 0.5
DEFAULT_MAX_NEWS_PER_STOCK = 8
DEFAULT_MAX_MARKET_NEWS = 10

# 只保留這些來源的新聞，其餘一律不列入信件（例如投資論壇/個人部落格/自動產生的
# 機構持股公告、加密貨幣網站等）。名單可依實際使用狀況增減。
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
APPLESCRIPT_PATH = SCRIPT_DIR / "send_mail.applescript"


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

    email_from = env.get("EMAIL_FROM", "").strip()
    email_to = env.get("EMAIL_TO", "").strip()
    missing = [
        name
        for name, value in [("EMAIL_FROM", email_from), ("EMAIL_TO", email_to)]
        if not value
    ]
    if missing:
        sys.exit(
            "[錯誤] .env.local 缺少以下設定：" + ", ".join(missing) +
            "\n請參考 send_news_email.py 開頭的說明填入。"
        )

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
        email_from, email_to, news_dir, stock_list_file, us_news_dir, us_stock_list_file,
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
            if (row.get("date") or "").startswith(today):
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
    （Jaccard）比對，比純字元序列比對更能抓到「同一件事、不同語序標題」的情況。
    回傳 [[row, row, ...], ...]，群組內、群組間都維持原本的先後順序"""
    clusters = []  # list of {"words": set, "rows": [row, ...]}
    for row in rows:
        words = title_words(row.get("title", ""))
        match = next(
            (c for c in clusters if jaccard(words, c["words"]) >= threshold),
            None,
        )
        if match:
            match["rows"].append(row)
        else:
            clusters.append({"words": words, "rows": [row]})
    return [c["rows"] for c in clusters]


def translate_to_zh_tw(text: str) -> str:
    """用 MyMemory 免費翻譯 API（不需要API key）把英文標題翻成繁體中文；
    翻譯失敗就回傳原文，不中斷整個流程"""
    if not text or not re.search(r"[A-Za-z]", text):
        return text  # 已經是中文或空字串，不用翻
    try:
        resp = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": "en|zh-TW"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        translated = payload.get("responseData", {}).get("translatedText", "").strip()
        return translated or text
    except (requests.RequestException, ValueError):
        return text


def build_news_section(section_title: str, stocks, news_dir: Path, today: str, max_per_stock: int = 0, credible_sources: set = None, translate: bool = False):
    lines = [f"== {section_title} ==", ""]
    total = 0
    for stock_id in stocks:
        csv_path = news_dir / f"{stock_id}.csv"
        rows = load_today_news(csv_path, today)
        if credible_sources is not None:
            rows = [r for r in rows if r.get("source", "").strip() in credible_sources]
        lines.append(f"【{stock_id}】")
        if not rows:
            lines.append("  （今日無新聞）")
        else:
            groups = cluster_similar_news(rows)
            shown_groups = groups[:max_per_stock] if max_per_stock else groups
            for group in shown_groups:
                rep = group[0]
                title = rep.get("title", "").strip()
                link = rep.get("link", "").strip()
                if translate:
                    title = translate_to_zh_tw(title)
                    time.sleep(0.3)  # 對免費翻譯API禮貌性地放慢速度
                if len(group) > 1:
                    sources = list(dict.fromkeys(r.get("source", "").strip() for r in group if r.get("source", "").strip()))
                    shown = "、".join(sources[:5]) + ("等" if len(sources) > 5 else "")
                    lines.append(f"  - {title}（共{len(group)}篇報導：{shown}）")
                else:
                    lines.append(f"  - {title}（{rep.get('source', '').strip()}）")
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
        rows = [r for r in rows if r.get("source", "").strip() in credible_sources]
    if not rows:
        lines.append("（今日無新聞）")
    else:
        groups = cluster_similar_news(rows)
        shown_groups = groups[:max_items] if max_items else groups
        for group in shown_groups:
            rep = group[0]
            title = rep.get("title", "").strip()
            link = rep.get("link", "").strip()
            if translate:
                title = translate_to_zh_tw(title)
                time.sleep(0.3)
            if len(group) > 1:
                sources = list(dict.fromkeys(r.get("source", "").strip() for r in group if r.get("source", "").strip()))
                shown = "、".join(sources[:5]) + ("等" if len(sources) > 5 else "")
                lines.append(f"- {title}（共{len(group)}篇報導：{shown}）")
            else:
                lines.append(f"- {title}（{rep.get('source', '').strip()}）")
            lines.append(f"  {link}")
            total += 1
        hidden = len(groups) - len(shown_groups)
        if hidden > 0:
            lines.append(f"…還有 {hidden} 則較舊的新聞未列出（完整記錄在 {csv_path.name}）")
    lines.append("")
    return "\n".join(lines), total


def send_via_mail_app(email_from: str, email_to: str, subject: str, body: str):
    result = subprocess.run(
        ["osascript", str(APPLESCRIPT_PATH), email_from, email_to, subject, body],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"[錯誤] 透過 Mail.app 寄信失敗：{result.stderr.strip()}")


def load_price_validation_warning() -> str:
    """如果 validate_prices.py 有發現價格異常/資料不同步，讀出來放進信件開頭"""
    report_path = SCRIPT_DIR / "price_validation_report.txt"
    if not report_path.exists():
        return ""
    content = report_path.read_text(encoding="utf-8").strip()
    return f"⚠️ 股價核對提醒\n{content}\n\n" if content else ""


def main():
    (
        email_from, email_to, news_dir, stock_list_file, us_news_dir, us_stock_list_file,
        market_news_dir, pocket_news_dir, max_per_stock, max_market_news,
    ) = load_config()
    stocks = load_stock_list(stock_list_file)
    us_stocks = load_stock_list(us_stock_list_file)
    today = date.today().isoformat()

    price_warning = load_price_validation_warning()
    tw_market_body, tw_market_total = build_market_news_section(
        "台股大盤新聞", market_news_dir / "tw.csv", today, max_market_news, TW_CREDIBLE_SOURCES, translate=False
    )
    print("翻譯美股大盤新聞標題...")
    us_market_body, us_market_total = build_market_news_section(
        "美股大盤新聞", market_news_dir / "us.csv", today, max_market_news, US_CREDIBLE_SOURCES, translate=True
    )
    pocket_body, pocket_total = build_market_news_section(
        "口袋證券新聞", pocket_news_dir / "pocket.csv", today, max_market_news, TW_CREDIBLE_SOURCES, translate=False
    )
    tw_body, tw_total = build_news_section("台股個股新聞", stocks, news_dir, today, max_per_stock, TW_CREDIBLE_SOURCES, translate=False)
    print("翻譯美股個股新聞標題...")
    us_body, us_total = build_news_section("美股個股新聞", us_stocks, us_news_dir, today, max_per_stock, US_CREDIBLE_SOURCES, translate=True)

    body = (
        f"股票新聞日報 - {today}\n\n{price_warning}"
        f"{tw_market_body}\n{us_market_body}\n{pocket_body}\n{tw_body}\n{us_body}"
    )
    subject = (
        f"股票新聞日報 {today}（大盤 {tw_market_total + us_market_total} 則 / "
        f"口袋證券 {pocket_total} 則 / 台股 {tw_total} 則 / 美股 {us_total} 則）"
    )

    print(
        f"寄送對象：{email_to}，"
        f"大盤 {tw_market_total + us_market_total} 則、口袋證券 {pocket_total} 則、"
        f"台股 {tw_total} 則、美股 {us_total} 則"
    )
    send_via_mail_app(email_from, email_to, subject, body)
    print("已寄出。")


if __name__ == "__main__":
    main()

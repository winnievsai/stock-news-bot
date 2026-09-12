#!/usr/bin/env python3
"""
每日待辦提醒信（Resend 版）
================================
用途：讀 reminders.csv（Winnie 手機上「叮嚀清單」Artifact 資料庫的本機備份，
     每天由 Claude 排程任務先把 Artifact 資料庫內容寫進這個檔案、commit/push 回
     repo，再呼叫這支腳本），把還沒完成的提醒整理成一封口吻自然、像秘書寫的信寄出。
     跟 send_news_email_resend.py 用一樣的模式：requests 直接打 Resend HTTP API，
     不依賴 macOS 的「郵件」App。

     注意：這支腳本本身不會去讀 Artifact 資料庫（一般 script 無法呼叫 Claude 的
     Artifact 工具），它只負責「讀 reminders.csv → 組信 → 寄信」，資料同步這一步
     是排程任務的前置步驟，不在這支腳本裡。

使用方式：
    python3 send_todo_email_resend.py

設定（跟其他腳本共用同一份 .env.local，或用環境變數注入）：
    RESEND_API_KEY=你的Resend API Key      (必填)
    EMAIL_FROM=onboarding@resend.dev        (選填，預設 Resend 測試寄件地址)
    EMAIL_TO=收件人地址                      (必填；多人用逗號分隔)
    REMINDERS_CSV=reminders.csv             (選填，預設 ./reminders.csv)
"""

import csv
import os
import sys
from datetime import date, datetime
from pathlib import Path

import requests

DEFAULT_EMAIL_FROM = "onboarding@resend.dev"
SCRIPT_DIR = Path(__file__).resolve().parent

WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]


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
            "\n請參考 send_todo_email_resend.py 開頭的說明填入。"
        )

    email_from = env.get("EMAIL_FROM", DEFAULT_EMAIL_FROM).strip()
    reminders_csv = SCRIPT_DIR / env.get("REMINDERS_CSV", "reminders.csv").strip()

    return resend_api_key, email_from, email_to, reminders_csv


def load_open_reminders(csv_path: Path):
    """讀 reminders.csv，回傳尚未完成的提醒列表（依建立時間由舊到新）"""
    if not csv_path.exists():
        return []
    rows = []
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("是否完成") or "").strip() not in ("是", "true", "True", "1"):
                rows.append(row)
    rows.sort(key=lambda r: (r.get("建立時間") or ""))
    return rows


def fmt_created(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return f"{dt.month}/{dt.day}"
    except ValueError:
        return value[:10]


def build_email(open_rows: list, today: date) -> tuple:
    weekday = WEEKDAY_ZH[today.weekday()]
    date_label = f"{today.month}月{today.day}日（週{weekday}）"

    if not open_rows:
        subject = f"今天沒有待辦事項 {today.isoformat()}"
        body = (
            f"早安，{date_label}。\n\n"
            "叮嚀清單上目前沒有還沒辦的事，難得可以喘口氣～\n"
            "想到什麼隨時打開手機記一筆，我會繼續幫你留意的。\n"
        )
        return subject, body

    lines_by_category = {}
    for row in open_rows:
        cat = (row.get("分類") or "其他").strip() or "其他"
        lines_by_category.setdefault(cat, []).append(row)

    body_parts = [f"早安，{date_label}，來看看今天還有哪些事要記得辦：\n"]
    for cat, rows in lines_by_category.items():
        body_parts.append(f"【{cat}】")
        for row in rows:
            text = (row.get("內容") or "").strip()
            created = fmt_created(row.get("建立時間"))
            suffix = f"（{created} 記的）" if created else ""
            body_parts.append(f"　・{text}{suffix}")
        body_parts.append("")

    body_parts.append(f"一共 {len(open_rows)} 件事，一件一件來就好，辛苦了！")
    body_parts.append("辦完的話，去叮嚀清單上打勾就會從這封信裡消失囉。")

    subject = f"今天要記得辦的 {len(open_rows)} 件事 {today.isoformat()}"
    body = "\n".join(body_parts) + "\n"
    return subject, body


def send_via_resend(api_key: str, email_from: str, email_to_list: list, subject: str, body: str):
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"from": email_from, "to": email_to_list, "subject": subject, "text": body},
        timeout=30,
    )
    if resp.status_code >= 300:
        sys.exit(f"[錯誤] Resend 寄信失敗（{resp.status_code}）：{resp.text[:300]}")


def main():
    resend_api_key, email_from, email_to, reminders_csv = load_config()
    open_rows = load_open_reminders(reminders_csv)
    subject, body = build_email(open_rows, date.today())

    email_to_list = [addr.strip() for addr in email_to.split(",") if addr.strip()]
    print(f"寄送對象：{', '.join(email_to_list)}，未完成提醒 {len(open_rows)} 筆")
    send_via_resend(resend_api_key, email_from, email_to_list, subject, body)
    print("已寄出。")


if __name__ == "__main__":
    main()

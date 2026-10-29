"""
iNDIEVOX 釋票監控 — 美秀集團《等無》巡迴 2026
有場次從「售完」變成「可購買」時，推播到手機（ntfy）＋電腦跳通知/響鈴。

安裝：  pip install requests beautifulsoup4
執行：  python indievox_watch.py            （監控全部場次）
        python indievox_watch.py 高雄        （只監控名稱/日期含「高雄」的場次）
        python indievox_watch.py --debug     （存下網頁 HTML，判斷有誤時用來檢查）

手機通知：App Store / Google Play 安裝「ntfy」→ 訂閱下面 NTFY_TOPIC 的名稱即可。
"""

import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

# ===== 設定 =====
EVENT_ID = "26_iv04280c7"
GAME_URL = f"https://www.indievox.com/activity/game/{EVENT_ID}"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC") or "andrew-mayhsiu-tix-7391"  # 雲端版從 GitHub Secret 讀取
INTERVAL = 60                            # 每幾秒檢查一次（別調太低，避免被擋）
TELEGRAM_TOKEN = ""                      # 選填：Telegram bot token
TELEGRAM_CHAT_ID = ""                    # 選填：你的 chat id
MAX_MINUTES = int(os.environ.get("MAX_MINUTES", "0"))  # 雲端版：跑幾分鐘後結束（0 = 不限）
IN_CLOUD = os.environ.get("GITHUB_ACTIONS") == "true"
# ================

SOLD_OUT = ["已售完", "售完", "選購一空", "sold out", "完售"]
NOT_YET = ["尚未開賣", "即將開賣", "coming soon"]
BUYABLE = ["立即購票", "立即訂購", "購票", "buy now", "選購"]

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept-Language": "zh-TW,zh;q=0.9",
}


TW = timezone(timedelta(hours=8))


def log(msg):
    print(f"[{datetime.now(TW):%m/%d %H:%M:%S}] {msg}", flush=True)


def classify(text):
    t = text.lower()
    if any(k.lower() in t for k in SOLD_OUT):
        return "售完"
    if any(k.lower() in t for k in NOT_YET):
        return "未開賣"
    if any(k.lower() in t for k in BUYABLE):
        return "可購買"
    return "未知"


def fetch_sessions(session, debug=False):
    r = session.get(GAME_URL, headers=HEADERS, timeout=15)
    r.raise_for_status()
    if debug:
        with open("indievox_debug.html", "w", encoding="utf-8") as f:
            f.write(r.text)
        log("已存 indievox_debug.html")

    soup = BeautifulSoup(r.text, "html.parser")
    results = {}
    rows = soup.select("table tr")
    for row in rows:
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        row_text = " ".join(cells)
        if not re.search(r"\d{4}/\d{1,2}/\d{1,2}", row_text):
            continue  # 跳過表頭
        name = " | ".join(cells[:-1])
        # 最後一欄通常是按鈕/狀態；連同按鈕文字一起判斷
        status_text = cells[-1] + " " + " ".join(
            b.get_text(" ", strip=True) for b in row.select("button, a, input"))
        status = classify(status_text)
        link = None
        btn = row.select_one("[data-href], a[href*='/ticket/']")
        if btn:
            link = btn.get("data-href") or btn.get("href")
            if link and link.startswith("/"):
                link = "https://www.indievox.com" + link
        results[name] = (status, link)

    if not results:  # 找不到表格時退回整頁判斷
        status = classify(soup.get_text(" ", strip=True))
        results["整頁"] = (status, None)
    return results


def notify(title, body, url=None):
    log(f"🔔 {title} — {body}")
    try:
        headers = {"Title": title.encode("utf-8"), "Priority": "urgent", "Tags": "ticket"}
        if url:
            headers["Click"] = url
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=body.encode("utf-8"),
                      headers=headers, timeout=10)
    except Exception as e:
        log(f"ntfy 失敗：{e}")
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          data={"chat_id": TELEGRAM_CHAT_ID,
                                "text": f"{title}\n{body}\n{url or GAME_URL}"}, timeout=10)
        except Exception as e:
            log(f"Telegram 失敗：{e}")
    # 電腦端：響鈴 + 跳通知
    if IN_CLOUD:
        return
    try:
        if sys.platform == "win32":
            import winsound
            for _ in range(5):
                winsound.Beep(1500, 300)
        else:
            print("\a" * 5, end="", flush=True)
            if sys.platform == "darwin":
                import subprocess
                subprocess.run(["osascript", "-e",
                                f'display notification "{body}" with title "{title}" sound name "Glass"'])
    except Exception:
        pass


def main():
    args = sys.argv[1:]
    debug = "--debug" in args
    keywords = [a for a in args if not a.startswith("--")]
    if os.environ.get("KEYWORDS"):
        keywords += [k for k in os.environ["KEYWORDS"].split(",") if k]
    start = time.time()
    log(f"開始監控 {GAME_URL}  每 {INTERVAL} 秒  篩選：{keywords or '全部'}")
    if not IN_CLOUD or os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        notify("監控已啟動", "收到這則代表手機通知設定成功")

    s = requests.Session()
    last = {}
    errors = 0
    while True:
        if MAX_MINUTES and time.time() - start > MAX_MINUTES * 60:
            log("本輪時間到，結束（下一輪會自動接手）")
            return
        try:
            sessions = fetch_sessions(s, debug)
            debug = False
            errors = 0
            for name, (status, link) in sessions.items():
                if keywords and not any(k in name for k in keywords):
                    continue
                prev = last.get(name)
                if prev != status:
                    log(f"{name}：{prev or '—'} → {status}")
                    if status == "可購買" and prev is not None:
                        notify("🎫 美秀有票了！", name, link or GAME_URL)
                    elif status == "可購買" and prev is None:
                        notify("🎫 目前可購買", name, link or GAME_URL)
                last[name] = status
            summary = "、".join(f"{v}" for v in last.values())
            log(f"✓ 已檢查（{summary}）")
        except Exception as e:
            errors += 1
            log(f"錯誤：{e}")
            if errors == 10:
                notify("監控出問題", f"連續 10 次失敗：{e}")
        time.sleep(INTERVAL + random.uniform(0, 15))


if __name__ == "__main__":
    main()

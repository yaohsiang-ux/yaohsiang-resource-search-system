#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""臺北市A單位搜尋系統 — 資料自動更新腳本（GitHub Actions 每週執行）

用法:
  python3 scripts/update_data.py sync    # 同步 C級巷弄站 Google Sheet → data/c_stations.json
  python3 scripts/update_data.py watch   # 官方來源異動偵測 → 更新 data/source_hashes.json
                                         #   異動清單寫到 stdout（workflow 據此開 Issue）

sync：改由 Actions 伺服器端抓 Google Sheet CSV，前端讀同源 JSON，
      不再依賴 corsproxy.io 第三方代理。
watch：只偵測、不自動改寫內嵌資料（政府 PDF 版面會變，自動解析有風險），
       發現異動就開 GitHub Issue 提醒人工（或交給 Claude）更新。
"""
import csv
import hashlib
import io
import json
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UA = {"User-Agent": "Mozilla/5.0 (compatible; yaohsiang-data-bot)"}
TPE = timezone(timedelta(hours=8))

SHEET_CSV = ("https://docs.google.com/spreadsheets/d/e/"
             "2PACX-1vTNoURt70rAaqHngG0-EQj7jD55_-4f4gUYG3rODy143ld4nx-jDjrcvfujCN7yW39azoBKLpKduzgv"
             "/pub?gid=0&single=true&output=csv")

# 監看的官方來源：id → (名稱, URL, 內容萃取函式名)
SOURCES = {
    "aunit_pdf": ("衛生局 A單位清冊 PDF", None, "aunit"),          # URL 動態從專區頁抓
    "dosw_subsidy": ("社會局 失能者自辦補助頁附件",
                     "https://dosw.gov.taipei/cp.aspx?n=743FC7866A1A3F3C&s=A9D96D0F8AAAC378",
                     "attachments"),
    "dosw_vendors": ("社會局 特約門市名單頁附件",
                     "https://dosw.gov.taipei/cp.aspx?n=457FA2416BF17247&s=74E8961109D68F2E",
                     "attachments"),
    "dementia_roster": ("失智服務網 布建清單頁附件",
                        "https://dementia.gov.taipei/Content_List.aspx?n=A7DEE678596F57AE",
                        "attachments"),
    "ltc_law": ("長期照顧服務申請及給付辦法（修正日期）",
                "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=L0070059",
                "law_date"),
}

HEALTH_LIST_PAGE = "https://health.gov.taipei/News.aspx?n=3B14F55B09E96685&sms=8F0619542D0F4F55"


def fetch(url, binary=False, timeout=60, retries=3):
    # 用 curl 而非 urllib：臺北市政府網站憑證鏈缺 SKI 欄位，
    # Python 3.x 的 OpenSSL 嚴格驗證會拒絕，curl 的鏈建構較寬容。
    # 政府主機偶發 TLS handshake 失敗（exit 35），重試即可。
    last = None
    for i in range(retries):
        try:
            body = subprocess.run(
                ["curl", "-sL", "--max-time", str(timeout), "-A", UA["User-Agent"], url],
                capture_output=True, check=True).stdout
            if body:
                return body if binary else body.decode("utf-8", "ignore")
            last = RuntimeError(f"empty response: {url}")
        except subprocess.CalledProcessError as e:
            last = e
        import time
        time.sleep(3 * (i + 1))
    raise last


def sync_c_stations():
    """Google Sheet CSV → data/c_stations.json（欄位對齊前端 loadGoogleSheetData）"""
    text = fetch(SHEET_CSV)
    rows = list(csv.reader(io.StringIO(text)))[1:]  # 去標題列
    stations = []
    for cols in rows:
        if len(cols) < 3 or not cols[0].strip():
            continue
        stations.append({
            "name": cols[0].strip(),
            "district": cols[1].strip(),
            "address": cols[2].strip(),
            "phone": cols[3].strip() if len(cols) > 3 else "",
            "note": cols[4].strip() if len(cols) > 4 else "",
        })
    if len(stations) < 50:  # 安全網：抓到異常少代表來源壞掉，不覆寫
        print(f"::error::C級據點僅 {len(stations)} 筆（預期 200+），不更新", file=sys.stderr)
        sys.exit(1)
    out = {
        "updated": datetime.now(TPE).strftime("%Y-%m-%d %H:%M"),
        "source": "臺北市 C級巷弄長照站 Google Sheet（每週自動同步）",
        "count": len(stations),
        "stations": stations,
    }
    DATA.mkdir(exist_ok=True)
    path = DATA / "c_stations.json"
    new = json.dumps(out, ensure_ascii=False, indent=1)
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    # 只比對 stations 內容，避免每週只有時間戳變動也 commit
    def core(s):
        try:
            return json.dumps(json.loads(s).get("stations"), ensure_ascii=False)
        except Exception:
            return ""
    if core(new) != core(old):
        path.write_text(new, encoding="utf-8")
        print(f"c_stations.json 已更新：{len(stations)} 筆")
    else:
        print(f"c_stations.json 無變動（{len(stations)} 筆）")


def extract_attachments(html):
    """萃取頁面上 Download.ashx 附件的檔名清單（穩定訊號，避免整頁 hash 誤報）"""
    import base64
    import urllib.parse as up
    names = []
    for m in re.finditer(r'href="(https://www-ws\.gov\.taipei/Download\.ashx[^"]+)"', html):
        q = up.parse_qs(up.urlparse(m.group(1).replace("&amp;", "&")).query)
        try:
            names.append(base64.b64decode(up.unquote(q["n"][0])).decode("utf-8"))
        except Exception:
            names.append(m.group(1)[:80])
    return "\n".join(sorted(set(names)))


def extract_law_date(html):
    # law.moj 的「修正日期：」與日期在不同 HTML 元素，跨元素搜尋
    i = html.find("修正日期")
    if i >= 0:
        m = re.search(r"民國\s*\d+\s*年\s*\d+\s*月\s*\d+\s*日", html[i:i + 500])
        if m:
            return "修正日期 " + re.sub(r"\s+", "", m.group(0))
    return "PARSE_FAIL"


def aunit_signal():
    """衛生局專區頁 → A單位清冊 PDF 內容 hash（連結網址會換，故 hash 檔案內容）"""
    page = fetch(HEALTH_LIST_PAGE)
    url = None
    for m in re.finditer(r'href="(https://www-ws\.gov\.taipei/Download\.ashx[^"]+)"', page):
        seg = page[m.start():m.start() + 500]
        if "A單位" in seg or "整合型" in seg:
            url = m.group(1).replace("&amp;", "&")
            break
    if not url:
        return "LINK_NOT_FOUND"
    pdf = fetch(url, binary=True)
    return hashlib.sha256(pdf).hexdigest()


def watch():
    DATA.mkdir(exist_ok=True)
    state_path = DATA / "source_hashes.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    first_run = not state
    changes = []
    for key, (label, url, mode) in SOURCES.items():
        try:
            if mode == "aunit":
                signal = aunit_signal()
            elif mode == "attachments":
                signal = extract_attachments(fetch(url))
            elif mode == "law_date":
                signal = extract_law_date(fetch(url))
            digest = hashlib.sha256(signal.encode("utf-8")).hexdigest()
            detail = signal if mode == "law_date" else ""
        except Exception as e:  # 來源掛掉不中斷其他來源
            print(f"::warning::{label} 抓取失敗: {e}", file=sys.stderr)
            continue
        prev = state.get(key, {}).get("hash")
        state[key] = {"hash": digest, "checked": datetime.now(TPE).strftime("%Y-%m-%d"),
                      **({"detail": detail} if detail else {})}
        if prev and prev != digest:
            changes.append(f"- **{label}** 內容有異動{f'（{detail}）' if detail else ''}")
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    if first_run:
        print("首次執行：已建立基準 hash，不通報")
    elif changes:
        print("CHANGES_DETECTED")
        print("\n".join(changes))
    else:
        print("所有監看來源無異動")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "sync":
        sync_c_stations()
    elif cmd == "watch":
        watch()
    else:
        print(__doc__)
        sys.exit(1)

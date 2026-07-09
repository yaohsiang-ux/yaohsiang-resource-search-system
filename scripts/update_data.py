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
    "ei_products": ("智慧科技輔具通過產品清單（衛福部公告附件）",
                    "https://www.mohw.gov.tw/cp-16-87062-1.html",
                    "ei_pdf"),
    "daycare_roster": ("社會局 日照及小規機一覽表頁附件",
                       "https://dosw.gov.taipei/cp.aspx?n=3E3C1D86A51BF473&s=B72AFFE457F98DE1",
                       "attachments"),
    "elder_roster": ("社會局 老人福利機構名冊頁附件",
                     "https://dosw.gov.taipei/News_Content.aspx?n=43A90D45AB7831E2&s=BAABA2454E899F9C",
                     "attachments"),
    "residential_roster": ("衛生局 住宿式長照機構一覽表頁附件",
                           "https://health.gov.taipei/News.aspx?n=E139461B864ECC75&sms=5F83E3615F00C15F",
                           "attachments"),
    "nursing_roster": ("衛生局 一般護理之家一覽表頁附件",
                       "https://health.gov.taipei/News_Content.aspx?n=B283D71AA0A7D98A&sms=EDD21D8B4B037BC3&s=3B9E85AB87282057",
                       "attachments"),
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


LTCAT_API = ("https://ltcat.mohw.gov.tw/Public/Products?handler=List"
             "&pageIndex={page}&pageSize=20&keyword=&sortField=updatedAt&sortDir=desc")


def sync_ei_products():
    """LTCAT 官方平台 JSON API → data/ei_products.json（智慧科技輔具通過產品，滾動更新）
    注意：pageIndex 是 0-based；pageSize 上限 20。"""
    items, page = [], 0
    while True:
        d = json.loads(fetch(LTCAT_API.format(page=page)))
        chunk = d["data"]["items"]
        items.extend(chunk)
        if len(items) >= d["data"]["totalCount"] or not chunk:
            break
        page += 1
    if len(items) < 5:  # 安全網：API 異常時不覆寫
        print(f"::error::LTCAT 僅回 {len(items)} 筆（預期 15+），不更新", file=sys.stderr)
        return
    products = []
    for it in items:
        codes = re.findall(r"EI\d{2}", it.get("subsidyCodeText") or "")
        vendor = re.sub(r"(股份有限公司|有限公司)$", "", (it.get("companyName") or "").strip())
        price = it.get("suggestedPriceTwd")
        products.append({
            "name": (it.get("productName") or "").strip(),
            "model": (it.get("productModel") or "").replace('"', "").strip(),
            "vendor": vendor,
            "codes": codes or ["EI05"],
            **({"price": int(price)} if it.get("isPricePublic") and price else {}),
        })
    products.sort(key=lambda p: (p["codes"][0], p["name"]))
    out = {
        "updated": datetime.now(TPE).strftime("%Y-%m-%d") + "（LTCAT 平台自動同步）",
        "source": "https://ltcat.mohw.gov.tw/Public/Products",
        "count": len(products),
        "items": products,
    }
    path = DATA / "ei_products.json"
    new = json.dumps(out, ensure_ascii=False, indent=1)

    def core(s):
        try:
            return json.dumps(json.loads(s).get("items"), ensure_ascii=False)
        except Exception:
            return ""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if core(new) != core(old):
        path.write_text(new, encoding="utf-8")
        print(f"ei_products.json 已更新：{len(products)} 筆")
    else:
        print(f"ei_products.json 無變動（{len(products)} 筆）")


WORKSHOP_API = ("https://data.taipei/api/v1/dataset/"
                "4a271c2b-a47a-4765-8440-f600fc0cb1c4?scope=resourceAquire&limit=200")
XZS_PAGE = "https://dosw.gov.taipei/cp.aspx?n=8AF55BABC5D4423D"


def _district(addr):
    m = re.search(r"[臺台]北市(\w{1,3}區)", addr or "")
    return m.group(1) if m else ""


def _save_facility_json(fname, label, items, min_count):
    if len(items) < min_count:
        print(f"::error::{label} 僅 {len(items)} 筆（預期 {min_count}+），不更新", file=sys.stderr)
        return
    out = {"updated": datetime.now(TPE).strftime("%Y-%m-%d"), "count": len(items), "items": items}
    path = DATA / fname
    new = json.dumps(out, ensure_ascii=False, indent=1)

    def core(s):
        try:
            return json.dumps(json.loads(s).get("items"), ensure_ascii=False)
        except Exception:
            return ""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if core(new) != core(old):
        path.write_text(new, encoding="utf-8")
        print(f"{fname} 已更新：{len(items)} 筆")
    else:
        print(f"{fname} 無變動（{len(items)} 筆）")


NURSING_CSV = ("https://data.taipei/api/dataset/b20c5ea7-dcae-446b-8d85-574a2bb2c907"
               "/resource/ed438da1-9e3c-4ccd-86be-6eeb4b275259/download")
ELDER_API = ("https://data.taipei/api/v1/dataset/"
             "2649f023-26ce-483a-a6f9-d7854522bcfd?scope=resourceAquire&limit=200")
SMALLMULTI_API = ("https://data.taipei/api/v1/dataset/"
                  "2ab15ace-a058-4170-8da2-ecfe5707e926?scope=resourceAquire&limit=200")


def _phone02(p):
    p = (p or "").strip()
    return ("(02)" + p) if p and not p.startswith("0") and not p.startswith("(") else p


def sync_elder_facilities():
    """臺北市老人福利機構名冊（data.taipei API，社會局年度更新）→ data/elder.json"""
    d = json.loads(fetch(ELDER_API))
    items = []
    for r in d["result"]["results"]:
        beds = (r.get("核定總床位數量") or "").strip()
        prop = (r.get("屬性") or "").strip()
        target = re.sub(r"\s+", "", r.get("收容對象") or "")
        items.append({
            "name": (r.get("機構名稱") or "").strip(),
            "category": "長照", "subCategory": "老人福利機構",
            "district": (r.get("區域別") or "").strip() or _district(r.get("地址")),
            "address": (r.get("地址") or "").strip(),
            "phone": _phone02(r.get("電話")),
            "capacity": (beds + "床") if beds else "",
            "note": "；".join(x for x in [prop, target] if x),
        })
    _save_facility_json("elder.json", "老人福利機構", items, 60)


def sync_small_multi():
    """臺北市小規模多機能（data.taipei API，社會局）→ data/small_multi.json"""
    d = json.loads(fetch(SMALLMULTI_API))
    items = []
    for r in d["result"]["results"]:
        items.append({
            "name": (r.get("機構名稱") or "").strip(),
            "category": "長照", "subCategory": "小規模多機能",
            "district": _district(r.get("地址")),
            "address": (r.get("地址") or "").strip(),
            "phone": _phone02(r.get("電話")),
            "capacity": "小規機",
            "note": (r.get("評鑑結果") or "").strip(),
        })
    _save_facility_json("small_multi.json", "小規模多機能", items, 10)


def sync_nursing_homes():
    """臺北市一般護理之家（data.gov.tw 132458 → data.taipei CSV，衛生局）→ data/nursing_homes.json"""
    text = fetch(NURSING_CSV)
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        print("::warning::護理之家 CSV 空，跳過", file=sys.stderr)
        return
    hdr = [h.strip().lstrip("﻿") for h in rows[0]]
    idx = {name: hdr.index(name) for name in ["機構名稱", "開放床數", "地址", "電話", "分機"] if name in hdr}
    items = []
    for r in rows[1:]:
        if len(r) <= idx.get("機構名稱", 99) or not r[idx["機構名稱"]].strip():
            continue
        addr = re.sub(r"\s+", "", r[idx["地址"]]) if "地址" in idx else ""
        phone = r[idx["電話"]].strip() if "電話" in idx else ""
        ext = r[idx["分機"]].strip() if "分機" in idx and len(r) > idx["分機"] else ""
        beds = r[idx["開放床數"]].strip() if "開放床數" in idx else ""
        items.append({
            "name": r[idx["機構名稱"]].strip(),
            "category": "長照", "subCategory": "護理之家",
            "district": _district(addr), "address": addr,
            "phone": phone + ("#" + ext if ext else ""),
            "capacity": (beds + "床") if beds else "",
            "note": "",
        })
    _save_facility_json("nursing_homes.json", "護理之家", items, 12)


LTC_MAP_LTC = "https://ltcpap.mohw.gov.tw/public/csv/ltc.csv"    # 機構層級
LTC_MAP_ALL = "https://ltcpap.mohw.gov.tw/public/csv/all.csv"    # 特約服務項目層級
TPE_CITY = "63000"  # 臺北市縣市代碼
# 臺北市行政區代碼對照（衛福部長照地圖鄉鎮市區欄）
TPE_DIST = {"63000010": "松山區", "63000020": "信義區", "63000030": "大安區",
            "63000040": "中山區", "63000050": "中正區", "63000060": "大同區",
            "63000070": "萬華區", "63000080": "文山區", "63000090": "南港區",
            "63000100": "內湖區", "63000110": "士林區", "63000120": "北投區"}


def _read_csv(url, timeout=180):
    # 衛福部長照地圖 all.csv 較大（3萬列），且境外 runner 連線較慢，給長 timeout
    text = fetch(url, timeout=timeout).lstrip("﻿")
    return list(csv.DictReader(io.StringIO(text)))


# 住宿式/日照：主源用臺北市政府 PDF（GitHub runner 連得上 www-ws），
# 備援用衛福部長照地圖 CSV（資料最全每日更新，但境外 runner 連不上→僅本機/台灣IP有效）
RESIDENTIAL_PAGE = "https://health.gov.taipei/News.aspx?n=E139461B864ECC75&sms=5F83E3615F00C15F"
DAYCARE_PAGE = "https://dosw.gov.taipei/cp.aspx?n=3E3C1D86A51BF473&s=B72AFFE457F98DE1"


def _taipei_pdf_url(page_url, keyword):
    """從臺北市政府頁面抓含 keyword 的最新 PDF 下載連結（連結會換版）"""
    import base64
    import urllib.parse as up
    page = fetch(page_url)
    for m in re.finditer(r'href="(https://www-ws\.gov\.taipei/Download\.ashx[^"]+)"', page):
        u = m.group(1).replace("&amp;", "&")
        q = up.parse_qs(up.urlparse(u).query)
        try:
            n = base64.b64decode(up.unquote(q["n"][0])).decode("utf-8")
        except Exception:
            n = ""
        if keyword in n and n.lower().endswith(".pdf"):
            return u
    return None


def _fetch_pdf_tmp(url):
    import tempfile
    data = fetch(url, binary=True, timeout=120)
    f = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    f.write(data)
    f.close()
    return f.name


def _sq(s):
    return re.sub(r"\s+", "", s or "")


def _parse_residential_pdf(path):
    """臺北市衛生局住宿式長照機構一覽表 PDF（標準表格）→ items。地址=倒2欄、電話=末欄。"""
    import pdfplumber
    items = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for tbl in page.extract_tables():
                for row in tbl:
                    c = [x or "" for x in row]
                    if len(c) < 6 or not c[0].strip().isdigit():
                        continue
                    beds = _sq(c[4])
                    items.append({
                        "name": _sq(c[3]), "category": "長照", "subCategory": "住宿式長照機構",
                        "district": _district(_sq(c[-2])), "address": _sq(c[-2]),
                        "phone": _sq(c[-1]),
                        "capacity": (beds + "床") if beds.isdigit() else (beds or "住宿式"),
                        "note": "",
                    })
    return items


def _parse_daycare_pdf(path):
    """臺北市社會局社區式長照機構一覽表 PDF（含服務類型欄）→ 只取日照、排除小規機與新北。"""
    import pdfplumber
    items = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for tbl in page.extract_tables():
                for row in tbl:
                    c = [x or "" for x in row]
                    if len(c) < 6 or not c[0].strip().isdigit():
                        continue
                    svc = _sq(c[3])
                    addr = _sq(c[4])
                    if svc not in ("日照", "日間照顧") or "北市" not in addr:  # 排除小規機/新北
                        continue
                    items.append({
                        "name": _sq(c[1]), "category": "長照", "subCategory": "長者日間照顧",
                        "district": _sq(c[2]) or _district(addr), "address": addr,
                        "phone": _phone02(_sq(c[5])), "capacity": "日照", "note": "",
                    })
    return items


def _mohw_residential():
    """備援：衛福部長照地圖 ltc.csv 種類3（臺北市住宿式，本機/台灣IP有效）"""
    items = []
    for r in _read_csv(LTC_MAP_LTC):
        if r.get("縣市") == TPE_CITY and r.get("機構種類") == "3":
            raw = (r.get("地址全址") or "").strip()
            dist = TPE_DIST.get((r.get("鄉鎮市區") or "").strip(), _district(raw))
            addr = raw if raw.startswith("臺北市") else f"臺北市{dist}{raw}"
            items.append({"name": (r.get("機構名稱") or "").strip(), "category": "長照",
                          "subCategory": "住宿式長照機構", "district": dist, "address": addr,
                          "phone": _phone02((r.get("機構電話") or "").strip()),
                          "capacity": "住宿式", "note": ""})
    return items


def _mohw_daycare():
    """備援：衛福部長照地圖 all.csv 日照特約（排除身障/小規機，本機/台灣IP有效）"""
    seen = {}
    for r in _read_csv(LTC_MAP_ALL):
        code = r.get("機構代碼") or ""
        if not code or not code[0].isdigit() or r.get("縣市") != TPE_CITY:
            continue
        if "日間照顧" not in (r.get("特約服務項目") or ""):
            continue
        name = (r.get("機構名稱") or "").strip()
        if "身障" in name or "身心障礙" in name or "小規模" in name:
            continue
        seen[code] = r
    items = []
    for r in seen.values():
        raw = (r.get("地址全址") or "").strip()
        dist = _district(raw) or TPE_DIST.get((r.get("區") or "").strip()) or ""
        addr = raw if raw.startswith("臺北市") else f"臺北市{dist}{raw}"
        items.append({"name": (r.get("機構名稱") or "").strip(), "category": "長照",
                      "subCategory": "長者日間照顧", "district": dist, "address": addr,
                      "phone": _phone02((r.get("機構電話") or "").strip()),
                      "capacity": "日照", "note": ""})
    return items


def sync_residential():
    """臺北市住宿式長照機構：臺北市衛生局 PDF 優先（runner 友善），衛福部 CSV 備援 → data/residential.json"""
    items = None
    try:
        url = _taipei_pdf_url(RESIDENTIAL_PAGE, "住宿")
        if url:
            items = _parse_residential_pdf(_fetch_pdf_tmp(url))
    except Exception as e:
        print(f"::warning::臺北市住宿式 PDF 解析失敗（{e}），改試衛福部 CSV", file=sys.stderr)
    if not items or len(items) < 8:
        items = _mohw_residential()
    _save_facility_json("residential.json", "住宿式長照機構", items, 8)


def sync_daycare():
    """臺北市長者日間照顧：臺北市社會局 PDF 優先（runner 友善），衛福部 CSV 備援 → data/daycare.json"""
    items = None
    try:
        url = _taipei_pdf_url(DAYCARE_PAGE, "社區式")
        if url:
            items = _parse_daycare_pdf(_fetch_pdf_tmp(url))
    except Exception as e:
        print(f"::warning::臺北市日照 PDF 解析失敗（{e}），改試衛福部 CSV", file=sys.stderr)
    if not items or len(items) < 50:
        items = _mohw_daycare()
    _save_facility_json("daycare.json", "長者日間照顧", items, 50)


def sync_sheltered_workshops():
    """臺北市庇護工場名冊（data.taipei 開放資料 API，勞動局年度更新）→ data/workshops.json"""
    d = json.loads(fetch(WORKSHOP_API))
    items = []
    for r in d["result"]["results"]:
        phone = (r.get("電話") or "").strip()
        items.append({
            "name": (r.get("工場名稱") or "").strip(),
            "category": "身障", "subCategory": "庇護工場",
            "district": _district(r.get("地址")),
            "address": (r.get("地址") or "").strip(),
            "phone": ("(02)" + phone) if phone and not phone.startswith("0") else phone,
            "capacity": "庇護工場",
            "note": (r.get("營業項目") or "").strip(),
        })
    _save_facility_json("workshops.json", "庇護工場", items, 30)


def sync_xiaozuosuo():
    """臺北市小作所（社區日間作業設施，社會局頁面表格）→ data/xiaozuosuo.json"""
    page = fetch(XZS_PAGE)
    items = []
    for row in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", page):
        import html as h
        cells = [h.unescape(re.sub(r"<[^>]+>", "", c)).replace("\xa0", " ").strip()
                 for c in re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", row)]
        # 資料列：[承辦單位, 設施名稱, 地址, 電話, 相關介紹]
        if len(cells) >= 4 and "北市" in cells[2] and "服務內容" not in cells[0]:
            phone = cells[3].strip()
            items.append({
                "name": cells[1], "category": "身障", "subCategory": "小作所",
                "district": _district(cells[2]), "address": cells[2],
                "phone": ("(02)" + phone) if phone and not phone.startswith("0") else phone,
                "capacity": "小作所",
                "note": re.sub(r"^財團法人|^社團法人", "", cells[0]),
            })
    _save_facility_json("xiaozuosuo.json", "小作所", items, 15)


VENDORS_PAGE = "https://dosw.gov.taipei/cp.aspx?n=457FA2416BF17247&s=74E8961109D68F2E"


def sync_vendors():
    """臺北市特約輔具門市（社會局 ODS 名單，只取臺北市）→ data/vendors.json
    groups 由官方 ● 欄位對應；items 品項標籤沿用 data/vendor_items_map.json（手工維護）。"""
    import base64
    import html as h
    import io
    import urllib.parse as up
    import zipfile
    from xml.etree import ElementTree as ET

    # 1. 從專區頁動態找 ODS 附件（重新上傳時網址會變）
    page = fetch(VENDORS_PAGE)
    ods_url = None
    for m in re.finditer(r'href="(https://www-ws\.gov\.taipei/Download\.ashx[^"]+)"', page):
        u = h.unescape(m.group(1))
        q = up.parse_qs(up.urlparse(u).query)
        try:
            name = base64.b64decode(up.unquote(q["n"][0])).decode("utf-8")
        except Exception:
            continue
        if name.endswith(".ods") and "門市名單" in name:
            ods_url = u
            break
    if not ods_url:
        print("::error::找不到門市名單 ODS 附件", file=sys.stderr)
        return

    # 2. 解析 ODS 主分頁（stdlib：zip + XML）
    TNS = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
    PNS = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p"
    z = zipfile.ZipFile(io.BytesIO(fetch(ods_url, binary=True)))
    root = ET.fromstring(z.read("content.xml"))
    table = next(t for t in root.iter(TNS + "table")
                 if "特約服務門市名單" in (t.get(TNS + "name") or ""))

    def row_cells(r):
        cells = []
        for c in r.iter(TNS + "table-cell"):
            rep = int(c.get(TNS + "number-columns-repeated", 1))
            paras = [" ".join("".join(p.itertext()).split()) for p in c.iter(PNS)]
            txt = "、".join(x for x in paras if x)
            if rep < 50:
                cells.extend([txt] * min(rep, 2))
        return cells

    rows = list(table.iter(TNS + "table-row"))
    # 表頭列：含「特約門市名稱」那一列，取得欄位索引
    header_idx = None
    for ri, r in enumerate(rows[:5]):
        cs = row_cells(r)
        if any("特約門市名稱" in c for c in cs):
            header_idx = ri
            header = cs
            break
    col = {}
    for i, cname in enumerate(header):
        for key, pat in [("seq", "序號"), ("name", "特約門市名稱"), ("phone", "連絡電話"),
                         ("addr", "特約門市地址"), ("city", "縣市別"), ("dist", "行政區"),
                         ("buy", "長照輔具(購買)"), ("rent", "長照輔具(租賃)"),
                         ("barrier", "居家無障礙"), ("dis", "身障輔具"), ("hearing", "助聽器")]:
            if pat in cname and key not in col:
                col[key] = i

    item_map = json.loads((DATA / "vendor_items_map.json").read_text(encoding="utf-8"))

    def norm(s):
        return re.sub(r"[\s（）()「」【】\-‐]", "", s or "")

    items_out, seq_taipei = [], 0
    need = max(col.values()) + 1
    for r in rows[header_idx + 1:]:
        cs = row_cells(r)
        cs += [""] * (need - len(cs))  # ODS 會省略列尾空儲存格
        if cs[col["city"]].strip() != "臺北市" or not cs[col["seq"]].strip().isdigit():
            continue
        name, addr = cs[col["name"]].strip(), cs[col["addr"]].strip()
        if not name:
            continue
        seq_taipei += 1
        groups = []
        if "●" in cs[col["buy"]]: groups.append("長照購置")
        if "●" in cs[col["rent"]]: groups.append("長照租賃")
        if "●" in cs[col["dis"]]: groups.append("身障購置")
        if "●" in cs[col["barrier"]]: groups.append("無障礙環境改善")
        items = list(item_map.get(norm(name)) or item_map.get(norm(addr)) or [])
        if "●" in cs[col["hearing"]] and "助聽器" not in items:
            items.append("助聽器")
        if "無障礙環境改善" in groups and "居家無障礙環境改善" not in items:
            items.append("居家無障礙環境改善")
        phone = cs[col["phone"]].strip()
        items_out.append({
            "id": f"A-{seq_taipei:03d}", "district": cs[col["dist"]].strip(),
            "name": name, "address": addr, "phone": phone,
            "groups": groups, "items": items, "source": "社會局特約名單",
        })
    _save_facility_json("vendors.json", "特約門市", items_out, 250)


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


def aunit_pdf_url():
    """衛生局專區頁 → A單位清冊 PDF 下載網址（連結會換版）"""
    page = fetch(HEALTH_LIST_PAGE)
    for m in re.finditer(r'href="(https://www-ws\.gov\.taipei/Download\.ashx[^"]+)"', page):
        seg = page[m.start():m.start() + 500]
        if "A單位" in seg or "整合型" in seg:
            return m.group(1).replace("&amp;", "&")
    return None


def aunit_signal():
    """A單位清冊 PDF 內容 hash（連結網址會換，故 hash 檔案內容）"""
    url = aunit_pdf_url()
    if not url:
        return "LINK_NOT_FOUND"
    return hashlib.sha256(fetch(url, binary=True)).hexdigest()


# A單位清冊 PDF 表格欄位右邊界（序號|單位名稱|行政區|服務區域里別|地址電話）
_AU_COLS = [37, 215, 249, 420, 580]


def _fmt_tel(raw):
    """PDF 電話 → 統一格式：加 (02) 前綴、分機空格。"""
    raw = raw.strip().replace("　", " ")
    if not raw:
        return ""
    # 多支電話以 / 或 、 分隔，逐一處理主碼
    raw = raw.replace("分機", " 分機").replace("轉", " 分機")
    # 開頭是 7-8 碼市話（無區碼）→ 補 (02)
    if re.match(r"^\d{4}-?\d{4}", raw) or re.match(r"^\d{7,8}(?!\d)", raw):
        raw = "(02)" + raw
    return re.sub(r"\s+", " ", raw).strip()


def parse_aunit_pdf(path):
    """A單位清冊 PDF → [{id,title,district,address,tel,area}]。字元級分欄，儲存格垂直置中。"""
    import pdfplumber
    C = _AU_COLS
    recs = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            chars = [c for c in page.chars if c["text"].strip()]
            seq_rows = {}
            for c in chars:
                if c["x1"] <= C[0] + 3 and c["text"].isdigit():
                    seq_rows.setdefault(round(c["top"]), []).append(c)
            seqs = []
            for top, cs in sorted(seq_rows.items()):
                cs.sort(key=lambda c: c["x0"])
                seqs.append((top, int("".join(c["text"] for c in cs))))
            if not seqs:
                continue
            tops = [t for t, _ in seqs]
            # 行政區合併儲存格：長橫線界定區塊，區名字塊標記歸屬
            seps = sorted(set(round(e["top"]) for e in page.horizontal_edges
                              if e["x0"] < C[1] + 1 and e["x1"] > C[2] - 1))
            dz = {}
            for c in chars:
                if C[1] < (c["x0"] + c["x1"]) / 2 <= C[2]:
                    dz.setdefault(round(c["top"] / 4), []).append(c)
            dblocks = []
            for _, cs in sorted(dz.items()):
                cs.sort(key=lambda c: c["x0"])
                txt = "".join(c["text"] for c in cs)
                if txt.endswith("區"):
                    dblocks.append((min(c["top"] for c in cs), txt))

            def district_of(st):
                for ctop, name in dblocks:
                    lo = max([s for s in seps if s <= ctop], default=-1e9)
                    hi = min([s for s in seps if s > ctop], default=1e9)
                    if lo <= st < hi:
                        return name
                return ""

            mids = [(tops[i] + tops[i + 1]) / 2 for i in range(len(tops) - 1)]
            # 首列下界 = 首序號上方最近的表格橫線（表頭下緣），避表頭又不切里別首行
            lo0 = max([s for s in seps if s < tops[0]], default=tops[0] - 12)
            los, his = [lo0] + mids, mids + [1e9]
            for i, (top, seq) in enumerate(seqs):
                lo, hi = los[i], his[i]

                def col(l, r):
                    cs = [c for c in chars if lo <= c["top"] < hi and l < (c["x0"] + c["x1"]) / 2 <= r]
                    cs.sort(key=lambda c: (round(c["top"] / 4), c["x0"]))
                    return "".join(c["text"] for c in cs)

                name = col(C[0], C[1])
                area = col(C[2], C[3])
                atc = [c for c in chars if lo <= c["top"] < hi and C[3] < (c["x0"] + c["x1"]) / 2 <= C[4]]
                rows = {}
                for c in atc:
                    rows.setdefault(round(c["top"] / 4), []).append(c)
                lines = []
                for _, cs in sorted(rows.items()):
                    cs.sort(key=lambda c: c["x0"])
                    lines.append("".join(c["text"] for c in cs))
                lines = [l for l in lines if "服務地址" not in l and "更新" not in l]
                addr = "、".join(l for l in lines if not re.match(r"^[\(0-9]", l))
                tel = " ".join(l for l in lines if re.match(r"^[\(0-9]", l))
                recs.append({"id": str(seq), "title": name, "district": district_of(top),
                             "address": addr.strip(), "tel": _fmt_tel(tel), "area": area.strip()})
    return recs


DISTRICTS_12 = {"大安區", "松山區", "文山區", "信義區", "內湖區", "南港區",
                "北投區", "士林區", "中山區", "大同區", "中正區", "萬華區"}


def validate_aunits(recs):
    """嚴格驗證關卡：回傳 (是否通過, 問題清單)。全自動只在通過時覆寫。"""
    problems = []
    if len(recs) < 60:
        problems.append(f"筆數過少 {len(recs)}")
    for r in recs:
        tag = f"seq{r['id']}"
        if not r["title"] or not r["area"] or not r["tel"]:
            problems.append(f"{tag} 欄位空缺")
        if r["district"] not in DISTRICTS_12:
            problems.append(f"{tag} 行政區異常({r['district']!r})")
        # 里別需含「里」且不含地址殘字（括號配對不檢查：官方原文本身即有巢狀/不配對括號）
        if "里" not in r["area"]:
            problems.append(f"{tag} 里別無里名")
        if re.search(r"\d+號|一覽表|單位名稱|服務區域", r["area"]):
            problems.append(f"{tag} 里別含表頭/地址殘字")
        if re.search(r"一覽表|單位名稱", r["title"]):
            problems.append(f"{tag} 名稱含表頭殘字")
    return (len(problems) == 0, problems)


def sync_a_units():
    """A單位清冊 PDF → data/a_units.json（全自動；未過驗證則不覆寫，交由 watch 開 Issue）"""
    import tempfile
    url = aunit_pdf_url()
    if not url:
        print("::warning::找不到 A單位清冊連結，跳過", file=sys.stderr)
        return
    pdf_bytes = fetch(url, binary=True)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp = f.name
    try:
        recs = parse_aunit_pdf(tmp)
    except Exception as e:
        print(f"::warning::A單位 PDF 解析失敗（{e}），不覆寫", file=sys.stderr)
        return
    ok, problems = validate_aunits(recs)
    if not ok:
        print(f"::warning::A單位解析未過驗證（{len(problems)} 項），不覆寫；問題：{problems[:5]}", file=sys.stderr)
        return
    out = {"updated": datetime.now(TPE).strftime("%Y-%m-%d") + "（衛生局清冊自動解析）",
           "source": HEALTH_LIST_PAGE, "count": len(recs), "items": recs}
    path = DATA / "a_units.json"
    new = json.dumps(out, ensure_ascii=False, indent=1)

    def core(s):
        try:
            return json.dumps(json.loads(s).get("items"), ensure_ascii=False)
        except Exception:
            return ""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if core(new) != core(old):
        path.write_text(new, encoding="utf-8")
        print(f"a_units.json 已更新：{len(recs)} 家")
    else:
        print(f"a_units.json 無變動（{len(recs)} 家）")


def ei_pdf_signal(page_url):
    """衛福部新聞稿頁 → 附件 PDF（智慧輔具通過產品清單）內容 hash"""
    page = fetch(page_url)
    m = re.search(r'href="(https://www\.mohw\.gov\.tw/dl-[^"]+)"', page)
    if not m:
        return "LINK_NOT_FOUND"
    return hashlib.sha256(fetch(m.group(1), binary=True)).hexdigest()


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
            elif mode == "ei_pdf":
                signal = ei_pdf_signal(url)
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
        # 每個 syncer 獨立容錯：單一來源掛掉不影響其他，不讓整個 job 失敗
        syncers = [sync_c_stations, sync_ei_products, sync_sheltered_workshops,
                   sync_xiaozuosuo, sync_vendors, sync_a_units, sync_nursing_homes,
                   sync_elder_facilities, sync_small_multi, sync_residential, sync_daycare]
        failed = []
        for fn in syncers:
            try:
                fn()
            except Exception as e:
                print(f"::warning::{fn.__name__} 失敗（保留舊資料）: {e}", file=sys.stderr)
                failed.append(fn.__name__)
        if failed:
            print(f"部分來源同步失敗（其餘正常）: {', '.join(failed)}")
        if len(failed) == len(syncers):  # 全掛才視為 job 失敗
            sys.exit(1)
    elif cmd == "watch":
        watch()
    else:
        print(__doc__)
        sys.exit(1)

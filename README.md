# 臺北市A單位搜尋系統｜燿翔

臺北市長照（A單位、輔具補助、特約廠商）＋身障（生活/醫療輔具補助）資源整合查詢。
線上網址：https://yaohsiang-ux.github.io/yaohsiang-resource-search-system/

## 資料版本（2026-07-04 更新）

| 資料集 | 版本 | 官方來源 |
|--------|------|---------|
| A單位名單（69家，含服務里別） | 衛生局 115/04/21 | 長照服務特約專區 |
| 長照輔具 E碼＋F碼（115年新制編碼） | 給付辦法 114/6/19 修正 | 失能者共同補助項目一覽表 |
| 長照3.0 雙軌說明（第二組智慧輔具） | 115/7/1 上路 | 行政院公報 031:113 |
| 北市自辦補助 TP01-18（18項） | 社會局 115 年計畫 | 失能者生活輔助器具自辦補助 |
| 身障生活輔具（242項） | 基準表 111/10/20 修正（112新制，現行） | 輔具費用補助辦法 |
| 身障醫療輔具（22項＋3費用） | 110/6/17 修正（現行） | 醫療輔具補助辦法 |
| C級巷弄站 | **每週一自動同步** | Google Sheet |

## 自動更新機制（.github/workflows/update-data.yml）

每週一 09:00（台北時間）自動執行：

1. **C級巷弄站同步**：伺服器端抓 Google Sheet → `data/c_stations.json` 直接 commit。
   前端優先讀同源 JSON（corsproxy.io 僅作備援）。
2. **官方來源異動偵測**：比對 5 個來源的內容 hash（A單位清冊 PDF、社會局自辦補助附件、
   特約門市名單附件、失智布建清單、給付辦法修正日期）。
   偵測到異動 → **自動開 GitHub Issue** 提醒更新（不自動改寫，政府 PDF 版面會變，
   自動解析有誤植風險）。收到 Issue 後把內容貼給 Claude Code 即可比對更新。

手動觸發：repo → Actions → 「資料自動更新」→ Run workflow。

## 維護

- 內嵌資料區塊在 `index.html`（aUnitData / ltcSubsidyData / disabilitySubsidyData /
  medicalSubsidyData / compareItems / resourcesData / generalResourcesData）。
- 更新單一資料塊：找到變數宣告、配對括號、整塊替換（或直接交給 Claude Code）。

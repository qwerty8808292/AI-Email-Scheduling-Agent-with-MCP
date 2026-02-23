# AI Email & Scheduling Agent with MCP

這是一個具備自主決策能力的 AI Agent，能協助忙碌的經理處理雜亂的收件匣，並透過標準化的數據協議管理會議預約。

## 專案架構

### 核心檔案

- `email_analyzer.py`
  - 讀取 `emails.json`
  - 使用 LLM 分析每封信件的類型、優先級、風險、會議資訊
  - 輸出 `email_analysis.json`（分析結果）

- `agent.py`
  - 載入 `email_analysis.json`
  - 依優先級排序後逐封處理
  - 透過 MCP 工具讀寫行事曆
  - 產生每封信的最終決策與回覆內容
  - 更新 `email_analysis.json`（加入 `decision` 欄位）

- `calendar_mcp.py`
  - 使用 `fastMCP` 封裝 `calendar.json`
  - 提供 MCP Tools（詳見 [MCP Server 說明](#mcp-server-說明)）：
    - `get_calendar_events`
    - `add_calendar_event`
    - `delete_calendar_events`
    - `backup_calendar`

### 資料檔案

- `emails.json`：測試郵件（13 封）
- `calendar.json`：目前行事曆（會被 Agent 更新）
- `calendar_backup1.json`：執行時建立的備份（每次執行會新增一個備份）
- `holiday.json`：國定假日資料
- `important_sender.json`：重要寄件人 / 網域清單
- `config.json`：模型、時區、工作時段、模擬日期設定

## 處理流程 (Workflow)

### 1. 郵件分析階段（`email_analyzer.py`）

- 讀取 `emails.json`
- 判斷是否為重要寄件人（網域 / email）
- 呼叫模型進行結構化分析（Pydantic schema）
- 產出 `analysis` 物件（分類、優先級、風險、會議資訊等），詳見 [分析輸出欄位定義（analysis）](#分析輸出欄位定義analysis)
- 將結果寫入 `email_analysis.json`

### 優先級評分規則（`priority`）

`priority` 由模型依郵件內容與寄件者條件判斷，採用「符合條件中的最高優先級」。
- `Priority 5`：急件且需回覆、48 小時內的重要會議取消/改期、24 小時內會議、重大金錢/合約/法律風險且需回覆、重要寄件人且回覆期限 ≤ 24 小時
- `Priority 4`：急件、48 小時內會議、回覆期限 ≤ 24 小時、重要寄件人且需回覆
- `Priority 3`：有明確會議時間（但非 48 小時內）、回覆期限 ≤ 48 小時、重要寄件人
- `Priority 2`：普通會議（無明確時間）、普通詢價、一般郵件、確認信、訂閱信件
- `Priority 1`：垃圾郵件

### 2. Agent 決策階段（`agent.py`）

- 讀取 `email_analysis.json`
- 依規則排序後逐封處理 (`sort_emails`)：
  - 先依 `priority` 由高到低 (5 → 1)
  - 同優先級時，會議意圖優先序為：`cancel` → `reschedule` → `new`（且有明確時間）→ 其他
  - 若仍同級，則依 `proposed_start` 較早者優先
- 啟動並連線 `calendar_mcp.py`
- 若為會議邀約：
  - 查詢行事曆 (`get_calendar_events`)
  - 檢查 `constraint flags`（作為是否接受/改期/提議替代時段的決策依據）
    - `has_conflict`：提議時段與既有行程重疊
    - `is_weekend`：提議時段落在週六或週日
    - `is_holiday`：提議時段日期落在 `holiday.json` 定義的假日（例如除夕）
    - `is_off_hours`：提議時段超出工作時間（依 `config.json` 的 `work_hours`）
    - `invalid_range`：開始時間晚於或等於結束時間（時間範圍無效）
  - 若上述 flags 有問題（如衝突 / 週末 / 假日），通常會改為提議替代時段 (`propose_alternative` / `propose_times`)
  - 生成決策（接受、改期、取消、提議替代時段）
  - 產出 `decision` 物件（action、reply、event 資訊、限制條件標記等），詳見 [Agent 決策輸出欄位定義（decision）](#agent-決策輸出欄位定義decision)
  - 必要時呼叫 `add_calendar_event` / `delete_calendar_events`

## Guardrails 與保護機制

- 雙層防護（未授權承諾風險）
  - 第一層：若 `analysis.has_risk = true`，強制將 `action` 改為 `escalate`
  - 第二層：完成決策後再對全部回覆內容做安全掃描，若偵測到未授權的金錢/合約/法律承諾，會再次改為 `escalate`
- 非會議信件禁止修改行事曆
  - 若信件不是「會議邀約」，即使模型回傳 `accept_and_add` / `reschedule` / `cancel`，程式也會攔截並改為 `ignore`
- 會議寫入前二次驗證
  - `check_constraints()`：程式端檢查函式，用來判斷時段是否有衝突、是否為週末/假日、是否超出工作時間、以及時間範圍是否有效
  - `accept_and_add` / `reschedule` 的 `confirmed_event` 需再次通過 `check_constraints()`，避免模型回傳不可行時段仍被寫入行事曆

## MCP Server 說明

本專案的 MCP Server 使用 `fastMCP` 實作，對 `calendar.json` 進行封裝。

### 提供的工具（Tools）

- `get_calendar_events`：讀取所有行程
- `add_calendar_event(start, end)`：新增行程
- `delete_calendar_events(start, end)`：依起訖時間刪除行程
- `backup_calendar()`：建立處理前備份（額外工具）

## 輸出欄位定義

### 分析輸出欄位定義（`analysis`）

- `email_id`：郵件 ID（對應 `emails.json` 的 `id`）
- `category`：郵件分類（`急件` / `一般` / `詢價` / `會議邀約` / `垃圾`）
- `priority`：優先級（1~5，數字越高越優先）
- `important_sender`：是否為重要寄件人（依網域或寄件者清單判斷）
- `needs_reply`：是否需要回覆
- `has_risk`：是否涉及未授權承諾風險（如金錢 / 合約 / 法律）
- `risk_types`：風險類型清單（`financial_commitment` / `contract_commitment` / `legal_commitment`）
- `meeting_intent`：會議意圖（`new` / `reschedule` / `cancel` / `fyi`；非會議可為 `null`）
- `time_specified`：信件是否提供明確會議時間
- `proposed_start`：模型解析出的提議開始時間（ISO 8601；無則 `null`）
- `proposed_end`：模型解析出的提議結束時間（ISO 8601；無則 `null`）
- `duration_minutes`：會議時長（分鐘；若無法判定可為 `null`）
- `event_information`：會議或任務的摘要資訊（例如議題、活動名稱）
- `reply_deadline`：回覆期限（ISO 8601；若信件未提及則 `null`）

### Agent 決策輸出欄位定義（`decision`）

- `action`：最終動作
  - `accept_and_add`：接受會議並新增到行事曆
  - `propose_alternative`：原提議時段不可行，改提 2~3 個替代時段
  - `propose_times`：對方未提供明確時間，主動提出 2~3 個可行時段
  - `reschedule`：接受改期，更新既有行程到新時段
  - `cancel`：接受取消，刪除既有行程
  - `reply`：可直接回覆信件
  - `reply_draft`：產生回覆草稿，需人工確認後送出
  - `escalate`：升級給人工處理（因風險或敏感承諾）
  - `ignore`：不需處理或不需回覆
- `reply`：回覆內容
- `decision_rationale`：決策理由
- `confirmed_event`：確認後要新增/更新的會議資訊（`title` / `start` / `end`；無則 `null`）
- `affected_event`：被改期或取消的原會議資訊（無則 `null`）
- `proposed_alternatives`：替代時段清單（2~3 個候選時段；無則 `null`）
- `constraints_applied`：實際觸發的限制條件


## 環境需求

- Python 3.10+
- Google GenAI API Key (Gemini)

## 安裝與執行
### 1. 安裝套件
```bash
pip install -r requirements.txt
```

### 2. 設定環境變數
建立 `.env`（已在 `.gitignore`）：

```bash
GOOGLE_API_KEY=your_api_key_here
```

### 3. 執行郵件分析

```bash
python email_analyzer.py
```

輸出：`email_analysis.json`（含 `analysis`）

### 4. 執行 Agent（含 MCP 行事曆操作）

```bash
python agent.py
```

輸出：
- `email_analysis.json`
- 更新後的 `calendar.json`
- 備份檔（例如 `calendar_backup1.json`）

## 未來可優化方向

- 尚未加入自動化測試（可補上單元測試 / 整合測試）
- 可在「郵件分析階段」平行處理多封郵件（一次跑多個 AI 請求）以縮短總處理時間

# AI Email & Scheduling Agent with MCP

這是一個具備自主決策能力的 AI Agent，能協助忙碌的經理處理雜亂的收件匣，並透過標準化的數據協議管理會議預約。

- 使用 `fastMCP` 將 `calendar.json` 封裝為 MCP Server，Agent 透過 stdio 自動連線與操作行事曆
- 使用 LLM + Pydantic schema 進行郵件分析（分類、優先級、風險、會議資訊）
- 支援會議衝突、週末、國定假日（含除夕）、工作時段等約束檢查
- 具 Guardrails：未授權承諾風險升級與回覆內容安全掃描

## 更新紀錄

### 2026-03-12：新增 `tests/` 自動化測試目錄
- 測試檔包含：
  - `tests/test_email_analyzer.py`：測試重要寄件人判斷與時間格式正規化邏輯
  - `tests/test_agent.py`：測試 `check_constraints()`、`sort_emails()` 與回覆安全掃描規則
  - `tests/test_process_email.py`：測試 `process_email()` 在接受會議、阻擋非會議修改、風險升級、草稿、衝突 fallback 等流程
  - `tests/test_calendar_mcp.py`：測試行事曆讀取、事件新增/刪除、重複時段攔截與備份檔建立行為
- 測試指令：

```bash
python -m unittest discover -s tests
```

## 目錄

- [專案架構](#專案架構)
- [處理流程](#處理流程)
- [Guardrails 與保護機制](#guardrails-與保護機制)
- [MCP Server 說明](#mcp-server-說明)
- [輸出欄位定義](#輸出欄位定義)
- [環境需求](#環境需求)
- [安裝與執行](#安裝與執行)
- [未來可優化方向](#未來可優化方向)

## 專案架構

### 核心檔案

| 檔案 | 角色 | 主要輸入 | 主要輸出 |
| --- | --- | --- | --- |
| `email_analyzer.py` | 郵件分析階段 | `emails.json`、`important_sender.json` | `email_analysis.json`（含 `analysis`） |
| `agent.py` | Agent 決策與排程執行 | `email_analysis.json`、`holiday.json`、MCP tools | 更新後 `email_analysis.json`（含 `decision`）、`calendar.json` |
| `calendar_mcp.py` | MCP Server（行事曆封裝） | `calendar.json` | MCP tools |

### 資料檔案

- `emails.json`：測試郵件（13 封）
- `calendar.json`：目前行事曆（會被 Agent 更新）
- `calendar_backup1.json`：執行時建立的備份（每次執行會新增一個備份）
- `holiday.json`：國定假日資料
- `important_sender.json`：重要寄件人 / 網域清單
- `config.json`：模型、時區、工作時段、模擬日期設定

## 處理流程

### 1. 郵件分析階段（`email_analyzer.py`）

- 讀取 `emails.json`
- 判斷是否為重要寄件人（網域 / email）
- 呼叫模型進行結構化分析（Pydantic schema）
- 若信件未提供完整時間（只給開始 or 結束，或未明確給出時段），LLM 會依信件內容推理開始時間 (`proposed_start`) 與結束時間 (`proposed_end`)
- 產出 `analysis` 物件（分類、優先級、風險、會議資訊等），詳見 [分析輸出欄位定義（analysis）](#analysis-fields)
- 將結果寫入 `email_analysis.json`

#### 優先級評分規則（`priority`）

`priority` 由模型依郵件內容與寄件者條件判斷，採用「符合條件中的最高優先級」。
- `Priority 5`：急件且需回覆、24 小時內會議、會議取消、會議改期、重大金錢/合約/法律風險且需回覆、重要寄件人且回覆期限 ≤ 24 小時
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
- 單封決策失敗時會記錄 error 並繼續處理下一封
- 若為會議邀約：
  - 查詢行事曆 (`get_calendar_events`)
  - 使用 `check_constraints()` 計算 `constraint flags`（根據提議時段 + 行事曆 + 假日 + 工作時段，作為是否接受/改期/提議替代時段的決策依據）
    - `has_conflict`：提議時段與既有行程重疊
    - `is_weekend`：提議時段落在週六或週日
    - `is_holiday`：提議時段日期落在 `holiday.json` 定義的假日（例如除夕）
    - `is_off_hours`：提議時段超出工作時間（依 `config.json` 的 `work_hours`）
    - `invalid_range`：開始時間晚於或等於結束時間（時間範圍無效）
  - 若上述 flags 有問題（如衝突 / 週末 / 假日），通常會改為提議替代時段 (`propose_alternative` / `propose_times`)
  - 生成決策（接受、改期、取消、提議替代時段）
  - 若需提議替代時段，會在 prompt 中注入已佔用時段、`holiday.json`，並納入 `pending_slots`（本次執行前面郵件已提議但未確認的時段）避免後續重複提議
  - 產出 `decision` 物件（action、reply、event 資訊、限制條件標記等），詳見 [Agent 決策輸出欄位定義（decision）](#decision-fields)
  - 若 `decision` 缺少必要欄位（例如 `accept_and_add` 沒有 `confirmed_event`），會降級為忽略 (`ignore`) 以避免錯誤寫入/刪除行事曆
  - 必要時呼叫 `add_calendar_event` / `delete_calendar_events`

## Guardrails 與保護機制

這部分是用來避免模型做出未授權承諾或錯誤修改行事曆。

- 雙層防護（未授權承諾風險）
  - 第一層：若風險欄位 `analysis.has_risk = true`，強制將動作 (`action`) 改為人工升級 (`escalate`)
  - 第二層：完成決策後再對全部回覆內容做安全掃描，若偵測到未授權的金錢/合約/法律承諾，會再次改為人工升級 (`escalate`)
  - 後處理：當動作為 `action = escalate` 時，會清空回覆內容 (`reply`)，避免誤送出自動回覆
- 非會議信件禁止修改行事曆
  - 若信件不是「會議邀約」，即使模型回傳接受並新增 (`accept_and_add`)、改期 (`reschedule`)、取消 (`cancel`)，程式也會攔截並改為 `ignore`
- 會議寫入前二次驗證
  - 約束檢查函式 `check_constraints()`：用來判斷時段是否有衝突、是否為週末/假日、是否超出工作時間，以及時間範圍是否有效
  - `accept_and_add` / `reschedule` 的確認會議 `confirmed_event` 需再次通過 `check_constraints()`，避免模型回傳不可行時段仍被寫入行事曆
- 當動作為回覆草稿 `action = reply_draft` 且有回覆內容時，系統會自動附加 `[草稿｜需人工確認後送出]`

## MCP Server 說明

本專案的 MCP Server 使用 `fastMCP` 實作，對 `calendar.json` 進行封裝。`agent.py` 會自動以 stdio 啟動並連線 `calendar_mcp.py`，且在連線 MCP Server 後，會先呼叫 `backup_calendar()` 建立當次執行前的行事曆備份，再開始逐封處理郵件。

### 提供的工具（Tools）

| Tool | 說明 |
| --- | --- |
| `get_calendar_events()` | 讀取所有行程 |
| `add_calendar_event(title, start, end)` | 新增行程 |
| `delete_calendar_events(start, end)` | 依起訖時間刪除行程 |
| `backup_calendar()` | 建立處理前備份 |

## 輸出欄位定義

<a id="analysis-fields"></a>
<details>
<summary><strong>分析輸出欄位定義（<code>analysis</code>）</strong></summary>

- `email_id`：郵件 ID（對應 `emails.json` 的 `id`）
- `category`：郵件分類（`急件` / `一般` / `詢價` / `會議邀約` / `垃圾`）
- `priority`：優先級（1~5，數字越高越優先）
- `important_sender`：是否為重要寄件人（依網域或寄件者清單判斷）
- `needs_reply`：是否需要回覆
- `has_risk`：是否涉及未授權承諾風險（如金錢 / 合約 / 法律）
- `risk_types`：風險類型清單
- `meeting_intent`：會議意圖（`new` / `reschedule` / `cancel` / `fyi`；非會議可為 `null`）
- `time_specified`：信件是否提供明確會議時間
- `proposed_start`：模型解析出的提議開始時間（ISO 8601；無則 `null`）
- `proposed_end`：模型解析出的提議結束時間（ISO 8601；無則 `null`）
- `duration_minutes`：會議時長（分鐘；若無法判定可為 `null`）
- `event_information`：會議或任務的摘要資訊（例如議題、活動名稱）
- `reply_deadline`：回覆期限（ISO 8601；若信件未提及則 `null`）

</details>

<a id="decision-fields"></a>
<details>
<summary><strong>Agent 決策輸出欄位定義（<code>decision</code>）</strong></summary>

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

</details>


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

輸出：
- `email_analysis.json`（含 `analysis`）

### 4. 執行 Agent（含 MCP 行事曆操作）

```bash
python agent.py
```

輸出：
- `email_analysis.json`
- 更新後的 `calendar.json`
- 備份檔（例如 `calendar_backup1.json`）

## 未來可優化方向

- 可在「郵件分析階段」平行處理多封郵件（一次跑多個 AI 請求）以縮短總處理時間

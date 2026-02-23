import json
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()
client = genai.Client()
base_dir = os.path.dirname(__file__)
with open(os.path.join(base_dir, "config.json"), "r") as f:
    config = json.load(f)
logging.basicConfig(
    level=getattr(logging, config.get("log_level", "INFO")),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# === Pydantic Schema ===


class EmailAnalysis(BaseModel):
    email_id: str
    category: Literal["急件", "一般", "詢價", "會議邀約", "垃圾"]
    priority: int = Field(ge=1, le=5)
    important_sender: bool
    needs_reply: bool
    has_risk: bool
    risk_types: List[
        Literal["financial_commitment", "contract_commitment", "legal_commitment"]
    ]
    meeting_intent: Optional[Literal["new", "reschedule", "cancel", "fyi"]] = None
    time_specified: bool
    proposed_start: Optional[str] = Field(
        default=None, description="ISO 8601 datetime in Asia/Taipei"
    )
    proposed_end: Optional[str] = Field(
        default=None, description="ISO 8601 datetime in Asia/Taipei"
    )
    duration_minutes: Optional[int] = None
    event_information: Optional[str] = None
    reply_deadline: Optional[str] = Field(default=None, description="ISO 8601 datetime")


# === Utility Functions ===


def load_json(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def is_important_sender(email_from: str, important_senders_data: dict) -> bool:
    """Check if sender is important via domain or exact email match."""
    email_lower = email_from.lower().strip()
    for domain in important_senders_data.get("important_domains", []):
        if email_lower.endswith(f"@{domain.lower()}"):
            return True
    for sender in important_senders_data.get("important_senders", []):
        if email_lower == sender.lower():
            return True
    return False


def normalize_dt(dt_str: str | None) -> str | None:
    """Normalize datetime string to naive ISO format in Asia/Taipei."""
    if not dt_str:
        return dt_str
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is not None:
        dt = dt.astimezone(ZoneInfo("Asia/Taipei"))
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def build_system_instruction(important_senders: dict) -> str:
    return f"""
    你是一個協助忙碌經理處理收件匣的 AI 助理。
    目前日期/時間假設為：{config["current_datetime"]} {config["timezone"]}。所有時間戳皆以 {config["timezone"]} 為準。
    
    任務：分析單封郵件並輸出符合 schema 的結構化欄位。
    
    重要寄件者 / 網域：
    {json.dumps(important_senders, ensure_ascii=False)}
    
    Priority 規則（取符合條件中的最高者）：
    **Priority 5**
    - 急件且需回覆
    - 會議時間 ≤ 24 小時內
    - 會議取消 (cancel)
    - 會議改期 (reschedule)
    - 涉及重大合約承諾 / 金錢承諾 / 法律風險且需回覆
    - 重要寄件人 + 回覆期限 ≤ 24 小時（利用 important_senders 判斷）
    
    **Priority 4**
    - 急件
    - 會議時間 ≤ 48 小時內
    - 回覆期限 ≤ 24 小時
    - 重要寄件人且需回覆
    
    **Priority 3**
    - 有明確會議時間（非 48 小時內）
    - 回覆期限 ≤ 48 小時
    - 重要寄件人
    
    **Priority 2**
    - 普通會議（無明確時間）
    - 普通詢價
    - 一般郵件
    - 確認信
    - 訂閱信件
    
    **Priority 1**
    - 垃圾郵件
    
    注意：
    - 若提供 `proposed_start`，必須同時提供 `proposed_end` (ISO 8601, Asia/Taipei)。
    - 若信件只提供開始時間，請根據內容推測 `proposed_end`；若只提供結束時間，請推測 `proposed_start`；若兩者皆未提供，請推測兩者。
    - `has_risk` 為 true 代表會涉及主動的金錢承諾、合約承諾、法律責任承諾。
    """


# === Main ===


def run_analysis():
    emails = load_json(os.path.join(base_dir, "emails.json"))
    important_senders = load_json(os.path.join(base_dir, "important_sender.json"))
    system_instruction = build_system_instruction(important_senders)
    results = []

    for email in emails:
        logger.info("Analyzing %s - %s", email["id"], email["subject"])
        prompt = f"""
        Email to analyze:
        {json.dumps(email, ensure_ascii=False, indent=2)}
        
        Pre-calculated:
        - important_sender: {is_important_sender(email.get("sender", email.get("from", "")), important_senders)}
        """

        try:
            response = client.models.generate_content(
                model=config["model"],
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=EmailAnalysis,
                    temperature=0.1,
                ),
            )
            analysis_data = json.loads(response.text)
            for key in ("proposed_start", "proposed_end", "reply_deadline"):
                if key in analysis_data:
                    analysis_data[key] = normalize_dt(analysis_data[key])
            results.append({"email_raw": email, "analysis": analysis_data})

        except Exception as e:
            logger.error("Error analyzing %s: %s", email["id"], e)
            results.append({"email_raw": email, "analysis": None, "error": str(e)})

    output_path = os.path.join(base_dir, "email_analysis.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.info("Analysis saved to %s", output_path)


if __name__ == "__main__":
    run_analysis()

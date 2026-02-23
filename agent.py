import json
import os
import zoneinfo
import logging
from datetime import datetime
from pydantic import BaseModel
from typing import List, Optional, Literal
from google import genai
from google.genai import types
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import asyncio
import sys

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
TZ = zoneinfo.ZoneInfo(config.get("timezone", "Asia/Taipei"))


# === Pydantic Schema ===


class EventInfo(BaseModel):
    title: str
    start: str
    end: str


class AgentDecision(BaseModel):
    action: Literal[
        "accept_and_add",
        "propose_alternative",
        "propose_times",
        "reschedule",
        "cancel",
        "reply",
        "reply_draft",
        "escalate",
        "ignore",
    ]
    reply: str
    decision_rationale: str
    confirmed_event: Optional[EventInfo] = None
    affected_event: Optional[EventInfo] = None
    proposed_alternatives: Optional[List[EventInfo]] = None


class ReplyFlag(BaseModel):
    email_id: str
    flagged: bool


SYSTEM_INSTRUCTION = f"""
你是一個具備自主決策能力的 AI Agent，負責管理忙碌高階主管的收件匣與行事曆。
目前日期/時間為：{config["current_datetime"]} {config["timezone"]}。所有時間戳皆以 {config["timezone"]} 為準。

請根據預先檢查的 Constraint Flags 進行決策：
若為會議邀約且 Constraint Flags 皆為 False 可接受：
    - action = "accept_and_add" 
    - 提供 confirmed_event（包含 title、start、end）
若為改期 (reschedule)：
    - action = "reschedule" 
    - 提供 confirmed_event（包含 title、start、end）
    - 提供 affected_event（包含 title、start、end）
若為取消 (cancel)：
    - action = "cancel" 
    - 提供 affected_event（包含 title、start、end）
若為會議且 Constraint Flags 有 True：
    - action = "propose_alternative"
    - 在 reply 中提出 2-3 個具體可行時段
    - 每個 proposed_alternatives 必須包含 start 與 end (ISO 8601)
    - 必須避開「已佔用時段」和「不可用日期（假日）」
若為會議但未提供明確時間：
    - action = "propose_times"
    - 在 reply 中提出 2-3 個具體可行時段
    - 每個 proposed_alternatives 必須包含 start 與 end (ISO 8601)
    - 必須避開「已佔用時段」和「不可用日期（假日）」

【非會議信件】
若需回覆：
    - action = "reply"
若需回覆但內容涉及關鍵資訊或主觀決策：
    - action = "reply_draft"
若無須處理：
    - action = "ignore"
    - reply 留空

【安全護欄 (Guardrails)】
若 `has_risk` 為 true 且涉及主動的金錢承諾、合約承諾、法律責任承諾：
    - action = "escalate"（最高優先）

【語言規範】
decision_rationale 與 reply 使用繁體中文。
"""


# === Utility Functions ===


def to_tz_aware(dt_str: str) -> datetime:
    """Parse ISO 8601 string and ensure Asia/Taipei timezone awareness."""
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt


def check_constraints(
    proposed_start: str,
    proposed_end: str,
    calendar_events: list[dict],
    holiday_set: set[str],
) -> dict:
    dt_start = to_tz_aware(proposed_start)
    dt_end = to_tz_aware(proposed_end)

    if dt_start >= dt_end:
        return {
            "has_conflict": False,
            "is_weekend": False,
            "is_holiday": False,
            "is_off_hours": False,
            "invalid_range": True,
        }

    # Weekend
    is_weekend = dt_start.weekday() >= 5 or dt_end.weekday() >= 5

    work_start = config.get("work_hours", {}).get("start", 9)
    work_end = config.get("work_hours", {}).get("end", 18)
    is_off_hours = (
        dt_start.hour < work_start
        or dt_start.hour >= work_end
        or dt_end.hour > work_end
        or (dt_end.hour == work_end and dt_end.minute > 0)
    )

    # Holiday
    date_start = dt_start.strftime("%Y-%m-%d")
    date_end = dt_end.strftime("%Y-%m-%d")
    is_holiday = date_start in holiday_set or date_end in holiday_set

    # Conflict (interval overlap)
    has_conflict = False
    for e in calendar_events:
        e_start = to_tz_aware(e["start"])
        e_end = to_tz_aware(e["end"])
        if max(dt_start, e_start) < min(dt_end, e_end):
            has_conflict = True
            break

    return {
        "has_conflict": has_conflict,
        "is_weekend": is_weekend,
        "is_holiday": is_holiday,
        "is_off_hours": is_off_hours,
        "invalid_range": False,
    }


def load_json(filename):
    path = os.path.join(base_dir, filename)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data, filename):
    path = os.path.join(base_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def sort_emails(emails: list) -> list:
    """Sorts emails by Priority."""

    def _sort_key(email_obj):
        analysis = email_obj.get("analysis")
        if not analysis:
            return (0, 99, "9999-12-31T23:59:59")
        priority = analysis.get("priority", 1)
        intent = analysis.get("meeting_intent")
        intent_score = 3
        if intent == "cancel":
            intent_score = 0
        elif intent == "reschedule":
            intent_score = 1
        elif intent == "new" and analysis.get("time_specified"):
            intent_score = 2
        proposed_start = analysis.get("proposed_start") or "9999-12-31T23:59:59"
        return (-priority, intent_score, proposed_start)

    return sorted(emails, key=_sort_key)


# === Core Processing ===


async def process_email(
    mcp_session: ClientSession,
    email_obj: dict,
    holiday_set: set[str],
    holidays_data: list[dict],
    pending_slots: list[dict],
):
    analysis = email_obj.get("analysis")
    email_raw = email_obj.get("email_raw")

    # Skip emails that failed analysis
    if not analysis:
        logger.warning("Skipping %s - analysis failed", email_raw["id"])
        return email_obj

    category = analysis.get("category")
    logger.info(
        "[P%s] Processing %s - %s",
        analysis["priority"],
        email_raw["id"],
        email_raw["subject"],
    )

    # Only fetch calendar for meeting-related emails
    is_meeting = category == "會議邀約"
    calendar_events = []
    if is_meeting:
        result = await mcp_session.call_tool("get_calendar_events")
        calendar_events = json.loads(result.content[0].text) if result.content else []

    # Compute constraint flags
    proposed_start = analysis.get("proposed_start")
    proposed_end = analysis.get("proposed_end")
    if proposed_start and proposed_end:
        flags = check_constraints(
            proposed_start, proposed_end, calendar_events, holiday_set
        )
    else:
        flags = {
            "has_conflict": False,
            "is_weekend": False,
            "is_holiday": False,
            "is_off_hours": False,
            "invalid_range": False,
        }

    prompt = f"""
    Email: {json.dumps(email_raw, ensure_ascii=False)}
    Analysis: {json.dumps(analysis, ensure_ascii=False)}
    
    Pre-calculated Constraint Flags:
    - has_conflict: {flags["has_conflict"]}
    - is_weekend: {flags["is_weekend"]}
    - is_holiday: {flags["is_holiday"]}
    - is_off_hours: {flags["is_off_hours"]}
    """

    # Inject calendar + holidays + pending slots for meeting emails
    if is_meeting:
        all_busy = calendar_events + pending_slots
        if all_busy:
            busy_lines = "\n".join(
                f"- {e['title']}: {e['start']} ~ {e['end']}" for e in all_busy
            )
        else:
            busy_lines = "（無）"
        if holidays_data:
            holiday_lines = "\n".join(
                f"- {h['date']} {h['name']}" for h in holidays_data
            )
        else:
            holiday_lines = "（無）"
        prompt += (
            f"\n已佔用時段（含待定提案）：\n{busy_lines}"
            f"\n\n不可用日期（假日）：\n{holiday_lines}\n"
        )

    try:
        response = client.models.generate_content(
            model=config["model"],
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=AgentDecision,
                temperature=0.1,
            ),
        )

        decision_data = json.loads(response.text)
        email_obj["decision"] = decision_data

        # Execute MCP tool calls based on action
        action = decision_data.get("action")

        # Only meeting emails can modify calendar
        if not is_meeting and action in {"accept_and_add", "reschedule", "cancel"}:
            logger.warning("Blocked: non-meeting email cannot %s", action)
            decision_data["action"] = "ignore"

        # Safety guard: has_risk must escalate
        elif analysis.get("has_risk") and action != "escalate":
            logger.warning("Safety guard: has_risk=True, forcing escalate")
            decision_data["action"] = "escalate"
            decision_data["decision_rationale"] += (
                " (系統攔截：has_risk=True，強制 escalate)"
            )

        elif action == "accept_and_add" and decision_data.get("confirmed_event"):
            event = decision_data["confirmed_event"]
            event_flags = check_constraints(
                event["start"],
                event["end"],
                calendar_events + pending_slots,
                holiday_set,
            )
            if any(event_flags.values()):
                logger.warning(
                    "Blocked: confirmed_event failed constraints %s", event_flags
                )
                decision_data["action"] = "propose_alternative"
                decision_data["decision_rationale"] += (
                    " (系統攔截：confirmed_event 未通過約束檢查)"
                )
            else:
                await mcp_session.call_tool(
                    "add_calendar_event",
                    arguments={
                        "title": event["title"],
                        "start": event["start"],
                        "end": event["end"],
                    },
                )
                logger.info("Added Event: %s", event["title"])

        elif (
            action == "reschedule"
            and decision_data.get("affected_event")
            and decision_data.get("confirmed_event")
        ):
            old = decision_data["affected_event"]
            new = decision_data["confirmed_event"]
            new_flags = check_constraints(
                new["start"], new["end"], calendar_events + pending_slots, holiday_set
            )
            if any(new_flags.values()):
                logger.warning("Blocked: new event failed constraints %s", new_flags)
                decision_data["action"] = "propose_alternative"
                decision_data["decision_rationale"] += (
                    " (系統攔截：reschedule 新時段未通過約束檢查)"
                )
            else:
                await mcp_session.call_tool(
                    "delete_calendar_events",
                    arguments={
                        "start": old["start"],
                        "end": old["end"],
                    },
                )
                await mcp_session.call_tool(
                    "add_calendar_event",
                    arguments={
                        "title": new["title"],
                        "start": new["start"],
                        "end": new["end"],
                    },
                )
                logger.info("Rescheduled Event to %s", new["start"])

        elif action == "cancel" and decision_data.get("affected_event"):
            old = decision_data["affected_event"]
            await mcp_session.call_tool(
                "delete_calendar_events",
                arguments={
                    "start": old["start"],
                    "end": old["end"],
                },
            )
            logger.info("Cancelled Event: %s", old["title"])

        elif action == "accept_and_add" and not decision_data.get("confirmed_event"):
            logger.warning(
                "accept_and_add without confirmed_event, falling back to ignore"
            )
            decision_data["action"] = "ignore"

        elif action == "reschedule" and (
            not decision_data.get("affected_event")
            or not decision_data.get("confirmed_event")
        ):
            logger.warning("reschedule missing event data, falling back to ignore")
            decision_data["action"] = "ignore"

        elif action == "cancel" and not decision_data.get("affected_event"):
            logger.warning("cancel without affected_event, falling back to ignore")
            decision_data["action"] = "ignore"

    except Exception as e:
        logger.error("Error processing %s: %s", email_raw["id"], e)
        email_obj["decision"] = None
        email_obj["error"] = str(e)

    if email_obj.get("decision"):
        applied = []
        if flags["has_conflict"]:
            applied.append("conflict")
        if flags["is_weekend"]:
            applied.append("weekend")
        if flags["is_holiday"]:
            applied.append("holiday")
        if flags["is_off_hours"]:
            applied.append("off_hours")
        if flags["invalid_range"]:
            applied.append("invalid_range")
        if analysis.get("has_risk"):
            applied.append("risk")
        email_obj["decision"]["constraints_applied"] = applied
        action = email_obj["decision"].get("action")
        reply_text = email_obj["decision"].get("reply", "")
        if action == "reply_draft" and reply_text:
            email_obj["decision"]["reply"] = reply_text + "\n[草稿｜需人工確認後送出]"
        elif action == "escalate":
            email_obj["decision"]["reply"] = ""

    return email_obj


# == Safety Guard ==


def scan_replies_for_commitments(results: list[dict]) -> list[dict]:
    """Post-processing safety scan."""
    to_scan = []
    for r in results:
        decision = r.get("decision") or {}
        reply = decision.get("reply", "").strip()
        if reply:
            to_scan.append({"email_id": r["email_raw"]["id"], "reply": reply})

    if not to_scan:
        logger.info("Reply safety scan: no replies to scan.")
        return results

    logger.info("Reply safety scan: scanning %d replies...", len(to_scan))
    prompt = (
        "以下是回覆草稿。請檢查每封是否包含未授權的金錢承諾、合約承諾、或法律責任承諾。"
        "若回覆中包含主動承諾的金額、價格、合約條款、或代表公司做出的承諾，則 flagged = true。\n\n"
        + json.dumps(to_scan, ensure_ascii=False, indent=2)
    )

    try:
        response = client.models.generate_content(
            model=config["model"],
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=list[ReplyFlag],
                temperature=0.1,
            ),
        )
        flagged_results = json.loads(response.text)
        flagged_ids = {
            item["email_id"] for item in flagged_results if item.get("flagged")
        }
        if flagged_ids:
            logger.warning("Flagged: %s", flagged_ids)
            for r in results:
                if r["email_raw"]["id"] in flagged_ids:
                    r["decision"]["action"] = "escalate"
                    r["decision"]["reply"] = ""
                    r["decision"]["decision_rationale"] += (
                        " (安全掃描攔截：回覆內容涉及未授權承諾)"
                    )
                    logger.warning("  %s: escalated", r["email_raw"]["id"])
        else:
            logger.info("All replies safe.")

    except Exception as e:
        logger.error("Scan error (non-blocking): %s", e)

    return results


async def main():
    email_analysis_path = os.path.join(base_dir, "email_analysis.json")
    if not os.path.exists(email_analysis_path):
        logger.error("Please run email_analyzer.py first.")
        return

    emails = load_json("email_analysis.json")
    holidays_data = load_json("holiday.json")
    holiday_set = {h["date"] for h in holidays_data.get("holidays", [])}
    holidays_list = holidays_data.get("holidays", [])
    sorted_emails = sort_emails(emails)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(base_dir, "calendar_mcp.py")],
    )

    logger.info("Connecting to Calendar MCP Server...")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            logger.info("Connected!")
            backup_result = await session.call_tool("backup_calendar")
            logger.info(backup_result.content[0].text)
            final_results = []
            pending_slots = []

            for email in sorted_emails:
                result = await process_email(
                    session, email, holiday_set, holidays_list, pending_slots
                )
                final_results.append(result)
                decision = result.get("decision") or {}
                alts = decision.get("proposed_alternatives") or []
                for alt in alts:
                    pending_slots.append(alt)

            final_results = scan_replies_for_commitments(final_results)
            save_json(final_results, "email_analysis.json")
            logger.info("Done processing all emails.")


if __name__ == "__main__":
    asyncio.run(main())

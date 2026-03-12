import copy
import unittest
from unittest import mock

from tests.support import (
    FakeMcpSession,
    import_agent_with_stubs,
    make_llm_response,
)


AGENT = import_agent_with_stubs()


def make_email_obj(*, category="會議邀約", has_risk=False):
    return {
        "email_raw": {
            "id": "email-1",
            "subject": "Project sync",
            "sender": "partner@example.com",
            "body": "Can we meet tomorrow morning?",
        },
        "analysis": {
            "category": category,
            "priority": 5,
            "important_sender": False,
            "needs_reply": True,
            "has_risk": has_risk,
            "risk_types": [],
            "meeting_intent": "new" if category == "會議邀約" else None,
            "time_specified": category == "會議邀約",
            "proposed_start": "2026-03-13T10:00:00" if category == "會議邀約" else None,
            "proposed_end": "2026-03-13T11:00:00" if category == "會議邀約" else None,
            "duration_minutes": 60 if category == "會議邀約" else None,
            "event_information": "Project sync",
            "reply_deadline": None,
        },
    }


class ProcessEmailTests(unittest.IsolatedAsyncioTestCase):
    async def test_meeting_accept_and_add_calls_calendar_tool(self):
        email_obj = make_email_obj()
        session = FakeMcpSession()
        decision = {
            "action": "accept_and_add",
            "reply": "可以，已加入行事曆。",
            "decision_rationale": "時段可行",
            "confirmed_event": {
                "title": "Project sync",
                "start": "2026-03-13T10:00:00",
                "end": "2026-03-13T11:00:00",
            },
        }

        with mock.patch.object(
            AGENT.client.models, "generate_content", return_value=make_llm_response(decision)
        ):
            result = await AGENT.process_email(session, copy.deepcopy(email_obj), set(), [], [])

        self.assertEqual(result["decision"]["action"], "accept_and_add")
        self.assertEqual(result["decision"]["constraints_applied"], [])
        self.assertEqual(
            session.calls,
            [
                ("get_calendar_events", None),
                (
                    "add_calendar_event",
                    {
                        "title": "Project sync",
                        "start": "2026-03-13T10:00:00",
                        "end": "2026-03-13T11:00:00",
                    },
                ),
            ],
        )

    async def test_non_meeting_cannot_modify_calendar(self):
        email_obj = make_email_obj(category="一般")
        session = FakeMcpSession()
        decision = {
            "action": "accept_and_add",
            "reply": "已接受。",
            "decision_rationale": "模型誤判",
            "confirmed_event": {
                "title": "Should not happen",
                "start": "2026-03-13T10:00:00",
                "end": "2026-03-13T11:00:00",
            },
        }

        with mock.patch.object(
            AGENT.client.models, "generate_content", return_value=make_llm_response(decision)
        ):
            result = await AGENT.process_email(session, copy.deepcopy(email_obj), set(), [], [])

        self.assertEqual(result["decision"]["action"], "ignore")
        self.assertEqual(result["decision"]["constraints_applied"], [])
        self.assertEqual(session.calls, [])

    async def test_risk_forces_escalate_and_clears_reply(self):
        email_obj = make_email_obj(category="一般", has_risk=True)
        session = FakeMcpSession()
        decision = {
            "action": "reply",
            "reply": "我們可以直接承諾這些條款。",
            "decision_rationale": "先回覆對方",
        }

        with mock.patch.object(
            AGENT.client.models, "generate_content", return_value=make_llm_response(decision)
        ):
            result = await AGENT.process_email(session, copy.deepcopy(email_obj), set(), [], [])

        self.assertEqual(result["decision"]["action"], "escalate")
        self.assertEqual(result["decision"]["reply"], "")
        self.assertEqual(result["decision"]["constraints_applied"], ["risk"])
        self.assertIn("強制 escalate", result["decision"]["decision_rationale"])
        self.assertEqual(session.calls, [])

    async def test_reply_draft_appends_manual_review_suffix(self):
        email_obj = make_email_obj(category="一般")
        session = FakeMcpSession()
        decision = {
            "action": "reply_draft",
            "reply": "以下是回覆建議。",
            "decision_rationale": "需要人工確認",
        }

        with mock.patch.object(
            AGENT.client.models, "generate_content", return_value=make_llm_response(decision)
        ):
            result = await AGENT.process_email(session, copy.deepcopy(email_obj), set(), [], [])

        self.assertEqual(result["decision"]["action"], "reply_draft")
        self.assertTrue(result["decision"]["reply"].endswith("[草稿｜需人工確認後送出]"))
        self.assertEqual(session.calls, [])

    async def test_invalid_confirmed_event_falls_back_to_propose_alternative(self):
        email_obj = make_email_obj()
        pending_slots = [
            {
                "title": "Pending hold",
                "start": "2026-03-13T10:30:00",
                "end": "2026-03-13T11:30:00",
            }
        ]
        session = FakeMcpSession()
        decision = {
            "action": "accept_and_add",
            "reply": "可以，已安排。",
            "decision_rationale": "先接受",
            "confirmed_event": {
                "title": "Project sync",
                "start": "2026-03-13T10:00:00",
                "end": "2026-03-13T11:00:00",
            },
        }

        with mock.patch.object(
            AGENT.client.models, "generate_content", return_value=make_llm_response(decision)
        ):
            result = await AGENT.process_email(
                session,
                copy.deepcopy(email_obj),
                set(),
                [],
                pending_slots,
            )

        self.assertEqual(result["decision"]["action"], "propose_alternative")
        self.assertIn("confirmed_event 未通過約束檢查", result["decision"]["decision_rationale"])
        self.assertEqual(session.calls, [("get_calendar_events", None)])


if __name__ == "__main__":
    unittest.main()

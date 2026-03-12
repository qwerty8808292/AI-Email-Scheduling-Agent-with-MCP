import json
import types
import unittest
from unittest import mock

from tests.support import import_agent_with_stubs


AGENT = import_agent_with_stubs()


class AgentUtilityTests(unittest.TestCase):
    def test_check_constraints_flags_conflict_weekend_holiday_and_off_hours(self):
        flags = AGENT.check_constraints(
            proposed_start="2026-03-14T08:30:00",
            proposed_end="2026-03-14T09:30:00",
            calendar_events=[
                {
                    "title": "existing",
                    "start": "2026-03-14T09:00:00",
                    "end": "2026-03-14T10:00:00",
                }
            ],
            holiday_set={"2026-03-14"},
        )

        self.assertEqual(
            flags,
            {
                "has_conflict": True,
                "is_weekend": True,
                "is_holiday": True,
                "is_off_hours": True,
                "invalid_range": False,
            },
        )

    def test_check_constraints_detects_invalid_range(self):
        flags = AGENT.check_constraints(
            proposed_start="2026-03-12T11:00:00",
            proposed_end="2026-03-12T10:00:00",
            calendar_events=[],
            holiday_set=set(),
        )

        self.assertTrue(flags["invalid_range"])
        self.assertFalse(flags["has_conflict"])

    def test_sort_emails_orders_by_priority_intent_and_start_time(self):
        emails = [
            {"email_raw": {"id": "none"}, "analysis": None},
            {
                "email_raw": {"id": "p4"},
                "analysis": {
                    "priority": 4,
                    "meeting_intent": "new",
                    "time_specified": True,
                    "proposed_start": "2026-03-13T10:00:00",
                },
            },
            {
                "email_raw": {"id": "new_late"},
                "analysis": {
                    "priority": 5,
                    "meeting_intent": "new",
                    "time_specified": True,
                    "proposed_start": "2026-03-13T15:00:00",
                },
            },
            {
                "email_raw": {"id": "cancel"},
                "analysis": {
                    "priority": 5,
                    "meeting_intent": "cancel",
                    "time_specified": True,
                    "proposed_start": "2026-03-13T16:00:00",
                },
            },
            {
                "email_raw": {"id": "reschedule"},
                "analysis": {
                    "priority": 5,
                    "meeting_intent": "reschedule",
                    "time_specified": True,
                    "proposed_start": "2026-03-13T14:00:00",
                },
            },
            {
                "email_raw": {"id": "new_early"},
                "analysis": {
                    "priority": 5,
                    "meeting_intent": "new",
                    "time_specified": True,
                    "proposed_start": "2026-03-13T09:00:00",
                },
            },
        ]

        sorted_ids = [item["email_raw"]["id"] for item in AGENT.sort_emails(emails)]

        self.assertEqual(
            sorted_ids,
            ["cancel", "reschedule", "new_early", "new_late", "p4", "none"],
        )

    def test_scan_replies_for_commitments_escalates_flagged_replies(self):
        results = [
            {
                "email_raw": {"id": "safe"},
                "decision": {
                    "action": "reply",
                    "reply": "We can share more details later.",
                    "decision_rationale": "safe",
                },
            },
            {
                "email_raw": {"id": "flagged"},
                "decision": {
                    "action": "reply",
                    "reply": "We confirm the contract terms today.",
                    "decision_rationale": "needs review",
                },
            },
        ]

        fake_response = types.SimpleNamespace(
            text=json.dumps(
                [
                    {"email_id": "safe", "flagged": False},
                    {"email_id": "flagged", "flagged": True},
                ]
            )
        )

        with mock.patch.object(
            AGENT.client.models, "generate_content", return_value=fake_response
        ) as mock_generate:
            updated = AGENT.scan_replies_for_commitments(results)

        mock_generate.assert_called_once()
        self.assertEqual(updated[0]["decision"]["action"], "reply")
        self.assertEqual(
            updated[0]["decision"]["reply"], results[0]["decision"]["reply"]
        )
        self.assertEqual(updated[1]["decision"]["action"], "escalate")
        self.assertEqual(updated[1]["decision"]["reply"], "")


if __name__ == "__main__":
    unittest.main()

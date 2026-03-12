import json
import os
import tempfile
import unittest

from tests.support import import_calendar_mcp_with_stubs


CALENDAR_MCP = import_calendar_mcp_with_stubs()


class CalendarMcpTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_calendar_path = CALENDAR_MCP.CALENDAR_PATH
        self.calendar_path = os.path.join(self.temp_dir.name, "calendar.json")
        CALENDAR_MCP.CALENDAR_PATH = self.calendar_path

    def tearDown(self):
        CALENDAR_MCP.CALENDAR_PATH = self.original_calendar_path
        self.temp_dir.cleanup()

    def write_calendar(self, data):
        with open(self.calendar_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def read_calendar(self):
        with open(self.calendar_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_get_calendar_events_returns_empty_when_file_missing(self):
        self.assertEqual(CALENDAR_MCP.get_calendar_events(), [])

    def test_get_calendar_events_returns_empty_when_json_is_invalid(self):
        with open(self.calendar_path, "w", encoding="utf-8") as f:
            f.write("{invalid json")

        self.assertEqual(CALENDAR_MCP.get_calendar_events(), [])

    def test_add_calendar_event_persists_new_event(self):
        result = CALENDAR_MCP.add_calendar_event(
            "Project sync", "2026-03-13T10:00:00", "2026-03-13T11:00:00"
        )

        self.assertEqual(result, "Event successfully added.")
        self.assertEqual(
            self.read_calendar(),
            [
                {
                    "title": "Project sync",
                    "start": "2026-03-13T10:00:00",
                    "end": "2026-03-13T11:00:00",
                }
            ],
        )

    def test_add_calendar_event_rejects_exact_duplicate(self):
        self.write_calendar(
            [
                {
                    "title": "Existing meeting",
                    "start": "2026-03-13T10:00:00",
                    "end": "2026-03-13T11:00:00",
                }
            ]
        )

        result = CALENDAR_MCP.add_calendar_event(
            "Another title", "2026-03-13T10:00:00", "2026-03-13T11:00:00"
        )

        self.assertEqual(
            result, "Event already exists at this time slot: Existing meeting"
        )
        self.assertEqual(
            self.read_calendar(),
            [
                {
                    "title": "Existing meeting",
                    "start": "2026-03-13T10:00:00",
                    "end": "2026-03-13T11:00:00",
                }
            ],
        )

    def test_delete_calendar_events_removes_matching_event(self):
        self.write_calendar(
            [
                {
                    "title": "Keep me",
                    "start": "2026-03-13T08:00:00",
                    "end": "2026-03-13T09:00:00",
                },
                {
                    "title": "Delete me",
                    "start": "2026-03-13T10:00:00",
                    "end": "2026-03-13T11:00:00",
                },
            ]
        )

        result = CALENDAR_MCP.delete_calendar_events(
            "2026-03-13T10:00:00", "2026-03-13T11:00:00"
        )

        self.assertEqual(result, "Successfully deleted 1 event(s).")
        self.assertEqual(
            self.read_calendar(),
            [
                {
                    "title": "Keep me",
                    "start": "2026-03-13T08:00:00",
                    "end": "2026-03-13T09:00:00",
                }
            ],
        )

    def test_backup_calendar_uses_next_available_backup_index(self):
        self.write_calendar(
            [
                {
                    "title": "Project sync",
                    "start": "2026-03-13T10:00:00",
                    "end": "2026-03-13T11:00:00",
                }
            ]
        )
        backup1_path = os.path.join(self.temp_dir.name, "calendar_backup1.json")
        with open(backup1_path, "w", encoding="utf-8") as f:
            json.dump([{"title": "older backup"}], f, ensure_ascii=False, indent=2)

        result = CALENDAR_MCP.backup_calendar()
        backup2_path = os.path.join(self.temp_dir.name, "calendar_backup2.json")

        self.assertEqual(result, "Backup created: calendar_backup2.json")
        self.assertTrue(os.path.exists(backup2_path))
        with open(backup2_path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), self.read_calendar())


if __name__ == "__main__":
    unittest.main()

import json
import os
import shutil
from fastmcp import FastMCP

CALENDAR_PATH = os.path.join(os.path.dirname(__file__), "calendar.json")
mcp = FastMCP("calendar")


def _load_calendar() -> list[dict]:
    if not os.path.exists(CALENDAR_PATH):
        return []
    with open(CALENDAR_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def _save_calendar(data: list[dict]):
    with open(CALENDAR_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@mcp.tool()
def backup_calendar() -> str:
    """Create a one-time backup of the current calendar.json before processing begins."""
    if not os.path.exists(CALENDAR_PATH):
        return "No calendar.json found to backup."
    base_dir = os.path.dirname(CALENDAR_PATH)
    backup_index = 1
    while True:
        backup_path = os.path.join(base_dir, f"calendar_backup{backup_index}.json")
        if not os.path.exists(backup_path):
            shutil.copy2(CALENDAR_PATH, backup_path)
            return f"Backup created: calendar_backup{backup_index}.json"
        backup_index += 1


@mcp.tool()
def get_calendar_events() -> list[dict]:
    """Retrieve all calendar events."""
    return _load_calendar()


@mcp.tool()
def add_calendar_event(title: str, start: str, end: str) -> str:
    """Add a new event to the calendar."""
    events = _load_calendar()

    # Check for basic exact duplicates to avoid accidental duplication
    for e in events:
        if e.get("start") == start and e.get("end") == end:
            return f"Event already exists at this time slot: {e.get('title')}"

    events.append({"title": title, "start": start, "end": end})
    _save_calendar(events)
    return "Event successfully added."


@mcp.tool()
def delete_calendar_events(start: str, end: str) -> str:
    """Delete an event from the calendar by start and end time."""
    events = _load_calendar()
    original_count = len(events)
    events = [
        e for e in events if not (e.get("start") == start and e.get("end") == end)
    ]
    if len(events) < original_count:
        _save_calendar(events)
        return f"Successfully deleted {original_count - len(events)} event(s)."
    return "No matching events found to delete."


if __name__ == "__main__":
    mcp.run()

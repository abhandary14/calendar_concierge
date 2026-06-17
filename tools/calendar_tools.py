import json
from datetime import datetime, timedelta, timezone

from langchain_core.tools import tool

from auth import get_services

_, calendar = get_services()


@tool
def get_upcoming_events(days: int = 3) -> str:
    """Get calendar events for the next N days. Returns JSON string."""
    now = datetime.now(timezone.utc).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    events = calendar.events().list(
        calendarId="primary",
        timeMin=now,
        timeMax=end,
        singleEvents=True,
        orderBy="startTime",
    ).execute().get("items", [])

    return json.dumps([
        {
            "summary": e.get("summary", "(no title)"),
            "start": e["start"].get("dateTime", e["start"].get("date")),
            "end": e["end"].get("dateTime", e["end"].get("date")),
            "attendees": [a["email"] for a in e.get("attendees", [])],
            "description": e.get("description", ""),
        }
        for e in events
    ])

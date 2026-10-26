"""
Calendar event (race) functions for Garmin Connect MCP Server
"""
import json
import re
from datetime import date
from typing import Any, Dict, List, Optional

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The garmin_client will be set by the main file
garmin_client = None

# Garmin's calendar-service groups every calendar entry under an itemType.
# Races the user added or subscribed to arrive as "event"; workouts, weigh-ins
# and badges share the same feed and are ignored here.
EVENT_ITEM_TYPE = "event"

# Garmin's calendar-service sends completionTarget.unit as one of these
# strings (Connect's custom-event picker: meter, kilometer, yard, mile).
# Always report meters; unknown units become None.
_DISTANCE_UNIT_TO_METERS = {
    "meter": 1.0,
    "kilometer": 1000.0,
    "yard": 0.9144,
    "mile": 1609.344,
}


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def _validate_date(value: str, field: str = "date") -> date:
    if not _DATE_RE.match(value):
        raise ValueError(f"Invalid {field} '{value}': expected YYYY-MM-DD")
    return date.fromisoformat(value)


def _month_range(start: date, end: date):
    """Yield (year, month) pairs covering start..end inclusive."""
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _fetch_month(year: int, month: int) -> List[Dict[str, Any]]:
    """Return the raw calendar items for one month.

    Despite its name, the library's get_scheduled_workouts returns the whole
    calendar feed for a month, events included. It takes a 1-indexed month and
    converts it to the 0-indexed month Garmin's calendar-service expects.
    """
    data = garmin_client.get_scheduled_workouts(year, month)
    if not isinstance(data, dict):
        return []
    items = data.get("calendarItems")
    return items if isinstance(items, list) else []


def _as_dict(value: Any) -> Dict[str, Any]:
    """Return a dict for Garmin fields that may be null or another shape."""
    return value if isinstance(value, dict) else {}


def _target_distance_meters(item: Dict[str, Any]) -> Optional[float]:
    """Return the event's distance goal, if it is expressed as a distance."""
    target = _as_dict(item.get("completionTarget"))
    if target.get("unitType") != "distance":
        return None
    value = target.get("value")
    scale = _DISTANCE_UNIT_TO_METERS.get(target.get("unit"))
    if not isinstance(value, (int, float)) or isinstance(value, bool) or scale is None:
        return None
    return float(value) * scale


def _fetch_event_detail(item: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch the event detail payload, which contains the custom goal."""
    event_id = item.get("id")
    event_uuid = _clean_str(item.get("shareableEventUuid"))
    if isinstance(event_id, (int, str)) and not isinstance(event_id, bool):
        path = f"/calendar-service/event/{event_id}"
    elif event_uuid:
        path = f"/calendar-service/event/{event_uuid}/shareable"
    else:
        return {}

    try:
        return _as_dict(garmin_client.connectapi(path))
    except Exception:
        # Event-detail availability must not make the calendar list unusable.
        return {}


def _goal_time_seconds(detail: Dict[str, Any]) -> Optional[float]:
    """Return the user's custom event goal when Garmin stores it as time."""
    customization = _as_dict(detail.get("eventCustomization"))
    goal = _as_dict(customization.get("customGoal"))
    value = goal.get("value")
    if (
        goal.get("unitType") != "time"
        or goal.get("unit") != "second"
        or not isinstance(value, (int, float))
        or isinstance(value, bool)
    ):
        return None
    return float(value)


def _clean_str(value: Any) -> Optional[str]:
    """Return a trimmed string, or None for the blanks Garmin sends."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _fetch_note(note_id: Any) -> Dict[str, Any]:
    """GET one calendar note ({"id", "noteName", "content", "date"}); {} on failure."""
    try:
        return _as_dict(garmin_client.connectapi(f"/calendar-service/note/{note_id}"))
    except Exception:
        return {}


def _curate_event(
    item: Dict[str, Any], detail: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Curate one raw calendar event into a compact shape.

    The month feed leaves id null for subscribed races and leaves location
    out for private ones; the detail record has both.
    """
    detail = detail or {}
    event_time = _as_dict(item.get("eventTimeLocal"))
    return {
        "event_id": item.get("id") or detail.get("id"),
        "title": item.get("title"),
        "date": item.get("date"),
        "is_race": bool(item.get("isRace")),
        "primary_event": bool(item.get("primaryEvent")),
        "subscribed": bool(item.get("subscribed")),
        "distance_meters": _target_distance_meters(item),
        "goal_time_seconds": _goal_time_seconds(detail),
        "start_time_local": event_time.get("startTimeHhMm"),
        "time_zone": event_time.get("timeZoneId"),
        "location": _clean_str(item.get("location")) or _clean_str(detail.get("location")),
        "url": item.get("url"),
        "event_uuid": item.get("shareableEventUuid"),
    }


def register_tools(app):
    """Register all calendar event tools with the MCP server app"""

    @app.tool()
    async def get_calendar_events(start_date: str, end_date: str) -> str:
        """Get races and events on the Garmin Connect calendar between two dates

        Returns calendar entries the user added or subscribed to in Garmin
        Connect, such as upcoming races. Use this to answer questions about
        which events or races are scheduled.

        These entries are not returned by get_scheduled_workouts, which covers
        only workouts, nor by get_goals. event_id is what
        update_calendar_event and delete_calendar_event take.

        Each event reports its distance in meters when Garmin stores one,
        the user's custom goal time in seconds when set, the local start
        time when the organiser published it, and two flags: is_race marks
        the entry as a race rather than a general event, and primary_event
        marks the goal race that an active training plan targets. Goal time
        comes from a per-event detail request.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            start = _validate_date(start_date, "start_date")
            end = _validate_date(end_date, "end_date")
            if start > end:
                return "start_date must be on or before end_date."

            events: List[Dict[str, Any]] = []
            seen = set()
            for year, month in _month_range(start, end):
                for item in _fetch_month(year, month):
                    if not isinstance(item, dict):
                        continue
                    if item.get("itemType") != EVENT_ITEM_TYPE:
                        continue
                    # The first and last month of the range extend past it.
                    day = item.get("date")
                    if not isinstance(day, str) or not start_date <= day <= end_date:
                        continue
                    # Multi-month responses can repeat an event at the seams.
                    key = (item.get("shareableEventUuid"), item.get("title"), day)
                    if key in seen:
                        continue
                    seen.add(key)
                    events.append(_curate_event(item, _fetch_event_detail(item)))

            events.sort(key=lambda event: (event["date"], event["title"] or ""))

            curated = {
                "count": len(events),
                "date_range": {"start": start_date, "end": end_date},
                "events": events,
            }
            return json.dumps(curated, indent=2, ensure_ascii=False)
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Error retrieving calendar events: {str(e)}"

    @app.tool()
    async def create_calendar_event(
        name: str,
        event_date: str,
        event_type: str = "running",
        distance_km: Optional[float] = None,
        goal_time_seconds: Optional[float] = None,
        start_time_local: Optional[str] = None,
        time_zone: Optional[str] = None,
        location: Optional[str] = None,
        primary: bool = False,
        is_race: bool = True,
        note: Optional[str] = None,
    ) -> str:
        """Add a race or event to the Garmin Connect calendar (private to you)

        Args:
            name: Event name, e.g. "Halve marathon Egmond"
            event_date: Date, YYYY-MM-DD; Garmin refuses dates a year or
                more ahead ("Event date must be less than a year in the future")
            event_type: Garmin event type, e.g. "running", "cycling",
                "multi_sport_triathlon"
            distance_km: Optional race distance in km
            goal_time_seconds: Optional goal time in seconds (e.g. 16200 = 4:30:00)
            start_time_local: Optional start time "HH:MM"; needs time_zone
            time_zone: IANA zone for start_time_local, e.g. "Europe/Amsterdam"
            location: Optional place, e.g. "Hoorn, NL"
            primary: Make this the primary (focus) race. Whether Garmin then
                clears the primary flag of the current primary race is unverified;
                check get_calendar_events afterwards.
            is_race: True for a race (default), False for a general event
            note: Optional note
        """
        try:
            _validate_date(event_date, "event_date")
            if (start_time_local is None) != (time_zone is None):
                return "start_time_local and time_zone must be given together."
            customization: Dict[str, Any] = {"isPrimaryEvent": primary}
            if goal_time_seconds:
                customization["customGoal"] = {
                    "value": float(goal_time_seconds), "unit": "second", "unitType": "time",
                }
            event: Dict[str, Any] = {
                "eventName": name,
                "date": event_date,
                "eventType": event_type,
                "race": is_race,
                "eventPrivacy": {"label": "PRIVATE", "isShareable": False, "isDiscoverable": False},
                "eventCustomization": customization,
            }
            if distance_km:
                event["completionTarget"] = {
                    "value": float(distance_km), "unit": "kilometer", "unitType": "distance",
                }
            if start_time_local is not None:
                event["eventTimeLocal"] = {"startTimeHhMm": start_time_local, "timeZoneId": time_zone}
            if location:
                event["location"] = location
            if note:
                event["note"] = note
            created = garmin_client.create_calendar_event(event)
            result: Dict[str, Any] = {"status": "success", "message": "Event created"}
            if isinstance(created, dict):
                result["event_id"] = created.get("id")
                result["name"] = created.get("eventName")
                result["date"] = created.get("date")
            return json.dumps(result, indent=2, ensure_ascii=False)
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Error creating calendar event: {str(e)}"

    @app.tool()
    async def update_calendar_event(
        event_id: int,
        name: Optional[str] = None,
        event_date: Optional[str] = None,
        distance_km: Optional[float] = None,
        goal_time_seconds: Optional[float] = None,
        start_time_local: Optional[str] = None,
        time_zone: Optional[str] = None,
        location: Optional[str] = None,
        primary: Optional[bool] = None,
        note: Optional[str] = None,
    ) -> str:
        """Change a race or event on the calendar; only the fields you pass change

        goal_time_seconds=0 removes the goal time. Setting primary=true: whether
        Garmin then clears the flag on the current primary race is unverified;
        check get_calendar_events afterwards. Safe to repeat with the same
        arguments.

        Args:
            event_id: event_id from get_calendar_events
            name: New name
            event_date: New date, YYYY-MM-DD
            distance_km: New race distance in km
            goal_time_seconds: New goal time in seconds, 0 to remove
            start_time_local: New start time "HH:MM" (keeps the event's time zone
                unless time_zone is also given)
            time_zone: New IANA time zone for the start time
            location: New place
            primary: Make this the primary race (true) or not (false)
            note: New note
        """
        try:
            if event_date is not None:
                _validate_date(event_date, "event_date")
            event = garmin_client.get_calendar_event(event_id)
            if not isinstance(event, dict) or not event:
                return f"No calendar event found with id {event_id}."
            event = dict(event)
            if name is not None:
                event["eventName"] = name
            if event_date is not None:
                event["date"] = event_date
            if distance_km is not None:
                event["completionTarget"] = {
                    "value": float(distance_km), "unit": "kilometer", "unitType": "distance",
                }
            if location is not None:
                event["location"] = location
            if note is not None:
                event["note"] = note
            if start_time_local is not None or time_zone is not None:
                event_time = dict(_as_dict(event.get("eventTimeLocal")))
                if start_time_local is not None:
                    event_time["startTimeHhMm"] = start_time_local
                if time_zone is not None:
                    event_time["timeZoneId"] = time_zone
                if not event_time.get("timeZoneId"):
                    return "start_time_local needs a time_zone; this event has none."
                event["eventTimeLocal"] = event_time
            if goal_time_seconds is not None or primary is not None:
                customization = dict(_as_dict(event.get("eventCustomization")))
                if goal_time_seconds is not None:
                    customization["customGoal"] = (
                        {"value": float(goal_time_seconds), "unit": "second", "unitType": "time"}
                        if goal_time_seconds else None
                    )
                if primary is not None:
                    customization["isPrimaryEvent"] = primary
                event["eventCustomization"] = customization
            garmin_client.update_calendar_event(event_id, event)
            return json.dumps({
                "status": "success",
                "event_id": event_id,
                "name": event.get("eventName"),
                "date": event.get("date"),
                "message": "Event updated",
            }, indent=2, ensure_ascii=False)
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Error updating calendar event: {str(e)}"

    @app.tool()
    async def delete_calendar_event(event_id: int, confirm: bool = False) -> str:
        """Delete a race or event from the calendar

        This cannot be undone. Call once with confirm=false (the default) to
        preview the event, then call again with confirm=true to delete it.

        Args:
            event_id: event_id from get_calendar_events
            confirm: Must be true to actually delete. Default false returns a preview.
        """
        try:
            if not confirm:
                event = garmin_client.get_calendar_event(event_id)
                if not isinstance(event, dict) or not event:
                    return f"No calendar event found with id {event_id}."
                return json.dumps({
                    "status": "needs_confirmation",
                    "event_id": event_id,
                    "name": event.get("eventName"),
                    "date": event.get("date"),
                    "primary_event": bool(
                        _as_dict(event.get("eventCustomization")).get("isPrimaryEvent")
                    ),
                    "message": (
                        "Deleting this event cannot be undone. Call again with "
                        "confirm=true to proceed."
                    ),
                }, indent=2, ensure_ascii=False)
            garmin_client.delete_calendar_event(event_id)
            return json.dumps({
                "status": "success", "event_id": event_id, "message": "Event deleted",
            }, indent=2)
        except Exception as e:
            return f"Error deleting calendar event: {str(e)}"

    @app.tool()
    async def create_calendar_note(note_date: str, title: str, content: str = "") -> str:
        """Add a note to a day on the Garmin Connect calendar

        Args:
            note_date: Day, YYYY-MM-DD
            title: Short title shown on the calendar, e.g. "Rustdag"
            content: Optional longer text
        """
        try:
            created = garmin_client.create_calendar_note(note_date, title, content)
            result: Dict[str, Any] = {"status": "success", "message": "Note created"}
            if isinstance(created, dict):
                result["note_id"] = created.get("id")
                result["date"] = created.get("date")
                result["title"] = created.get("noteName")
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"Error creating calendar note: {str(e)}"

    @app.tool()
    async def update_calendar_note(
        note_id: int,
        note_date: Optional[str] = None,
        title: Optional[str] = None,
        content: Optional[str] = None,
    ) -> str:
        """Change a calendar note; only the fields you pass change

        Changing note_date moves the note. content="" clears the text. Safe to
        repeat with the same arguments.

        Args:
            note_id: note id from create_calendar_note or get_calendar_week
            note_date: New day, YYYY-MM-DD
            title: New title
            content: New text, "" to clear
        """
        try:
            current = _fetch_note(note_id)
            if not current:
                return f"No calendar note found with id {note_id}."
            new_date = note_date if note_date is not None else current.get("date")
            new_title = title if title is not None else current.get("noteName")
            new_content = content if content is not None else (current.get("content") or "")
            garmin_client.update_calendar_note(note_id, new_date, new_title, new_content)
            return json.dumps({
                "status": "success", "note_id": note_id, "date": new_date,
                "title": new_title, "content": new_content or None,
                "message": "Note updated",
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"Error updating calendar note: {str(e)}"

    @app.tool()
    async def delete_calendar_note(note_id: int, confirm: bool = False) -> str:
        """Delete a calendar note

        This cannot be undone. Call with confirm=true to delete; the default
        confirm=false only asks for confirmation.

        Args:
            note_id: note id from create_calendar_note or get_calendar_week
            confirm: Must be true to actually delete
        """
        if not confirm:
            return json.dumps({
                "status": "needs_confirmation",
                "note_id": note_id,
                "message": "Deleting a note cannot be undone. Call again with confirm=true to proceed.",
            }, indent=2)
        try:
            garmin_client.delete_calendar_note(note_id)
            return json.dumps({
                "status": "success", "note_id": note_id, "message": "Note deleted",
            }, indent=2)
        except Exception as e:
            return f"Error deleting calendar note: {str(e)}"

    @app.tool()
    async def get_calendar_week(week_date: Optional[str] = None) -> str:
        """Get everything on the Garmin calendar for one week (Monday-Sunday):
        scheduled workouts, notes (with their text), races and other items

        Args:
            week_date: Any day in the week, YYYY-MM-DD (default today)
        """
        try:
            if week_date is not None:
                _validate_date(week_date, "week_date")
            week = garmin_client.get_calendar_week(week_date, first_day_of_week=1)
            if not isinstance(week, dict):
                return "No calendar data found."
            items = []
            for item in week.get("calendarItems") or []:
                if not isinstance(item, dict):
                    continue
                entry = {
                    "id": item.get("id"),
                    "type": item.get("itemType"),
                    "date": item.get("date"),
                    "title": item.get("title"),
                    "sport": item.get("sportTypeKey"),
                    "workout_id": item.get("workoutId"),
                }
                # The week feed carries a note's title but not its text.
                if item.get("itemType") == "note" and item.get("id"):
                    entry["content"] = _clean_str(_fetch_note(item["id"]).get("content"))
                items.append({k: v for k, v in entry.items() if v is not None})
            items.sort(key=lambda i: (i.get("date") or "", i.get("type") or ""))
            return json.dumps({
                "start_date": week.get("startDate"),
                "end_date": week.get("endDate"),
                "count": len(items),
                "items": items,
            }, indent=2, ensure_ascii=False)
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Error retrieving calendar week: {str(e)}"

    return app

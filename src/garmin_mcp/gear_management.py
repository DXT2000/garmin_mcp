"""
Gear management functions for Garmin Connect MCP Server
"""

import json
import logging
import re
from typing import Any, Dict, List, Literal, Optional, Union

# The garmin_client will be set by the main file
garmin_client = None
logger = logging.getLogger(__name__)

# Activity type mappings for gear defaults: Garmin activity typeId -> typeKey,
# as returned by get_activity_types, for the types gear is usually a default
# for. Any other typeId is reported as activity_<typeId>.
ACTIVITY_TYPE_MAPPING = {
    1: "running",
    2: "cycling",
    3: "hiking",
    4: "other",
    5: "mountain_biking",
    6: "trail_running",
    7: "street_running",
    8: "track_running",
    9: "walking",
    10: "road_biking",
    13: "strength_training",
    15: "casual_walking",
    16: "speed_walking",
    18: "treadmill_running",
    19: "cyclocross",
    20: "downhill_biking",
    21: "track_cycling",
    22: "recumbent_cycling",
    25: "indoor_cycling",
    26: "swimming",
    27: "lap_swimming",
    28: "open_water_swimming",
    143: "gravel_cycling",
    152: "virtual_ride",
    153: "virtual_run",
    154: "obstacle_run",
    156: "indoor_running",
    175: "e_bike_mountain",
    176: "e_bike_fitness",
    181: "ultra_run",
}

GEAR_V2_LIST_ENDPOINT = "/gear-service/gear/v2/list"


def _parse_iso_date(iso_string: Optional[str]) -> Optional[str]:
    """Extract a date from an optional ISO datetime string."""
    if not iso_string:
        return None
    return iso_string.split("T")[0] if "T" in iso_string else iso_string


def _normalize_gear_uuid(uuid: Optional[str]) -> Optional[str]:
    """Normalize UUIDs returned in hyphenated and unhyphenated forms."""
    if not isinstance(uuid, str):
        return None
    return uuid.replace("-", "").lower()


_UNDASHED_UUID_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def _dashed_gear_uuid(uuid: str) -> str:
    """Return the dashed UUID form the gear-service v2 endpoints require.

    get_gear hands out the legacy undashed form, which v2 rejects with 404, so
    the v2 tools accept both and send dashed.
    """
    if not isinstance(uuid, str) or not uuid.strip():
        raise ValueError("gear_uuid must be a non-empty string")
    uuid = uuid.strip()
    if _UNDASHED_UUID_RE.fullmatch(uuid):
        return f"{uuid[:8]}-{uuid[8:12]}-{uuid[12:16]}-{uuid[16:20]}-{uuid[20:]}".lower()
    return uuid.lower()


def _usage_pct(used: Optional[float], maximum: Optional[float]) -> Optional[int]:
    if not used or not maximum:
        return None
    return round(100 * used / maximum)


def _curate_gear_v2(g: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten one gear-service v2 record; keys Garmin leaves empty are dropped."""
    distance_m = g.get("distanceUsedMeters")
    duration_s = g.get("durationUsedSeconds")
    max_m = g.get("maxUsageDistanceMeters")
    max_s = g.get("maxUsageDurationSeconds")
    item = {
        "uuid": g.get("uuid"),
        "type": g.get("gearType"),
        "status": g.get("status"),
        "name": g.get("name"),
        "brand": g.get("brand"),
        "model": g.get("model"),
        "first_use_date": _parse_iso_date(g.get("firstUseDate")),
        "usage_type": g.get("usageType"),
        "activities": g.get("numActivitiesLinked"),
        "days_used": g.get("daysUsed"),
        "distance_used_km": round(distance_m / 1000, 1) if distance_m is not None else None,
        "duration_used_hours": round(duration_s / 3600, 1) if duration_s is not None else None,
        "max_distance_km": round(max_m / 1000, 1) if max_m else None,
        "distance_used_pct": _usage_pct(distance_m, max_m),
        "max_duration_hours": round(max_s / 3600, 1) if max_s else None,
        "duration_used_pct": _usage_pct(duration_s, max_s),
        "notes": g.get("notes") or None,
    }
    defaults = [
        a.get("activityTypeKey")
        for a in g.get("associatedActivityTypes") or []
        if isinstance(a, dict) and a.get("defaultGear")
    ]
    if defaults:
        item["default_for"] = defaults
    return {k: v for k, v in item.items() if v is not None}


def _is_missing_gear(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "gear" in message
        and ("doesn't exist" in message or "not found" in message or "404" in message)
    )


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def register_tools(app):
    """Register all gear management tools with the MCP server app"""

    @app.tool()
    async def get_gear(include_stats: bool = True) -> str:
        """Get all gear registered with the user account

        Returns complete gear inventory including free-text notes, usage statistics,
        and default activity associations. The notes field is null when unavailable.
        defaults maps each activity type (Garmin typeKey, e.g. "running") to the
        names of its default gear.
        A gear item's stats_error reports a failed usage-statistics request;
        a null total_distance_km means Garmin did not provide a distance.
        No parameters required - user profile is fetched automatically.

        Args:
            include_stats: Include usage statistics for each gear item (default True).
                           Set to False for faster response with large gear collections.
        """
        try:
            # 1. Get user_profile_id automatically from last used device
            device_info = garmin_client.get_device_last_used()
            if not device_info:
                return "Could not retrieve user profile. Please ensure you have a synced device."
            user_profile_id = device_info.get("userProfileNumber")

            # 2. Get all gear
            gear_list = garmin_client.get_gear(user_profile_id)
            if not gear_list:
                return "No gear found."

            # Notes are only exposed by Garmin's v2 gear endpoint. Its UUIDs are
            # hyphenated, unlike the legacy endpoint used for the existing fields.
            notes_by_uuid: Dict[str, Optional[str]] = {}
            try:
                gear_v2_list = garmin_client.connectapi(GEAR_V2_LIST_ENDPOINT) or []
                if not isinstance(gear_v2_list, list):
                    logger.debug(
                        "Unexpected Garmin v2 gear response type: %s",
                        type(gear_v2_list).__name__,
                    )
                    gear_v2_list = []
                for gear_v2 in gear_v2_list:
                    if not isinstance(gear_v2, dict):
                        continue
                    normalized_v2_uuid = _normalize_gear_uuid(gear_v2.get("uuid"))
                    if normalized_v2_uuid:
                        notes_by_uuid[normalized_v2_uuid] = gear_v2.get("notes")
            except Exception as exc:
                logger.debug(
                    "Garmin v2 gear list unavailable: %s", exc, exc_info=True
                )

            # 3. Get defaults to map gear -> activity types
            defaults_list = garmin_client.get_gear_defaults(user_profile_id) or []
            defaults_by_uuid = {}
            for d in defaults_list:
                uuid = d.get("uuid")
                activity_pk = d.get("activityTypePk")
                activity_type = ACTIVITY_TYPE_MAPPING.get(
                    activity_pk, f"activity_{activity_pk}"
                )
                if uuid not in defaults_by_uuid:
                    defaults_by_uuid[uuid] = []
                defaults_by_uuid[uuid].append(activity_type)

            # 4. Build curated gear list
            curated_gear = []
            active_count = 0
            retired_count = 0

            for g in gear_list:
                uuid = g.get("uuid")
                status = g.get("gearStatusName", "").lower()

                if status == "active":
                    active_count += 1
                elif status == "retired":
                    retired_count += 1

                gear_item = {
                    "uuid": uuid,
                    "name": g.get("displayName"),
                    "full_name": g.get("customMakeModel"),
                    "type": g.get("gearTypeName"),
                    "status": status,
                    "date_begin": _parse_iso_date(g.get("dateBegin")),
                    "date_end": _parse_iso_date(g.get("dateEnd")),
                    "notes": notes_by_uuid.get(
                        _normalize_gear_uuid(uuid), g.get("notes")
                    ),
                }

                # Add max distance in km if set
                max_meters = g.get("maximumMeters")
                if max_meters and max_meters > 0:
                    gear_item["max_distance_km"] = round(max_meters / 1000, 1)

                # Add default activity associations
                if uuid in defaults_by_uuid:
                    gear_item["is_default_for"] = defaults_by_uuid[uuid]

                # 5. Get stats if requested
                if include_stats:
                    try:
                        stats = garmin_client.get_gear_stats(uuid)
                    except Exception as exc:
                        gear_item["stats_error"] = str(exc)
                    else:
                        if stats:
                            distance = stats.get("totalDistance")
                            gear_item["stats"] = {
                                "total_activities": stats.get("totalActivities"),
                                "total_distance_km": round(
                                    distance / 1000, 1
                                ) if distance is not None else None,
                            }

                curated_gear.append(gear_item)

            # Sort: active first, then by date_begin descending
            curated_gear.sort(
                key=lambda x: (x["status"] != "active", x.get("date_begin") or ""),
                reverse=True,
            )
            # Re-sort to ensure active comes first
            curated_gear.sort(key=lambda x: x["status"] != "active")

            # Build defaults summary (activity -> gear names; an activity type
            # can have several default gear items, and displayName is null for
            # gear without a nickname)
            defaults_summary: Dict[str, List[str]] = {}
            for uuid, activities in defaults_by_uuid.items():
                gear = next((g for g in curated_gear if g["uuid"] == uuid), {})
                gear_name = gear.get("name") or gear.get("full_name") or uuid
                for activity in activities:
                    defaults_summary.setdefault(activity, []).append(gear_name)

            return json.dumps(
                {
                    "gear_count": len(curated_gear),
                    "active_count": active_count,
                    "retired_count": retired_count,
                    "defaults": defaults_summary,
                    "gear": curated_gear,
                },
                indent=2,
            )
        except Exception as e:
            return f"Error retrieving gear: {str(e)}"

    @app.tool()
    async def add_gear_to_activity(activity_id: int, gear_uuid: str) -> str:
        """Associate gear with an activity

        Links a specific piece of gear (like shoes, bike, etc.) to an activity.

        Args:
            activity_id: ID of the activity
            gear_uuid: UUID of the gear to add (get from get_gear)
        """
        try:
            garmin_client.add_gear_to_activity(gear_uuid, activity_id)

            return json.dumps(
                {
                    "success": True,
                    "activity_id": activity_id,
                    "gear_uuid": gear_uuid,
                    "message": "Gear successfully added to activity",
                },
                indent=2,
            )
        except Exception as e:
            if _is_missing_gear(e):
                return (
                    f"No gear association changed; gear {gear_uuid} was not found "
                    f"for activity {activity_id}."
                )
            return f"Error adding gear to activity: {str(e)}"

    @app.tool()
    async def remove_gear_from_activity(activity_id: int, gear_uuid: str) -> str:
        """Remove gear association from an activity

        Unlinks a specific piece of gear from an activity.

        Args:
            activity_id: ID of the activity
            gear_uuid: UUID of the gear to remove
        """
        try:
            garmin_client.remove_gear_from_activity(gear_uuid, activity_id)

            return json.dumps(
                {
                    "success": True,
                    "activity_id": activity_id,
                    "gear_uuid": gear_uuid,
                    "message": "Gear successfully removed from activity",
                },
                indent=2,
            )
        except Exception as e:
            if _is_missing_gear(e):
                return (
                    f"No gear association changed; gear {gear_uuid} was not found "
                    f"for activity {activity_id}."
                )
            return f"Error removing gear from activity: {str(e)}"

    @app.tool()
    async def list_gear(
        status: Literal["active", "retired", "all"] = "active",
        gear_type: Optional[str] = None,
        limit: int = 50,
    ) -> str:
        """List gear from Garmin's current (v2) gear service, with usage totals

        Each item has distance and time used, the retirement threshold when
        one is set, and distance_used_pct / duration_used_pct towards it (e.g.
        shoes at 640 of 800 km show 80). uuid is the dashed form all gear
        tools accept. get_gear is the older inventory with default-gear names.

        Args:
            status: "active" (default), "retired" or "all"
            gear_type: Optional filter such as "SHOES" or "BIKE"
            limit: Maximum items to return (1-100, default 50)
        """
        try:
            statuses = {
                "active": ["ACTIVE"],
                "retired": ["RETIRED"],
                "all": ["ACTIVE", "RETIRED"],
            }[status]
            limit = min(max(1, limit), 100)
            gear = garmin_client.get_gear_list(
                start=0, limit=limit, gear_statuses=statuses, gear_type=gear_type
            )
            if not gear:
                return "No gear found."
            curated = [_curate_gear_v2(g) for g in gear if isinstance(g, dict)]
            return json.dumps({"count": len(curated), "gear": curated}, indent=2)
        except Exception as e:
            return f"Error listing gear: {str(e)}"

    @app.tool()
    async def get_gear_details(gear_uuid: str) -> str:
        """Get one piece of gear: usage totals, threshold, and use per activity type

        Args:
            gear_uuid: Gear UUID, dashed or undashed (from list_gear or get_gear)
        """
        try:
            details = garmin_client.get_gear_details(_dashed_gear_uuid(gear_uuid))
            if not details:
                return f"No gear found with UUID {gear_uuid}."
            curated = _curate_gear_v2(details)
            counts = {
                c.get("activityTypeKey"): c.get("count")
                for c in details.get("countsByActivityType") or []
                if isinstance(c, dict)
            }
            if counts:
                curated["activities_by_type"] = counts
            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving gear details: {str(e)}"

    @app.tool()
    async def get_gear_activities(gear_uuid: str, limit: int = 20) -> str:
        """List the activities a piece of gear was used in

        Args:
            gear_uuid: Gear UUID, dashed or undashed (from list_gear or get_gear)
            limit: Maximum activities to return (1-100, default 20)
        """
        try:
            limit = min(max(1, limit), 100)
            activities = garmin_client.get_gear_activities(
                _dashed_gear_uuid(gear_uuid), limit=limit
            )
            if not activities:
                return f"No activities found for gear {gear_uuid}."
            curated = []
            for a in activities:
                if not isinstance(a, dict):
                    continue
                distance = a.get("distance")
                duration = a.get("duration")
                curated.append({
                    "activity_id": a.get("activityId"),
                    "name": a.get("activityName"),
                    "type": (a.get("activityType") or {}).get("typeKey"),
                    "start_time_local": a.get("startTimeLocal"),
                    "distance_km": round(distance / 1000, 2) if distance is not None else None,
                    "duration_min": round(duration / 60, 1) if duration is not None else None,
                })
            return json.dumps({"count": len(curated), "activities": curated}, indent=2)
        except Exception as e:
            return f"Error retrieving gear activities: {str(e)}"

    @app.tool()
    async def search_gear_models(keywords: str) -> str:
        """Search Garmin's brand/model catalogue (the "Add gear" autocomplete)

        Use it to spell brand and model the way Garmin knows them before
        create_gear.

        Args:
            keywords: Search text, e.g. "pegasus" or "canyon aeroad"
        """
        try:
            results = garmin_client.get_gear_make_models(keywords)
            if not results:
                return f"No gear models found for '{keywords}'."
            type_names: Dict[Any, Any] = {}
            try:
                type_names = {
                    t.get("gearTypePk"): t.get("gearTypeName")
                    for t in garmin_client.get_gear_types() or []
                    if isinstance(t, dict)
                }
            except Exception as exc:
                logger.debug("Gear types unavailable: %s", exc, exc_info=True)
            curated = []
            for r in results:
                if not isinstance(r, dict):
                    continue
                make = r.get("gearMakeDTO") or {}
                model = r.get("gearModelDTO") or {}
                type_pk = model.get("gearTypePk")
                curated.append({
                    "brand": make.get("gearMakeName"),
                    "model": model.get("gearModelName"),
                    "full_name": r.get("makeModelName"),
                    "gear_type": type_names.get(type_pk, type_pk),
                })
            return json.dumps({"count": len(curated), "models": curated}, indent=2)
        except Exception as e:
            return f"Error searching gear models: {str(e)}"

    @app.tool()
    async def get_gear_types() -> str:
        """Get the gear types Garmin knows and the type keys you currently use

        your_active_types / your_retired_types hold the uppercase keys that
        create_gear's gear_type and list_gear's filter take.
        """
        try:
            all_types = garmin_client.get_gear_types() or []
            user_types = garmin_client.get_user_gear_types() or {}
            return json.dumps({
                "gear_types": [
                    t.get("gearTypeName") for t in all_types if isinstance(t, dict)
                ],
                "your_active_types": user_types.get("activeGearTypes", []),
                "your_retired_types": user_types.get("retiredGearTypes", []),
            }, indent=2)
        except Exception as e:
            return f"Error retrieving gear types: {str(e)}"

    @app.tool()
    async def create_gear(
        gear_type: str,
        brand: str,
        model: str,
        name: str,
        first_use_date: str,
        max_usage_distance_km: Optional[float] = None,
        max_usage_duration_min: Optional[float] = None,
        usage_type: str = "DISTANCE",
        notes: Optional[str] = None,
        default_for_activity_types: Optional[List[str]] = None,
    ) -> str:
        """Add a piece of gear (shoes, bike, ...) to Garmin Connect

        Args:
            gear_type: Uppercase key, e.g. "SHOES" or "BIKE" (see get_gear_types)
            brand: Brand, e.g. "ASICS" (search_gear_models gives Garmin's spelling)
            model: Model, e.g. "Novablast 6"
            name: Nickname shown in Garmin Connect
            first_use_date: First use, YYYY-MM-DD
            max_usage_distance_km: Optional retirement threshold in km (e.g. 800 for shoes)
            max_usage_duration_min: Optional retirement threshold in minutes
            usage_type: How usage is tracked: "DISTANCE" (default); other values are unverified
            notes: Optional free-text notes
            default_for_activity_types: Optional lowercase activity type keys this
                gear becomes the default for, e.g. ["running", "treadmill_running"]
        """
        try:
            created = garmin_client.create_gear(
                gear_type=gear_type,
                brand=brand,
                model=model,
                name=name,
                first_use_date=first_use_date,
                usage_type=usage_type,
                max_usage_distance_km=max_usage_distance_km,
                max_usage_duration_min=max_usage_duration_min,
                # Garmin answers 400 for an empty notes string (1-2000
                # characters) but accepts null, so no note is sent as None.
                notes=notes or None,
                activity_type_keys=default_for_activity_types,
            )
            result: Dict[str, Any] = {"status": "success", "message": "Gear created"}
            if isinstance(created, dict):
                result["gear"] = _curate_gear_v2(created)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error creating gear: {str(e)}"

    @app.tool()
    async def update_gear(
        gear_uuid: str,
        name: Optional[str] = None,
        brand: Optional[str] = None,
        model: Optional[str] = None,
        first_use_date: Optional[str] = None,
        max_usage_distance_km: Optional[float] = None,
        max_usage_duration_min: Optional[float] = None,
        notes: Optional[str] = None,
        default_for_activity_types: Optional[List[str]] = None,
    ) -> str:
        """Edit a piece of gear; only the fields you pass change

        notes="" clears the note; max_usage_distance_km=0 or
        max_usage_duration_min=0 removes that retirement threshold.
        default_for_activity_types REPLACES the whole list (pass [] to make
        the gear the default for nothing). To retire gear use set_gear_status.
        Safe to repeat with the same arguments.

        Args:
            gear_uuid: Gear UUID, dashed or undashed (from list_gear or get_gear)
            name: New nickname
            brand: New brand
            model: New model
            first_use_date: New first-use date, YYYY-MM-DD
            max_usage_distance_km: New retirement threshold in km, 0 to remove
            max_usage_duration_min: New retirement threshold in minutes, 0 to remove
            notes: New notes, "" to clear
            default_for_activity_types: Lowercase activity type keys, e.g. ["running"]
        """
        fields = {
            "name": name,
            "brand": brand,
            "model": model,
            "first_use_date": first_use_date,
            "max_usage_distance_km": max_usage_distance_km,
            "max_usage_duration_min": max_usage_duration_min,
            "notes": notes,
            "activity_type_keys": default_for_activity_types,
        }
        changes = {k: v for k, v in fields.items() if v is not None}
        if not changes:
            return "Error updating gear: pass at least one field to change."
        # Garmin answers 400 for an empty notes string but clears the note on
        # null, and garmin_client.update_gear treats None as "unchanged", so a
        # clear is a separate PUT of the full gear object with notes null.
        clear_notes = changes.get("notes") == ""
        if clear_notes:
            del changes["notes"]
        try:
            uuid = _dashed_gear_uuid(gear_uuid)
            updated = garmin_client.update_gear(uuid, **changes) if changes else None
            if clear_notes:
                current = garmin_client.get_gear_details(uuid)
                if not isinstance(current, dict) or not current:
                    return f"Error updating gear: gear {gear_uuid} not found."
                garmin_client.client.put(
                    "connectapi",
                    f"/gear-service/gear/v2/{uuid}",
                    json={**current, "notes": None},
                )
                updated = garmin_client.get_gear_details(uuid)
            result: Dict[str, Any] = {"status": "success", "message": "Gear updated"}
            if isinstance(updated, dict):
                result["gear"] = _curate_gear_v2(updated)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error updating gear: {str(e)}"

    @app.tool()
    async def set_gear_status(
        gear_uuid: str, status: Literal["active", "retired"]
    ) -> str:
        """Retire a piece of gear, or make a retired one active again

        Retiring is reversible, unlike delete_gear; use it for worn-out shoes.
        Safe to repeat with the same arguments.

        Args:
            gear_uuid: Gear UUID, dashed or undashed (from list_gear or get_gear)
            status: "retired" or "active"
        """
        try:
            uuid = _dashed_gear_uuid(gear_uuid)
            garmin_client.set_gear_status(uuid, status.upper())
            return json.dumps({
                "status": "success",
                "gear_uuid": uuid,
                "gear_status": status.upper(),
                "message": f"Gear set to {status}",
            }, indent=2)
        except Exception as e:
            return f"Error setting gear status: {str(e)}"

    @app.tool()
    async def delete_gear(gear_uuid: str, confirm: bool = False) -> str:
        """Delete a piece of gear from Garmin Connect

        This cannot be undone. Call once with confirm=false (the default) to
        preview the gear, then call again with confirm=true to delete it.
        set_gear_status retires gear reversibly instead.

        Args:
            gear_uuid: Gear UUID, dashed or undashed (from list_gear or get_gear)
            confirm: Must be true to actually delete. Default false returns a preview.
        """
        try:
            uuid = _dashed_gear_uuid(gear_uuid)
            if not confirm:
                details = garmin_client.get_gear_details(uuid)
                if not details:
                    return f"No gear found with UUID {gear_uuid}."
                return json.dumps({
                    "status": "needs_confirmation",
                    "gear": _curate_gear_v2(details),
                    "message": (
                        "Deleting this gear cannot be undone. Call again with "
                        "confirm=true to proceed, or use set_gear_status to "
                        "retire it, which can be undone."
                    ),
                }, indent=2)
            garmin_client.delete_gear(uuid)
            return json.dumps({
                "status": "success",
                "gear_uuid": uuid,
                "message": "Gear deleted",
            }, indent=2)
        except Exception as e:
            return f"Error deleting gear: {str(e)}"

    return app

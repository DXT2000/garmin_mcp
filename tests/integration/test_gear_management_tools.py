"""Integration tests for the gear-service v2 tools in gear_management.py."""
import json

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import gear_management

DASHED = "11111111-2222-3333-4444-555555555555"
UNDASHED = "11111111222233334444555555555555"

GEAR_V2 = {
    "uuid": DASHED,
    "userProfilePk": 1,
    "gearType": "SHOES",
    "status": "ACTIVE",
    "name": "Race shoes",
    "brand": "Brand",
    "model": "Model 5",
    "usageType": "DISTANCE",
    "maxUsageDistanceMeters": 800000.0,
    "maxUsageDurationSeconds": 0,
    "firstUseDate": "2026-03-01T00:00:00.0",
    "numActivitiesLinked": 42,
    "durationUsedSeconds": 115200,
    "distanceUsedMeters": 640000.0,
    "daysUsed": 200,
    "processing": False,
    "createdDate": "2026-03-01T10:00:00.0",
}


@pytest.fixture
def app_with_gear(mock_garmin_client):
    gear_management.configure(mock_garmin_client)
    return gear_management.register_tools(FastMCP("Test Gear v2"))


def _text(result):
    return result[0][0].text


@pytest.mark.asyncio
async def test_list_gear_curates_usage_and_threshold(app_with_gear, mock_garmin_client):
    mock_garmin_client.get_gear_list.return_value = [GEAR_V2]
    payload = json.loads(_text(await app_with_gear.call_tool("list_gear", {"status": "all"})))
    gear = payload["gear"][0]
    assert payload["count"] == 1
    assert gear["uuid"] == DASHED and gear["type"] == "SHOES"
    assert gear["distance_used_km"] == 640.0 and gear["max_distance_km"] == 800.0
    assert gear["distance_used_pct"] == 80
    assert gear["duration_used_hours"] == 32.0
    assert "max_duration_hours" not in gear  # 0 means no time threshold
    assert gear["first_use_date"] == "2026-03-01"
    mock_garmin_client.get_gear_list.assert_called_once_with(
        start=0, limit=50, gear_statuses=["ACTIVE", "RETIRED"], gear_type=None
    )


@pytest.mark.asyncio
async def test_list_gear_exception(app_with_gear, mock_garmin_client):
    mock_garmin_client.get_gear_list.side_effect = Exception("API Error")
    assert "Error listing gear" in _text(await app_with_gear.call_tool("list_gear", {}))


@pytest.mark.asyncio
async def test_get_gear_details_sends_dashed_uuid_for_undashed_input(
    app_with_gear, mock_garmin_client
):
    mock_garmin_client.get_gear_details.return_value = {
        **GEAR_V2,
        "countsByActivityType": [{"activityTypeKey": "running", "count": 40}],
    }
    payload = json.loads(_text(
        await app_with_gear.call_tool("get_gear_details", {"gear_uuid": UNDASHED})
    ))
    mock_garmin_client.get_gear_details.assert_called_once_with(DASHED)
    assert payload["activities_by_type"] == {"running": 40}


@pytest.mark.asyncio
async def test_get_gear_details_exception(app_with_gear, mock_garmin_client):
    mock_garmin_client.get_gear_details.side_effect = Exception("API Error")
    result = await app_with_gear.call_tool("get_gear_details", {"gear_uuid": DASHED})
    assert "Error retrieving gear details" in _text(result)


@pytest.mark.asyncio
async def test_get_gear_activities_curates(app_with_gear, mock_garmin_client):
    mock_garmin_client.get_gear_activities.return_value = [{
        "activityId": 99, "activityName": "Easy run",
        "activityType": {"typeKey": "running"}, "startTimeLocal": "2026-10-20 07:00:00",
        "distance": 10500.0, "duration": 3300.0, "averageHR": 140,
    }]
    payload = json.loads(_text(await app_with_gear.call_tool(
        "get_gear_activities", {"gear_uuid": UNDASHED, "limit": 500}
    )))
    assert payload["activities"][0] == {
        "activity_id": 99, "name": "Easy run", "type": "running",
        "start_time_local": "2026-10-20 07:00:00", "distance_km": 10.5, "duration_min": 55.0,
    }
    mock_garmin_client.get_gear_activities.assert_called_once_with(DASHED, limit=100)


@pytest.mark.asyncio
async def test_get_gear_activities_exception(app_with_gear, mock_garmin_client):
    mock_garmin_client.get_gear_activities.side_effect = Exception("API Error")
    result = await app_with_gear.call_tool("get_gear_activities", {"gear_uuid": DASHED})
    assert "Error retrieving gear activities" in _text(result)


MAKE_MODEL = {
    "gearMakeDTO": {"gearMakePk": 105, "gearMakeName": "Nike"},
    "gearModelDTO": {"gearModelPk": 133, "gearTypePk": 1, "gearModelName": "Air Zoom Pegasus"},
    "makeModelName": "Nike Air Zoom Pegasus",
}


@pytest.mark.asyncio
async def test_search_gear_models_names_the_gear_type(app_with_gear, mock_garmin_client):
    mock_garmin_client.get_gear_make_models.return_value = [MAKE_MODEL]
    mock_garmin_client.get_gear_types.return_value = [{"gearTypePk": 1, "gearTypeName": "Shoes"}]
    payload = json.loads(_text(
        await app_with_gear.call_tool("search_gear_models", {"keywords": "pegasus"})
    ))
    assert payload["models"][0] == {
        "brand": "Nike", "model": "Air Zoom Pegasus",
        "full_name": "Nike Air Zoom Pegasus", "gear_type": "Shoes",
    }


@pytest.mark.asyncio
async def test_search_gear_models_survives_gear_types_failure(app_with_gear, mock_garmin_client):
    mock_garmin_client.get_gear_make_models.return_value = [MAKE_MODEL]
    mock_garmin_client.get_gear_types.side_effect = Exception("types down")
    payload = json.loads(_text(
        await app_with_gear.call_tool("search_gear_models", {"keywords": "pegasus"})
    ))
    assert payload["models"][0]["gear_type"] == 1


@pytest.mark.asyncio
async def test_search_gear_models_exception(app_with_gear, mock_garmin_client):
    mock_garmin_client.get_gear_make_models.side_effect = Exception("API Error")
    result = await app_with_gear.call_tool("search_gear_models", {"keywords": "x"})
    assert "Error searching gear models" in _text(result)


@pytest.mark.asyncio
async def test_get_gear_types(app_with_gear, mock_garmin_client):
    mock_garmin_client.get_gear_types.return_value = [
        {"gearTypePk": 1, "gearTypeName": "Shoes"}, {"gearTypePk": 2, "gearTypeName": "Bike"},
    ]
    mock_garmin_client.get_user_gear_types.return_value = {
        "activeGearTypes": ["BIKE", "SHOES"], "retiredGearTypes": ["SHOES"],
    }
    payload = json.loads(_text(await app_with_gear.call_tool("get_gear_types", {})))
    assert payload == {
        "gear_types": ["Shoes", "Bike"],
        "your_active_types": ["BIKE", "SHOES"],
        "your_retired_types": ["SHOES"],
    }


@pytest.mark.asyncio
async def test_get_gear_types_exception(app_with_gear, mock_garmin_client):
    mock_garmin_client.get_gear_types.side_effect = Exception("API Error")
    assert "Error retrieving gear types" in _text(await app_with_gear.call_tool("get_gear_types", {}))


@pytest.mark.asyncio
async def test_create_gear_passes_defaults_as_activity_type_keys(app_with_gear, mock_garmin_client):
    mock_garmin_client.create_gear.return_value = GEAR_V2
    payload = json.loads(_text(await app_with_gear.call_tool("create_gear", {
        "gear_type": "SHOES", "brand": "Brand", "model": "Model 5", "name": "Race shoes",
        "first_use_date": "2026-03-01", "max_usage_distance_km": 800,
        "default_for_activity_types": ["running"],
    })))
    assert payload["status"] == "success" and payload["gear"]["uuid"] == DASHED
    kwargs = mock_garmin_client.create_gear.call_args.kwargs
    assert kwargs["activity_type_keys"] == ["running"]
    assert kwargs["max_usage_distance_km"] == 800


@pytest.mark.asyncio
async def test_create_gear_exception(app_with_gear, mock_garmin_client):
    mock_garmin_client.create_gear.side_effect = Exception("API Error")
    result = await app_with_gear.call_tool("create_gear", {
        "gear_type": "SHOES", "brand": "B", "model": "M", "name": "N", "first_use_date": "2026-03-01",
    })
    assert "Error creating gear" in _text(result)


@pytest.mark.asyncio
async def test_update_gear_forwards_only_given_fields_including_clearing_values(
    app_with_gear, mock_garmin_client
):
    mock_garmin_client.update_gear.return_value = GEAR_V2
    await app_with_gear.call_tool("update_gear", {
        "gear_uuid": UNDASHED, "notes": "", "max_usage_distance_km": 0,
        "default_for_activity_types": [],
    })
    mock_garmin_client.update_gear.assert_called_once_with(
        DASHED, notes="", max_usage_distance_km=0, activity_type_keys=[]
    )


@pytest.mark.asyncio
async def test_update_gear_without_fields_changes_nothing(app_with_gear, mock_garmin_client):
    result = await app_with_gear.call_tool("update_gear", {"gear_uuid": DASHED})
    assert "pass at least one field" in _text(result)
    mock_garmin_client.update_gear.assert_not_called()


@pytest.mark.asyncio
async def test_update_gear_exception(app_with_gear, mock_garmin_client):
    mock_garmin_client.update_gear.side_effect = Exception("API Error")
    result = await app_with_gear.call_tool("update_gear", {"gear_uuid": DASHED, "name": "X"})
    assert "Error updating gear" in _text(result)


@pytest.mark.asyncio
async def test_set_gear_status_retires_with_dashed_uuid(app_with_gear, mock_garmin_client):
    payload = json.loads(_text(await app_with_gear.call_tool(
        "set_gear_status", {"gear_uuid": UNDASHED, "status": "retired"}
    )))
    mock_garmin_client.set_gear_status.assert_called_once_with(DASHED, "RETIRED")
    assert payload["gear_status"] == "RETIRED"


@pytest.mark.asyncio
async def test_set_gear_status_exception(app_with_gear, mock_garmin_client):
    mock_garmin_client.set_gear_status.side_effect = Exception("API Error")
    result = await app_with_gear.call_tool("set_gear_status", {"gear_uuid": DASHED, "status": "active"})
    assert "Error setting gear status" in _text(result)


@pytest.mark.asyncio
async def test_delete_gear_without_confirm_only_previews(app_with_gear, mock_garmin_client):
    mock_garmin_client.get_gear_details.return_value = GEAR_V2
    payload = json.loads(_text(await app_with_gear.call_tool("delete_gear", {"gear_uuid": DASHED})))
    assert payload["status"] == "needs_confirmation"
    assert payload["gear"]["activities"] == 42
    mock_garmin_client.delete_gear.assert_not_called()


@pytest.mark.asyncio
async def test_delete_gear_with_confirm_deletes(app_with_gear, mock_garmin_client):
    payload = json.loads(_text(await app_with_gear.call_tool(
        "delete_gear", {"gear_uuid": UNDASHED, "confirm": True}
    )))
    mock_garmin_client.delete_gear.assert_called_once_with(DASHED)
    assert payload["status"] == "success"


@pytest.mark.asyncio
async def test_delete_gear_exception(app_with_gear, mock_garmin_client):
    mock_garmin_client.delete_gear.side_effect = Exception("API Error")
    result = await app_with_gear.call_tool("delete_gear", {"gear_uuid": DASHED, "confirm": True})
    assert "Error deleting gear" in _text(result)

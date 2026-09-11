from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from orchestrator_agent.app.cloud_journey.tools import JourneyService


def test_state_survives_engine_and_service_restart(engine, service) -> None:
    journey_id = service.start("APM004001", "sam")["journey_id"]
    service.continue_journey(journey_id)
    database_url = engine.url.render_as_string(hide_password=False)

    engine.dispose()
    restarted_engine = create_engine(
        database_url, connect_args={"check_same_thread": False, "timeout": 30}
    )
    restarted_factory = sessionmaker(
        bind=restarted_engine, expire_on_commit=False, class_=Session
    )
    restarted_service = JourneyService(restarted_factory)

    recovered = restarted_service.status(journey_id)
    assert recovered["current_state"] == "WAITING_FOR_APPROVAL"
    assert recovered["version"] == 8
    assert recovered["requested_by"] == "sam"
    restarted_engine.dispose()


def test_full_audit_history_distinguishes_user_and_agent(service) -> None:
    journey_id = service.start("APM004001", "sam")["journey_id"]
    service.continue_journey(journey_id)
    service.record_external_approval(journey_id, "reviewer")
    result = service.resume_after_approval(journey_id)

    assert result["state_path"] == [
        "CREATED",
        "VALIDATING_APM",
        "APM_VALIDATED",
        "DISCOVERING_CLOUD_SERVICES",
        "COLLECTING_ASSET_INVENTORY",
        "ASSET_INVENTORY_COMPLETE",
        "GENERATING_PLAN",
        "WAITING_FOR_APPROVAL",
        "APPROVED",
        "PROVISIONING_AGENT_IDENTITY",
        "AGENT_IDENTITY_READY",
        "PREPARING_APP_FACTORY",
        "APP_FACTORY_READY",
        "SUBMITTING_CLOUD_BUILD",
        "CLOUD_BUILD_RUNNING",
        "VALIDATING_DEPLOYMENT",
        "COMPLETED",
    ]
    approval = next(
        event for event in result["history"] if event["to_state"] == "APPROVED"
    )
    cloud_build = next(
        event
        for event in result["history"]
        if event["to_state"] == "SUBMITTING_CLOUD_BUILD"
    )
    assert (approval["actor_type"], approval["actor_id"]) == (
        "APPROVAL_BACKEND",
        "reviewer",
    )
    assert (cloud_build["actor_type"], cloud_build["actor_id"]) == (
        "AGENT",
        "app-factory-helper-agent",
    )
    assert cloud_build["metadata"]["integration"] == "cloud-build-mcp"

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event, func, inspect, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from journey_durability.runtime import BatchRuntime
from journey_poc.cloud_journey.business_operations import LocalBusinessGateway
from journey_durability.checkpoints import CheckpointStore, OperationBusy
from journey_durability.models import (
    AgentExecution,
    BatchAgent,
    CheckpointEvent,
    CheckpointStage,
    CheckpointStatus,
    DurableBase,
    OperationCheckpoint,
)
from journey_poc.cloud_journey.models import Journey, JourneyExternalDependency, utc_now
from journey_poc.cloud_journey.state_machine import (
    InvalidTransition,
    JourneyUnavailable,
)


@pytest.fixture
def durable_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'checkpoints.sqlite'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    DurableBase.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def store(durable_engine):
    return CheckpointStore(sessionmaker(bind=durable_engine, expire_on_commit=False))


@pytest.fixture
def business(session_factory):
    return LocalBusinessGateway(session_factory)


def new_journey(service, *, inventory=True):
    # Create the batch input without running the legacy interactive validation.
    journey = service.state_machine.create_journey(
        apm_id="APM004001",
        requested_by="sam",
        requested_by_email="sam@example.com",
        role="PROJECT_OWNER",
        access_group_id="GROUP_1",
        context={"inventory": {"application_name": "Example"}} if inventory else {},
    )
    return journey.id


def test_jobs_resume_same_ad_request_after_restart_and_stop_at_readiness(
    service, business, store, durable_engine, engine
):
    journey_id = new_journey(service)
    runtime = BatchRuntime(store, business)
    apm = runtime.run(BatchAgent.APM_VALIDATION, journey_id, "workflow-1")
    submitted = runtime.run(
        BatchAgent.AD_PROVISIONING, journey_id, "workflow-1", mode="submit"
    )
    assert apm.checkpoint_status == "COMPLETED"
    assert submitted.checkpoint_status == "WAITING"
    assert submitted.current_stage == "AD_POLLING"
    checkpoint_url = str(durable_engine.url)
    business_url = str(engine.url)
    durable_engine.dispose()
    engine.dispose()
    restarted_engine = create_engine(checkpoint_url)
    restarted_business_engine = create_engine(business_url)
    restarted_store = CheckpointStore(
        sessionmaker(bind=restarted_engine, expire_on_commit=False)
    )
    restarted_business = LocalBusinessGateway(
        sessionmaker(bind=restarted_business_engine, expire_on_commit=False)
    )
    try:
        restarted = BatchRuntime(restarted_store, restarted_business)
        pending = restarted.run(
            BatchAgent.AD_PROVISIONING, journey_id, "workflow-2", mode="poll"
        )
        complete = restarted.run(
            BatchAgent.AD_PROVISIONING, journey_id, "workflow-3", mode="poll"
        )
        ready = restarted.run(BatchAgent.APP_FACTORY_HELPER, journey_id, "workflow-3")
        assert pending.checkpoint_status == "WAITING"
        assert complete.checkpoint_status == ready.checkpoint_status == "COMPLETED"
        assert (
            submitted.checkpoint_id == pending.checkpoint_id == complete.checkpoint_id
        )
        assert (
            submitted.external_reference
            == pending.external_reference
            == complete.external_reference
        )
        assert service.status(journey_id)["current_state"] == "READY_TO_PROVISION"
        with restarted_store.session_factory() as session:
            checkpoints = session.scalars(select(OperationCheckpoint)).all()
            assert len(checkpoints) == 3
            executions = session.scalars(select(AgentExecution)).all()
            assert len(executions) == 5
            assert all(
                item.execution_result == "SUCCEEDED" and item.ended_at
                for item in executions
            )
            events = session.scalars(
                select(CheckpointEvent).where(
                    CheckpointEvent.checkpoint_id == complete.checkpoint_id
                )
            ).all()
            assert {item.execution_id for item in events} == {
                submitted.execution_id,
                pending.execution_id,
                complete.execution_id,
            }
            assert any(e.previous_status == e.new_status == "WAITING" for e in events)
    finally:
        restarted_engine.dispose()
        restarted_business_engine.dispose()


def test_business_commit_before_checkpoint_failure_does_not_resubmit(
    service, business, store, monkeypatch, session_factory
):
    journey_id = new_journey(service)
    runtime = BatchRuntime(store, business)
    runtime.run(BatchAgent.APM_VALIDATION, journey_id, "run")
    original_save = store.save
    failed = False

    def fail_once(checkpoint, **kwargs):
        nonlocal failed
        if kwargs["status"] == CheckpointStatus.WAITING and not failed:
            failed = True
            raise RuntimeError("checkpoint write interrupted")
        return original_save(checkpoint, **kwargs)

    monkeypatch.setattr(store, "save", fail_once)
    with pytest.raises(RuntimeError, match="interrupted"):
        runtime.run(BatchAgent.AD_PROVISIONING, journey_id, "run", mode="submit")
    monkeypatch.setattr(
        business, "submit_ad", lambda *_args: pytest.fail("duplicate submission")
    )
    recovered = runtime.run(
        BatchAgent.AD_PROVISIONING, journey_id, "retry", mode="poll"
    )
    assert recovered.checkpoint_status == "WAITING"
    assert recovered.external_reference
    with session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(JourneyExternalDependency))
            == 1
        )
    with store.session_factory() as session:
        failed_execution = session.scalar(
            select(AgentExecution).where(AgentExecution.execution_result == "FAILED")
        )
        assert failed_execution.error == "checkpoint write interrupted"


def test_poll_failure_retains_reference_and_recovers(
    service, business, store, monkeypatch
):
    journey_id = new_journey(service)
    runtime = BatchRuntime(store, business)
    runtime.run(BatchAgent.APM_VALIDATION, journey_id, "run")
    submitted = runtime.run(BatchAgent.AD_PROVISIONING, journey_id, "run")
    original = business.poll_ad

    def fail(*_args):
        raise RuntimeError("MyAccess unavailable")

    monkeypatch.setattr(business, "poll_ad", fail)
    with pytest.raises(RuntimeError, match="unavailable"):
        runtime.run(BatchAgent.AD_PROVISIONING, journey_id, "run2", mode="poll")
    with store.session_factory() as session:
        checkpoint = session.get(OperationCheckpoint, submitted.checkpoint_id)
        assert checkpoint.checkpoint_status == "FAILED"
        assert checkpoint.current_stage == "AD_POLLING"
        assert checkpoint.external_reference == submitted.external_reference
        assert checkpoint.last_error == "MyAccess unavailable"
    monkeypatch.setattr(business, "poll_ad", original)
    recovered = runtime.run(BatchAgent.AD_PROVISIONING, journey_id, "run3", mode="poll")
    assert recovered.external_reference == submitted.external_reference


def test_expired_invocation_is_fenced_and_can_be_recovered(store):
    old = store.claim("J-LEASE", BatchAgent.AD_PROVISIONING, "old")
    with pytest.raises(OperationBusy):
        store.claim("J-LEASE", BatchAgent.AD_PROVISIONING, "concurrent")
    with store.session_factory.begin() as session:
        session.execute(
            update(OperationCheckpoint).values(
                lease_expires_at=utc_now() - timedelta(seconds=1)
            )
        )
    new = store.claim("J-LEASE", BatchAgent.AD_PROVISIONING, "new")
    with pytest.raises(OperationBusy):
        store.save(
            old, stage=CheckpointStage.AD_SUBMISSION, status=CheckpointStatus.RUNNING
        )
    with pytest.raises(OperationBusy):
        store.finish(old)
    with store.session_factory() as session:
        assert (
            session.get(AgentExecution, old.latest_execution_id).execution_result
            == "INTERRUPTED"
        )
        assert (
            session.get(OperationCheckpoint, new.checkpoint_id).latest_execution_id
            == new.latest_execution_id
        )


def test_validation_error_is_a_completed_operation(service, business, store):
    journey_id = new_journey(service, inventory=False)
    business.pending_polls = 0
    runtime = BatchRuntime(store, business)
    runtime.run(BatchAgent.APM_VALIDATION, journey_id, "run")
    runtime.run(BatchAgent.AD_PROVISIONING, journey_id, "run", mode="submit")
    runtime.run(BatchAgent.AD_PROVISIONING, journey_id, "run", mode="poll")
    result = runtime.run(BatchAgent.APP_FACTORY_HELPER, journey_id, "run")
    assert result.checkpoint_status == "COMPLETED"
    status = service.status(journey_id)
    assert status["current_state"] == "APP_FACTORY_VALIDATION_ERROR"
    validation = next(
        item
        for item in status["operation_status"]
        if item["operation_key"] == "app-factory-validation"
    )
    assert validation["result"]["validation_errors"]


def test_store_boundaries_status_reads_and_batch_ownership(
    service, business, store, engine, durable_engine
):
    journey_id = new_journey(service)
    BatchRuntime(store, business).run(BatchAgent.APM_VALIDATION, journey_id, "run")
    with store.session_factory() as session:
        before = session.scalar(select(func.count()).select_from(CheckpointEvent))
    assert (
        business.authorized_status(journey_id, "sam")["current_state"]
        == "APM_VALIDATED"
    )
    with pytest.raises(JourneyUnavailable):
        business.authorized_status(journey_id, "abdur")
    with store.session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(CheckpointEvent)) == before
        )
    assert "operation_checkpoint" not in inspect(engine).get_table_names()
    assert set(inspect(durable_engine).get_table_names()) == {
        "agent_execution",
        "operation_checkpoint",
        "checkpoint_event",
    }
    with pytest.raises(ValueError):
        store.claim(journey_id, "chat-assistant", "run")
    with pytest.raises(IntegrityError):
        with store.session_factory.begin() as session:
            session.execute(
                update(OperationCheckpoint).values(checkpoint_status="RETRYING")
            )


def test_completed_work_is_not_repeated(service, business, store, monkeypatch):
    journey_id = new_journey(service)
    runtime = BatchRuntime(store, business)
    first = runtime.run(BatchAgent.APM_VALIDATION, journey_id, "run1")
    monkeypatch.setattr(
        business, "validate_apm", lambda *_args: pytest.fail("repeated validation")
    )
    second = runtime.run(BatchAgent.APM_VALIDATION, journey_id, "run2")
    assert first.checkpoint_id == second.checkpoint_id
    assert first.execution_id != second.execution_id


def test_poll_without_submission_fails_without_creating_external_request(
    service, business, store, session_factory
):
    journey_id = new_journey(service)
    with pytest.raises(ValueError, match="saved MyAccess"):
        BatchRuntime(store, business).run(
            BatchAgent.AD_PROVISIONING, journey_id, "run", mode="poll"
        )
    with session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(JourneyExternalDependency))
            == 0
        )


def test_simultaneous_invocations_have_only_one_owner(store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def claim(run):
        barrier.wait()
        try:
            return store.claim("J-CONCURRENT", BatchAgent.AD_PROVISIONING, run)
        except OperationBusy:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["one", "two"]))
    assert sum(result is not None for result in results) == 1
    with store.session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(OperationCheckpoint)) == 1
        )
        assert session.scalar(select(func.count()).select_from(AgentExecution)) == 1


def test_hard_crash_after_submission_recovers_from_business_record(
    service, business, store, monkeypatch
):
    journey_id = new_journey(service)
    runtime = BatchRuntime(store, business)
    runtime.run(BatchAgent.APM_VALIDATION, journey_id, "run")
    original = business.submit_ad

    def commit_then_crash(*args):
        original(*args)
        raise SystemExit("process terminated")

    monkeypatch.setattr(business, "submit_ad", commit_then_crash)
    with pytest.raises(SystemExit):
        runtime.run(BatchAgent.AD_PROVISIONING, journey_id, "crashed")
    with store.session_factory.begin() as session:
        session.execute(
            update(OperationCheckpoint)
            .where(OperationCheckpoint.operation_key == "ad-provisioning")
            .values(lease_expires_at=utc_now() - timedelta(seconds=1))
        )
    monkeypatch.setattr(
        business, "submit_ad", lambda *_args: pytest.fail("duplicate external request")
    )
    recovered = runtime.run(
        BatchAgent.AD_PROVISIONING, journey_id, "restarted", mode="poll"
    )
    assert recovered.checkpoint_status == "WAITING"
    assert recovered.external_reference


def test_chat_status_authorization_uses_verified_identity_and_has_no_writes(
    service, session_factory, monkeypatch
):
    from types import SimpleNamespace
    from journey_poc.cloud_journey import chat_status
    from journey_poc.cloud_journey.identity import verified_identity_state

    journey_id = new_journey(service)
    monkeypatch.setattr(chat_status, "SessionLocal", session_factory)

    def context(subject, runtime_subject=None):
        return SimpleNamespace(
            user_id=runtime_subject or subject,
            state=verified_identity_state(
                {"subject": subject, "email": f"{subject}@example.com"}
            ),
        )

    before = service.status(journey_id)
    assert chat_status.get_journey_progress(journey_id, context("sam"))["ok"]
    assert chat_status.get_journey_progress_by_apm_id("APM004001", context("sam"))["ok"]
    forbidden = chat_status.get_journey_progress(journey_id, context("abdur"))
    missing = chat_status.get_journey_progress("J-MISSING", context("abdur"))
    assert forbidden == missing
    assert forbidden["ok"] is False
    assert (
        chat_status.get_journey_progress(journey_id, context("sam", "abdur"))["ok"]
        is False
    )
    assert service.status(journey_id) == before


def test_batch_transitions_do_not_allow_interactive_resume_without_approval(
    service, business, store
):
    journey_id = new_journey(service)
    runtime = BatchRuntime(store, business)
    runtime.run(BatchAgent.APM_VALIDATION, journey_id, "run")
    with pytest.raises(InvalidTransition):
        service.resume_after_approval(journey_id)
    assert service.status(journey_id)["current_state"] == "APM_VALIDATED"
    runtime.run(BatchAgent.AD_PROVISIONING, journey_id, "run", mode="submit")
    with pytest.raises(InvalidTransition):
        service.resume_after_approval(journey_id)
    assert service.status(journey_id)["current_state"] == "PROVISIONING_AGENT_IDENTITY"

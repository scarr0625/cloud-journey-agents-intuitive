from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "agent_apm_validation.main",
        "agent_ad_provisioning.main",
        "agent_app_factory.main",
        "agent_assistant.main",
        "agent_orchestrator.main",
    ],
)
def test_deployed_entry_points_import_without_business_database(module):
    # Even broken business configuration must not affect the agent deployments.
    script = f"""
import os, importlib, sys
os.environ['DATABASE_URL'] = 'invalid://must-not-be-used'
os.environ['CLOUD_SQL_INSTANCE'] = 'must-not-be-used'
importlib.import_module({module!r})
assert not any(name.startswith('journey_poc') for name in sys.modules)
if {module!r} in ('agent_assistant.main', 'agent_orchestrator.main'):
    assert not any(name.startswith('journey_durability') for name in sys.modules)
else:
    assert not any(name.startswith('journey_sessions') for name in sys.modules)
"""
    subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True
    )


@pytest.mark.parametrize(
    "module", ["agent_apm_validation", "agent_ad_provisioning", "agent_app_factory"]
)
def test_each_batch_image_has_its_own_fixed_identity(module):
    result = subprocess.run(
        [sys.executable, "-m", f"{module}.main", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--journey-id" in result.stdout
    assert "--workflow-run-id" in result.stdout
    assert "--agent" not in result.stdout
    assert ("--mode" in result.stdout) == (module == "agent_ad_provisioning")


def test_durable_configuration_never_falls_back_to_business_database(monkeypatch):
    from journey_durability.database import build_durable_engine

    monkeypatch.delenv("DURABLE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DURABLE_CLOUD_SQL_INSTANCE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    with pytest.raises(ValueError, match="DURABLE_DATABASE_URL"):
        build_durable_engine()


def test_docker_contexts_package_only_their_own_agent_and_required_libraries():
    root = Path(__file__).resolve().parents[1]
    folders = list((root / "src").iterdir())
    assert len(folders) == 5
    for folder in folders:
        dockerfile = (folder / "Dockerfile").read_text()
        assert f"COPY src/{folder.name} " in dockerfile
        assert "COPY . " not in dockerfile
        assert "examples" not in dockerfile
        for other in folders:
            if other != folder:
                assert f"COPY src/{other.name} " not in dockerfile
        batch = folder.name not in {"agent-assistant", "agent-orchestrator"}
        assert ("COPY packages/journey-durability " in dockerfile) == batch
        assert ("COPY packages/journey-sessions " in dockerfile) != batch

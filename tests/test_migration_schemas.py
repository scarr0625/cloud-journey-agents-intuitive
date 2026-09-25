"""Keep checked-in PostgreSQL schemas aligned with the actual runtime models."""

from importlib.metadata import version
from pathlib import Path
import re
import tomllib

from google.adk.sessions.migration import _schema_check_utils
from google.adk.sessions.schemas.v1 import Base as SessionBase
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from cloud_journey_agents.durability.models import DurableBase
from journey_poc.cloud_journey.models import Base as BusinessBase


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"


def compact(sql):
    return re.sub(r"\s+", "", sql)


@pytest.mark.parametrize(
    "metadata,filename",
    [
        (SessionBase.metadata, "session-state/000_adk_sessions.sql"),
        (DurableBase.metadata, "durable-state/000_execution_checkpoints.sql"),
    ],
)
def test_migration_tables_and_indexes_match_runtime_metadata(metadata, filename):
    migration = compact((MIGRATIONS / filename).read_text())
    dialect = postgresql.dialect()
    for table in metadata.sorted_tables:
        ddl = CreateTable(table, if_not_exists=True).compile(dialect=dialect)
        assert compact(str(ddl)) + ";" in migration, table.name
        for index in table.indexes:
            ddl = CreateIndex(index, if_not_exists=True).compile(dialect=dialect)
            assert compact(str(ddl)) + ";" in migration, index.name


def test_session_schema_version_and_dependency_pins_stay_in_sync():
    expected_version = "2.9.2"
    assert version("google-adk") == expected_version
    root_project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    shared_project = tomllib.loads((ROOT / "src/pyproject.toml").read_text())
    requirement = f"google-adk=={expected_version}"
    assert requirement in root_project["project"]["dependencies"]
    assert requirement in shared_project["project"]["optional-dependencies"]["chat"]
    migration = compact((MIGRATIONS / "session-state/000_adk_sessions.sql").read_text())
    schema_version = _schema_check_utils.LATEST_SCHEMA_VERSION
    assert f"VALUES('schema_version','{schema_version}')" in migration


def included_sql(path):
    """Resolve psql relative includes exactly as the bootstrap will use them."""
    content = path.read_text()
    for relative in re.findall(r"^\\ir\s+(\S+)\s*$", content, flags=re.MULTILINE):
        included = (path.parent / relative).resolve()
        assert included.is_relative_to(MIGRATIONS)
        content += "\n" + included_sql(included)
    return content


@pytest.mark.parametrize(
    "directory,expected_tables",
    [
        ("business-state", set(BusinessBase.metadata.tables)),
        ("durable-state", set(DurableBase.metadata.tables)),
        ("session-state", set(SessionBase.metadata.tables)),
    ],
)
def test_each_database_bootstrap_contains_only_its_own_tables(directory, expected_tables):
    sql = included_sql(MIGRATIONS / directory / "apply.sql")
    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))
    assert tables == expected_tables
    # A schema bootstrap must not grant the legacy demo users business access.
    assert not re.search(r"INSERT INTO (access_groups|access_group_members|apm_group_assignments)", sql)


def test_three_database_bootstrap_includes_resolve_after_folder_move():
    sql = included_sql(MIGRATIONS / "bootstrap.sql")
    assert set(re.findall(r"^\\connect :(\w+)", sql, flags=re.MULTILINE)) == {
        "business_db", "durable_db", "session_db"
    }
    assert "DROP DATABASE" not in sql
    # ON_ERROR_STOP propagates SQL errors through every nested psql script.
    # A psql quit command would only terminate the current included script.
    assert "RAISE EXCEPTION 'business_db, durable_db and session_db must be distinct.'" in sql
    assert "\\quit" not in sql

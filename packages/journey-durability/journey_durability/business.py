"""The business gateway contract consumed by batch execution, independent of SQL."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class BusinessProgress:
    stage: str
    status: str
    result_reference: str
    external_reference: str | None = None
    # COMPLETED means the operation finished; a negative business result must
    # also stop Workflows from launching the next agent.
    successful: bool = True


class BusinessGateway(Protocol):
    def read_progress(
        self, journey_id: str, operation_key: str
    ) -> BusinessProgress | None: ...
    def validate_apm(self, journey_id: str) -> BusinessProgress: ...
    def submit_ad(self, journey_id: str, idempotency_key: str) -> BusinessProgress: ...
    def poll_ad(self, journey_id: str, request_id: str) -> BusinessProgress: ...
    def validate_app_factory(self, journey_id: str) -> BusinessProgress: ...

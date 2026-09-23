import json

import httpx
import pytest

from journey_durability.mcp_gateway import McpBusinessGateway
from journey_mcp.client import McpClient, McpError, READ_TOOLS


class FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


def business_result(operation="ad-provisioning", outcome="PENDING"):
    return {
        "journey_id": "J-123",
        "operation_key": operation,
        "outcome": outcome,
        "result_reference": "result-123",
        "external_reference": "MA-123",
    }


@pytest.mark.parametrize(
    "operation,outcome,stage,status,successful",
    [
        ("apm-validation", "VALID", "APM_VALIDATION", "COMPLETED", True),
        ("apm-validation", "INVALID", "APM_VALIDATION", "COMPLETED", False),
        ("ad-provisioning", "PENDING", "AD_POLLING", "WAITING", True),
        ("ad-provisioning", "PROVISIONED", "AD_POLLING", "COMPLETED", True),
        ("ad-provisioning", "REJECTED", "AD_POLLING", "COMPLETED", False),
        (
            "app-factory-validation",
            "READY_TO_PROVISION",
            "APP_FACTORY_VALIDATION",
            "COMPLETED",
            True,
        ),
        (
            "app-factory-validation",
            "VALIDATION_ERROR",
            "APP_FACTORY_VALIDATION",
            "COMPLETED",
            False,
        ),
    ],
)
def test_business_outcomes_map_to_checkpoint_progress(
    operation, outcome, stage, status, successful
):
    gateway = McpBusinessGateway(FakeClient(business_result(operation, outcome)))
    result = gateway.read_progress("J-123", operation)
    assert (result.stage, result.status, result.successful) == (
        stage,
        status,
        successful,
    )


def test_absent_operation_is_not_a_failed_journey():
    assert (
        McpBusinessGateway(FakeClient(None)).read_progress("J-123", "apm-validation")
        is None
    )


@pytest.mark.parametrize("outcome", ["PROVISIONED", "REJECTED"])
def test_reconciled_terminal_ad_result_retains_request_reference(outcome):
    result = business_result(outcome=outcome)
    result["external_reference"] = None
    with pytest.raises(McpError, match="external_reference"):
        McpBusinessGateway(FakeClient(result)).read_progress("J-123", "ad-provisioning")


def test_ad_submission_and_poll_preserve_operation_and_request_ids():
    client = FakeClient(business_result())
    gateway = McpBusinessGateway(client)
    submitted = gateway.submit_ad("J-123", "J-123:ad-provisioning")
    gateway.poll_ad("J-123", submitted.external_reference)
    assert client.calls == [
        (
            "submit_ad_provisioning",
            {"journey_id": "J-123", "idempotency_key": "J-123:ad-provisioning"},
        ),
        ("poll_ad_provisioning", {"journey_id": "J-123", "request_id": "MA-123"}),
    ]
    client.result["external_reference"] = "MA-OTHER"
    with pytest.raises(McpError, match="original"):
        gateway.poll_ad("J-123", "MA-123")


@pytest.mark.parametrize(
    "field,value",
    [
        ("journey_id", "J-OTHER"),
        ("operation_key", "other"),
        ("result_reference", None),
        ("external_reference", None),
        ("outcome", "UNKNOWN"),
    ],
)
def test_invalid_business_result_is_not_saved_as_a_valid_checkpoint(field, value):
    result = business_result()
    result[field] = value
    with pytest.raises(McpError):
        McpBusinessGateway(FakeClient(result)).submit_ad("J-123", "key")


def test_read_only_mcp_client_rejects_writes_before_authentication_or_network(
    monkeypatch,
):
    client = McpClient(READ_TOOLS, url="https://example.invalid/mcp")
    monkeypatch.setattr(
        client, "_headers", lambda *_: pytest.fail("must reject before transport")
    )
    with pytest.raises(McpError, match="cannot call"):
        client.call("submit_ad_provisioning", {"journey_id": "J-123"})


@pytest.mark.parametrize("tool_error", [False, True])
def test_real_mcp_sdk_streamable_http_transport(monkeypatch, tool_error):
    # Exercise the actual SDK handshake and tool call without external services.
    original_client = httpx.AsyncClient
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        method = body["method"]
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {"name": "get_journey_status", "inputSchema": {"type": "object"}}
                ]
            }
        else:
            assert method == "tools/call"
            assert body["params"]["arguments"] == {"journey_id": "J-123"}
            result = {
                "isError": tool_error,
                "content": [],
                "structuredContent": {
                    "journey_id": "J-123",
                    "journey_state": "APM_VALIDATED",
                },
            }
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": result}
        )

    class MockClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockClient)
    monkeypatch.delenv("MCP_CLOUD_RUN_AUDIENCE", raising=False)
    monkeypatch.setenv("MCP_USER_AUTH_HEADER", "X-User-Authorization")
    client = McpClient(READ_TOOLS, url="https://mcp.test/mcp")
    if tool_error:
        with pytest.raises(McpError):
            client.call(
                "get_journey_status",
                {"journey_id": "J-123"},
                user_token="verified-token",
            )
    else:
        result = client.call(
            "get_journey_status", {"journey_id": "J-123"}, user_token="verified-token"
        )
        assert result["journey_state"] == "APM_VALIDATED"
    assert any(
        json.loads(request.content)["method"] == "tools/call" for request in requests
    )
    assert all(
        request.headers["X-User-Authorization"] == "Bearer verified-token"
        for request in requests
    )

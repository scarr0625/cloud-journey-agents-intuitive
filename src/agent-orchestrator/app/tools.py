"""Route a user question to the independently deployed Assistant over HTTP.

The tool forwards the verified request token and, when configured, adds
a workload ID token for the Assistant's Cloud Run audience. It also sends
the previous Assistant session ID so follow-up questions share context.

The response is validated before returning its answer and saving the
updated downstream session reference. Credentials are never added to that
session state. Missing configuration or failed HTTP responses produce an
explicit tool error instead of an invented Journey answer.
"""

import httpx
from google.adk.tools import ToolContext

from cloud_journey_agents.identity import service_identity_token
from cloud_journey_agents.sessions.conversation import QueryResponse

from .context import ASSISTANT_SESSION_ID_KEY, assistant_context
from .settings import assistant_audience, assistant_url


def query_assistant(query: str, tool_context: ToolContext) -> dict:
    """Route a user question to the Journey Assistant with verified user context."""
    token, session_id = assistant_context(tool_context)
    url = assistant_url()
    if not url or not token:
        return {
            "ok": False,
            "message": "Assistant URL and verified user context are required",
        }
    headers = {"X-User-Authorization": f"Bearer {token}"}
    audience = assistant_audience()
    if audience:
        headers["Authorization"] = f"Bearer {service_identity_token(audience)}"
    payload = {"query": query}
    if session_id:
        payload["session_id"] = session_id
    try:
        response = httpx.post(
            f"{url}/v1/query", json=payload, headers=headers, timeout=90
        )
        response.raise_for_status()
        result = QueryResponse.model_validate(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "ok": False,
            "message": f"Assistant request failed ({type(exc).__name__})",
        }
    # Persist only the downstream session reference, never the delegated token.
    tool_context.state[ASSISTANT_SESSION_ID_KEY] = result.session_id
    return {"ok": True, "answer": result.answer}

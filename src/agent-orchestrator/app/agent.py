"""Compose the Orchestrator's ADK agent with its Assistant-routing tool.

The model and prompt come from this application's settings and instruction
modules. query_assistant is its only tool, keeping the conversation router
focused on delegating user questions to the independently deployed service.

This module creates the agent definition. The session binding constructs
its persistent runtime on demand; batch execution is outside this agent's
tool surface.
"""

from .settings import APP_NAME, MODEL

from google.adk.agents import Agent

from .prompt import INSTRUCTION
from .tools import query_assistant

root_agent = Agent(
    name=APP_NAME,
    model=MODEL,
    instruction=INSTRUCTION,
    tools=[query_assistant],
)

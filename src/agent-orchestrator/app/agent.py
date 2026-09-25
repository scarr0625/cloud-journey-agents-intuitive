"""Compose the Orchestrator's model, prompt, and routing tools."""

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

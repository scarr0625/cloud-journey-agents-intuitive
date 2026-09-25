"""Compose the Assistant's model, prompt, and read-only tools."""

from .settings import APP_NAME, MODEL

from google.adk.agents import Agent

from .prompt import INSTRUCTION
from .tools import STATUS_TOOLS

root_agent = Agent(
    name=APP_NAME,
    model=MODEL,
    instruction=INSTRUCTION,
    tools=STATUS_TOOLS,
)

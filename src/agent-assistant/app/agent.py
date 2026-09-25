"""Compose the Assistant's ADK agent from its model, prompt, and status tools.

Keeping composition here lets HTTP and session modules use the same agent
definition. The tool list is limited to authorized Journey/APM status
reads; business writes and checkpoint execution belong to batch agents.

Importing this module creates the agent definition. sessions.py creates
the runner and database-backed conversation runtime when a request needs it.
"""

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

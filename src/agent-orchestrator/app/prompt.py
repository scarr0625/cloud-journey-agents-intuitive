"""Define the Orchestrator's responsibility for conversation routing.

Instructions direct each question to query_assistant and require clear
reporting of returned answers or tool failures. Business operations and
checkpoint execution are explicitly assigned to the separate batch flow.

agent.py installs this prompt with the routing tool. The server and tool
implementations enforce authentication and delegation independently of
the model's instructions.
"""

INSTRUCTION = (
    "Route every user question to query_assistant and report its answer. "
    "You coordinate conversation routing. Business operations and checkpoint "
    "execution are owned by the separate batch workflow. Report tool failures clearly."
)

"""Application package for the independently deployed Journey Orchestrator.

This agent owns conversational routing to the Assistant and its own
persisted session context. Adjacent modules define its model, routing
tool, verified request context, and HTTP interface.

Initializing this package does not launch batch workflows or open a
database; durable business execution belongs to the non-interactive agents.
"""

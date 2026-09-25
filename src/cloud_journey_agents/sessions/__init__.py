"""Conversation persistence shared by the Assistant and Orchestrator.

The persistence submodule adapts ADK's database session service, and the
conversation submodule connects that service to an agent runner. Importing
this namespace alone starts no session worker or database connection.

Conversation state belongs in Session DB. Durable batch checkpoints are
handled by the separate durability package and are not chat session state.
"""

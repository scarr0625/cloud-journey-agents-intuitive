"""Durable checkpoint capability for the three non-interactive agents.

Submodules provide workflow contracts, execution recovery, checkpoint
persistence, and optional MCP and HTTP/CLI adapters. Agents import the
pieces they use; importing this namespace opens no database connection.

The capability is independent of the shared batch.py and batch_server.py.
See this directory's README.md for the copy set, integration call sites,
required helpers, and database migration when porting it to the main repo.
"""

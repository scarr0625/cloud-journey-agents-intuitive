"""Schwab-side transactional persistence reference; never installed in agent images.

The host MCP server must authenticate requests and construct a trusted Principal.
PersistenceService then checks resource access and performs database transactions.
This package does not expose an unauthenticated HTTP service or create schemas.
"""

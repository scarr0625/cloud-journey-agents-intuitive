"""Application package for the independently deployed Journey Assistant.

The agent answers authorized status questions through read-only MCP tools.
Its model, prompt, request context, HTTP routes, and session binding live
in adjacent modules. This initializer only marks the package and does not
construct the agent runner or open Session DB.
"""

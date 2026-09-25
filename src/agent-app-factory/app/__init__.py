"""Application package for the independently deployed App Factory agent.

job.py defines the domain workflow, durability.py binds it to shared
checkpoint execution, and server.py provides CLI and HTTP entry points.
The package manifest exposes this app directory as agent_app_factory.

This initializer marks the package without opening databases or starting
business work. Each invocation uses the agent's own deployment identity.
"""

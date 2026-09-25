"""Shared infrastructure installed inside each independently deployed agent.

Importing this package activates the operating system's certificate trust
store before workloads create HTTP clients. This lets identity and MCP
requests use the same trusted certificate authorities as the host.

Agent entry points select the infrastructure they need from submodules.
This initializer starts no service and opens no database connection; the
package is code inside each image, not a network intermediary.
"""

import truststore

# Apply the host's trusted CAs before any workload creates an HTTP client.
truststore.inject_into_ssl()

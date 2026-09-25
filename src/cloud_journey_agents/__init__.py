"""Infrastructure shared inside each agent image; no service or database startup."""

import truststore

# Apply the host's trusted CAs before any workload creates an HTTP client.
truststore.inject_into_ssl()

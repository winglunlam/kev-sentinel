"""kev-sentinel: semantic drift monitoring for APIs and scrapers.

Two detection layers run against a recorded baseline:

1. Deterministic rules (schema fingerprint, status, content type, counts, blank
   ratio, payload size). Cheap, exact, but blind to a payload whose shape is
   unchanged while its meaning changed.
2. Semantic questions answered by a local Kev checkpoint. Recurring per-endpoint
   judgements, one batched request per endpoint, no text generation, no egress.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
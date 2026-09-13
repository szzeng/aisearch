"""Persistent P2 scheduler: device qualification, lease lifecycle and blocking recovery.

The package implements the ADR-0003 stable protocol (``p2-contracts.v1``) on top of the
frozen ADR-0002 job table. It never replaces the P1 scan path: only processors registered
with a :class:`~photo_search.scheduler.engine.Scheduler` are claimed by it.
"""

from photo_search.scheduler.contracts import (
    CONTRACT_VERSION,
    JobRequest,
    JobResult,
    ProcessorSpec,
    RuntimeCapabilities,
)

__all__ = [
    "CONTRACT_VERSION",
    "JobRequest",
    "JobResult",
    "ProcessorSpec",
    "RuntimeCapabilities",
]

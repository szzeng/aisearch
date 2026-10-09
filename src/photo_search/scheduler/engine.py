"""The persistent scheduler closed loop: claim, qualify, dispatch, submit, recover.

Design boundaries taken straight from the task card and the frozen ADRs:

* the main process is the only writer; an executor is an injected callable that receives a
  plain JSON ``JobRequest`` and returns a plain JSON ``JobResult``;
* one short ``BEGIN IMMEDIATE`` transaction selects a job, resolves the device and writes
  the dispatch snapshot together with the lease — never decoding, hashing or sleeping
  inside that transaction;
* a job that cannot be served is moved ``QUEUED -> RUNNING -> BLOCKED`` in the *same*
  transaction (the only transitions ADR-0002 §6.1 allows), consumes no retry attempt and
  never receives an observable lease;
* only processors registered with this scheduler are claimed, so the frozen P1
  ``catalog_scan`` path keeps its own claim/heartbeat semantics untouched.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from typing import Any

from photo_search.artifacts.store import ArtifactStore, PreparedArtifact
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    ARTIFACT_INVALID,
    INVALID_JOB_RESULT,
    INVALID_STATE_TRANSITION,
    RETRY_EXHAUSTED,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    RUNTIME_RESOLUTION_MISMATCH,
    SCHEDULER_EXECUTOR_ERROR,
    SCHEDULER_PAUSED,
    CatalogError,
)
from photo_search.jobs import queue as jobs
from photo_search.scheduler import capability as capability_module
from photo_search.scheduler import eligibility, state
from photo_search.scheduler.contracts import (
    ErrorEnvelope,
    InputSnapshot,
    JobRequest,
    JobResult,
    ProcessorSpec,
    RuntimeCapabilities,
)

#: Upper bound on how many runnable rows one claim examines, so an all-blocked backlog
#: cannot turn a single claim into an unbounded scan.
MAX_CANDIDATES_PER_CLAIM = 256

Executor = Callable[[dict[str, Any]], dict[str, Any]]


class Scheduler:
    """Main-process scheduler over one catalog connection pair (read + single writer)."""

    def __init__(
        self,
        catalog: Catalog,
        specs: Sequence[ProcessorSpec],
        *,
        lease_seconds: int | None = None,
        retry_base_ms: int = state.DEFAULT_RETRY_BASE_MS,
        retry_multiplier: int = state.DEFAULT_RETRY_MULTIPLIER,
        retry_max_ms: int = state.DEFAULT_RETRY_MAX_MS,
        artifact_store: ArtifactStore | None = None,
        job_ids: Sequence[int] | None = None,
    ) -> None:
        self.catalog = catalog
        self.job_ids = None if job_ids is None else tuple(int(value) for value in job_ids)
        self._specs: dict[str, ProcessorSpec] = {}
        for spec in specs:
            self._specs[spec.processor_id] = spec
        self.lease_seconds = int(
            catalog.lease_seconds if lease_seconds is None else lease_seconds
        )
        self.retry_base_ms = int(retry_base_ms)
        self.retry_multiplier = int(retry_multiplier)
        self.retry_max_ms = int(retry_max_ms)
        self.artifact_store = artifact_store or ArtifactStore.open(
            catalog.db_path.parent.parent
        )

    # ------------------------------------------------------------------ registry
    @property
    def specs(self) -> tuple[ProcessorSpec, ...]:
        return tuple(self._specs[key] for key in sorted(self._specs))

    def spec_for(self, processor_id: str) -> ProcessorSpec | None:
        return self._specs.get(str(processor_id))

    def _processor_ids(self) -> list[str]:
        return sorted(self._specs)

    # --------------------------------------------------------------------- policy
    def read_state(self) -> state.SchedulerState:
        return state.read_state(self.catalog.connection)

    def pause(self, *, now_ms: int, reason: str | None = None) -> dict[str, Any]:
        """Persistently stop claiming new work; live leases are untouched."""
        with self.catalog.write() as connection:
            snapshot, changed = state.set_paused(connection, paused=True, now_ms=now_ms)
        return {**snapshot.as_dict(), "changed": changed, "reason": reason}

    def resume(self, *, now_ms: int) -> dict[str, Any]:
        with self.catalog.write() as connection:
            snapshot, changed = state.set_paused(connection, paused=False, now_ms=now_ms)
        return {**snapshot.as_dict(), "changed": changed, "reason": None}

    def configure_aging_window(self, *, aging_window_ms: int, now_ms: int) -> dict[str, Any]:
        with self.catalog.write() as connection:
            snapshot, changed = state.configure_aging_window(
                connection, aging_window_ms=aging_window_ms, now_ms=now_ms
            )
        return {**snapshot.as_dict(), "changed": changed, "reason": None}

    # ------------------------------------------------------------------ runtime
    def record_capability(
        self, snapshot: RuntimeCapabilities, *, now_ms: int
    ) -> RuntimeCapabilities:
        with self.catalog.write() as connection:
            return capability_module.record_capability(connection, snapshot, now_ms=now_ms)

    def record_qualification(self, **options: Any) -> capability_module.QualificationEvidence:
        with self.catalog.write() as connection:
            return capability_module.record_qualification(connection, **options)

    def latest_capability(self) -> RuntimeCapabilities | None:
        return capability_module.latest_capability(self.catalog.connection)

    # -------------------------------------------------------------------- claim
    def claim_next(
        self,
        *,
        worker_id: str,
        now_ms: int,
        limit: int = MAX_CANDIDATES_PER_CLAIM,
    ) -> JobRequest | None:
        """Claim the best runnable job, or return ``None`` when nothing can be served."""
        with self.catalog.write() as connection:
            policy = state.read_state(connection)
            if policy.paused:
                raise CatalogError(
                    SCHEDULER_PAUSED,
                    "background scheduling is paused; no new job is claimed",
                    details={"revision": policy.revision},
                )
            processor_ids = self._processor_ids()
            jobs.requeue_due_retries(connection, now_ms=now_ms, processor_ids=processor_ids)
            jobs.fail_exhausted_leases(
                connection, now_ms=now_ms, processor_ids=processor_ids
            )
            candidates = jobs.claimable_candidates(
                connection,
                now_ms=now_ms,
                processor_ids=processor_ids,
                aging_window_ms=policy.aging_window_ms,
                limit=limit,
                task_types=[spec.task_type for spec in self.specs],
                job_ids=self.job_ids,
            )
            if not candidates:
                return None
            # One capability lookup per runtime package: an unhealthy GPU package must not
            # decide the fate of a processor that runs on a different runtime.
            snapshots: dict[str, RuntimeCapabilities | None] = {}
            for row in candidates:
                spec = self._specs.get(str(row["processor_id"]))
                if spec is None:  # pragma: no cover - the SQL already filters this
                    continue
                if spec.runtime_package_id not in snapshots:
                    snapshots[spec.runtime_package_id] = capability_module.latest_capability(
                        connection, runtime_package_id=spec.runtime_package_id
                    )
                snapshot = snapshots[spec.runtime_package_id]
                evidence = capability_module.qualification_evidence(
                    connection, processor_id=spec.processor_id
                )
                decision = eligibility.resolve(
                    spec,
                    requested_device=str(row["requested_device"]),
                    capability=snapshot,
                    evidence=evidence,
                )
                if not decision.eligible:
                    if str(row["state"]) == jobs.STATE_RUNNING:
                        # An expired lease we cannot serve right now: never take it over just
                        # to block it, because a takeover counts as one lost execution.
                        continue
                    self._block_unqualified(connection, row, decision, worker_id=worker_id, now_ms=now_ms)
                    continue
                try:
                    claimed = jobs.claim_job(
                        connection,
                        job_id=int(row["job_id"]),
                        worker_id=str(worker_id),
                        now_ms=int(now_ms),
                        lease_seconds=self.lease_seconds,
                    )
                except CatalogError as error:
                    if error.code == RETRY_EXHAUSTED:
                        # The takeover already committed its terminal failure; keep looking.
                        continue
                    raise
                connection.execute(
                    """
                    UPDATE jobs
                       SET resolved_device = ?, runtime_provider = ?, precision = ?,
                           runtime_capability_revision = ?, dispatch_spec_revision = ?,
                           blocking_reason = NULL
                     WHERE job_id = ?
                    """,
                    (
                        decision.device,
                        decision.provider,
                        decision.precision,
                        decision.capability_revision,
                        spec.spec_revision,
                        claimed.job_id,
                    ),
                )
                return self._request_from(jobs.get_job(connection, claimed.job_id))
            return None

    def _block_unqualified(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        decision: eligibility.Qualification,
        *,
        worker_id: str,
        now_ms: int,
    ) -> jobs.Job:
        """QUEUED -> RUNNING -> BLOCKED inside one transaction, consuming no attempt."""
        job = jobs.get_job(connection, int(row["job_id"]))
        claimed = jobs.claim_job(
            connection,
            job_id=job.job_id,
            worker_id=str(worker_id),
            now_ms=int(now_ms),
            lease_seconds=self.lease_seconds,
        )
        error = CatalogError(
            str(decision.reason_code or RUNTIME_CAPABILITY_UNAVAILABLE),
            decision.detail,
            details={"processor_id": claimed.processor_id, "requested_device": claimed.requested_device},
        )
        blocked = jobs.finish(
            connection,
            job_id=claimed.job_id,
            lease_token=str(claimed.lease_token),
            state=jobs.STATE_BLOCKED,
            input_revision=claimed.input_revision,
            processor_id=claimed.processor_id,
            now_ms=int(now_ms),
            error=error,
        )
        connection.execute(
            "UPDATE jobs SET blocking_reason = ?, resolved_device = NULL, runtime_provider = NULL, "
            "precision = NULL, runtime_capability_revision = NULL, dispatch_spec_revision = NULL "
            "WHERE job_id = ?",
            (str(blocked.last_error_code), blocked.job_id),
        )
        return jobs.get_job(connection, blocked.job_id)

    def _request_from(self, job: jobs.Job) -> JobRequest:
        return JobRequest(
            job_id=job.job_id,
            subject_type=job.subject_type,
            subject_id=job.subject_id,
            input_revision=job.input_revision,
            task_type=job.task_type,
            processor_id=job.processor_id,
            lease_token=str(job.lease_token),
            requested_device=job.requested_device,
            resolved_device=str(job.resolved_device),
            runtime_provider=str(job.runtime_provider),
            precision=str(job.precision),
            runtime_capability_revision=str(job.runtime_capability_revision),
            # ``None`` means "this subject does not carry that fact", never "skip the
            # submission check"; the lease/revision check still runs on every result.
            input_snapshot=InputSnapshot(
                subject_revision=job.input_revision,
                asset_sha256=None,
                location_id=None,
                byte_size=None,
                mtime_ns=None,
            ),
        )

    # ----------------------------------------------------------------- lifecycle
    def heartbeat(
        self,
        *,
        job_id: int,
        lease_token: str,
        worker_id: str,
        now_ms: int,
        lease_seconds: int | None = None,
    ) -> jobs.Job:
        with self.catalog.write() as connection:
            return jobs.heartbeat(
                connection,
                job_id=int(job_id),
                lease_token=str(lease_token),
                worker_id=str(worker_id),
                now_ms=int(now_ms),
                lease_seconds=self.lease_seconds if lease_seconds is None else int(lease_seconds),
            )

    def submit(self, result: JobResult, *, now_ms: int) -> jobs.Job:
        """Validate, publish and atomically commit artifact/outbox plus job state."""
        # File hashing/staging stays outside a SQLite transaction. This first qualification
        # avoids even a temporary file for an already stale/late execution; the exact same
        # checks run again under BEGIN IMMEDIATE before publication and database writes.
        job = jobs.validate_submission(
            self.catalog.connection,
            job_id=result.job_id,
            lease_token=result.lease_token,
            now_ms=int(now_ms),
            input_revision=result.input_revision,
            processor_id=result.processor_id,
        )
        self._validate_result_identity(job, result)
        spec = self.spec_for(result.processor_id)
        if spec is None:
            raise CatalogError(
                INVALID_JOB_RESULT,
                "the result processor is not registered with this scheduler",
            )
        if result.artifacts and result.state != jobs.STATE_SUCCEEDED:
            raise CatalogError(
                ARTIFACT_INVALID,
                "only a qualified SUCCEEDED result may publish artifacts",
            )
        prepared: list[PreparedArtifact] = []
        try:
            for ref in result.artifacts:
                prepared.append(self.artifact_store.prepare(ref))
            with self.catalog.write() as connection:
                job = jobs.validate_submission(
                    connection,
                    job_id=result.job_id,
                    lease_token=result.lease_token,
                    now_ms=int(now_ms),
                    input_revision=result.input_revision,
                    processor_id=result.processor_id,
                )
                self._validate_result_identity(job, result)
                self.artifact_store.persist_result(
                    connection,
                    job=job,
                    result=result,
                    spec=spec,
                    prepared=prepared,
                    now_ms=int(now_ms),
                )
                # P2-03A: a structured GPS fact becomes the asset's current GeoFact in the
                # very same transaction as the artifact, the artifact row and the job
                # terminal state. Imported lazily so the scheduler does not depend on the
                # geo package at import time.
                from photo_search.geo import extract as geo_extract

                if str(spec.processor_id) == geo_extract.GPS_PROCESSOR_ID:
                    geo_extract.persist_geo_fact(
                        connection,
                        job=job,
                        result=result,
                        prepared=prepared,
                        now_ms=int(now_ms),
                    )
                # P2-03B: a qualified nearby-city result becomes the immutable machine
                # resolution (and, when the visible set really changed, the independent
                # suggestion revision) in the very same transaction. Imported lazily for the
                # same reason as the geo hook: the scheduler does not depend on the resolver
                # at import time, and this build still has no resident scheduler thread.
                from photo_search.geo import resolver as geo_resolver

                if str(spec.processor_id) == geo_resolver.RESOLVER_PROCESSOR_ID:
                    geo_resolver.persist_resolution(
                        connection,
                        job=job,
                        result=result,
                        prepared=prepared,
                        now_ms=int(now_ms),
                    )
                # P3-01: a qualified OCR document becomes the asset's current OCR text (the
                # normalised search copy plus the region metadata) in the very same
                # transaction as the artifact, the artifact row and the job terminal state.
                # Imported lazily like the other hooks, and only the light ``contract``
                # module is touched unless this really is an OCR submission.
                from photo_search.ocr import contract as ocr_contract

                if str(spec.processor_id) in ocr_contract.OCR_PROCESSOR_IDS:
                    from photo_search.ocr import processor as ocr_processor

                    ocr_processor.persist_ocr_document(
                        connection,
                        job=job,
                        result=result,
                        prepared=prepared,
                        now_ms=int(now_ms),
                    )
                # P3-02: a qualified face document becomes the asset's current face facts
                # (the detection rows, the canonical vectors and the durable association
                # queue row) in the very same transaction as the artifact, the artifact row
                # and the job terminal state. The conservative *grouping* deliberately runs
                # later, once the independent face candidate index is confirmed, so a crash
                # can neither lose the facts nor silently report a face as grouped. Imported
                # lazily like the other hooks: the scheduler does not depend on the face
                # package at import time, and this build still has no resident scheduler
                # thread.
                from photo_search.face import contract as face_contract

                if str(spec.processor_id) == face_contract.FACE_PROCESSOR_ID:
                    from photo_search.face import processor as face_processor

                    face_processor.persist_face_document(
                        connection,
                        job=job,
                        result=result,
                        prepared=prepared,
                        now_ms=int(now_ms),
                    )
                # P4-00B: a qualified taken-time fact becomes the asset's current
                # ``asset_metadata`` row (and, when it really changed, the one
                # ``CAPTURE_TIME_CHANGED`` increment of the shared filter revision) in the
                # very same transaction as the artifact, the artifact row and the job
                # terminal state. Imported lazily like the other hooks: the scheduler does
                # not depend on the taken-time package at import time.
                from photo_search.taken_time import extract as taken_time_extract

                if str(spec.processor_id) == taken_time_extract.TAKEN_TIME_PROCESSOR_ID:
                    taken_time_extract.persist_taken_time_row(
                        connection,
                        job=job,
                        result=result,
                        prepared=prepared,
                        now_ms=int(now_ms),
                    )
                run_after_ms: int | None = None
                blocking_reason: str | None = None
                error: CatalogError | None = None
                if result.error is not None:
                    error = CatalogError(result.error.code, result.error.message)
                if result.state == jobs.STATE_RETRY_WAIT:
                    run_after_ms = int(now_ms) + state.backoff_delay_ms(
                        job.attempts + 1,
                        base_ms=self.retry_base_ms,
                        multiplier=self.retry_multiplier,
                        max_ms=self.retry_max_ms,
                    )
                elif result.state == jobs.STATE_BLOCKED:
                    blocking_reason = (
                        result.error.blocking_reason if result.error else None
                    ) or (result.error.code if result.error else RUNTIME_CAPABILITY_UNAVAILABLE)
                # ADR-0002 §6.5: progress only ever moves forward. A retry/blocked envelope may
                # legitimately report 0.0; that must not erase what this execution reached.
                progress: float | None = (
                    result.progress
                    if result.progress >= float(job.progress or 0.0)
                    else None
                )
                jobs.finish(
                    connection,
                    job_id=job.job_id,
                    lease_token=result.lease_token,
                    state=result.state,
                    input_revision=result.input_revision,
                    processor_id=result.processor_id,
                    now_ms=int(now_ms),
                    progress=progress,
                    error=error,
                    run_after_ms=run_after_ms,
                )
                connection.execute(
                    "UPDATE jobs SET blocking_reason = ? WHERE job_id = ?",
                    (blocking_reason, job.job_id),
                )
            self.artifact_store.discard(prepared)
            return jobs.get_job(self.catalog.connection, job.job_id)
        except BaseException:
            # If a file was published before SQLite rolled back, it is an unreferenced
            # orphan and is safe to remove. Pre-existing content-addressed files stay.
            self.artifact_store.discard(prepared, remove_published=True)
            raise

    @staticmethod
    def _validate_result_identity(job: jobs.Job, result: JobResult) -> None:
        if str(job.task_type) != result.task_type:
            raise CatalogError(
                INVALID_JOB_RESULT,
                "JobResult.task_type does not match the leased job",
                details={"job_id": job.job_id},
            )
        dispatched = (job.resolved_device, job.runtime_provider, job.precision)
        reported = (result.resolved_device, result.runtime_provider, result.precision)
        if dispatched != reported:
            raise CatalogError(
                RUNTIME_RESOLUTION_MISMATCH,
                f"job {job.job_id} was dispatched as {dispatched} but reports {reported}",
                details={
                    "job_id": job.job_id,
                    "dispatched": [value for value in dispatched],
                    "reported": [value for value in reported],
                },
            )

    def cancel(self, *, job_id: int, now_ms: int, reason: str | None = None) -> jobs.Job:
        """Cancel one job; clearing the lease in the same commit refuses late results."""
        with self.catalog.write() as connection:
            job = jobs.get_job(connection, int(job_id))
            if job.state in jobs.TERMINAL_STATES:
                raise CatalogError(
                    INVALID_STATE_TRANSITION,
                    f"job {job.job_id} is already {job.state}",
                    details={"job_id": job.job_id, "state": job.state},
                )
            connection.execute(
                """
                UPDATE jobs
                   SET state = ?, lease_token = NULL, lease_expires_at = NULL, worker_id = NULL,
                       blocking_reason = NULL, updated_at = ?
                 WHERE job_id = ?
                """,
                (jobs.STATE_CANCELLED, int(now_ms), job.job_id),
            )
            if reason:
                connection.execute(
                    "UPDATE jobs SET last_error_code = ?, last_error_message = ? WHERE job_id = ?",
                    ("CANCELLED", str(reason)[: jobs.MAX_ERROR_MESSAGE_CHARS], job.job_id),
                )
            return jobs.get_job(connection, job.job_id)

    def expire_leases(self, *, now_ms: int) -> list[int]:
        """Fail expired executions that have no retry budget left (ADR-0002 §6.2)."""
        with self.catalog.write() as connection:
            rows = jobs.fail_exhausted_leases(
                connection, now_ms=int(now_ms), processor_ids=self._processor_ids()
            )
        return [int(row["job_id"]) for row in rows]

    def recover_blocked(
        self, *, now_ms: int, capability_revision: str | None = None
    ) -> list[int]:
        """Re-qualify capability-blocked jobs after a new healthy capability revision."""
        resumed: list[int] = []
        with self.catalog.write() as connection:
            processor_ids = self._processor_ids()
            if not processor_ids:
                return resumed
            pinned: RuntimeCapabilities | None = None
            if capability_revision is not None:
                pinned = capability_module.capability_by_revision(
                    connection, str(capability_revision)
                )
                if pinned is None:
                    return resumed
            placeholders = ", ".join("?" for _ in processor_ids) or "''"
            rows = connection.execute(
                "SELECT job_id, processor_id, requested_device FROM jobs "
                "WHERE state = ? AND blocking_reason = ? "
                f"AND processor_id IN ({placeholders}) ORDER BY created_at, job_id",
                (jobs.STATE_BLOCKED, RUNTIME_CAPABILITY_UNAVAILABLE, *processor_ids),
            ).fetchall()
            for row in rows:
                spec = self._specs.get(str(row["processor_id"]))
                if spec is None:  # pragma: no cover - filtered by the query
                    continue
                snapshot = pinned or capability_module.latest_capability(
                    connection, runtime_package_id=spec.runtime_package_id
                )
                if snapshot is None:
                    continue
                evidence = capability_module.qualification_evidence(
                    connection, processor_id=spec.processor_id
                )
                decision = eligibility.resolve(
                    spec,
                    requested_device=str(row["requested_device"]),
                    capability=snapshot,
                    evidence=evidence,
                )
                if not decision.eligible:
                    continue
                jobs.resume_blocked(connection, job_id=int(row["job_id"]), now_ms=int(now_ms))
                connection.execute(
                    "UPDATE jobs SET blocking_reason = NULL, last_error_code = NULL, "
                    "last_error_message = NULL, resolved_device = NULL, runtime_provider = NULL, "
                    "precision = NULL, runtime_capability_revision = NULL, "
                    "dispatch_spec_revision = NULL WHERE job_id = ?",
                    (int(row["job_id"]),),
                )
                resumed.append(int(row["job_id"]))
        return resumed

    # ------------------------------------------------------------------- driving
    def run_once(
        self, executor: Executor, *, worker_id: str, now_ms: int
    ) -> bool:
        """Claim, dispatch and commit at most one job; ``False`` when nothing ran."""
        try:
            request = self.claim_next(worker_id=worker_id, now_ms=now_ms)
        except CatalogError as error:
            if error.code == SCHEDULER_PAUSED:
                return False
            raise
        if request is None:
            return False
        result = self._run_executor(executor, request, now_ms=now_ms)
        self.submit(result, now_ms=now_ms)
        return True

    def run_until_idle(
        self,
        executor: Executor,
        *,
        worker_id: str,
        now_ms: int,
        max_steps: int = 1000,
    ) -> int:
        """Drain every currently runnable job; never sleeps and never busy-waits."""
        steps = 0
        while steps < int(max_steps):
            if not self.run_once(executor, worker_id=worker_id, now_ms=now_ms):
                break
            steps += 1
        return steps

    def _run_executor(
        self, executor: Executor, request: JobRequest, *, now_ms: int
    ) -> JobResult:
        try:
            raw = executor(request.to_envelope())
            return JobResult.from_envelope(raw)
        except CatalogError as error:
            # A malformed or out-of-contract result is a protocol incompatibility, which
            # ADR-0003 §3 classifies as a deterministic failure, not a transient retry.
            return self._failure_result(
                request,
                state=jobs.STATE_FAILED,
                code=error.code,
                message=error.message,
                retryable=False,
                now_ms=now_ms,
            )
        except Exception as error:  # noqa: BLE001 - one bad execution must not kill the loop
            # Only the exception type is recorded: a message could carry a local path.
            return self._failure_result(
                request,
                state=jobs.STATE_RETRY_WAIT,
                code=SCHEDULER_EXECUTOR_ERROR,
                message=f"the executor raised {type(error).__name__}",
                retryable=True,
                now_ms=now_ms,
            )

    @staticmethod
    def _failure_result(
        request: JobRequest,
        *,
        state: str,
        code: str,
        message: str,
        retryable: bool,
        now_ms: int,
    ) -> JobResult:
        return JobResult(
            job_id=request.job_id,
            lease_token=request.lease_token,
            input_revision=request.input_revision,
            task_type=request.task_type,
            processor_id=request.processor_id,
            state=state,
            resolved_device=request.resolved_device,
            runtime_provider=request.runtime_provider,
            precision=request.precision,
            progress=0.0,
            artifacts=(),
            error=ErrorEnvelope(
                code=str(code),
                message=str(message)[:500],
                retryable=bool(retryable),
                blocking_reason=None,
            ),
            completed_at=int(now_ms),
        )

    # ---------------------------------------------------------------- diagnostics
    def counts(self) -> dict[str, int]:
        rows = self.catalog.read_all(
            "SELECT state, COUNT(*) AS total FROM jobs GROUP BY state ORDER BY state"
        )
        return {str(row["state"]): int(row["total"]) for row in rows}

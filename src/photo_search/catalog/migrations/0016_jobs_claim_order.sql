-- P4-02E: the bounded semantic-backlog claim (P4-02B section 9, finding F1).
--
-- F1 was an access-path defect, not a semantic one: ``jobs.claimable_candidates`` returned
-- the right page, but it materialised and sorted the *whole* runnable backlog on every claim
-- (both ``UNION ALL`` sides showed ``USE TEMP B-TREE FOR ORDER BY``), so one unreserved
-- 300 000-event semantic burst degraded from ~96 to ~11 jobs/s. The claim is now "bounded
-- candidates + exact adjudication": for every ``(side, priority)`` class it takes the oldest
-- ``limit`` rows - inside one class ``effective_priority`` is non-increasing in
-- ``created_at``, so the class order *is* ``created_at, job_id`` - and then adjudicates the
-- merged set with the frozen formula and the three frozen sort keys.
--
-- This migration is additive and index-only: no table, column, row, trigger or CHECK changes,
-- and it backfills nothing.
--
-- * ``idx_jobs_claim_order`` serves that class lookup on both runnable sides: the equality on
--   ``(state, priority)`` is a real index seek and ``(created_at, job_id)`` supplies the class
--   order, so the claim plan no longer contains a temp b-tree. It also gives the
--   ``SELECT DISTINCT priority`` that enumerates the classes a ``(state)`` prefix to walk.
-- * ``idx_jobs_retry_due`` and ``idx_jobs_expired_lease`` are *partial* indexes for the two
--   statements ``Scheduler.claim_next`` runs inside the same ``BEGIN IMMEDIATE`` before it
--   lists candidates (``jobs.requeue_due_retries`` and ``jobs.fail_exhausted_leases``). Those
--   statements matched nothing during a burst yet were planned as a whole-index scan and a
--   whole-table ``SCAN jobs``: ``state = ?`` is a *bound parameter*, and the planner estimates
--   an equality on a leading index column whose single distinct value covers every row as
--   "all rows", so the cheaper-looking plan is the scan. A partial index whose predicate is
--   exactly that state restricts the index to the rows the statement can match; in a burst it
--   is empty and costs microseconds, while returning exactly the same rows. SQLite verifies
--   the predicate against the bound value, so binding any other state keeps using the general
--   index and the result set is unchanged (evidence: docs/reports/P4-02E-backlog-claim.md).

CREATE INDEX idx_jobs_claim_order ON jobs(state, priority, created_at, job_id);

CREATE INDEX idx_jobs_retry_due ON jobs(run_after) WHERE state = 'RETRY_WAIT';

-- The lease index is deliberately *narrower* than the claim's own runnable condition: its
-- predicate repeats the ``attempts`` floor that only ``fail_exhausted_leases`` asks for.
-- ``claimable_candidates`` (and the P1 ``_claimable_row``) never mention that term, so the
-- planner cannot borrow this index for the expiry side of the claim and keeps using
-- ``idx_jobs_claim_order``, whose ``(created_at, job_id)`` tail satisfies the class order
-- without a temporary b-tree. A predicate that matched every expired ``RUNNING`` row would win
-- that cost comparison whenever the partition is nearly empty, and the claim would fall back to
-- ``USE TEMP B-TREE FOR ORDER BY`` on that side (measured; see the P4-02E report).
--
-- The predicate is spelled ``max_attempts <= attempts + 1`` on purpose: SQLite normalises the
-- query's ``attempts + 1 >= max_attempts`` into exactly that form, and only then does its
-- implication check accept the partial index. Written the other way round the index is never
-- used and the statement falls back to ``SCAN jobs``.
CREATE INDEX idx_jobs_expired_lease ON jobs(lease_expires_at)
    WHERE state = 'RUNNING' AND max_attempts <= attempts + 1;

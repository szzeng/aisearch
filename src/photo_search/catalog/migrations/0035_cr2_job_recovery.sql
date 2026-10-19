-- Preserve the original terminal failure and make the CR2 format retry one-shot.
-- The unique five-field identity mirrors jobs' durable logical key. A job can only
-- receive one CR2 compatibility recovery for its current processor and input revision.
CREATE TABLE cr2_job_recoveries (
    job_id             INTEGER PRIMARY KEY REFERENCES jobs(job_id) ON DELETE RESTRICT,
    subject_type       TEXT NOT NULL CHECK(subject_type <> ''),
    subject_id         TEXT NOT NULL CHECK(subject_id <> ''),
    input_revision     TEXT NOT NULL CHECK(input_revision <> ''),
    task_type          TEXT NOT NULL CHECK(task_type <> ''),
    processor_id       TEXT NOT NULL CHECK(processor_id <> ''),
    source_state       TEXT NOT NULL CHECK(source_state IN ('FAILED', 'BLOCKED')),
    source_error_code  TEXT NOT NULL CHECK(source_error_code = 'FORMAT_UNSUPPORTED'),
    source_error_message TEXT,
    recovered_at       INTEGER NOT NULL CHECK(recovered_at >= 0),
    UNIQUE(subject_type, subject_id, input_revision, task_type, processor_id)
);

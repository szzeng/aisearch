-- Distinguish reversible per-face postponement from a face-local recommendation exclusion.
-- Existing hidden faces have ambiguous intent, so retain them as TEMPORARY.
ALTER TABLE face_inbox_hidden
ADD COLUMN ignore_state TEXT NOT NULL DEFAULT 'TEMPORARY'
CHECK (ignore_state IN ('TEMPORARY', 'EXCLUDED'));

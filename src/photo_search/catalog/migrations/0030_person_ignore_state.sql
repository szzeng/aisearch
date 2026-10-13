-- Existing hidden people have ambiguous historical intent, so retain them as temporary ignores.
ALTER TABLE person_hidden
ADD COLUMN ignore_state TEXT NOT NULL DEFAULT 'TEMPORARY'
CHECK (ignore_state IN ('TEMPORARY', 'EXCLUDED'));

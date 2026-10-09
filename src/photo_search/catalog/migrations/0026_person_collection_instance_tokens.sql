-- IDs deleted by the v24 INTEGER PRIMARY KEY schema left no historical high-water mark.
-- Bind API operations and opaque cursors to an independent, immutable collection instance.
ALTER TABLE person_collections
    ADD COLUMN instance_token TEXT NOT NULL DEFAULT '';

UPDATE person_collections
SET instance_token = lower(hex(randomblob(16)));

CREATE UNIQUE INDEX idx_person_collections_instance_token
    ON person_collections(instance_token);

CREATE TRIGGER person_collections_validate_instance_token_insert
BEFORE INSERT ON person_collections
WHEN length(NEW.instance_token) <> 32 OR NEW.instance_token GLOB '*[^0-9a-f]*'
BEGIN
    SELECT RAISE(ABORT, 'invalid person collection instance token');
END;

CREATE TRIGGER person_collections_instance_token_immutable
BEFORE UPDATE OF instance_token ON person_collections
WHEN NEW.instance_token <> OLD.instance_token
BEGIN
    SELECT RAISE(ABORT, 'person collection instance token is immutable');
END;

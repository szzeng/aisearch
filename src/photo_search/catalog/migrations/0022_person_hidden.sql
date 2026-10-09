-- Person-album visibility is independent of identity and face assignment.
CREATE TABLE person_hidden (
    person_id INTEGER PRIMARY KEY REFERENCES persons(person_id) ON DELETE RESTRICT
);

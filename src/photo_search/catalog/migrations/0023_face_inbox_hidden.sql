-- Pending-face inbox visibility is per detected face and independent of assignment.
CREATE TABLE face_inbox_hidden (
    face_id TEXT PRIMARY KEY REFERENCES faces(face_id) ON DELETE CASCADE
);

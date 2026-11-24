-- The creation time remains the version's historical timestamp.
ALTER TABLE resume_versions ADD COLUMN saved_at DATETIME(6) NULL;

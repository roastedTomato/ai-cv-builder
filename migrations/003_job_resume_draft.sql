-- One editable resume draft per user and job.
ALTER TABLE jobs ADD COLUMN resume_draft JSON NULL;

-- Keep the time an interview answer was submitted across MySQL reconnects.
ALTER TABLE interview_turns ADD COLUMN answered_at DATETIME(6) NULL AFTER answer;

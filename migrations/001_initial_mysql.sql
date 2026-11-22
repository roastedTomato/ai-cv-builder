-- Fictional-test application schema. Run against a dedicated, empty MySQL 8 database.
-- Every business key includes user_id; source_version_id intentionally has no FK.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    applied_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS base_resumes (
    user_id VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin PRIMARY KEY,
    content JSON NOT NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS companies (
    user_id VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    name VARCHAR(255) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (user_id, id),
    KEY companies_by_user (user_id, created_at, id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS jobs (
    user_id VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    company_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    title VARCHAR(255) NOT NULL,
    job_text LONGTEXT NOT NULL,
    revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
    version_counter BIGINT UNSIGNED NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (user_id, id),
    KEY jobs_by_company (user_id, company_id, created_at, id),
    CONSTRAINT jobs_company_fk FOREIGN KEY (user_id, company_id)
        REFERENCES companies (user_id, id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS resume_versions (
    user_id VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    job_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    number BIGINT UNSIGNED NOT NULL,
    source_version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    source_version_number BIGINT UNSIGNED NULL,
    job_text LONGTEXT NOT NULL,
    resume_content JSON NOT NULL,
    module_order JSON NOT NULL,
    visual_settings JSON NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (user_id, id),
    UNIQUE KEY versions_job_number (user_id, job_id, number),
    KEY versions_by_job (user_id, job_id, created_at),
    CONSTRAINT versions_job_fk FOREIGN KEY (user_id, job_id)
        REFERENCES jobs (user_id, id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS interviews (
    user_id VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    company_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    company_name VARCHAR(255) NOT NULL,
    job_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    job_title VARCHAR(255) NOT NULL,
    version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    version_number BIGINT UNSIGNED NOT NULL,
    job_text LONGTEXT NOT NULL,
    resume_content JSON NOT NULL,
    status VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
    error JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (user_id, id),
    UNIQUE KEY interviews_one_per_job (user_id, job_id),
    KEY interviews_by_user (user_id, created_at),
    CONSTRAINT interviews_job_fk FOREIGN KEY (user_id, job_id)
        REFERENCES jobs (user_id, id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS interview_turns (
    user_id VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    interview_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    ordinal TINYINT UNSIGNED NOT NULL,
    id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    question TEXT NOT NULL,
    category VARCHAR(64) NULL,
    reference_answer LONGTEXT NULL,
    answer LONGTEXT NULL,
    feedback JSON NULL,
    follow_up TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (user_id, interview_id, ordinal),
    UNIQUE KEY turns_id (user_id, id),
    CONSTRAINT turns_interview_fk FOREIGN KEY (user_id, interview_id)
        REFERENCES interviews (user_id, id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS tasks (
    user_id VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    kind VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    input_data JSON NOT NULL,
    result_data JSON NULL,
    error JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (user_id, id),
    KEY tasks_by_user_status (user_id, status, created_at)
) ENGINE=InnoDB;

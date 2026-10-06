-- Initial schema for Parametric Insurance Data Platform
-- Automatically mounted into Postgres container via /docker-entrypoint-initdb.d

CREATE TABLE IF NOT EXISTS datasets (
    id SERIAL PRIMARY KEY,
    data_key VARCHAR(128) UNIQUE NOT NULL,
    provider_id VARCHAR(64) NOT NULL,
    parameter_type VARCHAR(64) NOT NULL,
    location VARCHAR(255) NOT NULL,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    bbox_north DOUBLE PRECISION,
    bbox_south DOUBLE PRECISION,
    bbox_east DOUBLE PRECISION,
    bbox_west DOUBLE PRECISION,
    start_date VARCHAR(32) NOT NULL,
    end_date VARCHAR(32) NOT NULL,
    resolution VARCHAR(32) DEFAULT '0.25deg',
    version VARCHAR(32) DEFAULT 'v1',
    status VARCHAR(32) DEFAULT 'ready',
    file_path VARCHAR(512),
    file_size_bytes BIGINT DEFAULT 0,
    record_count INTEGER DEFAULT 0,
    checksum_sha256 VARCHAR(64),
    metadata_json TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS idx_datasets_data_key ON datasets (data_key);
CREATE INDEX IF NOT EXISTS idx_datasets_provider_param ON datasets (provider_id, parameter_type);
CREATE INDEX IF NOT EXISTS idx_datasets_status ON datasets (status);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id VARCHAR(64) PRIMARY KEY,
    data_key VARCHAR(128) NOT NULL,
    provider_id VARCHAR(64) NOT NULL,
    status VARCHAR(32) DEFAULT 'pending',
    error_message TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
    started_at TIMESTAMP WITHOUT TIME ZONE,
    completed_at TIMESTAMP WITHOUT TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_data_key ON ingestion_jobs (data_key);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs (status);

CREATE TABLE IF NOT EXISTS parametric_policies (
    id SERIAL PRIMARY KEY,
    policy_number VARCHAR(64) UNIQUE NOT NULL,
    insured_entity VARCHAR(255) NOT NULL,
    location VARCHAR(255) NOT NULL,
    parameter_type VARCHAR(64) NOT NULL,
    trigger_condition VARCHAR(64) NOT NULL,
    trigger_threshold DOUBLE PRECISION NOT NULL,
    payout_limit DOUBLE PRECISION NOT NULL,
    status VARCHAR(32) DEFAULT 'active',
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS idx_policies_number ON parametric_policies (policy_number);

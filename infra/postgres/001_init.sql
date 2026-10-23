CREATE SCHEMA IF NOT EXISTS ecofusion;

CREATE TABLE IF NOT EXISTS ecofusion.pipeline_runs (
    run_id UUID PRIMARY KEY,
    pipeline_name VARCHAR(100) NOT NULL,
    layer VARCHAR(20) NOT NULL
        CHECK (layer IN ('bronze', 'silver', 'gold')),
    status VARCHAR(30) NOT NULL
        CHECK (status IN (
            'running',
            'completed',
            'failed',
            'skipped'
        )),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS ecofusion.bronze_objects (
    object_id BIGSERIAL PRIMARY KEY,
    sha256 CHAR(64) NOT NULL,
    source_file TEXT NOT NULL,
    bucket TEXT NOT NULL,
    object_key TEXT NOT NULL,
    file_size_bytes BIGINT,
    first_ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ,
    CONSTRAINT uq_bronze_location UNIQUE (bucket, object_key),
    CONSTRAINT chk_sha256_format
        CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_file_size
        CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0)
);

CREATE INDEX IF NOT EXISTS idx_bronze_sha256
    ON ecofusion.bronze_objects (sha256);

CREATE TABLE IF NOT EXISTS ecofusion.pipeline_events (
    event_id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL
        REFERENCES ecofusion.pipeline_runs(run_id),
    event_type VARCHAR(100) NOT NULL,
    event_message TEXT,
    event_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_events_run
    ON ecofusion.pipeline_events (run_id);

SELECT 'ECOFUSION SCHEMA INITIALIZED' AS result;

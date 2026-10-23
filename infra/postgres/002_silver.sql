CREATE TABLE IF NOT EXISTS ecofusion.silver_publications (
    silver_id BIGSERIAL PRIMARY KEY,
    source_object_id BIGINT NOT NULL UNIQUE
        REFERENCES ecofusion.bronze_objects(object_id),
    owner_id BIGINT NOT NULL,
    publication_id BIGINT NOT NULL,
    from_id BIGINT,
    published_at TIMESTAMPTZ,
    text TEXT NOT NULL DEFAULT '',
    post_type TEXT,
    likes_count INTEGER NOT NULL DEFAULT 0,
    reposts_count INTEGER NOT NULL DEFAULT 0,
    comments_count INTEGER NOT NULL DEFAULT 0,
    views_count INTEGER NOT NULL DEFAULT 0,
    attachment_count INTEGER NOT NULL DEFAULT 0,
    source TEXT,
    source_url TEXT NOT NULL,
    raw_json JSONB NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_silver_vk_publication
        UNIQUE (owner_id, publication_id)
);

CREATE INDEX IF NOT EXISTS idx_silver_published_at
    ON ecofusion.silver_publications (published_at DESC);

CREATE INDEX IF NOT EXISTS idx_silver_owner
    ON ecofusion.silver_publications (owner_id);

CREATE TABLE IF NOT EXISTS ecofusion.silver_attachments (
    attachment_row_id BIGSERIAL PRIMARY KEY,
    silver_id BIGINT NOT NULL
        REFERENCES ecofusion.silver_publications(silver_id)
        ON DELETE CASCADE,
    position INTEGER NOT NULL,
    attachment_type TEXT NOT NULL,
    attachment_id BIGINT,
    payload JSONB NOT NULL,
    CONSTRAINT uq_silver_attachment
        UNIQUE (silver_id, position)
);

CREATE TABLE IF NOT EXISTS ecofusion.silver_processing_status (
    source_object_id BIGINT PRIMARY KEY
        REFERENCES ecofusion.bronze_objects(object_id),
    status TEXT NOT NULL
        CHECK (status IN ('completed', 'failed')),
    error_message TEXT,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT 'SILVER SCHEMA READY' AS result;

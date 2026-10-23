CREATE TABLE IF NOT EXISTS ecofusion.silver_classifications (
    silver_id BIGINT PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    publication_id BIGINT NOT NULL,
    categories JSONB NOT NULL DEFAULT '[]'::jsonb,
    region_mentions JSONB NOT NULL DEFAULT '[]'::jsonb,
    region TEXT,
    region_confidence TEXT NOT NULL DEFAULT 'unknown'
        CHECK (region_confidence IN (
            'single_explicit_mention',
            'ambiguous',
            'unknown'
        )),
    needs_review BOOLEAN NOT NULL DEFAULT TRUE,
    classifier_version TEXT NOT NULL DEFAULT '35.3',
    classified_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_silver_classifications_region
    ON ecofusion.silver_classifications (region);

CREATE INDEX IF NOT EXISTS idx_silver_classifications_review
    ON ecofusion.silver_classifications (needs_review);

CREATE INDEX IF NOT EXISTS idx_silver_classifications_categories
    ON ecofusion.silver_classifications USING GIN (categories);

SELECT 'CLASSIFICATION TABLE READY' AS result;

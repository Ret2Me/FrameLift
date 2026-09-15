BEGIN;

-- Preserve the complete opportunity and immutable signed plan contract used by
-- observation-plan-v2. Existing rows retain the conservative single-satellite
-- transmission assumption until explicitly backfilled.
ALTER TABLE planning_opportunities
    ADD COLUMN exclusive_transmission BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN metadata_json JSONB NOT NULL DEFAULT '{}';

ALTER TABLE observation_plans
    ADD COLUMN document_json JSONB;

ALTER TABLE planning_opportunities
    ADD CONSTRAINT planning_opportunities_metadata_object
    CHECK (jsonb_typeof(metadata_json) = 'object');

ALTER TABLE observation_plans
    ADD CONSTRAINT observation_plans_document_object
    CHECK (document_json IS NULL OR jsonb_typeof(document_json) = 'object');

-- A staged migration permits deployments to backfill existing revisions before
-- making the complete document mandatory. New writers must always supply it.
COMMENT ON COLUMN observation_plans.document_json IS
    'Complete observation-plan-v2 document; backfill legacy rows before SET NOT NULL';

COMMIT;

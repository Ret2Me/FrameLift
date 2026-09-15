BEGIN;

CREATE TABLE planning_tle_snapshots (
    tle_fingerprint TEXT PRIMARY KEY CHECK (tle_fingerprint ~ '^[0-9a-f]{64}$'),
    norad_id INTEGER NOT NULL CHECK (norad_id > 0),
    satellite_name TEXT NOT NULL,
    line1 TEXT NOT NULL,
    line2 TEXT NOT NULL,
    tle_epoch TIMESTAMPTZ NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    source_uri TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (norad_id, line1, line2)
);

CREATE INDEX planning_tle_latest
    ON planning_tle_snapshots (norad_id, fetched_at DESC);

CREATE TABLE planning_opportunities (
    opportunity_id TEXT PRIMARY KEY,
    norad_id INTEGER NOT NULL CHECK (norad_id > 0),
    satellite_name TEXT NOT NULL,
    station_id TEXT NOT NULL,
    receiver_resource_id TEXT NOT NULL,
    transmitter_uuid TEXT,
    satnogs_station_id INTEGER CHECK (satnogs_station_id IS NULL OR satnogs_station_id > 0),
    start_utc TIMESTAMPTZ NOT NULL,
    end_utc TIMESTAMPTZ NOT NULL,
    tle_fingerprint TEXT NOT NULL REFERENCES planning_tle_snapshots(tle_fingerprint),
    max_elevation_deg DOUBLE PRECISION NOT NULL,
    min_range_km DOUBLE PRECISION NOT NULL CHECK (min_range_km > 0),
    frequency_hz DOUBLE PRECISION CHECK (frequency_hz IS NULL OR frequency_hz > 0),
    modulation TEXT,
    nominal_unique_samples DOUBLE PRECISION NOT NULL CHECK (nominal_unique_samples >= 0),
    expected_unique_samples DOUBLE PRECISION NOT NULL CHECK (expected_unique_samples >= 0),
    priority DOUBLE PRECISION NOT NULL CHECK (priority > 0),
    probability_json JSONB NOT NULL,
    feature_snapshot_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_utc > start_utc),
    CHECK (expected_unique_samples <= nominal_unique_samples)
);

CREATE INDEX planning_opportunities_horizon
    ON planning_opportunities (start_utc, end_utc);
CREATE INDEX planning_opportunities_resource
    ON planning_opportunities (station_id, receiver_resource_id, start_utc);

CREATE TABLE observation_plans (
    plan_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    horizon_start TIMESTAMPTZ NOT NULL,
    horizon_end TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    trigger TEXT NOT NULL,
    solver TEXT NOT NULL,
    objective_value DOUBLE PRECISION NOT NULL,
    tle_fingerprints JSONB NOT NULL,
    diagnostics_json JSONB NOT NULL DEFAULT '{}',
    canonical_fingerprint TEXT NOT NULL CHECK (canonical_fingerprint ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (plan_id, revision),
    CHECK (horizon_end > horizon_start)
);

CREATE TABLE observation_plan_assignments (
    plan_id TEXT NOT NULL,
    plan_revision INTEGER NOT NULL,
    opportunity_id TEXT NOT NULL REFERENCES planning_opportunities(opportunity_id),
    satnogs_observation_id BIGINT,
    satnogs_state TEXT NOT NULL DEFAULT 'not_exported'
        CHECK (satnogs_state IN ('not_exported','exported','scheduled','missing','cancelled')),
    PRIMARY KEY (plan_id, plan_revision, opportunity_id),
    FOREIGN KEY (plan_id, plan_revision)
        REFERENCES observation_plans(plan_id, revision) ON DELETE CASCADE
);

COMMIT;

BEGIN;

CREATE TABLE planning_reception_evidence (
    evidence_id TEXT PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    norad_id INTEGER NOT NULL CHECK (norad_id > 0),
    station_id TEXT,
    receiver_resource_id TEXT,
    listened BOOLEAN NOT NULL,
    transmitter_state TEXT NOT NULL CHECK (
        transmitter_state IN ('confirmed','expected','not_transmitting','unknown')
    ),
    decoded BOOLEAN,
    signal_present BOOLEAN,
    source TEXT NOT NULL CHECK (source IN ('local','satnogs')),
    evidence_weight DOUBLE PRECISION NOT NULL CHECK (evidence_weight > 0),
    source_observation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, source_observation_id)
);

CREATE INDEX planning_reception_evidence_history
    ON planning_reception_evidence (norad_id, observed_at DESC);

COMMIT;

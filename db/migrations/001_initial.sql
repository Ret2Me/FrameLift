BEGIN;

CREATE TABLE recordings (
    recording_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    station_id TEXT,
    observation_id TEXT,
    -- Nullable only for unsplit calibration fixtures (for example GPS or 50-ohm noise).
    -- The table-level CHECK below requires event identity and time before split use.
    transmission_event_id TEXT,
    norad_id INTEGER,
    transmitter_uuid TEXT,
    protocol_id TEXT,
    capture_layer TEXT NOT NULL CHECK (capture_layer IN ('iq_raw','iq_doppler_corrected','iq_doppler_state_unknown','audio','noise_reference')),
    sample_rate_hz DOUBLE PRECISION CHECK (sample_rate_hz > 0),
    center_frequency_hz DOUBLE PRECISION,
    start_utc TIMESTAMPTZ,
    duration_s DOUBLE PRECISION CHECK (duration_s IS NULL OR duration_s >= 0),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    object_uri TEXT NOT NULL,
    license_spdx TEXT NOT NULL,
    license_url TEXT,
    license_text_hash TEXT,
    license_verified BOOLEAN NOT NULL DEFAULT FALSE,
    publication_reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    attribution TEXT,
    tle_live_line1 TEXT,
    tle_live_line2 TEXT,
    tle_best_line1 TEXT,
    tle_best_line2 TEXT,
    tle_age_hours DOUBLE PRECISION,
    station_lat DOUBLE PRECISION,
    station_lon DOUBLE PRECISION,
    station_alt_m DOUBLE PRECISION,
    max_elevation_deg DOUBLE PRECISION,
    culmination_az_deg DOUBLE PRECISION,
    quality_flags TEXT[] NOT NULL DEFAULT '{}',
    signal_confirmation INTEGER CHECK (signal_confirmation BETWEEN 1 AND 4),
    human_waterfall_label TEXT,
    live_decoded_frames INTEGER CHECK (live_decoded_frames IS NULL OR live_decoded_frames >= 0),
    split TEXT,
    parent_recording_id TEXT REFERENCES recordings(recording_id),
    transform_config_hash TEXT,
    pipeline_version TEXT NOT NULL,
    CHECK ((parent_recording_id IS NULL) = (transform_config_hash IS NULL)),
    CHECK (split IS NULL OR (transmission_event_id IS NOT NULL AND start_utc IS NOT NULL))
);

CREATE TABLE attempts (
    attempt_id BIGSERIAL PRIMARY KEY,
    recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
    config_hash TEXT NOT NULL,
    config_json JSONB NOT NULL,
    decoder TEXT NOT NULL,
    decoder_version TEXT NOT NULL,
    cascade_level SMALLINT,
    status TEXT NOT NULL CHECK (status IN ('running','ok','error','timeout')),
    lease_token TEXT NOT NULL,
    error_class TEXT,
    n_frames_raw INTEGER NOT NULL DEFAULT 0 CHECK (n_frames_raw >= 0),
    n_frames_crc_valid INTEGER NOT NULL DEFAULT 0 CHECK (n_frames_crc_valid >= 0),
    n_syncwords_detected INTEGER,
    longest_clean_run_bits INTEGER,
    timing_err_variance DOUBLE PRECISION,
    preamble_corr_peak DOUBLE PRECISION,
    estimated_cfo_hz DOUBLE PRECISION,
    estimated_baud_hz DOUBLE PRECISION,
    cpu_seconds DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (cpu_seconds >= 0),
    peak_rss_mb DOUBLE PRECISION,
    wall_seconds DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (wall_seconds >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    UNIQUE (recording_id, config_hash)
);

CREATE TABLE frames (
    frame_id BIGSERIAL PRIMARY KEY,
    attempt_id BIGINT NOT NULL REFERENCES attempts(attempt_id) ON DELETE CASCADE,
    transmission_event_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    payload_bytes BYTEA NOT NULL,
    crc_variant TEXT NOT NULL,
    crc_valid BOOLEAN NOT NULL,
    t_start_utc TIMESTAMPTZ,
    t_confidence DOUBLE PRECISION
);

CREATE TABLE failure_attribution (
    recording_id TEXT PRIMARY KEY REFERENCES recordings(recording_id),
    best_config_hash TEXT,
    categories TEXT[] NOT NULL DEFAULT '{}',
    necessary_axes TEXT[] NOT NULL DEFAULT '{}',
    ablation_json JSONB NOT NULL DEFAULT '{}',
    estimated_snr_db DOUBLE PRECISION,
    theoretical_threshold_db DOUBLE PRECISION,
    phase_limited BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX valid_event_payload_lookup
    ON frames (transmission_event_id, payload_sha256)
    WHERE crc_valid;

CREATE INDEX frames_by_attempt ON frames (attempt_id);

COMMIT;

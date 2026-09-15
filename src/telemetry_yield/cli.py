"""Dependency-light CLI for reproducible telemetry-recovery experiments."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path

from .environment import write_environment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telemetry-yield")
    subparsers = parser.add_subparsers(dest="command", required=True)

    env = subparsers.add_parser("env", help="capture tool and package versions")
    env.add_argument("--output", type=Path, default=Path("reports/environment.json"))

    capabilities = subparsers.add_parser(
        "capabilities", help="write the built-in receiver plugin capabilities"
    )
    capabilities.add_argument(
        "--output",
        type=Path,
        default=Path("reports/receiver-capabilities.json"),
    )

    inventory = subparsers.add_parser(
        "inventory-archive", help="build a protocol-neutral IQ campaign inventory"
    )
    inventory.add_argument("catalogue", type=Path)
    inventory.add_argument("--output", type=Path, required=True)

    profiles = subparsers.add_parser(
        "profiles", help="discover gr-satellites SatYAML capability profiles"
    )
    profiles.add_argument("paths", type=Path, nargs="+")
    profiles.add_argument("--output", type=Path, required=True)

    scrape = subparsers.add_parser("scrape-camras", help="parse a saved CAMRAS index HTML")
    scrape.add_argument("html", type=Path)
    scrape.add_argument("--output", type=Path, required=True)

    convert = subparsers.add_parser("convert-camras", help="convert c16le CAMRAS raw to SigMF")
    convert.add_argument("raw", type=Path)
    convert.add_argument("output_base", type=Path)
    convert.add_argument("--sample-rate", type=float, required=True)
    convert.add_argument("--sample-rate-basis", required=True)
    convert.add_argument(
        "--doppler-state",
        choices=("pre_correction", "post_correction", "unknown"),
        required=True,
    )
    convert.add_argument("--frequency", type=float, required=True)
    convert.add_argument("--start-utc", required=True)
    convert.add_argument("--observation-id", type=int, required=True)
    convert.add_argument(
        "--confirm-ci16-le",
        action="store_true",
        help="confirm the source datatype/endianness was independently verified",
    )

    decode_afsk = subparsers.add_parser(
        "decode-afsk1200",
        help="run/resume the built-in bounded-memory Bell-202 AX.25 plugin",
    )
    decode_afsk.add_argument("iq", type=Path)
    decode_afsk.add_argument("--output", type=Path, required=True)
    decode_afsk.add_argument(
        "--api-version",
        default="telemetry-yield-afsk1200-file-api-v1",
        help="fail-closed input contract version",
    )
    decode_afsk.add_argument("--checkpoint", type=Path)
    decode_afsk.add_argument("--external-baseline", type=Path)
    decode_afsk.add_argument("--sample-rate", type=int, default=57_600)
    decode_afsk.add_argument("--baudrate", type=float, default=1_200.0)
    decode_afsk.add_argument("--mark-hz", type=float, default=1_200.0)
    decode_afsk.add_argument("--space-hz", type=float, default=2_200.0)
    decode_afsk.add_argument("--audio-sample-rate", type=int, default=9_600)
    decode_afsk.add_argument("--window-blocks", type=int, default=280)
    decode_afsk.add_argument("--stride-blocks", type=int, default=140)
    decode_afsk.add_argument("--permutation-block-samples", type=int, default=4_096)
    decode_afsk.add_argument(
        "--rate-error-ppm",
        type=float,
        action="append",
        help="fixed timing-rate hypothesis; repeat to replace the built-in bank",
    )
    decode_afsk.add_argument("--phase-bins", type=int, default=16)
    decode_afsk.add_argument("--top-timing-hypotheses", type=int, default=6)
    decode_afsk.add_argument("--max-candidate-records", type=int, default=4_096)
    decode_afsk.add_argument("--segment-start-sample", type=int, default=0)
    decode_afsk.add_argument("--segment-sample-count", type=int)
    decode_afsk.add_argument("--event-key", default="capture-segment")
    decode_afsk.add_argument("--expected-size-bytes", type=int)
    decode_afsk.add_argument("--expected-sha256")
    decode_afsk.add_argument(
        "--max-windows",
        type=int,
        help="process at most this many pending windows, then write a partial result",
    )
    decode_afsk.add_argument(
        "--no-resume",
        action="store_true",
        help="start a new checkpoint instead of loading a matching one",
    )

    decode_fsk = subparsers.add_parser(
        "decode-fsk-ax25",
        help="run/resume the bounded FSK/GFSK/GMSK AX.25 route",
    )
    decode_fsk.add_argument("iq", type=Path)
    decode_fsk.add_argument("--output", type=Path, required=True)
    decode_fsk.add_argument(
        "--api-version",
        default="telemetry-yield-fsk-ax25-file-api-v1",
        help="fail-closed input contract version",
    )
    decode_fsk.add_argument("--checkpoint", type=Path)
    decode_fsk.add_argument("--external-baseline", type=Path)
    decode_fsk.add_argument("--sample-rate", type=int, required=True)
    decode_fsk.add_argument("--baudrate", type=float, required=True)
    decode_fsk.add_argument("--decimation", type=int, required=True)
    decode_fsk.add_argument(
        "--g3ruh",
        action=argparse.BooleanOptionalAction,
        required=True,
        help="explicitly enable or disable the x^17+x^12+1 descrambler",
    )
    decode_fsk.add_argument("--window-seconds", type=float, default=5.0)
    decode_fsk.add_argument("--hop-seconds", type=float, default=2.5)
    decode_fsk.add_argument(
        "--rate-error-ppm",
        type=float,
        action="append",
        help="fixed timing-rate hypothesis; repeat to replace the built-in bank",
    )
    decode_fsk.add_argument("--phase-bins", type=int, default=32)
    decode_fsk.add_argument("--top-timing-hypotheses", type=int, default=32)
    decode_fsk.add_argument("--max-candidate-records", type=int, default=20_000)
    decode_fsk.add_argument("--segment-start-sample", type=int, default=0)
    decode_fsk.add_argument("--segment-sample-count", type=int)
    decode_fsk.add_argument("--event-key", default="capture-segment")
    decode_fsk.add_argument("--expected-size-bytes", type=int)
    decode_fsk.add_argument("--expected-sha256")
    decode_fsk.add_argument(
        "--max-windows",
        type=int,
        help="process at most this many pending windows, then write a partial result",
    )
    decode_fsk.add_argument(
        "--no-resume",
        action="store_true",
        help="start a new checkpoint instead of loading a matching one",
    )

    decode_blind_fsk = subparsers.add_parser(
        "decode-blind-phase-fsk",
        help="run the content-bound clipping-robust blind FSK/AX.25 receiver",
    )
    decode_blind_fsk.add_argument("iq", type=Path)
    decode_blind_fsk.add_argument("--output", type=Path, required=True)
    decode_blind_fsk.add_argument(
        "--api-version",
        default="telemetry-yield-blind-phase-fsk-file-api-v2",
        help="fail-closed input contract version",
    )
    decode_blind_fsk.add_argument("--sample-rate", type=int, required=True)
    decode_blind_fsk.add_argument(
        "--baudrate", "--symbol-rate", dest="baudrate", type=float, required=True
    )
    decode_blind_fsk.add_argument("--expected-size-bytes", type=int, required=True)
    decode_blind_fsk.add_argument("--expected-sha256", required=True)
    decode_blind_fsk.add_argument("--scratch-directory", type=Path)
    decode_blind_fsk.add_argument(
        "--maximum-scratch-mib",
        type=int,
        default=512,
        help="hard temporary selector-storage bound",
    )
    decode_blind_fsk.add_argument(
        "--analysis-window-seconds", type=float, default=0.5
    )
    decode_blind_fsk.add_argument(
        "--decoder-window-seconds", type=float, default=2.75
    )
    decode_blind_fsk.add_argument(
        "--decoder-window-lead-seconds", type=float, default=1.25
    )
    decode_blind_fsk.add_argument("--candidate-window-limit", type=int, default=32)
    decode_blind_fsk.add_argument("--window-nms-seconds", type=float, default=2.0)
    decode_blind_fsk.add_argument(
        "--constant-radius-factor",
        type=float,
        action="append",
        help="constant-radius declipping hypothesis; repeat to form a bank",
    )
    decode_blind_fsk.add_argument(
        "--phase-difference-lag",
        type=int,
        action="append",
        help="phase-difference lag; repeat to form a bank",
    )
    decode_blind_fsk.add_argument(
        "--descramble-mode",
        choices=("plain", "g3ruh"),
        action="append",
        help="AX.25 line-code hypothesis; repeat to form a bounded bank",
    )
    decode_blind_fsk.add_argument(
        "--rate-error-ppm",
        type=float,
        action="append",
        help="fixed timing-rate hypothesis; repeat to form a bank",
    )
    decode_blind_fsk.add_argument("--phase-bins", type=int, default=64)
    decode_blind_fsk.add_argument(
        "--short-timing-hypotheses", type=int, default=16
    )
    decode_blind_fsk.add_argument(
        "--deep-timing-hypotheses", type=int, default=16
    )
    decode_blind_fsk.add_argument("--minimum-consecutive-flags", type=int, default=4)
    decode_blind_fsk.add_argument("--minimum-frame-body-bits", type=int, default=128)
    decode_blind_fsk.add_argument(
        "--short-least-reliable-symbols", type=int, default=64
    )
    decode_blind_fsk.add_argument("--short-maximum-flips", type=int, default=2)
    decode_blind_fsk.add_argument("--short-maximum-attempts", type=int, default=20_000)
    decode_blind_fsk.add_argument(
        "--deep-least-reliable-symbols", type=int, default=64
    )
    decode_blind_fsk.add_argument("--deep-maximum-flips", type=int, default=5)
    decode_blind_fsk.add_argument("--deep-maximum-attempts", type=int, default=400_000)
    decode_blind_fsk.add_argument("--maximum-regions-per-start", type=int, default=8)
    decode_blind_fsk.add_argument("--candidate-neighbor-radius", type=int, default=1)
    decode_blind_fsk.add_argument("--error-unit-penalty", type=float, default=0.05)
    decode_blind_fsk.add_argument("--maximum-map-seed-states", type=int, default=512)

    extract_rml24 = subparsers.add_parser(
        "extract-rml24-hdf5",
        help="safely extract the verified RML24 HDF5 ZIP member",
    )
    extract_rml24.add_argument("archive", type=Path)
    extract_rml24.add_argument("output", type=Path)
    extract_rml24.add_argument("--plan", type=Path, required=True)
    extract_rml24.add_argument(
        "--report",
        type=Path,
        default=Path("reports/rml24-hdf5-extraction-v1.json"),
    )

    inventory_rml24 = subparsers.add_parser(
        "inventory-rml24-hdf5",
        help="stream labels/SNR/rates without loading the RML24 IQ array",
    )
    inventory_rml24.add_argument("hdf5", type=Path)
    inventory_rml24.add_argument("--output", type=Path, required=True)
    inventory_rml24.add_argument("--batch-size", type=int, default=65_536)
    inventory_rml24.add_argument("--max-records", type=int)

    convert_rml24_pickle = subparsers.add_parser(
        "convert-rml24-pickle",
        help="strictly parse RML24 Pickles into bounded NPY group shards",
    )
    convert_rml24_pickle.add_argument("archive", type=Path)
    convert_rml24_pickle.add_argument("output_directory", type=Path)
    convert_rml24_pickle.add_argument("--plan", type=Path, required=True)
    convert_rml24_pickle.add_argument("--manifest", type=Path)
    convert_rml24_pickle.add_argument("--max-array-mib", type=int, default=64)

    benchmark_rml24 = subparsers.add_parser(
        "benchmark-rml24-physical",
        help="run the built-in supported-family RML24 BER benchmark",
    )
    benchmark_rml24.add_argument("shard_manifest", type=Path)
    benchmark_rml24.add_argument("--output", type=Path, required=True)
    benchmark_rml24.add_argument("--batch-size", type=int, default=128)
    benchmark_rml24.add_argument("--max-records", type=int)
    benchmark_rml24.add_argument("--sample-rate", type=float, default=1_000_000.0)
    benchmark_rml24.add_argument("--max-shift-bits", type=int, default=8)
    benchmark_rml24.add_argument(
        "--recovery-version",
        choices=("legacy", "carrier_timing_v2"),
        default="legacy",
        help="select an explicit versioned physical recovery path",
    )

    benchmark_rml24_hdf5 = subparsers.add_parser(
        "benchmark-rml24-hdf5-physical",
        help="stream the verified RML24 HDF5 through the physical BER benchmark",
    )
    benchmark_rml24_hdf5.add_argument("hdf5", type=Path)
    benchmark_rml24_hdf5.add_argument("--inventory-report", type=Path, required=True)
    benchmark_rml24_hdf5.add_argument("--extraction-report", type=Path, required=True)
    benchmark_rml24_hdf5.add_argument("--class-map", type=Path, required=True)
    benchmark_rml24_hdf5.add_argument("--output", type=Path, required=True)
    benchmark_rml24_hdf5.add_argument("--batch-size", type=int, default=128)
    benchmark_rml24_hdf5.add_argument("--max-records", type=int)
    benchmark_rml24_hdf5.add_argument("--sample-rate", type=float, default=1_000_000.0)
    benchmark_rml24_hdf5.add_argument("--max-shift-bits", type=int, default=8)
    benchmark_rml24_hdf5.add_argument(
        "--recovery-version",
        choices=("legacy", "carrier_timing_v2"),
        default="legacy",
        help="select an explicit versioned physical recovery path",
    )

    planning_fetch = subparsers.add_parser(
        "planning-fetch-publication",
        help="fetch the frozen read-only SatNOGS planning cohort",
    )
    planning_fetch.add_argument("--config", type=Path, required=True)
    planning_fetch.add_argument("--raw-dir", type=Path, required=True)
    planning_fetch.add_argument("--manifest", type=Path, required=True)
    planning_fetch.add_argument(
        "--cache-dir",
        type=Path,
        required=True,
        help=(
            "legacy-compatible path; publication pages resume from their "
            "hash-bound raw sidecars without a duplicate HTTP cache"
        ),
    )
    planning_fetch.add_argument(
        "--shared-rate-limit-file",
        type=Path,
        help="cross-process state for the anonymous observations API quota",
    )
    planning_fetch.add_argument(
        "--api-token-file",
        type=Path,
        help="private mode-0600 SatNOGS Network API token file",
    )

    planning_build = subparsers.add_parser(
        "planning-build-dataset",
        help="normalize a hashed SatNOGS snapshot using embedded historical TLEs",
    )
    planning_build.add_argument("--config", type=Path, required=True)
    planning_build.add_argument("--snapshot-manifest", type=Path, required=True)
    planning_build.add_argument("--raw-dir", type=Path)
    planning_build.add_argument("--output", type=Path, required=True)
    planning_build.add_argument("--manifest", type=Path, required=True)
    planning_build.add_argument(
        "--covariates",
        type=Path,
        help="optional hashed weather/Kp/hardware archive joined during normalization",
    )

    planning_evaluate = subparsers.add_parser(
        "planning-evaluate-publication",
        help="run frozen temporal and leave-one-group-out probability evaluation",
    )
    planning_evaluate.add_argument("--config", type=Path, required=True)
    planning_evaluate.add_argument("--dataset", type=Path, required=True)
    planning_evaluate.add_argument("--output-dir", type=Path, required=True)

    planning_probability_model = subparsers.add_parser(
        "planning-build-probability-model",
        help="freeze the gate-selected probability model for a successor campaign",
    )
    planning_probability_model.add_argument("--dataset", type=Path, required=True)
    planning_probability_model.add_argument(
        "--dataset-manifest", type=Path, required=True
    )
    planning_probability_model.add_argument("--evaluation", type=Path, required=True)
    planning_probability_model.add_argument("--output", type=Path, required=True)

    planning_audit = subparsers.add_parser(
        "planning-audit-publication",
        help="independently recompute publication hashes, counts and primary metrics",
    )
    planning_audit.add_argument("--dataset", type=Path, required=True)
    planning_audit.add_argument("--dataset-manifest", type=Path, required=True)
    planning_audit.add_argument("--evaluation", type=Path, required=True)
    planning_audit.add_argument("--output", type=Path, required=True)

    planning_export = subparsers.add_parser(
        "planning-export-satnogs",
        help="convert a reviewed immutable plan JSON to a SatNOGS dry-run schedule batch",
    )
    planning_export.add_argument("--plan", type=Path, required=True)
    planning_export.add_argument("--output", type=Path, required=True)

    planning_provenance = subparsers.add_parser(
        "planning-write-provenance",
        help="hash planning source, config, methods and tests into one manifest",
    )
    planning_provenance.add_argument("--project-root", type=Path, default=Path("."))
    planning_provenance.add_argument("--output", type=Path, required=True)

    planning_readiness = subparsers.add_parser(
        "planning-publication-readiness",
        help="fail closed unless all technical and human publication gates pass",
    )
    planning_readiness.add_argument("--snapshot-manifest", type=Path, required=True)
    planning_readiness.add_argument("--dataset-manifest", type=Path, required=True)
    planning_readiness.add_argument("--evaluation", type=Path, required=True)
    planning_readiness.add_argument("--independent-audit", type=Path, required=True)
    planning_readiness.add_argument("--source-manifest", type=Path, required=True)
    planning_readiness.add_argument("--results-manifest", type=Path, required=True)
    planning_readiness.add_argument("--junit", type=Path, required=True)
    planning_readiness.add_argument(
        "--test-attestation",
        type=Path,
        help="hash- and time-bound proof that JUnit covers the frozen planning source",
    )
    planning_readiness.add_argument("--probability-model", type=Path)
    planning_readiness.add_argument(
        "--covariate-evidence",
        type=Path,
        help="raw HTTP evidence manifest bound to the historical covariate archive",
    )
    planning_readiness.add_argument("--output", type=Path, required=True)
    planning_readiness.add_argument(
        "--publication-config",
        type=Path,
        help="enable expanded v4 cohort and external-validation gates",
    )
    planning_readiness.add_argument(
        "--prospective-config",
        type=Path,
        help="enable the registered 30-day prospective campaign gate",
    )
    planning_readiness.add_argument(
        "--prospective-ledger",
        type=Path,
        help="hash-chained prospective plans and outcomes",
    )
    planning_readiness.add_argument(
        "--project-license-attested", action="store_true"
    )
    planning_readiness.add_argument(
        "--reachable-contact-attested", action="store_true"
    )
    planning_readiness.add_argument(
        "--attribution-review-attested", action="store_true"
    )
    planning_readiness.add_argument(
        "--release-attestation",
        type=Path,
        help=(
            "persistent human release attestation bound to the study config "
            "and frozen source identity"
        ),
    )

    planning_results = subparsers.add_parser(
        "planning-write-publication-results",
        help="write claim-guarded manuscript results and CSV tables",
    )
    planning_results.add_argument("--dataset-manifest", type=Path, required=True)
    planning_results.add_argument("--evaluation", type=Path, required=True)
    planning_results.add_argument("--independent-audit", type=Path, required=True)
    planning_results.add_argument("--probability-model", type=Path)
    planning_results.add_argument(
        "--covariate-evidence",
        type=Path,
        help="raw HTTP evidence manifest bound to the historical covariate archive",
    )
    planning_results.add_argument("--output-dir", type=Path, required=True)

    planning_submit = subparsers.add_parser(
        "planning-submit-satnogs",
        help="submit one reviewed export; requires token env and exact SHA-256 confirmation",
    )
    planning_submit.add_argument("--export", type=Path, required=True)
    planning_submit.add_argument("--confirm-sha256", required=True)
    planning_submit.add_argument("--user-agent", required=True)
    planning_submit.add_argument("--receipt", type=Path, required=True)

    planning_cohort = subparsers.add_parser(
        "planning-design-cohort",
        help="freeze a larger outcome-blind, stratified SatNOGS target cohort",
    )
    planning_cohort.add_argument("--transmitters", type=Path, required=True)
    planning_cohort.add_argument("--stats", type=Path, required=True)
    planning_cohort.add_argument("--output", type=Path, required=True)
    planning_cohort.add_argument("--study-id", required=True)
    planning_cohort.add_argument("--frozen-at", required=True)
    planning_cohort.add_argument("--start", required=True)
    planning_cohort.add_argument("--end", required=True)
    planning_cohort.add_argument("--user-agent", required=True)
    planning_cohort.add_argument("--maximum-targets", type=int, default=50)
    planning_cohort.add_argument("--minimum-total-observations", type=int, default=500)
    planning_cohort.add_argument("--external-validation-fraction", type=float, default=0.2)
    planning_cohort.add_argument("--random-seed", type=int, default=7538)
    planning_cohort.add_argument(
        "--sampling-interval",
        action="append",
        nargs=2,
        default=[],
        metavar=("START", "END"),
        help=(
            "repeatable outcome-blind UTC interval applied to every target; "
            "omit to acquire the full start/end span"
        ),
    )

    planning_station_expansion = subparsers.add_parser(
        "planning-design-station-expansion",
        help="freeze an outcome-blind station set that covers target frequencies",
    )
    planning_station_expansion.add_argument("--target-pool", type=Path, required=True)
    planning_station_expansion.add_argument(
        "--campaign-config", type=Path, required=True
    )
    planning_station_expansion.add_argument(
        "--transmitters", type=Path, required=True
    )
    planning_station_expansion.add_argument(
        "--baseline-hardware", type=Path, required=True
    )
    planning_station_expansion.add_argument(
        "--station-catalog", type=Path, required=True
    )
    planning_station_expansion.add_argument("--output", type=Path, required=True)
    planning_station_expansion.add_argument("--frozen-at", required=True)
    planning_station_expansion.add_argument(
        "--minimum-observations", type=int, default=10_000
    )
    planning_station_expansion.add_argument(
        "--required-compatible-stations-per-target", type=int, default=1
    )

    planning_antennas = subparsers.add_parser(
        "planning-snapshot-antennas",
        help="capture current, versioned SatNOGS antenna ranges for future-only use",
    )
    planning_antennas.add_argument("--station-id", type=int, action="append", required=True)
    planning_antennas.add_argument("--output", type=Path, required=True)
    planning_antennas.add_argument("--cache-dir", type=Path, required=True)
    planning_antennas.add_argument("--user-agent", required=True)
    planning_antennas.add_argument("--allow-testing", action="store_true")

    planning_covariates = subparsers.add_parser(
        "planning-fetch-covariates",
        help=(
            "fetch forecast-aligned Open-Meteo historical weather and GFZ Kp "
            "for a normalized dataset"
        ),
    )
    planning_covariates.add_argument("--dataset", type=Path, required=True)
    planning_covariates.add_argument("--output", type=Path, required=True)
    planning_covariates.add_argument("--cache-dir", type=Path, required=True)
    planning_covariates.add_argument("--user-agent", required=True)
    planning_covariates.add_argument("--hardware-archive", type=Path)
    planning_covariates.add_argument(
        "--minimum-request-interval-seconds", type=float, default=1.0
    )
    planning_covariates.add_argument(
        "--weather-maximum-missing-days",
        type=int,
        default=7,
        help="merge nearby observation dates into NASA calls; retain only required hours",
    )

    planning_forecast = subparsers.add_parser(
        "planning-fetch-forecast",
        help="capture current Open-Meteo/GFZ forecasts and SatNOGS antenna inventory",
    )
    planning_forecast.add_argument("--config", type=Path, required=True)
    planning_forecast.add_argument("--output", type=Path, required=True)
    planning_forecast.add_argument("--cache-dir", type=Path, required=True)
    planning_forecast.add_argument("--user-agent", required=True)
    planning_forecast.add_argument(
        "--hardware-archive",
        type=Path,
        help="optional validated local gain/noise declarations",
    )

    planning_covariate_evidence = subparsers.add_parser(
        "planning-write-covariate-evidence",
        help="embed and hash the raw HTTP responses referenced by an archive",
    )
    planning_covariate_evidence.add_argument(
        "--covariates", type=Path, required=True
    )
    planning_covariate_evidence.add_argument(
        "--cache-dir", type=Path, required=True
    )
    planning_covariate_evidence.add_argument("--output", type=Path, required=True)

    planning_hardware_archive = subparsers.add_parser(
        "planning-build-hardware-archive",
        help="validate measured station hardware declarations and hash the archive",
    )
    planning_hardware_archive.add_argument("--input", type=Path, required=True)
    planning_hardware_archive.add_argument("--output", type=Path, required=True)

    planning_enrich = subparsers.add_parser(
        "planning-enrich-dataset",
        help="time-align a frozen weather/Kp/hardware archive with normalized observations",
    )
    planning_enrich.add_argument("--dataset", type=Path, required=True)
    planning_enrich.add_argument("--covariates", type=Path, required=True)
    planning_enrich.add_argument("--output", type=Path, required=True)
    planning_enrich.add_argument("--manifest", type=Path, required=True)

    planning_covariate_evaluation = subparsers.add_parser(
        "planning-evaluate-covariates",
        help="run exploratory temporal weather/hardware feature ablations",
    )
    planning_covariate_evaluation.add_argument("--dataset", type=Path, required=True)
    planning_covariate_evaluation.add_argument("--output-dir", type=Path, required=True)
    planning_covariate_evaluation.add_argument(
        "--bootstrap-replicates", type=int, default=2000
    )

    prospective_init = subparsers.add_parser(
        "planning-prospective-init",
        help="register and hash-chain an at-least-30-day prospective campaign",
    )
    prospective_init.add_argument("--config", type=Path, required=True)
    prospective_init.add_argument("--ledger", type=Path, required=True)

    prospective_commit = subparsers.add_parser(
        "planning-prospective-commit-plan",
        help="commit an immutable plan and all supplied input artifacts",
    )
    prospective_commit.add_argument("--config", type=Path, required=True)
    prospective_commit.add_argument("--ledger", type=Path, required=True)
    prospective_commit.add_argument("--plan", type=Path, required=True)
    prospective_commit.add_argument(
        "--input-artifact", type=Path, action="append", default=[]
    )

    prospective_outcomes = subparsers.add_parser(
        "planning-prospective-record-outcomes",
        help="append reconciled outcomes for previously committed opportunities",
    )
    prospective_outcomes.add_argument("--config", type=Path, required=True)
    prospective_outcomes.add_argument("--ledger", type=Path, required=True)
    prospective_outcomes.add_argument("--outcomes", type=Path, required=True)

    prospective_report_parser = subparsers.add_parser(
        "planning-prospective-report",
        help="verify the ledger and report the 30-day publication gates",
    )
    prospective_report_parser.add_argument("--config", type=Path, required=True)
    prospective_report_parser.add_argument("--ledger", type=Path, required=True)
    prospective_report_parser.add_argument("--output", type=Path, required=True)

    prospective_run = subparsers.add_parser(
        "planning-prospective-run-shadow",
        help="refresh live inputs, solve, and commit one read-only shadow plan",
    )
    prospective_run.add_argument("--config", type=Path, required=True)
    prospective_run.add_argument("--publication-config", type=Path, required=True)
    prospective_run.add_argument("--transmitters", type=Path, required=True)
    prospective_run.add_argument("--covariates", type=Path, required=True)
    prospective_run.add_argument("--history-dataset", type=Path, required=True)
    prospective_run.add_argument("--ledger", type=Path, required=True)
    prospective_run.add_argument("--plan", type=Path, required=True)
    prospective_run.add_argument("--state-db", type=Path, required=True)
    prospective_run.add_argument("--cache-dir", type=Path, required=True)
    prospective_run.add_argument("--user-agent", required=True)
    prospective_run.add_argument(
        "--blockers",
        type=Path,
        help="maintenance and high-priority reservation intervals",
    )
    prospective_run.add_argument(
        "--target-overrides",
        type=Path,
        help="power, priority and transmitter calendar/region constraints",
    )
    prospective_run.add_argument(
        "--probability-model",
        type=Path,
        help="content-bound frozen model for a separately registered campaign",
    )
    prospective_run.add_argument(
        "--source-manifest",
        type=Path,
        help="source manifest captured immediately before this planning run",
    )
    prospective_run.add_argument(
        "--input-artifact",
        type=Path,
        action="append",
        default=[],
        help="additional immutable evidence artifact to bind to the plan",
    )

    prospective_reconcile = subparsers.add_parser(
        "planning-prospective-reconcile-shadow",
        help="read-only match ended recommendations to natural SatNOGS observations",
    )
    prospective_reconcile.add_argument("--config", type=Path, required=True)
    prospective_reconcile.add_argument("--ledger", type=Path, required=True)
    prospective_reconcile.add_argument("--raw-snapshot", type=Path, required=True)
    prospective_reconcile.add_argument("--outcomes", type=Path, required=True)
    prospective_reconcile.add_argument("--cache-dir", type=Path, required=True)
    prospective_reconcile.add_argument("--user-agent", required=True)
    prospective_reconcile.add_argument("--lookback-hours", type=int, default=48)
    prospective_reconcile.add_argument(
        "--shared-rate-limit-file",
        type=Path,
        help="share the anonymous observations API quota with other collectors",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "env":
        write_environment(args.output)
        print(args.output)
        return 0
    if args.command == "capabilities":
        from .generic_receiver import default_receiver

        receiver = default_receiver()
        payload = {
            "schema_version": "receiver-capabilities-v1",
            "scope": {
                "generic_receiver": True,
                "satnogs_is_required": False,
                "all_protocols_implemented": False,
            },
            "capabilities": asdict(receiver.capabilities),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(args.output)
        return 0
    if args.command == "inventory-archive":
        from .archive_inventory import write_archive_inventory

        inventory = write_archive_inventory(args.catalogue, args.output)
        summary = inventory["summary"]
        print(
            f"{summary['observation_count']} observations, "
            f"{summary['iq_available_count']} with IQ -> {args.output}"
        )
        return 0
    if args.command == "profiles":
        from .satyaml_registry import build_satyaml_registry

        registry = build_satyaml_registry(args.paths)
        payload = registry.to_dict()
        payload["summary"] = {
            "profile_count": len(registry.profiles),
            "unambiguous_name_count": len(registry.by_name),
            "unambiguous_norad_count": len(registry.by_norad),
            "diagnostic_count": len(registry.diagnostics),
            "executable_compatibility_verified": False,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(
            f"{len(registry.profiles)} profiles, "
            f"{len(registry.diagnostics)} diagnostics -> {args.output}"
        )
        return 0
    if args.command == "scrape-camras":
        from .camras import parse_camras_index

        rows = parse_camras_index(args.html.read_text(encoding="utf-8"))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps([asdict(row) for row in rows], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"{len(rows)} rows -> {args.output}")
        return 0
    if args.command == "convert-camras":
        from .camras import convert_camras_raw_to_sigmf
        from .download_guard import assert_storage_capacity

        assert_storage_capacity(args.raw.stat().st_size, args.output_base.parent)
        result = convert_camras_raw_to_sigmf(
            args.raw,
            args.output_base,
            sample_rate_hz=args.sample_rate,
            sample_rate_basis=args.sample_rate_basis,
            doppler_state=args.doppler_state,
            center_frequency_hz=args.frequency,
            start_utc=args.start_utc,
            observation_id=args.observation_id,
            confirm_ci16_le=args.confirm_ci16_le,
        )
        print(result.metadata_path)
        return 0
    if args.command == "decode-afsk1200":
        from .afsk1200_orchestration import Afsk1200RunConfig, run_afsk1200_file
        from .afsk1200_plugin import Afsk1200Config

        rate_errors = (
            tuple(args.rate_error_ppm)
            if args.rate_error_ppm is not None
            else Afsk1200Config().timing_rate_errors_ppm
        )
        plugin = Afsk1200Config(
            sample_rate_hz=args.sample_rate,
            baudrate=args.baudrate,
            mark_hz=args.mark_hz,
            space_hz=args.space_hz,
            audio_sample_rate_hz=args.audio_sample_rate,
            window_blocks=args.window_blocks,
            stride_blocks=args.stride_blocks,
            permutation_block_samples=args.permutation_block_samples,
            timing_rate_errors_ppm=rate_errors,
            timing_phase_bins=args.phase_bins,
            timing_top_n=args.top_timing_hypotheses,
            max_candidate_records=args.max_candidate_records,
        )
        selected = Afsk1200RunConfig(
            plugin=plugin,
            segment_start_sample=args.segment_start_sample,
            segment_sample_count=args.segment_sample_count,
            event_key=args.event_key,
            expected_input_size_bytes=args.expected_size_bytes,
            expected_input_sha256=args.expected_sha256,
        )
        result = run_afsk1200_file(
            args.iq,
            args.output,
            config=selected,
            checkpoint_path=args.checkpoint,
            external_baseline_path=args.external_baseline,
            resume=not args.no_resume,
            max_windows=args.max_windows,
            api_version=args.api_version,
        )
        counters = result["resource_counters"]
        ledger = result["candidate_ledger"]
        print(
            f"{result['status']}: {counters['windows_completed']}/"
            f"{counters['windows_total']} windows, "
            f"trusted union={ledger['union_count']} -> {args.output}"
        )
        return 0
    if args.command == "decode-fsk-ax25":
        from .fsk_ax25_plugin import FskAx25Config
        from .fsk_file_orchestration import FskAx25RunConfig, run_fsk_ax25_file

        rate_errors = (
            tuple(args.rate_error_ppm)
            if args.rate_error_ppm is not None
            else FskAx25Config().timing_rate_errors_ppm
        )
        plugin = FskAx25Config(
            sample_rate_hz=args.sample_rate,
            baudrate=args.baudrate,
            decimation=args.decimation,
            g3ruh=args.g3ruh,
            window_seconds=args.window_seconds,
            hop_seconds=args.hop_seconds,
            timing_rate_errors_ppm=rate_errors,
            timing_phase_bins=args.phase_bins,
            timing_top_n=args.top_timing_hypotheses,
            max_candidate_records=args.max_candidate_records,
        )
        selected = FskAx25RunConfig(
            plugin=plugin,
            segment_start_sample=args.segment_start_sample,
            segment_sample_count=args.segment_sample_count,
            event_key=args.event_key,
            expected_input_size_bytes=args.expected_size_bytes,
            expected_input_sha256=args.expected_sha256,
        )
        result = run_fsk_ax25_file(
            args.iq,
            args.output,
            config=selected,
            checkpoint_path=args.checkpoint,
            external_baseline_path=args.external_baseline,
            resume=not args.no_resume,
            max_windows=args.max_windows,
            api_version=args.api_version,
        )
        counters = result["resource_counters"]
        ledger = result["candidate_ledger"]
        print(
            f"{result['status']}: {counters['windows_completed']}/"
            f"{counters['windows_total']} windows, "
            f"trusted union={ledger['union_count']} -> {args.output}"
        )
        return 0
    if args.command == "decode-blind-phase-fsk":
        from .blind_phase_fsk_file import (
            BlindPhaseFskRunConfig,
            run_blind_phase_fsk_file,
        )
        from .clipping_robust_fsk import BlindPhaseFskConfig

        receiver = BlindPhaseFskConfig(
            sample_rate_hz=args.sample_rate,
            baudrate=args.baudrate,
            analysis_window_seconds=args.analysis_window_seconds,
            decoder_window_seconds=args.decoder_window_seconds,
            decoder_window_lead_seconds=args.decoder_window_lead_seconds,
            candidate_window_limit=args.candidate_window_limit,
            window_nms_seconds=args.window_nms_seconds,
            constant_radius_factors=(
                tuple(args.constant_radius_factor)
                if args.constant_radius_factor is not None
                else (3.0,)
            ),
            phase_difference_lags=(
                tuple(args.phase_difference_lag)
                if args.phase_difference_lag is not None
                else (1,)
            ),
            descramble_modes=(
                tuple(mode == "g3ruh" for mode in args.descramble_mode)
                if args.descramble_mode is not None
                else (False, True)
            ),
            rate_errors_ppm=(
                tuple(args.rate_error_ppm)
                if args.rate_error_ppm is not None
                else (0.0,)
            ),
            phase_bins=args.phase_bins,
            short_search_timing_hypotheses=args.short_timing_hypotheses,
            deep_search_timing_hypotheses=args.deep_timing_hypotheses,
            minimum_consecutive_flags=args.minimum_consecutive_flags,
            minimum_frame_body_bits=args.minimum_frame_body_bits,
            short_least_reliable_symbols=args.short_least_reliable_symbols,
            short_maximum_flips=args.short_maximum_flips,
            short_maximum_attempts=args.short_maximum_attempts,
            deep_least_reliable_symbols=args.deep_least_reliable_symbols,
            deep_maximum_flips=args.deep_maximum_flips,
            deep_maximum_attempts=args.deep_maximum_attempts,
            maximum_regions_per_start=args.maximum_regions_per_start,
            candidate_neighbor_radius=args.candidate_neighbor_radius,
            error_unit_penalty=args.error_unit_penalty,
            maximum_map_seed_states=args.maximum_map_seed_states,
        )
        selected = BlindPhaseFskRunConfig(
            receiver=receiver,
            expected_input_size_bytes=args.expected_size_bytes,
            expected_input_sha256=args.expected_sha256,
            maximum_scratch_bytes=args.maximum_scratch_mib * 1024 * 1024,
        )
        result = run_blind_phase_fsk_file(
            args.iq,
            args.output,
            config=selected,
            scratch_directory=args.scratch_directory,
            api_version=args.api_version,
        )
        counters = result["resource_counters"]
        print(
            f"{result['status']}: {counters['decoded_windows']} blind windows, "
            f"trusted={counters['native_trusted_groups']}, "
            f"repaired-untrusted={len(result['candidates']['repaired_untrusted'])} "
            f"-> {args.output}"
        )
        return 0
    if args.command == "extract-rml24-hdf5":
        from .download_guard import DownloadPlan
        from .rml24_archive import extract_rml24_hdf5

        result = extract_rml24_hdf5(
            args.archive,
            args.output,
            plan=DownloadPlan.load(args.plan),
            report_path=args.report,
        )
        print(f"{result['status']} -> {args.output}; report {args.report}")
        return 0
    if args.command == "inventory-rml24-hdf5":
        from .rml24_archive import inventory_rml24_hdf5, write_rml24_hdf5_inventory

        result = inventory_rml24_hdf5(
            args.hdf5,
            batch_size=args.batch_size,
            max_records=args.max_records,
        )
        write_rml24_hdf5_inventory(args.output, result)
        print(
            f"{result['records_scanned']}/{result['record_count']} records, "
            f"{result['group_count']} groups -> {args.output}"
        )
        return 0
    if args.command == "convert-rml24-pickle":
        from .download_guard import DownloadPlan
        from .rml24_pickle_convert import convert_published_rml24_pickle_zip

        result = convert_published_rml24_pickle_zip(
            args.archive,
            args.output_directory,
            manifest_path=args.manifest,
            max_array_bytes=args.max_array_mib * 1024 * 1024,
            plan=DownloadPlan.load(args.plan),
        )
        print(
            f"{result['paired_group_count']}/{result['group_count']} paired groups; "
            f"pickle executed={result['pickle_executed']}"
        )
        return 0
    if args.command == "benchmark-rml24-physical":
        from .rml24_benchmark import (
            Rml24GroupShardSource,
            run_rml24_benchmark,
            write_rml24_benchmark_report,
        )
        from .rml24_physical_plugin import AlignmentPolicy, Rml24PhysicalBerPlugin

        source = Rml24GroupShardSource(
            args.shard_manifest,
            batch_size=args.batch_size,
        )
        plugin = Rml24PhysicalBerPlugin(
            sample_rate_hz=args.sample_rate,
            alignment_policy=AlignmentPolicy(max_shift_bits=args.max_shift_bits),
            recovery_version=args.recovery_version,
        )
        result = run_rml24_benchmark(
            source,
            bit_demodulator=plugin,
            max_records=args.max_records,
        )
        ber = result["metrics"]["bit_demodulation"]
        adapter = ber.get("adapter")
        if isinstance(adapter, dict):
            adapter["recovery_version"] = args.recovery_version
            if args.recovery_version == "carrier_timing_v2":
                adapter["name"] = "rml24-carrier-timing-v2-v1"
                adapter["recovery"] = {
                    "BPSK": "bounded second-power FFT carrier plus legacy timing",
                    "QPSK": "bounded fourth-power FFT carrier plus legacy timing",
                    "OQPSK": (
                        "bounded fourth-power FFT carrier plus six truth-free "
                        "half-symbol branch/boundary-pairing hypotheses"
                    ),
                    "GMSK/2FSK": "legacy phase-discriminator path unchanged",
                }
        write_rml24_benchmark_report(args.output, result)
        print(
            f"BER status={ber['status']}, recovery={args.recovery_version}, "
            f"supported={ber.get('supported_records', 0)}, "
            f"unsupported={ber.get('unsupported_records', 0)} -> {args.output}"
        )
        return 0
    if args.command == "benchmark-rml24-hdf5-physical":
        from .rml24_benchmark import (
            Rml24Hdf5PhysicalSource,
            run_rml24_benchmark,
            write_rml24_benchmark_report,
        )
        from .rml24_physical_plugin import AlignmentPolicy, Rml24PhysicalBerPlugin

        source = Rml24Hdf5PhysicalSource(
            args.hdf5,
            inventory_report=args.inventory_report,
            extraction_report=args.extraction_report,
            class_map=args.class_map,
            batch_size=args.batch_size,
        )
        plugin = Rml24PhysicalBerPlugin(
            sample_rate_hz=args.sample_rate,
            alignment_policy=AlignmentPolicy(max_shift_bits=args.max_shift_bits),
            recovery_version=args.recovery_version,
        )
        result = run_rml24_benchmark(
            source,
            bit_demodulator=plugin,
            max_records=args.max_records,
        )
        ber = result["metrics"]["bit_demodulation"]
        adapter = ber.get("adapter")
        if isinstance(adapter, dict):
            adapter["recovery_version"] = args.recovery_version
            if args.recovery_version == "carrier_timing_v2":
                adapter["name"] = "rml24-carrier-timing-v2-v1"
                adapter["recovery"] = {
                    "BPSK": "bounded second-power FFT carrier plus legacy timing",
                    "QPSK": "bounded fourth-power FFT carrier plus legacy timing",
                    "OQPSK": (
                        "bounded fourth-power FFT carrier plus six truth-free "
                        "half-symbol branch/boundary-pairing hypotheses"
                    ),
                    "GMSK/2FSK": "legacy phase-discriminator path unchanged",
                }
        write_rml24_benchmark_report(args.output, result)
        print(
            f"BER status={ber['status']}, recovery={args.recovery_version}, "
            f"supported={ber.get('supported_records', 0)}, "
            f"unsupported={ber.get('unsupported_records', 0)} -> {args.output}"
        )
        return 0
    if args.command == "planning-fetch-publication":
        from .planning.publication import acquire_frozen_cohort
        from .satnogs import read_api_token_file

        api_token = (
            read_api_token_file(args.api_token_file)
            if args.api_token_file is not None
            else None
        )
        manifest = acquire_frozen_cohort(
            args.config,
            raw_dir=args.raw_dir,
            manifest_path=args.manifest,
            cache_dir=args.cache_dir,
            shared_rate_limit_path=args.shared_rate_limit_file,
            api_token=api_token,
        )
        print(
            f"{manifest['total_rows_before_deduplication']} rows -> {args.manifest}"
        )
        return 0
    if args.command == "planning-build-dataset":
        from .planning.publication import build_publication_dataset

        manifest = build_publication_dataset(
            args.config,
            args.snapshot_manifest,
            dataset_path=args.output,
            dataset_manifest_path=args.manifest,
            raw_dir=args.raw_dir,
            covariate_archive_path=args.covariates,
        )
        print(
            f"{manifest['normalized_rows']} normalized rows, "
            f"count gate={manifest['publication_count_gate']} -> {args.manifest}"
        )
        return 0
    if args.command == "planning-evaluate-publication":
        from .planning.publication import evaluate_publication_dataset

        report = evaluate_publication_dataset(
            args.config,
            args.dataset,
            output_dir=args.output_dir,
        )
        evaluated = sum(
            task.get("status") == "evaluated"
            for task in report["tasks"].values()
        )
        print(f"{evaluated} evaluated probability tasks -> {args.output_dir}")
        return 0
    if args.command == "planning-build-probability-model":
        from .planning.learned_probability import build_frozen_probability_model

        model = build_frozen_probability_model(
            args.dataset,
            args.dataset_manifest,
            args.evaluation,
            args.output,
        )
        selected = {
            task: values["selected_model"]
            for task, values in model["tasks"].items()
        }
        print(f"frozen probability model {selected} -> {args.output}")
        return 0
    if args.command == "planning-audit-publication":
        from .planning.audit import audit_publication_artifacts

        audit = audit_publication_artifacts(
            dataset_path=args.dataset,
            dataset_manifest_path=args.dataset_manifest,
            evaluation_path=args.evaluation,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(
            f"audit passed={audit['passed']}, failed={audit['failed_check_count']} -> {args.output}"
        )
        return 0
    if args.command == "planning-export-satnogs":
        from .planning.satnogs_adapter import build_satnogs_schedule_export
        from .planning.satnogs_submit import satnogs_export_sha256
        from .planning.store import plan_from_document

        document = json.loads(args.plan.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("plan JSON must be an object")
        export = build_satnogs_schedule_export(plan_from_document(document))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(export.as_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"{len(export.api_payload)} dry-run jobs, "
            f"confirm-sha256={satnogs_export_sha256(export)} -> {args.output}"
        )
        return 0
    if args.command == "planning-write-provenance":
        from .planning.provenance import write_planning_source_manifest

        manifest = write_planning_source_manifest(
            args.project_root,
            args.output,
        )
        print(
            f"{manifest['file_count']} files, identity={manifest['source_identity_sha256']} -> {args.output}"
        )
        return 0
    if args.command == "planning-publication-readiness":
        from .planning.readiness import assess_planning_publication_readiness

        legacy_release_flags = (
            args.project_license_attested,
            args.reachable_contact_attested,
            args.attribution_review_attested,
        )
        if args.release_attestation is not None and any(legacy_release_flags):
            raise ValueError(
                "--release-attestation cannot be combined with one-shot attestation flags"
            )
        readiness = assess_planning_publication_readiness(
            snapshot_manifest_path=args.snapshot_manifest,
            dataset_manifest_path=args.dataset_manifest,
            evaluation_path=args.evaluation,
            independent_audit_path=args.independent_audit,
            source_manifest_path=args.source_manifest,
            results_manifest_path=args.results_manifest,
            junit_path=args.junit,
            test_attestation_path=args.test_attestation,
            project_license_attested=args.project_license_attested,
            reachable_contact_attested=args.reachable_contact_attested,
            attribution_review_attested=args.attribution_review_attested,
            release_attestation_path=args.release_attestation,
            publication_config_path=args.publication_config,
            prospective_config_path=args.prospective_config,
            prospective_ledger_path=args.prospective_ledger,
            probability_model_path=args.probability_model,
            covariate_evidence_path=args.covariate_evidence,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(readiness, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"technical_ready={readiness['technical_publication_ready']}, "
            f"external_ready={readiness['external_release_ready']} -> {args.output}"
        )
        return 0
    if args.command == "planning-write-publication-results":
        from .planning.manuscript import write_publication_results

        manifest = write_publication_results(
            dataset_manifest_path=args.dataset_manifest,
            evaluation_path=args.evaluation,
            independent_audit_path=args.independent_audit,
            output_dir=args.output_dir,
            probability_model_path=args.probability_model,
            covariate_evidence_path=args.covariate_evidence,
        )
        print(f"{len(manifest['artifacts'])} publication artifacts -> {args.output_dir}")
        return 0
    if args.command == "planning-submit-satnogs":
        from .planning.satnogs_submit import (
            SatnogsSubmissionClient,
            satnogs_export_from_dict,
        )

        token = os.environ.get("SATNOGS_API_TOKEN", "")
        export_payload = json.loads(args.export.read_text(encoding="utf-8"))
        if not isinstance(export_payload, dict):
            raise ValueError("SatNOGS export must be a JSON object")
        receipt = SatnogsSubmissionClient(
            api_token=token,
            user_agent=args.user_agent,
        ).submit(
            satnogs_export_from_dict(export_payload),
            confirmation_sha256=args.confirm_sha256,
        )
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(
            json.dumps(receipt.as_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"submitted {len(receipt.response_observation_ids)} SatNOGS jobs -> {args.receipt}"
        )
        return 0
    if args.command == "planning-design-cohort":
        from .planning.cohort import design_expanded_cohort
        from .planning.dataset import parse_api_datetime

        cohort = design_expanded_cohort(
            args.transmitters,
            args.stats,
            output_path=args.output,
            study_id=args.study_id,
            frozen_at=parse_api_datetime(args.frozen_at, name="frozen_at"),
            start=parse_api_datetime(args.start, name="start"),
            end=parse_api_datetime(args.end, name="end"),
            user_agent=args.user_agent,
            maximum_targets=args.maximum_targets,
            minimum_total_observations=args.minimum_total_observations,
            external_validation_fraction=args.external_validation_fraction,
            random_seed=args.random_seed,
            sampling_intervals=tuple(
                (
                    parse_api_datetime(values[0], name="sampling_interval.start"),
                    parse_api_datetime(values[1], name="sampling_interval.end"),
                )
                for values in args.sampling_interval
            ),
        )
        print(
            f"{len(cohort['targets'])} targets, "
            f"{len(cohort['external_validation_norad_ids'])} external -> {args.output}"
        )
        return 0
    if args.command == "planning-design-station-expansion":
        from .planning.cohort import design_station_expansion
        from .planning.dataset import parse_api_datetime

        expansion = design_station_expansion(
            args.target_pool,
            args.campaign_config,
            args.transmitters,
            args.baseline_hardware,
            args.station_catalog,
            output_path=args.output,
            frozen_at=parse_api_datetime(args.frozen_at, name="frozen_at"),
            minimum_observations=args.minimum_observations,
            required_compatible_stations_per_target=(
                args.required_compatible_stations_per_target
            ),
        )
        print(
            f"{len(expansion['selected_stations'])} added stations, "
            f"{expansion['final_covered_target_count']} covered targets -> {args.output}"
        )
        return 0
    if args.command == "planning-snapshot-antennas":
        from .planning.covariates import (
            SATNOGS_HARDWARE_SOURCE,
            HardwareRecord,
            write_covariate_archive,
        )
        from .planning.station_inventory import SatnogsStationInventoryProvider
        from .satnogs import SatNOGSClient

        client = SatNOGSClient(
            user_agent=args.user_agent,
            cache_dir=args.cache_dir,
            max_concurrency=1,
        )
        snapshot = SatnogsStationInventoryProvider(client).fetch(
            args.station_id, allow_testing=args.allow_testing
        )
        captured_at = snapshot.retrieved_at
        evidence_by_station = dict(snapshot.evidence_sha256_by_station)
        hardware = tuple(
            HardwareRecord.from_station(
                station,
                effective_from=captured_at,
                known_at=captured_at,
                source=SATNOGS_HARDWARE_SOURCE,
                evidence_sha256=evidence_by_station[station.satnogs_station_id],
            )
            for station in snapshot.stations
        )
        write_covariate_archive(args.output, hardware=hardware)
        print(f"{len(hardware)} antenna configurations -> {args.output}")
        return 0
    if args.command == "planning-fetch-covariates":
        from .planning.covariates import (
            fetch_historical_covariates,
            read_covariate_archive,
            write_covariate_archive,
        )
        from .planning.dataset import read_normalized_jsonl

        rows = read_normalized_jsonl(args.dataset)
        weather, kp = fetch_historical_covariates(
            rows,
            user_agent=args.user_agent,
            cache_dir=args.cache_dir,
            minimum_request_interval_seconds=args.minimum_request_interval_seconds,
            weather_maximum_missing_days=args.weather_maximum_missing_days,
        )
        hardware = ()
        if args.hardware_archive is not None:
            _, _, hardware = read_covariate_archive(args.hardware_archive)
        write_covariate_archive(
            args.output, weather=weather, kp=kp, hardware=hardware
        )
        print(
            f"{len(weather)} weather hours, {len(kp)} Kp intervals, "
            f"{len(hardware)} hardware snapshots -> {args.output}"
        )
        return 0
    if args.command == "planning-fetch-forecast":
        from .planning.covariates import (
            fetch_forecast_covariates,
            read_covariate_archive,
            write_covariate_archive,
        )
        from .planning.prospective import ProspectiveCampaignConfig
        from .planning.station_inventory import SatnogsStationInventoryProvider
        from .satnogs import SatNOGSClient

        config = ProspectiveCampaignConfig.load(args.config)
        client = SatNOGSClient(
            user_agent=args.user_agent,
            cache_dir=args.cache_dir / "satnogs-stations",
            max_concurrency=1,
        )
        station_snapshot = SatnogsStationInventoryProvider(client).fetch(
            config.station_ids, skip_unavailable=True
        )
        stations = station_snapshot.stations
        weather, kp, hardware = fetch_forecast_covariates(
            stations,
            start=config.start,
            end=config.end,
            user_agent=args.user_agent,
            cache_dir=args.cache_dir,
            hardware_evidence_sha256=dict(
                station_snapshot.evidence_sha256_by_station
            ),
        )
        if args.hardware_archive is not None:
            _, _, local_hardware = read_covariate_archive(args.hardware_archive)
            hardware = (*hardware, *local_hardware)
        write_covariate_archive(
            args.output, weather=weather, kp=kp, hardware=hardware
        )
        print(
            f"{len(weather)} forecast hours, {len(kp)} Kp intervals, "
            f"{len(hardware)} hardware snapshots, "
            f"{len(station_snapshot.excluded_station_ids)} unavailable stations "
            f"excluded -> {args.output}"
        )
        return 0
    if args.command == "planning-write-covariate-evidence":
        from .planning.covariates import write_covariate_evidence_manifest

        evidence = write_covariate_evidence_manifest(
            args.covariates, args.cache_dir, args.output
        )
        print(
            f"{evidence['response_count']} raw source responses -> {args.output}"
        )
        return 0
    if args.command == "planning-build-hardware-archive":
        from .planning.covariates import build_hardware_archive_from_declarations

        archive = build_hardware_archive_from_declarations(args.input, args.output)
        print(f"{len(archive['hardware'])} hardware declarations -> {args.output}")
        return 0
    if args.command == "planning-enrich-dataset":
        from .planning.covariates import enrich_dataset_file

        manifest = enrich_dataset_file(
            args.dataset,
            args.covariates,
            output_path=args.output,
            manifest_path=args.manifest,
        )
        print(
            f"{manifest['row_count']} enriched rows -> {args.output}"
        )
        return 0
    if args.command == "planning-evaluate-covariates":
        from .planning.evaluation import write_covariate_ablation_report

        report = write_covariate_ablation_report(
            args.dataset,
            args.output_dir,
            bootstrap_replicates=args.bootstrap_replicates,
        )
        print(
            f"{len(report['tasks'])} covariate tasks -> "
            f"{args.output_dir / 'covariate-ablation.json'}"
        )
        return 0
    if args.command == "planning-prospective-init":
        from .planning.prospective import initialize_campaign

        event = initialize_campaign(args.config, args.ledger)
        print(f"campaign initialized event={event.event_sha256} -> {args.ledger}")
        return 0
    if args.command == "planning-prospective-commit-plan":
        from .planning.prospective import commit_plan

        event = commit_plan(
            args.config,
            args.ledger,
            args.plan,
            input_artifacts=args.input_artifact,
        )
        print(f"plan committed event={event.event_sha256} -> {args.ledger}")
        return 0
    if args.command == "planning-prospective-record-outcomes":
        from .planning.prospective import record_outcomes

        events = record_outcomes(
            args.config, args.ledger, args.outcomes
        )
        print(f"{len(events)} outcomes appended -> {args.ledger}")
        return 0
    if args.command == "planning-prospective-report":
        from .planning.prospective import write_prospective_report

        report = write_prospective_report(
            args.config, args.ledger, args.output
        )
        print(
            f"publication_complete={report['publication_complete']}, "
            f"elapsed_days={report['elapsed_days']:.2f} -> {args.output}"
        )
        return 0
    if args.command == "planning-prospective-run-shadow":
        from .planning.campaign_runner import run_shadow_once

        result = run_shadow_once(
            campaign_config_path=args.config,
            publication_config_path=args.publication_config,
            transmitter_inventory_path=args.transmitters,
            covariate_archive_path=args.covariates,
            history_dataset_path=args.history_dataset,
            ledger_path=args.ledger,
            plan_path=args.plan,
            state_database_path=args.state_db,
            cache_dir=args.cache_dir,
            user_agent=args.user_agent,
            blocker_path=args.blockers,
            target_overrides_path=args.target_overrides,
            probability_model_path=args.probability_model,
            source_manifest_path=args.source_manifest,
            additional_input_artifacts=tuple(args.input_artifact),
        )
        print(
            f"{result['assignment_count']} assignments, "
            f"{result['opportunity_count']} opportunities, dry-run -> {args.plan}"
        )
        return 0
    if args.command == "planning-prospective-reconcile-shadow":
        from .planning.campaign_runner import reconcile_shadow_network

        result = reconcile_shadow_network(
            campaign_config_path=args.config,
            ledger_path=args.ledger,
            raw_snapshot_path=args.raw_snapshot,
            outcomes_path=args.outcomes,
            cache_dir=args.cache_dir,
            user_agent=args.user_agent,
            lookback_hours=args.lookback_hours,
            shared_rate_limit_path=args.shared_rate_limit_file,
        )
        print(
            f"{result['matched_outcomes']} matched outcomes from "
            f"{result['network_observations']} observations -> {args.outcomes}"
        )
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())

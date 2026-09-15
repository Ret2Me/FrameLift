"""Bind a complete planning test run to the exact frozen source identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence


TEST_ATTESTATION_SCHEMA = "observation-planning-test-attestation-v1"
PUBLICATION_TEST_COMMAND = (
    ".venv/bin/pytest",
    "-q",
    "tests/test_observation_planning.py",
    "tests/test_planning_history_pagination.py",
    "tests/test_planning_input_audit.py",
    "tests/test_planning_manuscript.py",
    "tests/test_planning_publication_dataset.py",
    "tests/test_planning_readiness.py",
    "tests/test_planning_satnogs_submit.py",
    "tests/test_planning_station_inventory.py",
    "tests/test_planning_v4.py",
    "tests/test_planning_v4_campaign_wiring.py",
    "tests/test_satnogs.py",
    "--junitxml=reports/pytest-planning-v4.xml",
)


class PlanningTestAttestationError(ValueError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_mapping(path: Path, *, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanningTestAttestationError(f"cannot read {label}") from exc
    if not isinstance(payload, Mapping):
        raise PlanningTestAttestationError(f"{label} must be a JSON object")
    return payload


def _parse_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PlanningTestAttestationError(f"{label} timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PlanningTestAttestationError(f"{label} timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise PlanningTestAttestationError(
            f"{label} timestamp must include a timezone"
        )
    return parsed.astimezone(UTC)


def _relative(path: Path, root: Path, *, label: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise PlanningTestAttestationError(
            f"{label} leaves the project root"
        ) from exc


def _source_manifest_summary(
    source_manifest_path: Path,
    *,
    project_root: Path,
) -> Mapping[str, object]:
    source = _load_mapping(source_manifest_path, label="source manifest")
    if source.get("schema_version") != "observation-planning-source-manifest-v1":
        raise PlanningTestAttestationError("source manifest schema mismatch")
    if Path(str(source.get("project_root", ""))).resolve() != project_root.resolve():
        raise PlanningTestAttestationError("source manifest project root mismatch")
    records = source.get("files")
    if not isinstance(records, list) or not records:
        raise PlanningTestAttestationError("source manifest has no files")
    paths: set[str] = set()
    required_test_modules: list[str] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise PlanningTestAttestationError("source manifest record is malformed")
        relative = record.get("path")
        if not isinstance(relative, str) or not relative or relative in paths:
            raise PlanningTestAttestationError("source manifest paths are invalid")
        paths.add(relative)
        path = (project_root / relative).resolve()
        _relative(path, project_root, label="source record")
        try:
            body = path.read_bytes()
        except OSError as exc:
            raise PlanningTestAttestationError(
                f"source file is unavailable: {relative}"
            ) from exc
        if len(body) != record.get("byte_length") or hashlib.sha256(body).hexdigest() != record.get(
            "sha256"
        ):
            raise PlanningTestAttestationError(
                f"source file identity mismatch: {relative}"
            )
        if relative in {
            "tests/test_observation_planning.py",
            "tests/test_satnogs.py",
        } or (
            relative.startswith("tests/test_planning") and relative.endswith(".py")
        ):
            required_test_modules.append(
                Path(relative).with_suffix("").as_posix().replace("/", ".")
            )
    identity = hashlib.sha256(
        json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if source.get("file_count") != len(records) or source.get(
        "source_identity_sha256"
    ) != identity:
        raise PlanningTestAttestationError("source manifest identity mismatch")
    created_at = _parse_timestamp(source.get("created_at"), label="source manifest")
    return {
        "identity": identity,
        "created_at": created_at,
        "required_test_modules": sorted(required_test_modules),
    }


def _junit_summary(
    junit_path: Path,
    *,
    required_test_modules: Sequence[str],
) -> Mapping[str, object]:
    try:
        root = ET.parse(junit_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise PlanningTestAttestationError("cannot read JUnit report") from exc
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        raise PlanningTestAttestationError("JUnit report has no test suites")
    tests = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", 0)) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", 0)) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", 0)) for suite in suites)
    starts: list[datetime] = []
    finishes: list[datetime] = []
    for suite in suites:
        started = _parse_timestamp(suite.attrib.get("timestamp"), label="JUnit suite")
        try:
            duration = float(suite.attrib.get("time", ""))
        except ValueError as exc:
            raise PlanningTestAttestationError(
                "JUnit suite duration is invalid"
            ) from exc
        if duration < 0:
            raise PlanningTestAttestationError("JUnit suite duration is negative")
        starts.append(started)
        finishes.append(started + timedelta(seconds=duration))
    classnames = {
        str(case.attrib.get("classname", ""))
        for case in root.iter("testcase")
        if case.attrib.get("classname")
    }
    covered_modules = sorted(
        module
        for module in required_test_modules
        if any(
            classname == module or classname.startswith(module + ".")
            for classname in classnames
        )
    )
    missing_modules = sorted(set(required_test_modules) - set(covered_modules))
    return {
        "tests": tests,
        "testcase_elements": len(list(root.iter("testcase"))),
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "started_at": min(starts).isoformat().replace("+00:00", "Z"),
        "finished_at": max(finishes).isoformat().replace("+00:00", "Z"),
        "required_test_modules": list(required_test_modules),
        "covered_test_modules": covered_modules,
        "missing_test_modules": missing_modules,
    }


def write_test_attestation(
    *,
    project_root: Path,
    source_manifest_path: Path,
    junit_path: Path,
    output_path: Path,
    now: datetime | None = None,
) -> Mapping[str, object]:
    project_root = project_root.resolve()
    source = _source_manifest_summary(
        source_manifest_path, project_root=project_root
    )
    junit = _junit_summary(
        junit_path,
        required_test_modules=source["required_test_modules"],  # type: ignore[arg-type]
    )
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    source_created_at = source["created_at"]
    test_started_at = _parse_timestamp(junit["started_at"], label="test start")
    test_finished_at = _parse_timestamp(junit["finished_at"], label="test finish")
    chronology_valid = (
        isinstance(source_created_at, datetime)
        and source_created_at <= test_started_at <= test_finished_at <= created_at
        and created_at - test_finished_at <= timedelta(minutes=10)
    )
    passed = (
        int(junit["tests"]) > 0
        and int(junit["failures"]) == 0
        and int(junit["errors"]) == 0
        and not junit["missing_test_modules"]
        and chronology_valid
    )
    payload = {
        "schema_version": TEST_ATTESTATION_SCHEMA,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "project_root": str(project_root),
        "source_manifest_path": _relative(
            source_manifest_path, project_root, label="source manifest"
        ),
        "source_manifest_sha256": _sha256(source_manifest_path),
        "source_identity_sha256": source["identity"],
        "junit_path": _relative(junit_path, project_root, label="JUnit report"),
        "junit_sha256": _sha256(junit_path),
        "command": list(PUBLICATION_TEST_COMMAND),
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "junit": junit,
        "chronology_valid": chronology_valid,
        "passed": passed,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if not passed:
        raise PlanningTestAttestationError("test attestation gates did not pass")
    return payload


def validate_test_attestation(
    attestation_path: Path,
    *,
    project_root: Path,
    source_manifest_path: Path,
    junit_path: Path,
) -> Mapping[str, object]:
    project_root = project_root.resolve()
    payload = _load_mapping(attestation_path, label="test attestation")
    source = _source_manifest_summary(
        source_manifest_path, project_root=project_root
    )
    junit = _junit_summary(
        junit_path,
        required_test_modules=source["required_test_modules"],  # type: ignore[arg-type]
    )
    created_at = _parse_timestamp(payload.get("created_at"), label="attestation")
    source_created_at = source["created_at"]
    test_started_at = _parse_timestamp(junit["started_at"], label="test start")
    test_finished_at = _parse_timestamp(junit["finished_at"], label="test finish")
    chronology_valid = (
        isinstance(source_created_at, datetime)
        and source_created_at <= test_started_at <= test_finished_at <= created_at
        and created_at - test_finished_at <= timedelta(minutes=10)
    )
    checks = {
        "schema": payload.get("schema_version") == TEST_ATTESTATION_SCHEMA,
        "project_root": Path(str(payload.get("project_root", ""))).resolve()
        == project_root,
        "source_manifest_path": payload.get("source_manifest_path")
        == _relative(source_manifest_path, project_root, label="source manifest"),
        "source_manifest_sha256": payload.get("source_manifest_sha256")
        == _sha256(source_manifest_path),
        "source_identity": payload.get("source_identity_sha256")
        == source["identity"],
        "junit_path": payload.get("junit_path")
        == _relative(junit_path, project_root, label="JUnit report"),
        "junit_sha256": payload.get("junit_sha256") == _sha256(junit_path),
        "command": payload.get("command") == list(PUBLICATION_TEST_COMMAND),
        "junit_summary": payload.get("junit") == junit,
        "chronology": payload.get("chronology_valid") is True
        and chronology_valid,
        "test_result": payload.get("passed") is True
        and int(junit["tests"]) > 0
        and int(junit["failures"]) == 0
        and int(junit["errors"]) == 0
        and not junit["missing_test_modules"],
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "source_identity_sha256": source["identity"],
        "junit_sha256": _sha256(junit_path),
        "tests": junit["tests"],
        "required_test_modules": junit["required_test_modules"],
        "missing_test_modules": junit["missing_test_modules"],
        "attestation_sha256": _sha256(attestation_path),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    if args.write:
        payload = write_test_attestation(
            project_root=args.project_root,
            source_manifest_path=args.source_manifest,
            junit_path=args.junit,
            output_path=args.attestation,
        )
        print(
            f"pass={payload['passed']}, tests={payload['junit']['tests']} -> "
            f"{args.attestation}"
        )
        return 0
    result = validate_test_attestation(
        args.attestation,
        project_root=args.project_root,
        source_manifest_path=args.source_manifest,
        junit_path=args.junit,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

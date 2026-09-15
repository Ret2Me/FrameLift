from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-fsk/audit_v4_3_determinism.py"


def _module():
    spec = importlib.util.spec_from_file_location("v4_3_determinism_audit_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AUDIT = _module()


def _write(path: Path, value: object) -> str:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _result(*, argv: str, elapsed: float, rss: int, payload: str = "aa") -> dict[str, object]:
    return {
        "schema_version": "blind-clipping-robust-fsk-development-v4-3",
        "implementation": {"argv": [argv], "python": "3.12.3"},
        "execution": {
            "elapsed_seconds": elapsed,
            "maximum_resident_set_kib": rss,
            "decoded_windows": 4,
        },
        "detections": [{"payload_hex": payload}],
    }


def test_runtime_only_differences_are_excluded(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first_sha = _write(first, _result(argv="first", elapsed=1.0, rss=10))
    second_sha = _write(second, _result(argv="second", elapsed=2.0, rss=20))
    report = AUDIT.audit(
        first=first,
        expected_first_sha256=first_sha,
        second=second,
        expected_second_sha256=second_sha,
    )
    assert report["status"] == "PASS_SEMANTIC_DETERMINISM"
    assert report["comparison"]["semantic_documents_equal"] is True
    assert report["comparison"]["first_semantic_sha256"] == report["comparison"]["second_semantic_sha256"]


def test_scientific_difference_is_not_excluded(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first_sha = _write(first, _result(argv="first", elapsed=1.0, rss=10))
    second_sha = _write(second, _result(argv="second", elapsed=2.0, rss=20, payload="bb"))
    report = AUDIT.audit(
        first=first,
        expected_first_sha256=first_sha,
        second=second,
        expected_second_sha256=second_sha,
    )
    assert report["status"] == "FAIL_SEMANTIC_DETERMINISM"
    assert report["comparison"]["semantic_documents_equal"] is False
    assert report["comparison"]["first_semantic_sha256"] != report["comparison"]["second_semantic_sha256"]

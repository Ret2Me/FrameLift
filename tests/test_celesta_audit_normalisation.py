from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_auditor():
    path = (
        Path(__file__).resolve().parents[1]
        / "work/positive-real-iq/audit_celesta_confirmation_v2.py"
    )
    spec = importlib.util.spec_from_file_location("celesta_v2_auditor", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_set_digest_is_order_and_duplicate_independent() -> None:
    auditor = _load_auditor()
    first = auditor.canonical_set_sha256({b"frame-b", b"frame-a"})
    second = auditor.canonical_set_sha256(set([b"frame-a", b"frame-b", b"frame-a"]))
    assert first == second


def test_canonical_set_digest_uses_unambiguous_frame_boundaries() -> None:
    auditor = _load_auditor()
    assert auditor.canonical_set_sha256({b"a", b"bc"}) != auditor.canonical_set_sha256(
        {b"ab", b"c"}
    )

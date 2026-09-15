import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ogg_audio_null_smoke import FAMILIES, make_control, merge_identities


@pytest.mark.parametrize("family", FAMILIES)
def test_control_is_finite_reproducible_and_seed_sensitive(family):
    first = make_control(family, 531, 8192, 48000)
    assert first.shape == (8192,)
    assert np.isfinite(first).all()
    assert np.std(first) > 0
    assert np.array_equal(first, make_control(family, 531, 8192, 48000))
    assert not np.array_equal(first, make_control(family, 532, 8192, 48000))


@pytest.mark.parametrize("family,count,rate", [("unknown", 8192, 48000), ("white_gaussian", 5, 48000), ("colored_ar1", 8192, 0)])
def test_invalid_control_specification(family, count, rate):
    with pytest.raises(ValueError):
        make_control(family, 0, count, rate)


def test_identity_merge_retains_additional_transitive_dependency(tmp_path):
    direct = {"path": str(tmp_path / "fast_ax25.py"), "sha256": "one"}
    dependency = {"path": str(tmp_path / "camras_replay.py"), "sha256": "two"}
    result = merge_identities([direct], [direct, dependency])
    assert result == sorted([direct, dependency], key=lambda row: row["path"])


def test_identity_merge_rejects_conflicting_frozen_hash(tmp_path):
    path = str(tmp_path / "camras_replay.py")
    with pytest.raises(ValueError, match="conflicting"):
        merge_identities([{"path": path, "sha256": "old"}], [{"path": path, "sha256": "new"}])

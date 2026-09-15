"""Freeze existing Python positive-waveform tests for independent Rust replay.

Development utility only. Pure Rust tests read the resulting fixed IQ bytes;
they never invoke Python or use truth until the explicit alignment scorer.
"""
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from telemetry_yield import rml24_physical_plugin as reference

source = ROOT / "tests/test_rml24_physical_plugin.py"
spec = importlib.util.spec_from_file_location("physical_python_positive_fixtures", source)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
rows = []
policy = reference.AlignmentPolicy()
for function, modulation, baud in [
    (fixtures._bpsk_fixture, "BPSK", 100_000.0),
    (fixtures._fsk_fixture, "2FSK", 250_000.0),
    (fixtures._gmsk_fixture, "GMSK", 250_000.0),
    (fixtures._qpsk_fixture, "QPSK", 100_000.0),
    (fixtures._oqpsk_fixture, "OQPSK", 100_000.0),
]:
    iq, truth = function()
    packed = np.ascontiguousarray(iq.T, dtype="<f4").tobytes()
    row = dict(modulation=modulation, iq_f32le_hex=packed.hex(),
               iq_sha256=hashlib.sha256(packed).hexdigest(), truth=truth.tolist(), versions=[])
    for version in ("legacy", "carrier_timing_v2"):
        arguments = dict(sample_rate_hz=1_000_000.0, symbol_rate_hz=baud)
        if modulation == "BPSK":
            fn = reference.demodulate_bpsk_unaligned if version == "legacy" else reference.demodulate_bpsk_v2_unaligned
            bits, diagnostics = fn(iq, **arguments)
            variants, names = (bits,), ("binary_timing",)
            aligned, alignment = reference.align_for_ber(bits, truth, policy=policy)
        elif modulation in ("2FSK", "GMSK"):
            bits, diagnostics = reference.demodulate_binary_fsk_unaligned(iq, **arguments)
            variants, names = (bits,), ("binary_timing",)
            aligned, alignment = reference.align_for_ber(bits, truth, policy=policy)
        else:
            fn = reference.demodulate_qpsk_unaligned if version == "legacy" else reference.demodulate_qpsk_v2_unaligned
            decisions, diagnostics = fn(iq, offset=modulation == "OQPSK", **arguments)
            variants, names = decisions.bit_variants, decisions.variant_names
            aligned, alignment = reference.align_qpsk_for_ber(decisions, truth, policy=policy)
        row["versions"].append(dict(config=dict(sample_rate_hz=1_000_000.0, symbol_rate_hz=baud,
            modulation=modulation, recovery_version=version, max_carrier_offset_hz=2000.0, delayed_branch=None),
            variant_names=names, lengths=[len(x) for x in variants],
            bit_sha256=[hashlib.sha256(np.asarray(x, dtype=np.uint8).tobytes()).hexdigest() for x in variants],
            diagnostics=diagnostics, policy=asdict(policy), alignment=alignment,
            aligned_sha256=hashlib.sha256(np.asarray(aligned, dtype=np.uint8).tobytes()).hexdigest()))
    rows.append(row)

document = dict(schema="python-physical-positive-fixture-v1", numpy=np.__version__,
    physical_source_sha256=hashlib.sha256((ROOT / "src/telemetry_yield/rml24_physical_plugin.py").read_bytes()).hexdigest(),
    fixture_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    note="Synthetic positive signals; correctness equivalence, not real satellite telemetry or publication qualification.",
    records=rows)
target = Path(__file__).with_name("physical_positive_oracle.json")
with target.open("x", encoding="utf-8") as stream:
    json.dump(document, stream, indent=2, allow_nan=False)
    stream.write("\n")
print(json.dumps(dict(records=len(rows), comparisons=sum(len(x["versions"]) for x in rows), output=str(target))))

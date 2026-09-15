"""One-time development oracle generator; never imported/run by Rust tests.

Uses immutable pre-migration Python as an independent reference, and refuses
to overwrite an existing fixture. Rust reproduces integer-LCG IQ exactly.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from telemetry_yield import rml24_physical_plugin as reference


def lcg(seed, count):
    state = seed
    output = []
    for _ in range(count):
        state = (state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        output.append(((state >> 32) - (1 << 31)) / (1 << 31))
    return np.array(output)


physical = []
profiles = [("BPSK", "legacy"), ("BPSK", "carrier_timing_v2"),
            ("FSK", "legacy"), ("QPSK", "legacy"),
            ("QPSK", "carrier_timing_v2"), ("OQPSK", "legacy"),
            ("OQPSK", "carrier_timing_v2")]
for seed, count, baud in ((42, 512, 6000.0), (91, 513, 6000.0), (17, 768, 7000.0)):
    scalars = lcg(seed, 2 * count)
    iq = np.column_stack((scalars[::2], scalars[1::2]))
    for modulation, version in profiles:
        arguments = dict(sample_rate_hz=48000.0, symbol_rate_hz=baud)
        if modulation == "BPSK":
            fn = reference.demodulate_bpsk_unaligned if version == "legacy" else reference.demodulate_bpsk_v2_unaligned
            bits, diagnostics = fn(iq, **arguments)
            variants, names = (bits,), ("binary_timing",)
        elif modulation == "FSK":
            bits, diagnostics = reference.demodulate_binary_fsk_unaligned(iq, **arguments)
            variants, names = (bits,), ("binary_timing",)
        else:
            fn = reference.demodulate_qpsk_unaligned if version == "legacy" else reference.demodulate_qpsk_v2_unaligned
            decisions, diagnostics = fn(iq, offset=modulation == "OQPSK", **arguments)
            variants, names = decisions.bit_variants, decisions.variant_names
        physical.append(dict(seed=seed, sample_count=count,
            config=dict(sample_rate_hz=48000.0, symbol_rate_hz=baud, modulation=modulation,
                        recovery_version=version, max_carrier_offset_hz=2000.0, delayed_branch=None),
            variant_names=names, lengths=[len(x) for x in variants],
            bit_sha256=[hashlib.sha256(np.asarray(x, dtype=np.uint8).tobytes()).hexdigest() for x in variants],
            diagnostics=diagnostics))

alignment = []
for index in range(24):
    truth = (lcg(index + 123, 17 + index % 5) < 0).astype(np.uint8)
    candidate = (lcg(index + 42, 11 + index % 9) < 0).astype(np.uint8)
    policy = reference.AlignmentPolicy(max_shift_bits=index % 9,
        allow_global_polarity_inversion=bool(index % 2), allow_iq_reflection=bool(index % 3),
        missing_truth_bits_count_as_errors=bool(index % 4))
    for qpsk in (False, True):
        row = dict(qpsk=qpsk, truth=truth.tolist(), policy=asdict(policy))
        if qpsk:
            variants = (candidate, np.concatenate((candidate[2:], [1, 0])))
            candidate_object = reference.QpskUnaligned(variants, ("first", "second"))
            row["candidate"] = dict(bit_variants=[x.tolist() for x in variants],
                                    variant_names=["first", "second"], diagnostics={})
            fn = reference.align_qpsk_for_ber
        else:
            candidate_object = candidate
            row["candidate"] = candidate.tolist()
            fn = reference.align_for_ber
        try:
            aligned, diagnostics = fn(candidate_object, truth, policy=policy)
            row.update(aligned=aligned.tolist(), diagnostics=diagnostics)
        except ValueError as error:
            row["error"] = str(error)
        alignment.append(row)

source = ROOT / "src/telemetry_yield/rml24_physical_plugin.py"
document = dict(schema="python-physical-differential-fixture-v1", numpy=np.__version__,
    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    note="Unaligned bits are not telemetry frames. Truth enters alignment fixtures only.",
    physical=physical, alignment=alignment)
target = Path(__file__).with_name("physical_oracle.json")
with target.open("x", encoding="utf-8") as stream:
    json.dump(document, stream, indent=2, allow_nan=False)
    stream.write("\n")
print(json.dumps(dict(physical_cases=len(physical), alignment_cases=len(alignment), output=str(target))))

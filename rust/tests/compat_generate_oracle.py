"""One-time Python compatibility oracle; never needed by Rust tests/runtime."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from telemetry_yield.crc import append_ax25_fcs
from telemetry_yield.gr_satellites_pdu_adapter import (
    extract_gr_satellites_hdlc_pdus, decode_gr_satellites_g3ruh_levels,
)
from telemetry_yield.symbol_boundary import (
    AX25_FLAG_BITS, g3ruh_input_for_plain_bits, nrzi_encode_satnogs, stuff_hdlc_bits,
)

FLAG = bytes(AX25_FLAG_BITS)
OBS4704 = bytes.fromhex("6073d927ea3ae8157bfd2b60")
STRICT = bytes.fromhex("94a662b2a0826094a662b29eb2e103f00018ad8001020304")


def bits(frame):
    return bytes((byte >> i) & 1 for byte in frame for i in range(8))


def region(payload):
    return stuff_hdlc_bits(bits(append_ax25_fcs(payload)))


def normalize(value):
    if isinstance(value, bytes):
        return list(value)
    if isinstance(value, (list, tuple)):
        return [normalize(x) for x in value]
    if isinstance(value, dict):
        return {"final_address" if key == "final" else key: normalize(x) for key, x in value.items()}
    return value


streams = [
    ("observation4704_left_padding4", FLAG + region(OBS4704)[4:] + FLAG, 10000),
    ("strict_ax25", FLAG + region(STRICT) + FLAG, 10000),
    ("non_ax25", FLAG + region(b"not-an-ax25-pdu") + FLAG, 10000),
    ("empty_crc", FLAG + region(b"") + FLAG, 10000),
    ("no_opening_flag", region(OBS4704) + FLAG, 10000),
    ("no_closing_flag", FLAG + region(STRICT), 10000),
    ("duplicate_emission", FLAG + region(STRICT) + FLAG + region(STRICT) + FLAG, 10000),
    ("bounded_prefix_truncation", FLAG + bytes(257) + region(OBS4704) + FLAG, 12),
    ("shorter_than_pdu_limit", FLAG + region(OBS4704) + FLAG, 1),
    ("long_abort", FLAG + region(STRICT) + bytes([0] + [1] * 12 + [0]) + FLAG, 10000),
]
bad_frame = bytearray(append_ax25_fcs(STRICT))
bad_frame[-1] ^= 128
streams.append(("bad_crc", FLAG + stuff_hdlc_bits(bits(bad_frame)) + FLAG, 10000))
for drop in range(8):
    payload = bytes([0, 255, 31, 248, 0, 42, 255])
    streams.append((f"left_padding{drop}", FLAG + region(payload)[drop:] + FLAG, 10000))
for length in (1, 2, 3, 14, 32):
    payload = bytes((i * 73 + length) % 256 for i in range(length))
    streams.append((f"payload_length{length}", FLAG * 3 + region(payload) + FLAG * 2, 10000))

cases = []
for name, decoded, maximum in streams:
    for stage in ("decoded", "levels"):
        values = decoded if stage == "decoded" else nrzi_encode_satnogs(g3ruh_input_for_plain_bits(decoded), initial_level=0)
        function = extract_gr_satellites_hdlc_pdus if stage == "decoded" else decode_gr_satellites_g3ruh_levels
        candidates = function(values, max_length=maximum)
        cases.append(dict(name=name, stage=stage, input_bits=list(values), max_length=maximum,
            expected=[normalize(asdict(x)) for x in candidates]))
source = ROOT / "src/telemetry_yield/gr_satellites_pdu_adapter.py"
document = dict(schema="python-gr-satellites-compat-differential-fixture-v1",
    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), cases=cases,
    note="Candidate PDU emission only; no promotion to trusted telemetry. Python final address key is normalized to shared Rust final_address.")
target = Path(__file__).with_name("compat_oracle.json")
with target.open("x", encoding="utf-8") as stream:
    json.dump(document, stream, indent=2, allow_nan=False)
    stream.write("\n")
print(json.dumps(dict(cases=len(cases), candidates=sum(len(x["expected"]) for x in cases), output=str(target))))

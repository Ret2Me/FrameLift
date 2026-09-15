"""Offline SQLite-selector fixture, never a Rust runtime dependency."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import struct

from telemetry_yield.blind_phase_fsk_file import select_ci16_phase_windows_streaming
from telemetry_yield.clipping_robust_fsk import BlindPhaseFskConfig


def main():
    tests = Path(__file__).resolve().parent
    root = tests.parents[1]
    target = tests / "clipping_file_fixtures"
    target.mkdir(exist_ok=False)
    base = json.loads((tests / "clipping_fixtures/oracle.json").read_text())
    document = {"schema":"clipping-file-sqlite-offline-oracle-v1", "runtime_dependency":False,
        "source_sha256":hashlib.sha256((root / "src/telemetry_yield/blind_phase_fsk_file.py").read_bytes()).hexdigest(), "cases":[]}
    config = BlindPhaseFskConfig(**{key:tuple(value) if isinstance(value,list) else value for key,value in base["cases"][0]["config"].items()})
    inputs = [(case["name"],tests / "clipping_fixtures" / case["filename"]) for case in base["cases"]]
    for name,raw in [("zero",bytes(35184*4)),("tied_constant",struct.pack("<hh",100,50)*35184),
        ("zero_baseline_nonzero_peak",bytes(5760*4*3)+struct.pack("<hh",100,50)*(35184-5760*3))]:
        path = target / f"{name}.ci16"
        path.write_bytes(raw)
        inputs.append((name,path))
    for name,path in inputs:
        case = {"name":name,"input_path":str(path.relative_to(tests)),"input_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "input_bytes":path.stat().st_size,"config":asdict(config)}
        try:
            selected,counters = select_ci16_phase_windows_streaming(path,config=config)
            case.update({"status":"complete","selected":[asdict(w) for w in selected],"counters":counters})
        except ValueError as error:
            case.update({"status":"rejected","error":str(error)})
        document["cases"].append(case)
    with (target / "oracle.json").open("x") as stream:
        json.dump(document,stream,allow_nan=False,sort_keys=True,indent=2)
    print(json.dumps({"cases":len(document["cases"]),"complete":sum(c["status"]=="complete" for c in document["cases"])}))


if __name__ == "__main__":
    main()

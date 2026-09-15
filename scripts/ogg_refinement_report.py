#!/usr/bin/env python3
"""Read-only scoring of development ablations and small held-aside validation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from ogg_pilot_report import compare_one


def read(path):
    return json.loads(path.read_text())


def frame_set(result):
    return {bytes.fromhex(row["payload_hex"]) for row in result["frames"]}


def stage_row(folder, refined, global_path, baseline, *, legacy=None):
    row = compare_one(folder, refined, baseline)
    global_comparison = compare_one(folder, global_path, baseline)
    candidate = read(refined)
    original = read(global_path)
    before, after = frame_set(original), frame_set(candidate)
    row["global_bank"] = global_comparison["counts"]
    row["added_by_diversity"] = [p.hex() for p in sorted(after - before)]
    row["lost_by_diversity"] = [p.hex() for p in sorted(before - after)]
    row["global_bank_wall_seconds"] = original["elapsed_seconds"]
    if legacy is not None:
        old = read(legacy)
        row["fast_global_exact_legacy_frames_and_provenance"] = old["frames"] == original["frames"]
        row["legacy_wall_seconds"] = old["elapsed_seconds"]
        row["observed_fast_global_elapsed_ratio"] = old["elapsed_seconds"] / original["elapsed_seconds"]
        row["observed_refined_elapsed_ratio"] = old["elapsed_seconds"] / candidate["elapsed_seconds"]
    return row


def totals(rows):
    return {key: {metric: sum(row["counts"][key][metric] for row in rows)
                  for metric in ("count", "bytes")}
            for key in rows[0]["counts"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    pilot = root / "work/satnogs-ogg-pilot-20260907"
    refinement = root / "work/satnogs-ogg-refinement-20260907"
    plan = read(refinement / "validation-plan.json")
    for identity in plan["identities"]:
        if hashlib.sha256(Path(identity["path"]).read_bytes()).hexdigest() != identity["sha256"]:
            raise ValueError("Validation source/selection identity changed")
    development = [stage_row(
        pilot / "inputs" / str(obs), refinement / "fast-diverse" / f"{obs}.json",
        refinement / "fast-global" / f"{obs}.json", pilot / "baseline" / f"{obs}-fsk-g3ruh/result.json",
        legacy=pilot / "native" / f"{obs}-v1.json",
    ) for obs in (14366383, 14115025)]
    validation = [stage_row(
        refinement / "validation-inputs/inputs" / str(obs),
        refinement / "validation-native" / f"{obs}-diverse.json",
        refinement / "validation-native" / f"{obs}-global.json",
        refinement / "validation-baseline" / str(obs) / "repeat-a/result.json",
    ) for obs in plan["observation_ids"]]
    tree = ET.parse(refinement / "qualification-tests.xml")
    suites = list(tree.getroot().iter("testsuite"))
    test_counts = {key: sum(int(suite.get(key, "0")) for suite in suites)
                   for key in ("tests", "failures", "errors", "skipped")}
    result = {
        "schema": "native-ogg-refinement-development-and-validation-v1",
        "development": development, "validation": validation,
        "development_totals": totals(development), "validation_totals": totals(validation),
        "development_and_validation_not_pooled_for_generalization": True,
        "positive_observations_are_same_mission": "CANVAS",
        "source_and_selection_identity_recheck_passed": True,
        "tests": test_counts,
        "test_count_unit": "JUnit cases, including pytest subtests (71 tests plus 42 subtests)",
        "strict_native_fcs_independently_rechecked": True,
        "additive_no_legacy_frame_loss": not any(row["lost_by_diversity"] for row in development + validation),
        "publication_ready": False, "deployment_ready": False,
        "large_scale_started": False,
        "limitations": [
            "Development recordings and three diagnostic windows were exposed before the change",
            "Two held-aside positive passes selected using archived outcomes; no population/random or cross-mission claim",
            "Archive absence is not proof of original receiver failure or unique new transmitted telemetry",
            "Existing component baseline was run on identical audio, not an exact historical station reconstruction",
            "Short reused silence/Gaussian controls do not qualify a low false-accept rate",
            "Wall-time ratios were observed under concurrent workloads and unequal search budgets",
        ],
    }
    if any(test_counts[key] for key in ("failures", "errors")):
        raise ValueError("Qualification tests did not pass")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
    lines = ["# Dopracowanie odbiornika OGG — rozwój i mała walidacja", "",
             "Nowa wersja przyspiesza sprawdzanie AX.25 i zachowuje dotychczasowe hipotezy zegara, dodając niewielki bank zróżnicowanych faz i błędów prędkości.", ""]
    for name, rows in (("Próbki rozwojowe — wyniki były znane", development),
                       ("Dwie inne obserwacje — wybrane przed uruchomieniem kandydata", validation)):
        lines += [f"## {name}", "",
                  "| Obserwacja | Archiwum | gr-satellites / to samo OGG | Szybki stary bank | Rozszerzony bank | Nowe wyłącznie nasze wobec obu |",
                  "|---|---:|---:|---:|---:|---:|"]
        for row in rows:
            c = row["counts"]
            lines.append(f"| {row['observation_id']} | {c['archive']['count']} | {c['baseline_same_audio']['count']} | {row['global_bank']['native_same_audio']['count']} | {c['native_same_audio']['count']} | {c['native_new_vs_archive_and_baseline']['count']} |")
        lines.append("")
    lines += ["## Interpretacja", "",
              "Liczniki oznaczają unikalne pełne PDU w obrębie obserwacji, bez FCS i bez doliczania powtórek. Wszystkie natywne FCS sprawdzono ponownie bitowo.",
              "Nie łączymy próbek rozwojowych i walidacyjnych w pozorną skuteczność ogólną. Wszystkie cztery obserwacje dotyczą tej samej misji.",
              "Zera, brakujące ramki i wyniki ujemne pozostają w raporcie. Treść ramek archiwalnych nie była podawana dekoderowi.",
              "Test wielkoskalowy nie został uruchomiony. Krótkie kontrole szumu nie są kwalifikacją fałszywych alarmów nowej ścieżki audio.",
              "Pełne bajty, bilanse, czasy i ograniczenia znajdują się w JSON obok tego dokumentu."]
    with args.output.with_suffix(".md").open("x") as stream:
        stream.write("\n".join(lines) + "\n")
    print(json.dumps({"development": result["development_totals"], "validation": result["validation_totals"],
                      "no_legacy_loss": result["additive_no_legacy_frame_loss"], "tests": test_counts}, indent=2))


if __name__ == "__main__":
    main()

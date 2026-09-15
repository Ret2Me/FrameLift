"""Dependency-free SVG figures for publication probability results."""

from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Mapping, Sequence


COLORS = {
    "global_rate": "#6b7280",
    "group_rate": "#7c3aed",
    "geometry_logit": "#0f766e",
    "full_logit": "#c2410c",
}


def _jsonl(path: Path) -> list[Mapping[str, object]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _svg_text(x: float, y: float, value: str, *, size: int = 12, anchor: str = "start") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
        f'text-anchor="{anchor}" font-family="sans-serif">{html.escape(value)}</text>'
    )


def write_reliability_svg(
    predictions_path: Path,
    output_path: Path,
    *,
    title: str,
    split: str = "temporal",
) -> None:
    rows = [row for row in _jsonl(predictions_path) if row.get("split") == split]
    width, height = 680, 470
    left, right, top, bottom = 70, 500, 45, 405
    plot_width, plot_height = right - left, bottom - top
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        _svg_text(width / 2, 25, title, size=16, anchor="middle"),
    ]
    for tick in range(0, 11, 2):
        value = tick / 10
        x = left + value * plot_width
        y = bottom - value * plot_height
        parts.append(f'<line x1="{x}" y1="{top}" x2="{x}" y2="{bottom}" stroke="#e5e7eb"/>')
        parts.append(f'<line x1="{left}" y1="{y}" x2="{right}" y2="{y}" stroke="#e5e7eb"/>')
        parts.append(_svg_text(x, bottom + 20, f"{value:.1f}", anchor="middle"))
        parts.append(_svg_text(left - 10, y + 4, f"{value:.1f}", anchor="end"))
    parts.extend(
        [
            f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{top}" stroke="#111827" stroke-dasharray="5 5"/>',
            f'<rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" fill="none" stroke="#111827"/>',
            _svg_text((left + right) / 2, height - 18, "Mean predicted probability", anchor="middle"),
            f'<text x="18" y="{(top + bottom) / 2}" font-size="12" text-anchor="middle" font-family="sans-serif" transform="rotate(-90 18 {(top + bottom) / 2})">Observed frequency</text>',
        ]
    )
    for legend_index, model in enumerate(COLORS):
        selected = [row for row in rows if row.get("model") == model]
        bins: defaultdict[int, list[Mapping[str, object]]] = defaultdict(list)
        for row in selected:
            probability = float(row["probability"])
            bins[min(9, int(probability * 10))].append(row)
        points: list[tuple[float, float, int]] = []
        for bin_rows in bins.values():
            predicted = sum(float(row["probability"]) for row in bin_rows) / len(bin_rows)
            observed = sum(int(row["outcome"]) for row in bin_rows) / len(bin_rows)
            points.append((predicted, observed, len(bin_rows)))
        points.sort()
        coordinates = " ".join(
            f"{left + x * plot_width:.1f},{bottom - y * plot_height:.1f}"
            for x, y, _ in points
        )
        color = COLORS[model]
        if coordinates:
            parts.append(
                f'<polyline points="{coordinates}" fill="none" stroke="{color}" stroke-width="2.5"/>'
            )
        for x, y, count in points:
            radius = min(7.0, 2.5 + count**0.5 / 3)
            parts.append(
                f'<circle cx="{left + x * plot_width:.1f}" cy="{bottom - y * plot_height:.1f}" r="{radius:.1f}" fill="{color}"/>'
            )
        legend_y = 75 + legend_index * 24
        parts.append(f'<line x1="530" y1="{legend_y}" x2="555" y2="{legend_y}" stroke="{color}" stroke-width="3"/>')
        parts.append(_svg_text(565, legend_y + 4, model))
    parts.append("</svg>")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def write_brier_svg(
    metrics: Mapping[str, Mapping[str, object]], output_path: Path, *, title: str
) -> None:
    models = [model for model in COLORS if model in metrics]
    values = [float(metrics[model]["brier"]) for model in models]
    maximum = max(values) * 1.15 if values else 1.0
    width, height = 680, 360
    left, right, top, bottom = 170, 620, 45, 315
    row_height = (bottom - top) / max(1, len(models))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        _svg_text(width / 2, 25, title, size=16, anchor="middle"),
    ]
    for index, (model, value) in enumerate(zip(models, values, strict=True)):
        y = top + index * row_height + row_height * 0.2
        bar_height = row_height * 0.6
        bar_width = (right - left) * value / maximum
        parts.append(_svg_text(left - 10, y + bar_height * 0.7, model, anchor="end"))
        parts.append(
            f'<rect x="{left}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="{COLORS[model]}"/>'
        )
        parts.append(_svg_text(left + bar_width + 7, y + bar_height * 0.7, f"{value:.4f}"))
    parts.append(_svg_text((left + right) / 2, height - 18, "Brier score (lower is better)", anchor="middle"))
    parts.append("</svg>")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def write_evaluation_figures(evaluation_path: Path, output_dir: Path) -> tuple[Path, ...]:
    report = json.loads(evaluation_path.read_text(encoding="utf-8"))
    outputs: list[Path] = []
    for task, task_report in report.get("tasks", {}).items():
        if task_report.get("status") != "evaluated":
            continue
        reliability = output_dir / f"{task}-reliability.svg"
        brier = output_dir / f"{task}-brier.svg"
        write_reliability_svg(
            Path(task_report["predictions_path"]),
            reliability,
            title=f"{task}: temporal reliability",
        )
        write_brier_svg(
            task_report["temporal"]["metrics"],
            brier,
            title=f"{task}: temporal Brier score",
        )
        outputs.extend((reliability, brier))
    composed = report.get("composed_reception", {})
    if isinstance(composed, Mapping) and composed.get("status") == "evaluated":
        reliability = output_dir / "reception_success-reliability.svg"
        brier = output_dir / "reception_success-brier.svg"
        write_reliability_svg(
            Path(str(composed["predictions_path"])),
            reliability,
            title="end-to-end packet reception: temporal reliability",
            split="temporal-composed",
        )
        temporal = composed.get("temporal", {})
        metrics = temporal.get("metrics", {}) if isinstance(temporal, Mapping) else {}
        if not isinstance(metrics, Mapping):
            raise ValueError("composed reception metrics must be an object")
        write_brier_svg(
            metrics,  # type: ignore[arg-type]
            brier,
            title="end-to-end packet reception: temporal Brier score",
        )
        outputs.extend((reliability, brier))
    return tuple(outputs)

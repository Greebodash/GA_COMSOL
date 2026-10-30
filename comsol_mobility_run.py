#!/usr/bin/env python3

import argparse
import csv
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any, Dict, Optional, Tuple, Union

import numpy as np

import comsol_run
from comsol_run import (
    evaluation_csv_paths,
    parse_markdown_config,
    parse_value,
)
from fit_mos2_mobility import N_PER_V, VT0, id_model


DEFAULT_CONFIG_FILE = "mobility_parameters.md"
SWEEP_SECTION = "id(vg) sweep"
PREVIEW_SECTION = "python preview"


@dataclass(frozen=True)
class MobilityWorkflow:
    config: Dict[str, Any]
    preview: Dict[str, Any]


def _read_extra_sections(filename: Union[str, Path]) -> Tuple[
    Dict[str, Any], Dict[str, Any]
]:
    sections = {SWEEP_SECTION: {}, PREVIEW_SECTION: {}}
    section = None

    with open(filename, encoding="utf-8") as config_file:
        for raw_line in config_file:
            line = raw_line.strip()
            if line.startswith("## "):
                title = line[3:].strip().lower()
                section = title if title in sections else None
                continue
            if section is None or not line.startswith("- "):
                continue

            match = re.match(r"^-\s*([^:]+):\s*(.+)$", line)
            if match:
                key, value = match.groups()
                sections[section][key.strip()] = parse_value(value)

    return sections[SWEEP_SECTION], sections[PREVIEW_SECTION]


def _finite_number(section: Dict[str, Any], key: str) -> float:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"'{key}' must be a numeric value.")
    if not math.isfinite(value):
        raise ValueError(f"'{key}' must be finite.")
    return float(value)


def load_workflow(
    filename: Union[str, Path] = DEFAULT_CONFIG_FILE,
) -> MobilityWorkflow:
    """Load COMSOL settings and Id(Vg) sweep/preview settings."""
    config = parse_markdown_config(filename)
    sweep, preview = _read_extra_sections(filename)


    plot_file = preview.get("plot_file")
    if not isinstance(plot_file, str) or not plot_file.strip():
        raise ValueError("'plot_file' must be configured under Python Preview.")
    donor_parameter = preview.get("donor_parameter")
    if not isinstance(donor_parameter, str) or not donor_parameter.strip():
        raise ValueError(
            "'donor_parameter' must name the donor parameter in COMSOL."
        )

    if config["model"].get("solve_model") is not True:
        raise ValueError(
            "The mobility workflow requires solve_model: true to use fresh "
            "COMSOL results."
        )

    parameter_names = {item["name"] for item in config["parameters"]}
    required_parameters = {
        "alpha_mu",
        "mu_C0",
        "mu_ph",
        "n_ref",
        "mu_min",
        donor_parameter,
        "Vg_start",
        "Vg_step",
        "Vg_finish",
    }
    missing = required_parameters - parameter_names
    if missing:
        raise ValueError(
            "Missing COMSOL mobility parameter(s): "
            + ", ".join(sorted(missing))
        )
    # for name in ("Vg_start", "Vg_step", "Vg_finish"):
    #     if _parameter_value(config, name) != _finite_number(sweep, name):
    #         raise ValueError(
    #             f"COMSOL parameter '{name}' must match its Id(Vg) Sweep value."
    #         )

    return MobilityWorkflow(config, preview)


def _parameter_value(config: Dict[str, Any], name: str) -> float:
    for parameter in config["parameters"]:
        if parameter["name"] == name:
            return float(parameter["value"])
    raise ValueError(f"COMSOL mobility parameter '{name}' is not configured.")


def _gate_values(config: Dict[str, Any]) -> np.ndarray:
    start = _parameter_value(config, "Vg_start")
    step = _parameter_value(config, "Vg_step")
    finish = _parameter_value(config, "Vg_finish")
    count = math.floor((finish - start) / step + 1e-12) + 1
    return start + step * np.arange(count, dtype=float)


def python_curve(workflow: MobilityWorkflow) -> Tuple[np.ndarray, np.ndarray]:
    """Calculate the configured mobility model over its Id(Vg) gate range."""
    config = workflow.config
    donor_name = workflow.preview["donor_parameter"]
    donor_concentration = _parameter_value(config, donor_name)
    alpha = _parameter_value(config, "alpha_mu")
    mu_c0 = _parameter_value(config, "mu_C0")
    mu_ph = _parameter_value(config, "mu_ph")
    n_ref = _parameter_value(config, "n_ref")
    mu_min = _parameter_value(config, "mu_min")

    if donor_concentration < 0:
        raise ValueError("Donor concentration cannot be negative.")
    if alpha < 0 or mu_c0 <= 0 or mu_ph <= 0 or n_ref <= 0 or mu_min < 0:
        raise ValueError(
            "Mobility settings require alpha_mu and mu_min >= 0, and "
            "mu_C0, mu_ph, and n_ref > 0."
        )

    vt = VT0 - donor_concentration / N_PER_V
    vg = _gate_values(config)
    current_a = id_model(
        vg,
        vt,
        mu_c0,
        mu_ph,
        alpha,
        n_ref=n_ref,
        mu_min=mu_min,
    ) * 1e-9
    return vg, current_a


def _read_comsol_curve(
    filename: Union[str, Path], evaluation: Dict[str, Any]
) -> Tuple[np.ndarray, np.ndarray]:
    with open(filename, newline="", encoding="utf-8") as result_file:
        reader = csv.DictReader(result_file)
        required_columns = (evaluation["x_column"], evaluation["y_column"])
        if reader.fieldnames is None or any(
            column not in reader.fieldnames for column in required_columns
        ):
            raise ValueError(
                f"COMSOL curve '{filename}' is missing columns "
                f"{required_columns!r}."
            )
        rows = list(reader)

    if not rows:
        raise ValueError(f"COMSOL curve '{filename}' contains no data rows.")
    vg = np.asarray(
        [float(row[evaluation["x_column"]]) for row in rows], dtype=float
    )
    current_a = np.asarray(
        [float(row[evaluation["y_column"]]) for row in rows], dtype=float
    )
    if not np.all(np.isfinite(vg)) or not np.all(np.isfinite(current_a)):
        raise ValueError(f"COMSOL curve '{filename}' contains non-finite values.")
    return vg, current_a


def _save_curve_plot(
    filename: Union[str, Path],
    python_data: Tuple[np.ndarray, np.ndarray],
    comsol_data: Optional[Tuple[np.ndarray, np.ndarray]] = None,
) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plot_path = Path(filename)
    plot_path.parent.mkdir(parents=True, exist_ok=True)
    fig, axis = plt.subplots(figsize=(6.2, 4.6))
    axis.plot(
        python_data[0],
        python_data[1],
        "b-",
        label="Python mobility model",
    )
    if comsol_data is not None:
        axis.plot(
            comsol_data[0],
            comsol_data[1],
            "ro",
            markersize=3,
            label="COMSOL Id(Vg)",
        )
    axis.set_xlabel("Gate voltage (V)")
    axis.set_ylabel("Drain current (A)")
    axis.legend()
    axis.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)
    return plot_path


def run_workflow(
    config_file: Union[str, Path] = DEFAULT_CONFIG_FILE,
    preview_only: bool = False,
) -> Path:
    """Save a Python preview and, unless requested otherwise, run COMSOL."""
    workflow = load_workflow(config_file)
    python_data = python_curve(workflow)
    plot_file = workflow.preview["plot_file"]
    _save_curve_plot(plot_file, python_data)
    print(f"Python curve saved to {plot_file}")

    if preview_only:
        return Path(plot_file)

    comsol_run.run(config_file)
    evaluations = workflow.config["evaluations"]
    id_vg_evaluations = [
        evaluation for evaluation in evaluations
        if evaluation["name"] == "id_vg"
    ]
    if len(id_vg_evaluations) != 1:
        raise ValueError(
            "The COMSOL config must define exactly one evaluation named 'id_vg'."
        )

    evaluation = id_vg_evaluations[0]
    csv_path = evaluation_csv_paths(
        workflow.config["model"]["evaluation_csv"],
        evaluations,
    )["id_vg"]
    comsol_data = _read_comsol_curve(csv_path, evaluation)
    return _save_curve_plot(plot_file, python_data, comsol_data)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview the configured MoS2 mobility curve and run COMSOL."
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_FILE,
        help="Markdown configuration file (default: %(default)s).",
    )
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="Save the Python model curve without launching COMSOL.",
    )
    args = parser.parse_args()
    run_workflow(args.config, preview_only=args.preview_only)


if __name__ == "__main__":
    main()

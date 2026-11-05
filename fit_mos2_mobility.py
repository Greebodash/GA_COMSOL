#!/usr/bin/env python3
"""Compare the configured compact Python model against a COMSOL Id(Vg) export.

All model inputs come from the Markdown configuration and remain fixed. The
reported errors measure agreement with the COMSOL curve, not physical accuracy
against experimental data. The compact model approximates carrier density
from gate overdrive; it does not resolve COMSOL's spatial semiconductor fields.
"""
import argparse
import csv
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import numpy as np

# ----------------------------------------------------------------------------
# Universal constants, not device-specific configuration.
Q = 1.602e-19
EPS0 = 8.854e-14


@dataclass(frozen=True)
class MobilityModelSettings:
    """Device-specific constants used by the compact mobility model."""

    n_per_v: float
    current_scale: float
    undoped_threshold: float
    n_ref: float
    mu_min: float


def _numeric_parameter(
    parameters: Sequence[Mapping[str, Any]],
    name: str,
) -> float:
    for parameter in parameters:
        if parameter["name"] == name:
            value = parameter["value"]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"COMSOL parameter '{name}' must be numeric.")
            if not math.isfinite(value):
                raise ValueError(f"COMSOL parameter '{name}' must be finite.")
            return float(value)
    raise ValueError(f"Required COMSOL parameter '{name}' is not configured.")


def _length_in_cm(parameter: Mapping[str, Any]) -> float:
    unit = str(parameter.get("unit", "")).strip().lower()
    conversion = {
        "m": 100.0,
        "cm": 1.0,
        "mm": 0.1,
        "um": 1e-4,
        "µm": 1e-4,
        "nm": 1e-7,
    }
    if unit not in conversion:
        raise ValueError(
            f"Unsupported length unit {unit!r} for COMSOL parameter "
            f"'{parameter['name']}'."
        )
    value = parameter["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"COMSOL parameter '{parameter['name']}' must be numeric."
        )
    result = float(value) * conversion[unit]
    if not math.isfinite(result) or result <= 0:
        raise ValueError(
            f"COMSOL parameter '{parameter['name']}' must be finite and "
            "greater than zero."
        )
    return result


def _preview_number(preview: Mapping[str, Any], name: str) -> float:
    value = preview.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Python Preview value '{name}' must be numeric.")
    if not math.isfinite(value):
        raise ValueError(f"Python Preview value '{name}' must be finite.")
    return float(value)


def _configured_length_cm(
    parameters: Mapping[str, Mapping[str, Any]],
    parameter_name: str,
    preview: Mapping[str, Any],
    fallback_name: str,
) -> float:
    if parameter_name in parameters:
        return _length_in_cm(parameters[parameter_name])
    return _preview_number(preview, fallback_name) * 1e-4


def build_model_settings(
    config: Mapping[str, Any],
    preview: Mapping[str, Any],
) -> MobilityModelSettings:
    """Derive compact-model constants from COMSOL parameters and preview data."""
    parameters = config["parameters"]
    parameter_by_name = {item["name"]: item for item in parameters}
    oxide_permittivity = _preview_number(
        preview, "oxide_relative_permittivity"
    )
    calibration = _preview_number(
        preview, "current_calibration_nA_per_V_at_100_mobility"
    )
    calibration_vd = _preview_number(preview, "calibration_drain_voltage")
    undoped_threshold = _preview_number(
        preview, "undoped_threshold_voltage"
    )

    n_ref = _numeric_parameter(parameters, "n_ref")
    mu_min = _numeric_parameter(parameters, "mu_min")
    channel_thickness = (
        _length_in_cm(parameter_by_name["t_MoS2"])
        if "t_MoS2" in parameter_by_name
        else _preview_number(preview, "channel_thickness_nm") * 1e-7
    )
    oxide_thickness = (
        _length_in_cm(parameter_by_name["t_SiO2"])
        if "t_SiO2" in parameter_by_name
        else _preview_number(preview, "oxide_thickness_nm") * 1e-7
    )
    drain_voltage = (
        _numeric_parameter(parameters, "Vd")
        if "Vd" in parameter_by_name
        else _preview_number(preview, "drain_voltage_v")
    )
    channel_width = _configured_length_cm(
        parameter_by_name,
        "W",
        preview,
        "channel_width_um",
    )
    channel_length = _configured_length_cm(
        parameter_by_name,
        "L_MoS2",
        preview,
        "channel_length_um",
    )
    calibration_width = _preview_number(preview, "calibration_width_um") * 1e-4
    calibration_length = (
        _preview_number(preview, "calibration_length_um") * 1e-4
    )
    if (
        not math.isfinite(oxide_permittivity)
        or oxide_permittivity <= 0
        or not math.isfinite(calibration)
        or calibration <= 0
        or not math.isfinite(calibration_vd)
        or calibration_vd <= 0
        or not math.isfinite(undoped_threshold)
        or not math.isfinite(drain_voltage)
        or drain_voltage <= 0
        or n_ref <= 0
        or mu_min < 0
        or channel_thickness <= 0
        or oxide_thickness <= 0
        or channel_width <= 0
        or channel_length <= 0
        or calibration_width <= 0
        or calibration_length <= 0
    ):
        raise ValueError("Python Preview/model mobility settings are invalid.")

    oxide_capacitance = (
        EPS0
        * oxide_permittivity
        / oxide_thickness
    )
    n_per_v = oxide_capacitance / (Q * channel_thickness)
    return MobilityModelSettings(
        n_per_v=n_per_v,
        current_scale=(
            calibration
            * drain_voltage
            / calibration_vd
            * (channel_width / channel_length)
            / (calibration_width / calibration_length)
        ),
        undoped_threshold=undoped_threshold,
        n_ref=n_ref,
        mu_min=mu_min,
    )

# ----------------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------------
def mu_eff(
    n,
    mu_c0,
    alpha,
    mu_ph,
    settings: MobilityModelSettings,
):
    """COMSOL mu_ele: add the floor to Coulomb mobility, then apply Matthiessen."""
    density = np.maximum(np.asarray(n, dtype=float), 1.0)
    mu_c = mu_c0 * (density / settings.n_ref) ** alpha + settings.mu_min
    return 1.0 / (1.0 / mu_c + 1.0 / mu_ph)


def id_model(
    vg,
    vt,
    mu_c0,
    mu_ph,
    alpha,
    settings: MobilityModelSettings,
):
    """Drain current (nA) from the configured compact linear-regime model."""
    vg = np.asarray(vg, float)
    dv = np.clip(vg - vt, 1e-9, None)
    mu = mu_eff(settings.n_per_v * dv, mu_c0, alpha, mu_ph, settings)
    return np.where(vg > vt, settings.current_scale * mu / 100.0 * dv, 0.0)


def nd_for_vt(vt, settings: MobilityModelSettings):
    """Signed net doping (cm^-3) corresponding to threshold voltage vt."""
    return (settings.undoped_threshold - vt) * settings.n_per_v


def configured_model_curve(
    vg: Sequence[float],
    config: Mapping[str, Any],
    preview: Mapping[str, Any],
) -> np.ndarray:
    """Predict current in nA using only the configured Markdown parameters."""
    parameters = config["parameters"]
    donor_name = preview["donor_parameter"]
    donor_concentration = _numeric_parameter(parameters, donor_name)
    alpha = _numeric_parameter(parameters, "alpha_mu")
    mu_c0 = _numeric_parameter(parameters, "mu_C0")
    mu_ph = _numeric_parameter(parameters, "mu_ph")
    settings = build_model_settings(config, preview)

    if donor_concentration < 0:
        raise ValueError("Donor concentration cannot be negative.")
    if alpha < 0 or mu_c0 <= 0 or mu_ph <= 0:
        raise ValueError(
            "Mobility settings require alpha_mu >= 0 and mu_C0, mu_ph > 0."
        )

    vt = settings.undoped_threshold - donor_concentration / settings.n_per_v
    return id_model(vg, vt, mu_c0, mu_ph, alpha, settings)


def read_comsol_curve(
    filename: Path,
    evaluation: Mapping[str, Any],
) -> tuple[np.ndarray, np.ndarray]:
    """Read gate voltage (V) and drain current (A) from a COMSOL CSV export."""
    if evaluation.get("x_unit") != "V" or evaluation.get("y_unit") != "A":
        raise ValueError("COMSOL comparison requires voltage in V and current in A.")

    x_column = evaluation["x_column"]
    y_column = evaluation["y_column"]
    voltages = []
    currents = []
    with filename.open(newline="", encoding="utf-8") as curve_file:
        reader = csv.DictReader(curve_file)
        if reader.fieldnames is None or any(
            column not in reader.fieldnames for column in (x_column, y_column)
        ):
            raise ValueError(
                f"COMSOL curve '{filename}' is missing columns "
                f"{x_column!r} and/or {y_column!r}."
            )
        for line_number, row in enumerate(reader, start=2):
            try:
                voltage = float(row[x_column])
                current = float(row[y_column])
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"Invalid numeric COMSOL data at {filename}:{line_number}."
                ) from error
            if not math.isfinite(voltage) or not math.isfinite(current):
                raise ValueError(
                    f"Non-finite COMSOL data at {filename}:{line_number}."
                )
            voltages.append(voltage)
            currents.append(current)

    if not voltages:
        raise ValueError(f"COMSOL curve '{filename}' contains no data rows.")
    return np.asarray(voltages), np.asarray(currents)


def comparison_metrics(
    comsol_current_na: Sequence[float],
    model_current_na: Sequence[float],
) -> Dict[str, float]:
    """Return fixed-model errors against COMSOL current, in nA."""
    target = np.asarray(comsol_current_na, dtype=float)
    predicted = np.asarray(model_current_na, dtype=float)
    if target.shape != predicted.shape or target.size == 0:
        raise ValueError("COMSOL and Python curves must have matching data points.")
    if not np.all(np.isfinite(target)) or not np.all(np.isfinite(predicted)):
        raise ValueError("COMSOL and Python curves must contain finite values.")

    errors = predicted - target
    target_range = float(np.ptp(target))
    if target_range == 0:
        raise ValueError(
            "Cannot calculate normalized error or R-squared for a constant "
            "COMSOL current curve."
        )
    rmse = float(np.sqrt(np.mean(errors**2)))
    total_variation = float(np.sum((target - np.mean(target)) ** 2))
    return {
        "mae_na": float(np.mean(np.abs(errors))),
        "rmse_na": rmse,
        "max_abs_error_na": float(np.max(np.abs(errors))),
        "nrmse_percent": rmse / target_range * 100,
        "r_squared": 1 - float(np.sum(errors**2)) / total_variation,
    }


def compare_to_comsol(
    filename: Path,
    evaluation: Mapping[str, Any],
    config: Mapping[str, Any],
    preview: Mapping[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, Dict[str, float]]:
    """Compare fixed Markdown parameters with a COMSOL-exported curve."""
    vg, comsol_current_a = read_comsol_curve(filename, evaluation)
    model_current_na = configured_model_curve(vg, config, preview)
    comsol_current_na = comsol_current_a * 1e9
    metrics = comparison_metrics(comsol_current_na, model_current_na)
    return vg, comsol_current_na, model_current_na, metrics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--config",
        default="mobility_parameters_MoS2.md",
        help="Markdown mobility configuration.",
    )
    ap.add_argument(
        "--comsol-csv",
        help=(
            "COMSOL evaluation CSV. Defaults to the configured CSV path for "
            "the id_vg evaluation."
        ),
    )
    ap.add_argument(
        "--evaluation",
        default="id_vg",
        help="Name of the configured evaluation to compare (default: id_vg).",
    )
    ap.add_argument(
        "--plot",
        help="Comparison plot path (defaults beside the COMSOL CSV).",
    )
    args = ap.parse_args()

    from comsol_mobility_run import load_workflow
    from comsol_run import evaluation_csv_paths

    workflow = load_workflow(args.config)
    config = workflow.config
    evaluation = next(
        (
            item
            for item in config["evaluations"]
            if item["name"] == args.evaluation
        ),
        None,
    )
    if evaluation is None:
        raise ValueError(
            f"Evaluation '{args.evaluation}' is not configured in {args.config}."
        )
    configured_csv = evaluation_csv_paths(
        config["model"]["evaluation_csv"],
        config["evaluations"],
    )[args.evaluation]
    csv_path = Path(args.comsol_csv or configured_csv)
    vg, comsol_current, model_current, metrics = compare_to_comsol(
        csv_path,
        evaluation,
        config,
        workflow.preview,
    )
    plot_path = Path(
        args.plot
        or csv_path.with_name(f"{csv_path.stem}_model_comparison.png")
    )

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    order = np.argsort(vg)
    fig, ax = plt.subplots(figsize=(7.2, 5.2))
    ax.plot(
        vg[order],
        comsol_current[order],
        "o",
        markersize=3,
        label="COMSOL",
    )
    ax.plot(
        vg[order],
        model_current[order],
        "-",
        label="Python model (Markdown parameters)",
    )
    ax.set_xlabel("Vg (V)")
    ax.set_ylabel("Id (nA)")
    ax.set_title("Fixed-parameter model vs COMSOL")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    plot_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)

    print(f"Compared {len(vg)} COMSOL points from {csv_path}")
    print(f"Markdown configuration: {args.config}")
    print(f"MAE: {metrics['mae_na']:.4g} nA")
    print(f"RMSE: {metrics['rmse_na']:.4g} nA")
    print(f"Maximum absolute error: {metrics['max_abs_error_na']:.4g} nA")
    print(f"NRMSE (COMSOL current range): {metrics['nrmse_percent']:.3g}%")
    print(f"R-squared: {metrics['r_squared']:.6g}")
    print(
        "These metrics measure agreement with the compact Python model; "
        "they do not establish physical accuracy against experiment."
    )
    print(f"Comparison plot saved to {plot_path}")


if __name__ == "__main__":
    main()

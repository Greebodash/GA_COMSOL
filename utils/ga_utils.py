"""Shared configuration, COMSOL, and spectrum helpers for GA workflows."""

import hashlib
import logging

import numpy as np


logger = logging.getLogger(__name__)


def parse_value(value):
    value = value.strip()

    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False

    try:
        if "." not in value and "e" not in value.lower():
            return int(value)
    except ValueError:
        pass

    try:
        return float(value)
    except ValueError:
        return value


def make_cache_key(solution):
    return tuple(round(float(value), 10) for value in solution)


def set_comsol_parameters(model, parameters, solution):
    if len(solution) != len(parameters):
        raise ValueError(
            f"Solution has {len(solution)} genes but "
            f"{len(parameters)} parameters are configured."
        )

    for value, parameter in zip(solution, parameters):
        model.parameter(
            parameter["name"],
            f"{float(value):.12g}[{parameter['unit']}]",
        )


def set_polarization_parameter(model, phi_a):
    model.parameter("phi_a", f"{float(phi_a):.12g}[deg]")


def set_fixed_parameters(model, fixed_parameters):
    if not fixed_parameters:
        return

    from utils.comsol_utils import set_model_parameter

    available = set(model.parameters())
    for parameter in fixed_parameters:
        name = str(parameter["name"]).strip()
        if name not in available:
            raise ValueError(
                f"Fixed parameter '{name}' was not found in the COMSOL model."
            )

        set_model_parameter(
            model,
            name,
            parameter.get("value", ""),
            parameter.get("unit", ""),
        )
        logger.info(
            "Set fixed parameter: %s = %s",
            name,
            str(parameter.get("value", "")),
        )


def extract_fresh_table(
    model,
    evaluation_tag="gev1",
    temporary_table_tag="ga_temp_table",
    require_min_points=True,
    print_table=False,
):
    """Extract a clean, finite, frequency-sorted COMSOL spectrum."""
    from utils.comsol_utils import extract_table

    frequency, transmission = extract_table(
        model,
        evaluation_tag=evaluation_tag,
        temporary_table_tag=temporary_table_tag,
        require_min_points=require_min_points,
        print_table=print_table,
    )

    frequency = np.asarray(frequency, dtype=float).reshape(-1)
    transmission = np.asarray(transmission, dtype=float).reshape(-1)

    if frequency.size == 0:
        raise ValueError(
            f"COMSOL evaluation {evaluation_tag} returned no data."
        )

    if frequency.size != transmission.size:
        raise ValueError(
            f"Evaluation {evaluation_tag}: frequency/transmission "
            f"length mismatch: "
            f"{frequency.size} vs {transmission.size}"
        )

    mask = np.isfinite(frequency) & np.isfinite(transmission)
    if require_min_points and np.count_nonzero(mask) < 3:
        raise ValueError(
            f"Evaluation {evaluation_tag} contains fewer than 3 finite "
            f"data points."
        )

    frequency = frequency[mask]
    transmission = transmission[mask]
    order = np.argsort(frequency)
    frequency = frequency[order]
    transmission = transmission[order]

    frequency, unique_indices = np.unique(frequency, return_index=True)
    transmission = transmission[unique_indices]

    if require_min_points and frequency.size < 3:
        raise ValueError("Not enough unique frequency points.")

    return frequency, transmission


def spectrum_hash(transmission):
    array = np.ascontiguousarray(
        np.asarray(transmission, dtype=np.float64)
    )
    return hashlib.sha256(array.tobytes()).hexdigest()[:16]


def prepare_spectrum(frequency_thz, transmission):
    frequency = np.asarray(frequency_thz, dtype=float).reshape(-1)
    values = np.asarray(transmission, dtype=float).reshape(-1)

    if frequency.size != values.size or frequency.size < 3:
        raise ValueError("Invalid spectrum dimensions.")

    if not np.all(np.isfinite(frequency)) or not np.all(np.isfinite(values)):
        raise ValueError("Spectrum contains NaN/Inf values.")

    order = np.argsort(frequency)
    frequency = frequency[order]
    values = values[order]

    frequency, unique_indices = np.unique(frequency, return_index=True)
    values = values[unique_indices]

    if frequency.size < 3:
        raise ValueError("Not enough unique frequency points.")

    return frequency, values


def create_gene_spaces(parameters):
    spaces = []

    for parameter in parameters:
        count = int(
            round((parameter["max"] - parameter["min"]) / parameter["step"])
        )
        values = parameter["min"] + np.arange(count + 1) * parameter["step"]
        values = np.round(values, 10)
        values = values[
            (values >= parameter["min"] - 1e-10)
            & (values <= parameter["max"] + 1e-10)
        ]
        spaces.append(values.tolist())

        logger.info(
            "Gene '%s': %d values [%g, %g], step=%g %s",
            parameter["name"],
            len(values),
            parameter["min"],
            parameter["max"],
            parameter["step"],
            parameter["unit"],
        )

    return spaces


def verify_comsol_parameters(model, parameters):
    available = model.parameters()

    for parameter in parameters:
        name = parameter["name"]
        if name not in available:
            raise ValueError(
                f"Optimization parameter '{name}' was not found "
                f"in the COMSOL model."
            )
        logger.info("Verified COMSOL parameter: %s", name)


def verify_model_parameters(model, model_config, fixed_parameters=None):
    available = model.parameters()
    if "phi_a" not in available:
        raise ValueError(
            "Polarization parameter 'phi_a' was not found in the COMSOL model."
        )

    set_polarization_parameter(model, model_config["phi_a"])
    logger.info(
        "Verified COMSOL parameter: phi_a = %.9g deg",
        model_config["phi_a"],
    )

    fixed_parameters = fixed_parameters or []
    if fixed_parameters:
        set_fixed_parameters(model, fixed_parameters)
        logger.info(
            "Verified %d fixed COMSOL parameter(s).",
            len(fixed_parameters),
        )

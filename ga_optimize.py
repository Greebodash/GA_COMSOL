#!/usr/bin/env python3
"""
COMSOL + PyGAD optimization driver.

Important fixes compared with the previous version:
- Exactly one fitness function and one cache implementation.
- No stale cache reuse across different models/configurations.
- Successful evaluations only are cached.
- Every successful spectrum is saved once, using a parameter-labelled filename.
- The configured COMSOL numerical evaluation is explicitly rerun before
    extraction through comsol_utils.py, using a fresh temporary table.
- Spectrum diagnostics include min/max values and a SHA-256 fingerprint, making
    stale-table problems obvious in app.log.
- Q is calculated directly from the extracted spectrum using the actual
  frequency coordinates; no assumption of uniform frequency spacing.
- Existing ga_results.csv is never silently treated as a cache for a different
  configuration.
"""

import csv
from datetime import datetime
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import mph
import numpy as np
import pygad
from scipy.signal import find_peaks

from utils.ga_utils import (
    create_gene_spaces,
    extract_fresh_table,
    make_cache_key,
    parse_value,
    prepare_spectrum,
    set_comsol_parameters,
    set_fixed_parameters,
    set_polarization_parameter,
    spectrum_hash,
    verify_comsol_parameters,
    verify_model_parameters,
)


CONFIG_FILE = "ga_optimize_parameters.md"

# Example Objective section for ga_optimize_parameters.md:
#
# ## Objective
# - objective_type: q_factor
# - peak_search_window_thz: 0.75
#
# Other examples:
#
# ## Objective
# - objective_type: peak_transmission
#
# ## Objective
# - objective_type: target_transmission
#
# ## Objective
# - objective_type: target_frequency_error
#
# ## Objective
# - objective_type: fwhm
#
# ## Objective
# - objective_type: custom_expression
# - objective_expression: peak_transmission * q_factor
#
OUTPUT_CSV = "ga_results.csv"
SPECTRA_DIR = "spectra"
CACHE_VERSION = "q-spectrum-v2"
FAILURE_FITNESS = -1e30


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            f"app_{datetime.now().strftime('%Y%m%d_%H%M_%f')}.log",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def parse_markdown_config(filename):
    """
    Expected:

    ## Model
    - input_file: models/model.mph
    - evaluation_tag: gev1
    - temporary_table_tag: ga_temp_table
        require_min_points=False,
    - phi_a: 0

    ## Genetic Algorithm
    - num_generations: 8
    - solutions_per_population: 5
    - num_parents_mating: 2
    - parent_selection_type: sss
    - keep_parents: 1
    - mutation_percent_genes: 40
    - stop_criteria: saturate_3
    - random_seed: 42

    ## Optimization Parameters
            if require_min_points:
                raise ValueError(
                    f"Evaluation {evaluation_tag} contains fewer than 3 finite "
                    "data points."
                )
    """

    if not os.path.exists(filename):
        raise FileNotFoundError(
            f"Configuration file not found: {os.path.abspath(filename)}"
        )

    config = {
        "model": {},
        "ga": {},
        "objective": {},
        "parameters": [],
        "fixed_parameters": [],
    }
    section = None

    with open(filename, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()

            # Accept pasted Markdown as well as ordinary Markdown.
            line = line.replace(r"\_", "_")
            line = line.replace(r"\|", "|")
            line = line.replace(r"\-", "-")
            line = line.replace(r"\#", "#")
            line = line.strip("*")

            if not line:
                continue

            if line.startswith("## "):
                title = line[3:].strip().lower()
                if title == "model":
                    section = "model"
                elif title == "genetic algorithm":
                    section = "ga"
                elif title == "optimization parameters":
                    section = "parameters"
                elif title == "fixed parameters":
                    section = "fixed_parameters"
                elif title in ("objective", "target function", "target"):
                    section = "objective"
                else:
                    section = None
                continue

            if section in ("model", "ga", "objective"):
                match = re.match(r"^-\s*([^:]+):\s*(.+)$", line)
                if match:
                    key = match.group(1).strip()
                    value = match.group(2).strip()
                    config[section][key] = parse_value(value)
                continue

            if section == "parameters" and line.startswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]

                if not cells:
                    continue

                if cells[0].lower() == "name":
                    continue

                if all(set(c) <= set("-:") for c in cells):
                    continue

                if len(cells) != 5:
                    raise ValueError(
                        "Optimization parameter table must contain exactly "
                        "5 columns: name, min, max, step, unit."
                    )

                name, minimum, maximum, step, unit = cells
                config["parameters"].append(
                    {
                        "name": name,
                        "min": float(minimum),
                        "max": float(maximum),
                        "step": float(step),
                        "unit": unit,
                    }
                )

            if section == "fixed_parameters" and line.startswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]

                if not cells or cells[0].lower() == "name":
                    continue

                if all(set(c) <= set("-:") for c in cells):
                    continue

                if len(cells) != 3:
                    raise ValueError(
                        "Fixed parameter table must contain exactly "
                        "3 columns: name, value, unit."
                    )

                name, value, unit = cells
                config["fixed_parameters"].append(
                    {
                        "name": name,
                        "value": value,
                        "unit": unit,
                    }
                )

    validate_config(config)
    return config


def validate_config(config):
    required_model = (
        "input_file",
        "target_frequency_thz",
    )
    required_ga = (
        "num_generations",
        "solutions_per_population",
        "num_parents_mating",
    )

    for key in required_model:
        if key not in config["model"]:
            raise ValueError(f"Missing model configuration: {key}")

    for key in required_ga:
        if key not in config["ga"]:
            raise ValueError(f"Missing GA configuration: {key}")

    config["model"].setdefault("evaluation_tag", "gev1")
    config["model"].setdefault("temporary_table_tag", "ga_temp_table")
    config["model"].setdefault("save_first_simulation_output", False)
    config["model"].setdefault("phi_a", 0.0)
    config.setdefault("fixed_parameters", [])

    try:
        config["model"]["phi_a"] = float(config["model"]["phi_a"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Model parameter phi_a must be numeric.") from exc

    if not config["parameters"]:
        raise ValueError("No optimization parameters were found.")

    # Generalizable objective configuration.
    config["objective"].setdefault("objective_type", "q_factor")

    supported = {
        "q_factor",
        "peak_transmission",
        "target_transmission",
        "peak_frequency",
        "target_frequency_error",
        "fwhm",
        "custom_expression",
    }

    objective_type = str(
        config["objective"]["objective_type"]
    ).strip().lower()

    if objective_type not in supported:
        raise ValueError(
            f"Unknown objective_type '{objective_type}'. "
            f"Supported: {sorted(supported)}"
        )

    config["objective"]["objective_type"] = objective_type

    if objective_type == "custom_expression":
        if not config["objective"].get("objective_expression"):
            raise ValueError(
                "custom_expression requires objective_expression."
            )

    names = set()
    for p in config["parameters"]:
        if not p["name"]:
            raise ValueError("Optimization parameter has an empty name.")
        if p["name"] in names:
            raise ValueError(f"Duplicate optimization parameter: {p['name']}")
        names.add(p["name"])

        if p["min"] > p["max"]:
            raise ValueError(
                f"Invalid range for {p['name']}: min > max."
            )
        if p["step"] <= 0:
            raise ValueError(
                f"Invalid step for {p['name']}: step must be > 0."
            )

        # Make sure the upper endpoint is reachable on the requested grid.
        n = round((p["max"] - p["min"]) / p["step"])
        if n < 1 and p["min"] != p["max"]:
            raise ValueError(
                f"Invalid grid for {p['name']}: range is smaller than step."
            )

    fixed_names = set()
    for parameter in config["fixed_parameters"]:
        name = parameter["name"]
        if not name:
            raise ValueError("Fixed parameter has an empty name.")
        if name in fixed_names:
            raise ValueError(f"Duplicate fixed parameter: {name}")
        if name in names:
            raise ValueError(
                f"Parameter '{name}' cannot be both fixed and optimized."
            )
        if name == "phi_a":
            raise ValueError(
                "Configure phi_a in the Model section, not Fixed Parameters."
            )
        if not parameter["value"]:
            raise ValueError(f"Fixed parameter '{name}' has no value.")
        fixed_names.add(name)


# ---------------------------------------------------------------------------
# Configuration-aware persistent cache
# ---------------------------------------------------------------------------

def make_config_id(config):
    """
    Cache identity changes if the model, table, target frequency, optimization
    grid, or GA fitness implementation changes.

    The objective function is intentionally not part of the cache identity.
    The same physical spectrum may be reused to evaluate a different objective
    without re-solving COMSOL, as long as the underlying model and geometry are
    the same.
    """

    payload = {
        "cache_version": CACHE_VERSION,
        "model": config["model"],
        "parameters": config["parameters"],
        "fixed_parameters": config.get("fixed_parameters", []),
        "fitness": "general-target-function-v1",
    }

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()[:16]


def read_cached_spectrum(spectrum_file):
    if not spectrum_file:
        raise FileNotFoundError("No cached spectrum file was recorded.")

    path = os.path.abspath(spectrum_file)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Cached spectrum not found: {path}")

    frequency = []
    transmission = []

    with open(path, "r", newline="", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("frequency_thz,transmission"):
                continue
            if "," not in line:
                continue
            values = [part.strip() for part in line.split(",", 1)]
            if len(values) != 2:
                continue
            try:
                frequency.append(float(values[0]))
                transmission.append(float(values[1]))
            except ValueError:
                continue

    if not frequency:
        raise ValueError(f"Cached spectrum file has no numeric data: {path}")

    return np.asarray(frequency, dtype=float), np.asarray(transmission, dtype=float)


def load_persistent_cache(filename, parameters, config_id):
    cache = {}

    if not os.path.exists(filename) or os.path.getsize(filename) == 0:
        logger.info("No persistent cache found.")
        return cache

    parameter_names = [p["name"] for p in parameters]

    try:
        with open(filename, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            if not reader.fieldnames:
                logger.warning("Cache CSV has no header.")
                return cache

            required = parameter_names + [
                "config_id",
                "objective_type",
                "fitness",
                "peak_frequency_thz",
                "fwhm_thz",
                "q_factor",
                "status",
            ]

            missing = [x for x in required if x not in reader.fieldnames]
            if missing:
                logger.warning(
                    "Ignoring old/incompatible cache. Missing columns: %s",
                    missing,
                )
                return cache

            for row_number, row in enumerate(reader, start=2):
                if row.get("status") != "OK":
                    continue

                # Objective changes do not affect config_id, but model or
                # fixed-parameter changes do and must invalidate this spectrum.
                if row.get("config_id") != config_id:
                    continue

                try:
                    solution = [float(row[name]) for name in parameter_names]
                    key = make_cache_key(solution)

                    cache[key] = {
                        "fitness": float(row["fitness"]),
                        "objective_type": row.get("objective_type", "q_factor"),
                        "config_id": row.get("config_id", config_id),
                        "spectrum_file": row.get("spectrum_file", ""),
                        "peak_frequency_thz": float(
                            row["peak_frequency_thz"]
                        ),
                        "fwhm_thz": float(row["fwhm_thz"]),
                        "q_factor": float(row["q_factor"]),
                        "solve_time_s": float(row["solve_time_s"]),
                    }
                except (KeyError, ValueError, TypeError) as exc:
                    logger.warning(
                        "Skipping invalid cache row %d: %s",
                        row_number,
                        exc,
                    )

    except OSError as exc:
        logger.warning("Could not read persistent cache: %s", exc)
        return cache

    logger.info(
        "Loaded %d successful cached evaluations for config %s.",
        len(cache),
        config_id,
    )
    return cache


# ---------------------------------------------------------------------------
# COMSOL
# ---------------------------------------------------------------------------

def start_comsol(input_file):
    if not os.path.exists(input_file):
        raise FileNotFoundError(
            f"COMSOL model not found: {os.path.abspath(input_file)}"
        )

    logger.info("Starting COMSOL backend...")
    client = mph.start()

    logger.info("Connected to COMSOL version: %s", client.version)
    logger.info("Loading COMSOL model: %s", input_file)

    model = client.load(input_file)
    logger.info("Model loaded successfully.")

    return client, model


def clear_comsol_solutions(model):
    """Release solution data retained by each COMSOL solver sequence."""

    solver_collection = model.java.sol()
    solver_tags = list(solver_collection.tags())

    for solver_tag in solver_tags:
        solver = solver_collection.get(solver_tag)
        solver.clearSolution()

    logger.info("Cleared COMSOL solution data")


# ---------------------------------------------------------------------------
# Spectrum extraction
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# GENERALIZABLE TARGET / OBJECTIVE FUNCTIONS
# ---------------------------------------------------------------------------
#
# The optimizer is deliberately separated from the physics/objective.
# To change what the GA optimizes, change "objective_type" and its settings
# in ga_parameters.md. No changes to the GA code are required.
#
# Supported objective types:
#
#   q_factor
#       Maximize Q = peak_frequency / FWHM
#
#   peak_transmission
#       Maximize transmission at/near the selected peak
#
#   target_transmission
#       Maximize transmission at a specified target frequency
#
#   peak_frequency
#       Maximize the frequency of the selected peak
#
#   target_frequency_error
#       Minimize |peak_frequency - target_frequency|
#       Internally converted to a maximization fitness.
#
#   fwhm
#       Minimize FWHM
#
#   custom_expression
#       Evaluate a mathematical expression using spectrum metrics.
#
# Custom expressions may use:
#   peak_frequency
#   peak_transmission
#   fwhm
#   q_factor
#   target_frequency
#   transmission_at_target
#
# Example:
#   objective_type: custom_expression
#   objective_expression: peak_transmission * q_factor
#
# The GA always MAXIMIZES fitness. For quantities that should be minimized,
# the built-in objectives return a positive inverse or negative error.


class FWHMUnavailableError(ValueError):
    """The sampled spectrum does not contain both half-height crossings."""


def find_target_peak(frequency_thz, transmission, target_frequency_thz,
                     search_window_thz=None):
    f, y = prepare_spectrum(frequency_thz, transmission)

    peaks, _ = find_peaks(y)

    if peaks.size == 0:
        peak_idx = int(np.argmax(y))
    else:
        if search_window_thz is None:
            search_window_thz = max(
                0.5,
                0.10 * abs(float(target_frequency_thz)),
            )

        nearby = peaks[
            np.abs(f[peaks] - target_frequency_thz)
            <= float(search_window_thz)
        ]

        candidates = nearby if nearby.size else peaks

        # Strongest peak among the candidate peaks.
        peak_idx = candidates[np.argmax(y[candidates])]

    return (
        float(f[peak_idx]),
        float(y[peak_idx]),
        int(peak_idx),
    )


def interpolate_at_frequency(frequency_thz, transmission, target_frequency_thz):
    f, y = prepare_spectrum(frequency_thz, transmission)

    target = float(target_frequency_thz)

    if target < f[0] or target > f[-1]:
        raise ValueError(
            f"Target frequency {target:g} THz is outside the "
            f"spectrum range [{f[0]:g}, {f[-1]:g}] THz."
        )

    return float(np.interp(target, f, y))


def calculate_fwhm_from_peak(
    frequency_thz,
    transmission,
    peak_idx,
):
    f, y = prepare_spectrum(frequency_thz, transmission)

    if peak_idx < 0 or peak_idx >= f.size:
        raise ValueError(
            f"Peak index {peak_idx} is outside the spectrum "
            f"range 0..{f.size - 1}."
        )

    peak_value = float(y[peak_idx])

    # Estimate a local baseline around the selected peak.
    span = max(5, f.size // 20)
    left = max(0, peak_idx - span)
    right = min(f.size, peak_idx + span + 1)

    baseline = float(np.min(y[left:right]))
    half_level = baseline + 0.5 * (peak_value - baseline)

    left_cross = None
    for i in range(peak_idx - 1, -1, -1):
        y1, y2 = y[i], y[i + 1]

        if (y1 - half_level) * (y2 - half_level) <= 0:
            if y2 == y1:
                left_cross = float(f[i])
            else:
                frac = (half_level - y1) / (y2 - y1)
                left_cross = float(
                    f[i] + frac * (f[i + 1] - f[i])
                )
            break

    right_cross = None
    for i in range(peak_idx, f.size - 1):
        y1, y2 = y[i], y[i + 1]

        if (y1 - half_level) * (y2 - half_level) <= 0:
            if y2 == y1:
                right_cross = float(f[i])
            else:
                frac = (half_level - y1) / (y2 - y1)
                right_cross = float(
                    f[i] + frac * (f[i + 1] - f[i])
                )
            break

    if left_cross is None or right_cross is None:
        missing_sides = []
        if left_cross is None:
            missing_sides.append("left")
        if right_cross is None:
            missing_sides.append("right")

        raise FWHMUnavailableError(
            "Could not determine both FWHM crossings: "
            f"missing {', '.join(missing_sides)} crossing; "
            f"peak={f[peak_idx]:.9g} THz, "
            f"half_level={half_level:.9g}, "
            f"spectrum=[{f[0]:.9g}, {f[-1]:.9g}] THz. "
            "Extend the frequency sweep so the selected peak returns "
            "below half height on both sides."
        )

    fwhm = right_cross - left_cross

    if not np.isfinite(fwhm) or fwhm <= 0:
        raise ValueError(f"Invalid FWHM: {fwhm}")

    return float(fwhm)


def calculate_spectrum_metrics(
    frequency_thz,
    transmission,
    target_frequency_thz,
    search_window_thz=None,
):
    """
    Extract all reusable spectrum metrics in one place.

    Every target function receives this same metrics dictionary, so adding a
    new objective does not require changing the COMSOL/GA machinery.
    """

    f, y = prepare_spectrum(
        frequency_thz,
        transmission,
    )

    peak_frequency, peak_transmission, peak_idx = (
        find_target_peak(
            f,
            y,
            target_frequency_thz,
            search_window_thz,
        )
    )

    fwhm = calculate_fwhm_from_peak(
        f,
        y,
        peak_idx,
    )

    q_factor = peak_frequency / fwhm

    transmission_at_target = interpolate_at_frequency(
        f,
        y,
        target_frequency_thz,
    )

    return {
        "peak_frequency": float(peak_frequency),
        "peak_transmission": float(peak_transmission),
        "fwhm": float(fwhm),
        "q_factor": float(q_factor),
        "target_frequency": float(target_frequency_thz),
        "transmission_at_target": float(transmission_at_target),
    }


def evaluate_target_function(metrics, objective_type, objective_expression=None):
    """
    Convert spectrum metrics into a PyGAD fitness value.

    PyGAD maximizes the returned value.
    """

    objective_type = str(objective_type).strip().lower()

    if objective_type == "q_factor":
        return float(metrics["q_factor"])

    if objective_type == "peak_transmission":
        return float(metrics["peak_transmission"])

    if objective_type == "target_transmission":
        return float(metrics["transmission_at_target"])

    if objective_type == "peak_frequency":
        return float(metrics["peak_frequency"])

    if objective_type == "target_frequency_error":
        error = abs(
            metrics["peak_frequency"]
            - metrics["target_frequency"]
        )

        # Maximization-compatible form. Exact match -> 1.
        return float(1.0 / (1.0 + error))

    if objective_type == "fwhm":
        # Smaller FWHM is better.
        return float(1.0 / metrics["fwhm"])

    if objective_type == "custom_expression":
        if not objective_expression:
            raise ValueError(
                "objective_expression is required when "
                "objective_type is custom_expression."
            )

        allowed_names = {
            "peak_frequency": metrics["peak_frequency"],
            "peak_transmission": metrics["peak_transmission"],
            "fwhm": metrics["fwhm"],
            "q_factor": metrics["q_factor"],
            "target_frequency": metrics["target_frequency"],
            "transmission_at_target": metrics["transmission_at_target"],
            "abs": abs,
            "min": min,
            "max": max,
            "sqrt": np.sqrt,
            "log": np.log,
            "log10": np.log10,
            "exp": np.exp,
            "pow": pow,
        }

        # Deliberately no builtins. This is intended for mathematical
        # expressions in the user's own configuration file.
        value = eval(
            str(objective_expression),
            {"__builtins__": {}},
            allowed_names,
        )

        value = float(value)

        if not np.isfinite(value):
            raise ValueError(
                f"Custom objective returned invalid value: {value}"
            )

        return value

    raise ValueError(
        f"Unknown objective_type '{objective_type}'. "
        "Supported: q_factor, peak_transmission, "
        "target_transmission, peak_frequency, "
        "target_frequency_error, fwhm, custom_expression."
    )


def evaluate_solution(
    model,
    parameters,
    solution,
    evaluation_tag,
    temporary_table_tag,
    target_frequency_thz,
    objective_type,
    objective_expression,
    search_window_thz=None,
    diagnostic_output_file=None,
):
    """
    One complete physical evaluation:
      1. Set geometry parameters.
      2. Solve COMSOL.
      3. Refresh numerical result features.
      4. Extract spectrum.
      5. Calculate Q.
      6. Return the spectrum for saving.
    """

    solution = np.asarray(solution, dtype=float)

    set_comsol_parameters(model, parameters, solution)

    logger.info("------------------------------------------------------------")
    logger.info("COMSOL evaluation")
    for value, parameter in zip(solution, parameters):
        logger.info(
            "  %s = %.10g %s",
            parameter["name"],
            value,
            parameter["unit"],
        )

    solve_start = time.time()
    model.solve()
    solve_time = time.time() - solve_start

    logger.info(
        "COMSOL solve completed in %.2f s",
        solve_time,
    )

    if diagnostic_output_file:
        try:
            model.save(diagnostic_output_file)
            logger.info(
                "Saved first-simulation diagnostic model: %s",
                os.path.abspath(diagnostic_output_file),
            )
        except Exception:
            logger.exception(
                "Could not save first-simulation diagnostic model: %s",
                os.path.abspath(diagnostic_output_file),
            )

    frequency, transmission = extract_fresh_table(
        model,
        evaluation_tag,
        temporary_table_tag,
    )

    # The extracted arrays are independent NumPy copies. Clear COMSOL's
    # retained solver state before the next GA candidate is solved.
    clear_comsol_solutions(model)

    logger.info(
        "Spectrum: n=%d | f=[%.9g, %.9g] THz | "
        "T=[%.9g, %.9g] | hash=%s",
        frequency.size,
        float(np.min(frequency)),
        float(np.max(frequency)),
        float(np.min(transmission)),
        float(np.max(transmission)),
        spectrum_hash(transmission),
    )

    metrics = calculate_spectrum_metrics(
        frequency,
        transmission,
        target_frequency_thz,
        search_window_thz=search_window_thz,
    )

    logger.info(
        "Metrics: peak=%.12g THz | T_peak=%.12g | "
        "FWHM=%.12g THz | Q=%.12g | T_target=%.12g",
        metrics["peak_frequency"],
        metrics["peak_transmission"],
        metrics["fwhm"],
        metrics["q_factor"],
        metrics["transmission_at_target"],
    )

    fitness = evaluate_target_function(
        metrics,
        objective_type,
        objective_expression,
    )

    logger.info(
        "Target function '%s' = %.12g",
        objective_type,
        fitness,
    )

    return (
        metrics,
        fitness,
        solve_time,
        frequency,
        transmission,
    )


# ---------------------------------------------------------------------------
# Output files
# ---------------------------------------------------------------------------

def create_csv_writer(filename, parameters):
    fieldnames = [
        "timestamp",
        "config_id",
        "objective_type",
        "generation",
        "solution_index",
    ]

    fieldnames += [p["name"] for p in parameters]

    fieldnames += [
        "peak_frequency_thz",
        "fwhm_thz",
        "q_factor",
        "fitness",
        "solve_time_s",
        "spectrum_hash",
        "spectrum_file",
        "status",
        "error",
    ]

    # If an old CSV has an incompatible header, do not append a new schema
    # into the middle of it. Use a versioned file instead.
    if os.path.exists(filename) and os.path.getsize(filename) > 0:
        with open(filename, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            old_header = next(reader, [])

        if old_header != fieldnames:
            stem = Path(filename).stem
            suffix = Path(filename).suffix
            filename = f"{stem}_v2{suffix}"
            logger.warning(
                "Existing %s has an incompatible schema. "
                "Using %s instead.",
                OUTPUT_CSV,
                filename,
            )

    file_exists = os.path.exists(filename)

    csv_file = open(
        filename,
        "a",
        newline="",
        encoding="utf-8",
    )

    writer = csv.DictWriter(
        csv_file,
        fieldnames=fieldnames,
    )

    if not file_exists or os.path.getsize(filename) == 0:
        writer.writeheader()
        csv_file.flush()

    return filename, csv_file, writer


def save_result(
    csv_file,
    csv_writer,
    config_id,
    objective_type,
    parameters,
    generation,
    solution_index,
    solution,
    peak_frequency=None,
    fwhm=None,
    q_factor=None,
    fitness=None,
    solve_time=None,
    spectrum_hash_value="",
    spectrum_file="",
    status="OK",
    error="",
):
    row = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config_id": config_id,
        "objective_type": objective_type,
        "generation": generation,
        "solution_index": solution_index,
        "peak_frequency_thz": peak_frequency,
        "fwhm_thz": fwhm,
        "q_factor": q_factor,
        "fitness": None if fitness is None else float(fitness),
        "solve_time_s": solve_time,
        "spectrum_hash": spectrum_hash_value,
        "spectrum_file": spectrum_file,
        "status": status,
        "error": error,
    }

    for i, parameter in enumerate(parameters):
        row[parameter["name"]] = float(solution[i])

    csv_writer.writerow(row)
    csv_file.flush()


def safe_filename_part(value):
    text = str(value)
    text = text.replace("-", "m")
    text = text.replace(".", "p")
    text = re.sub(r"[^A-Za-z0-9_]+", "_", text)
    return text


def spectrum_filename(parameters, solution):
    parts = []

    for value, parameter in zip(solution, parameters):
        value_text = f"{float(value):.10g}"
        parts.append(
            f"{safe_filename_part(parameter['name'])}_"
            f"{safe_filename_part(value_text)}"
            f"{safe_filename_part(parameter['unit'])}"
        )

    return os.path.join(
        SPECTRA_DIR,
        "__".join(parts) + ".csv",
    )


def save_transmission_spectrum(
    frequency,
    transmission,
    parameters,
    solution,
    config_id,
    generation,
    solution_index,
):
    os.makedirs(SPECTRA_DIR, exist_ok=True)

    filename = spectrum_filename(parameters, solution)

    frequency = np.asarray(frequency, dtype=float).reshape(-1)
    transmission = np.asarray(transmission, dtype=float).reshape(-1)

    if frequency.size != transmission.size:
        raise ValueError(
            f"Frequency/transmission mismatch: "
            f"{frequency.size} vs {transmission.size}"
        )

    # Do not overwrite an existing geometry spectrum.
    if os.path.exists(filename) and os.path.getsize(filename) > 0:
        logger.info(
            "Spectrum already exists; keeping: %s",
            os.path.abspath(filename),
        )
        return filename

    with open(filename, "w", newline="", encoding="utf-8") as f:
        f.write("# COMSOL transmission spectrum\n")
        f.write(f"# config_id = {config_id}\n")
        f.write(f"# generation = {generation}\n")
        f.write(f"# solution_index = {solution_index}\n")

        for value, parameter in zip(solution, parameters):
            f.write(
                f"# {parameter['name']} = "
                f"{float(value):.12g} {parameter['unit']}\n"
            )

        f.write(f"# spectrum_hash = {spectrum_hash(transmission)}\n")

        writer = csv.writer(f)
        writer.writerow(["frequency_thz", "transmission"])

        for freq, value in zip(frequency, transmission):
            writer.writerow(
                [f"{freq:.12g}", f"{value:.12g}"]
            )

    logger.info(
        "Transmission spectrum saved: %s",
        os.path.abspath(filename),
    )

    return filename


# ---------------------------------------------------------------------------
# GA helpers
# ---------------------------------------------------------------------------

def diagnose_saved_model():
    config = parse_markdown_config(CONFIG_FILE)
    model_config = config["model"]
    input_file = str(model_config["input_file"])
    diagnostic_file = os.path.splitext(input_file)[0] + "_out.mph"
    evaluation_tag = str(model_config.get("evaluation_tag", "gev1"))
    temporary_table_tag = str(
        model_config.get("temporary_table_tag", "ga_temp_table")
    )

    if not os.path.exists(diagnostic_file):
        raise FileNotFoundError(
            f"Diagnostic model not found: {os.path.abspath(diagnostic_file)}"
        )

    logger.info("Loading diagnostic model: %s", os.path.abspath(diagnostic_file))
    client = None

    try:
        client, model = start_comsol(diagnostic_file)
        frequency, transmission = extract_fresh_table(
            model,
            evaluation_tag,
            temporary_table_tag,
            require_min_points=False,
            print_table=True,
        )
        print("Extracted frequency (THz):", frequency)
        print("Extracted transmission:", transmission)
    finally:
        if client is not None:
            client.clear()


# ---------------------------------------------------------------------------
# Optimization
# ---------------------------------------------------------------------------

def optimize():
    config = parse_markdown_config(CONFIG_FILE)

    model_config = config["model"]
    ga_config = config["ga"]
    parameters = config["parameters"]

    input_file = model_config["input_file"]
    evaluation_tag = str(
        model_config.get("evaluation_tag", "gev1")
    )
    temporary_table_tag = str(
        model_config.get("temporary_table_tag", "ga_temp_table")
    )
    save_first_simulation_output = bool(
        model_config.get("save_first_simulation_output", False)
    )
    diagnostic_output_file = os.path.splitext(input_file)[0] + "_out.mph"
    target_frequency = float(
        model_config["target_frequency_thz"]
    )
    phi_a = float(model_config["phi_a"])

    objective_config = config["objective"]
    objective_type = objective_config["objective_type"]
    objective_expression = objective_config.get(
        "objective_expression"
    )

    search_window_thz = objective_config.get(
        "peak_search_window_thz",
        None,
    )

    if search_window_thz is not None:
        search_window_thz = float(search_window_thz)

    num_generations = int(
        ga_config["num_generations"]
    )
    sol_per_pop = int(
        ga_config["solutions_per_population"]
    )
    num_parents = int(
        ga_config["num_parents_mating"]
    )

    config_id = make_config_id(config)

    logger.info("=" * 72)
    logger.info("COMSOL MULTI-PARAMETER GENETIC ALGORITHM")
    logger.info("=" * 72)
    logger.info("Configuration: %s", os.path.abspath(CONFIG_FILE))
    logger.info("Config ID: %s", config_id)
    logger.info("COMSOL model: %s", input_file)
    logger.info("COMSOL evaluation: %s", evaluation_tag)
    logger.info("Temporary result table: %s", temporary_table_tag)
    logger.info(
        "First-simulation diagnostic output: %s",
        (
            os.path.abspath(diagnostic_output_file)
            if save_first_simulation_output
            else "disabled"
        ),
    )
    logger.info(
        "Target frequency: %.9g THz",
        target_frequency,
    )
    logger.info(
        "Polarization angle phi_a: %.9g deg (0=x, 90=y)",
        phi_a,
    )
    logger.info(
        "Objective type: %s",
        objective_type,
    )
    if objective_expression:
        logger.info(
            "Objective expression: %s",
            objective_expression,
        )

    total_combinations = 1
    for p in parameters:
        count = int(
            round((p["max"] - p["min"]) / p["step"])
        ) + 1
        total_combinations *= count

    logger.info(
        "Search space contains %d parameter combinations.",
        total_combinations,
    )

    client = None
    csv_file = None

    try:
        client, model = start_comsol(input_file)
        verify_model_parameters(
            model,
            model_config,
            config.get("fixed_parameters", []),
        )
        verify_comsol_parameters(model, parameters)

        gene_spaces = create_gene_spaces(parameters)

        output_csv, csv_file, csv_writer = create_csv_writer(
            OUTPUT_CSV,
            parameters,
        )

        logger.info(
            "Results CSV: %s",
            os.path.abspath(output_csv),
        )

        fitness_cache = load_persistent_cache(
            output_csv,
            parameters,
            config_id,
        )

        logger.info(
            "Persistent cache entries: %d",
            len(fitness_cache),
        )

        current_generation = {"number": 0}
        diagnostic_state = {
            "pending": save_first_simulation_output,
        }

        def fitness_func(
            ga_instance,
            solution,
            solution_idx,
        ):
            solution = np.asarray(solution, dtype=float)
            key = make_cache_key(solution)

            # ------------------------------------------------------------
            # CACHE HIT
            # ------------------------------------------------------------
            if key in fitness_cache:
                cached = fitness_cache[key]

                logger.info("=" * 72)
                logger.info(
                    "CACHE HIT | status=%s",
                    cached.get("status", "OK"),
                )
                logger.info("Solution: %s", key)
                if cached.get("status") == "FAILED_FWHM":
                    logger.info(
                        "Cached failure: %s",
                        cached.get("error", "FWHM unavailable"),
                    )
                else:
                    logger.info(
                        "Cached peak = %.9g THz",
                        cached["peak_frequency_thz"],
                    )
                    logger.info(
                        "Cached FWHM = %.9g THz",
                        cached["fwhm_thz"],
                    )
                logger.info(
                    "Cached fitness = %.9g",
                    cached["fitness"],
                )
                logger.info("=" * 72)

                if cached.get("objective_type") != objective_type:
                    try:
                        frequency, transmission = read_cached_spectrum(
                            cached.get("spectrum_file", "")
                        )
                        metrics = calculate_spectrum_metrics(
                            frequency,
                            transmission,
                            target_frequency,
                            search_window_thz=search_window_thz,
                        )
                        recomputed_fitness = evaluate_target_function(
                            metrics,
                            objective_type,
                            objective_expression,
                        )
                        logger.info(
                            "Recomputed cache hit for objective '%s': %.12g",
                            objective_type,
                            recomputed_fitness,
                        )
                        return recomputed_fitness
                    except Exception as exc:
                        logger.warning(
                            "Could not recompute cached objective '%s' from "%
                            "spectrum file %s: %s",
                            objective_type,
                            cached.get("spectrum_file", ""),
                            exc,
                        )

                return cached["fitness"]

            # ------------------------------------------------------------
            # CACHE MISS
            # ------------------------------------------------------------

            logger.info("=" * 72)
            logger.info(
                "CACHE MISS | generation=%d | solution=%d",
                current_generation["number"],
                solution_idx,
            )

            try:
                diagnostic_file = None
                if diagnostic_state["pending"]:
                    diagnostic_file = diagnostic_output_file
                    diagnostic_state["pending"] = False

                (
                    metrics,
                    fitness,
                    solve_time,
                    frequency,
                    transmission,
                ) = evaluate_solution(
                    model,
                    parameters,
                    solution,
                    evaluation_tag,
                    temporary_table_tag,
                    target_frequency,
                    objective_type,
                    objective_expression,
                    search_window_thz,
                    diagnostic_file,
                )

                peak_frequency = metrics["peak_frequency"]
                fwhm = metrics["fwhm"]
                q_factor = metrics["q_factor"]

                if not np.isfinite(fitness):
                    raise ValueError(
                        f"Invalid target-function value: {fitness}"
                    )

                # Save spectrum BEFORE caching the result.
                spectrum_file = save_transmission_spectrum(
                    frequency,
                    transmission,
                    parameters,
                    solution,
                    config_id,
                    current_generation["number"],
                    solution_idx,
                )

                spectrum_hash_value = spectrum_hash(transmission)

                # Only successful evaluations enter the in-memory cache.
                # Preserve the objective and saved spectrum used for this run so
                # future cache hits can recompute a different objective without
                # re-solving COMSOL.
                fitness_cache[key] = {
                    "fitness": fitness,
                    "status": "OK",
                    "objective_type": objective_type,
                    "spectrum_file": spectrum_file,
                    "peak_frequency_thz": float(peak_frequency),
                    "fwhm_thz": float(fwhm),
                    "q_factor": float(q_factor),
                    "solve_time_s": float(solve_time),
                }

                save_result(
                    csv_file,
                    csv_writer,
                    config_id,
                    objective_type,
                    parameters,
                    current_generation["number"],
                    solution_idx,
                    solution,
                    peak_frequency,
                    fwhm,
                    q_factor,
                    fitness,
                    solve_time,
                    spectrum_hash_value,
                    spectrum_file,
                    status="OK",
                )

                logger.info(
                    "SUCCESS | peak=%.9g THz | FWHM=%.9g THz | Q=%.9g",
                    peak_frequency,
                    fwhm,
                    q_factor,
                )
                logger.info("=" * 72)

                return fitness

            except FWHMUnavailableError as exc:
                failure_fitness = FAILURE_FITNESS
                failure_record = {
                    "fitness": failure_fitness,
                    "status": "FAILED_FWHM",
                    "error": str(exc),
                }
                fitness_cache[key] = failure_record

                logger.warning(
                    "FWHM unavailable; returning %.6g | solution=%s | %s",
                    failure_fitness,
                    key,
                    exc,
                )

                try:
                    save_result(
                        csv_file,
                        csv_writer,
                        config_id,
                        objective_type,
                        parameters,
                        current_generation["number"],
                        solution_idx,
                        solution,
                        fitness=failure_fitness,
                        status="FAILED_FWHM",
                        error=str(exc),
                    )
                except Exception:
                    logger.exception(
                        "Could not write failed FWHM evaluation to CSV."
                    )

                logger.info("=" * 72)
                return failure_fitness

            except Exception as exc:
                # Other failures may be transient, so they are not cached.
                logger.exception("COMSOL evaluation failed.")

                try:
                    save_result(
                        csv_file,
                        csv_writer,
                        config_id,
                        objective_type,
                        parameters,
                        current_generation["number"],
                        solution_idx,
                        solution,
                        status="FAILED",
                        error=str(exc),
                    )
                except Exception:
                    logger.exception(
                        "Could not write failed evaluation to CSV."
                    )

                logger.info("=" * 72)

                # Large negative fitness makes failed geometries unattractive.
                return FAILURE_FITNESS

        def on_generation(ga_instance):
            current_generation["number"] = (
                ga_instance.generations_completed
            )

            solution, fitness, index = (
                ga_instance.best_solution()
            )

            logger.info("=" * 72)
            logger.info(
                "GENERATION %d COMPLETE",
                ga_instance.generations_completed,
            )
            logger.info(
                "Best fitness = %.12g",
                fitness,
            )

            for i, p in enumerate(parameters):
                logger.info(
                    "Best %s = %.12g %s",
                    p["name"],
                    solution[i],
                    p["unit"],
                )

            logger.info("=" * 72)

        random_seed = ga_config.get("random_seed", None)
        if random_seed is not None:
            random_seed = int(random_seed)

        stop_criteria = ga_config.get(
            "stop_criteria",
            "saturate_8",
        )
        if isinstance(stop_criteria, str):
            stop_criteria = [stop_criteria]

        ga_kwargs = dict(
            num_generations=num_generations,
            num_parents_mating=num_parents,
            sol_per_pop=sol_per_pop,
            num_genes=len(parameters),
            gene_space=gene_spaces,
            fitness_func=fitness_func,
            parent_selection_type=ga_config.get(
                "parent_selection_type",
                "sss",
            ),
            keep_parents=int(
                ga_config.get("keep_parents", 1)
            ),
            crossover_type=ga_config.get(
                "crossover_type",
                "uniform",
            ),
            mutation_type=ga_config.get(
                "mutation_type",
                "random",
            ),
            mutation_percent_genes=float(
                ga_config.get(
                    "mutation_percent_genes",
                    30,
                )
            ),
            stop_criteria=stop_criteria,
            suppress_warnings=True,
            on_generation=on_generation,
        )

        if random_seed is not None:
            ga_kwargs["random_seed"] = random_seed

        ga_instance = pygad.GA(**ga_kwargs)

        logger.info("Starting PyGAD optimization...")
        start_time = time.time()

        ga_instance.run()

        elapsed = time.time() - start_time

        solution, fitness, solution_idx = (
            ga_instance.best_solution()
        )

        logger.info("=" * 72)
        logger.info("OPTIMIZATION COMPLETE")
        logger.info("Elapsed time: %.2f s", elapsed)
        logger.info("Best Q/fitness: %.12g", fitness)

        for i, p in enumerate(parameters):
            logger.info(
                "Best %s = %.12g %s",
                p["name"],
                solution[i],
                p["unit"],
            )

        logger.info(
            "Best solution index: %d",
            solution_idx,
        )
        logger.info("=" * 72)

        return solution, fitness

    finally:
        if csv_file is not None:
            csv_file.close()

        if client is not None:
            try:
                logger.info("Clearing COMSOL models...")
                client.clear()
                logger.info("COMSOL backend closed.")
            except Exception:
                logger.exception(
                    "Error while closing COMSOL backend."
                )


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--diagnose":
        diagnose_saved_model()
    else:
        optimize()

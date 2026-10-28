#!/usr/bin/env python3
"""Optimize the transmission-spectrum difference of two COMSOL models."""

import csv
from datetime import datetime
import hashlib
import json
import logging
import os
import re
import sys
import time

import mph
import numpy as np
import pygad

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


CONFIG_FILE = "ga_optimize_difference_parameters.md"
OUTPUT_CSV = "ga_difference_results.csv"
SPECTRA_DIR = "spectra_difference"
CACHE_VERSION = "difference-spectrum-v2"
FAILURE_FITNESS = -1e30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            f"difference_app_{datetime.now().strftime('%Y%m%d_%H%M_%f')}.log",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)


def parse_markdown_config(filename):
    """Read the two-model GA configuration from Markdown."""
    if not os.path.exists(filename):
        raise FileNotFoundError(f"Configuration file not found: {os.path.abspath(filename)}")

    config = {
        "model": {},
        "ga": {},
        "objective": {},
        "parameters": [],
        "fixed_parameters": [],
    }
    section = None

    with open(filename, "r", encoding="utf-8") as config_file:
        for raw_line in config_file:
            line = raw_line.strip()
            line = line.replace(r"\_", "_").replace(r"\|", "|")
            line = line.replace(r"\-", "-").replace(r"\#", "#")
            line = line.strip("*")

            if not line:
                continue
            if line.startswith("## "):
                title = line[3:].strip().lower()
                section = {
                    "model": "model",
                    "genetic algorithm": "ga",
                    "objective": "objective",
                    "optimization parameters": "parameters",
                    "fixed parameters": "fixed_parameters",
                }.get(title)
                continue

            if section in ("model", "ga", "objective"):
                match = re.match(r"^-\s*([^:]+):\s*(.+)$", line)
                if match:
                    key, value = match.groups()
                    config[section][key.strip()] = parse_value(value)
                continue

            if section == "parameters" and line.startswith("|"):
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                if not cells or cells[0].lower() == "name":
                    continue
                if all(set(cell) <= set("-:") for cell in cells):
                    continue
                if len(cells) != 5:
                    raise ValueError("Optimization parameter table must contain 5 columns.")
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
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                if not cells or cells[0].lower() == "name":
                    continue
                if all(set(cell) <= set("-:") for cell in cells):
                    continue
                if len(cells) != 3:
                    raise ValueError(
                        "Fixed parameter table must contain 3 columns."
                    )
                name, value, unit = cells
                config["fixed_parameters"].append(
                    {
                        "name": name,
                        "value": parse_value(value),
                        "unit": unit,
                    }
                )

    validate_config(config)
    return config


def validate_config(config):
    required_model = (
        "reference_file",
        "sample_file",
        "evaluation_tag",
        "target_frequency_thz",
    )
    required_ga = ("num_generations", "solutions_per_population", "num_parents_mating")

    for key in required_model:
        if key not in config["model"]:
            raise ValueError(f"Missing model configuration: {key}")
    for key in required_ga:
        if key not in config["ga"]:
            raise ValueError(f"Missing GA configuration: {key}")
    if not config["parameters"]:
        raise ValueError("No optimization parameters were found.")

    model = config["model"]
    model.setdefault("temporary_table_tag_reference", "difference_reference_table")
    model.setdefault("temporary_table_tag_sample", "difference_sample_table")
    model.setdefault("phi_a", 0.0)
    model["phi_a"] = float(model["phi_a"])
    for name in ("reference_file", "sample_file"):
        if not str(model[name]).strip():
            raise ValueError(f"Model configuration {name} cannot be empty.")
    if os.path.abspath(model["reference_file"]) == os.path.abspath(
        model["sample_file"]
    ):
        raise ValueError("Reference and sample models must be different files.")

    objective = config["objective"]
    objective.setdefault("objective_type", "peak_transmission")
    objective["objective_type"] = str(objective["objective_type"]).strip().lower()
    if objective["objective_type"] != "peak_transmission":
        raise ValueError(
            "Difference optimization maximizes the signed positive "
            "reference-minus-sample transmission dip; objective_type must be "
            "'peak_transmission'."
        )
    window = objective.get("peak_search_window_thz")
    if window is not None and float(window) <= 0:
        raise ValueError("peak_search_window_thz must be greater than zero.")

    names = set()
    for parameter in config["parameters"]:
        if (
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", parameter["name"])
            or parameter["name"] in names
        ):
            raise ValueError(f"Invalid or duplicate optimization parameter: {parameter['name']}")
        if parameter["min"] > parameter["max"] or parameter["step"] <= 0:
            raise ValueError(f"Invalid optimization range for {parameter['name']}")
        names.add(parameter["name"])

    fixed_names = set()
    for parameter in config["fixed_parameters"]:
        name = parameter["name"]
        if (
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
            or name in fixed_names
            or name in names
        ):
            raise ValueError(f"Invalid or duplicate fixed parameter: {name}")
        if not isinstance(parameter["value"], (int, float)):
            raise ValueError(f"Fixed parameter {name} must have a numeric value.")
        if any(char in parameter["unit"] for char in "|\r\n"):
            raise ValueError(f"Invalid unit for fixed parameter {name}.")
        fixed_names.add(name)


def make_config_id(config):
    payload = {
        "cache_version": CACHE_VERSION,
        "model": config["model"],
        "parameters": config["parameters"],
        "fixed_parameters": config["fixed_parameters"],
        "objective": config["objective"],
        "fitness": "positive-reference-minus-sample-dip-v2",
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def load_persistent_cache(filename, parameters, config_id):
    cache = {}

    if not os.path.exists(filename) or os.path.getsize(filename) == 0:
        logger.info("No persistent cache found.")
        return cache

    parameter_names = [parameter["name"] for parameter in parameters]

    try:
        with open(filename, "r", newline="", encoding="utf-8") as cache_file:
            reader = csv.DictReader(cache_file)
            if not reader.fieldnames:
                logger.warning("Cache CSV has no header.")
                return cache

            required = parameter_names + [
                "config_id",
                "fitness",
                "status",
            ]
            missing = [name for name in required if name not in reader.fieldnames]
            if missing:
                logger.warning(
                    "Ignoring old/incompatible cache. Missing columns: %s",
                    missing,
                )
                return cache

            for row_number, row in enumerate(reader, start=2):
                if row.get("status") != "OK":
                    continue
                if row.get("config_id") != config_id:
                    continue

                try:
                    solution = [float(row[name]) for name in parameter_names]
                    key = make_cache_key(solution)
                    cache[key] = {
                        "fitness": float(row["fitness"]),
                        "config_id": config_id,
                    }
                except (KeyError, ValueError, TypeError) as exc:
                    logger.warning("Skipping invalid cache row %d: %s", row_number, exc)
    except OSError as exc:
        logger.warning("Could not read persistent cache: %s", exc)
        return cache

    logger.info("Loaded %d successful cached evaluations.", len(cache))
    return cache


def start_comsol_pair(reference_file, sample_file):
    for filename in (reference_file, sample_file):
        if not os.path.exists(filename):
            raise FileNotFoundError(f"COMSOL model not found: {os.path.abspath(filename)}")

    logger.info("Starting COMSOL backends for reference and sample models...")
    client1 = mph.start()
    try:
        client2 = mph.start()
    except Exception:
        client1.clear()
        raise

    try:
        model1 = client1.load(reference_file)
        model2 = client2.load(sample_file)
    except Exception:
        client1.clear()
        client2.clear()
        raise

    logger.info("Loaded reference model: %s", reference_file)
    logger.info("Loaded sample model: %s", sample_file)
    return (client1, model1), (client2, model2)


def shared_difference_spectrum(
    reference_frequency,
    reference_transmission,
    sample_frequency,
    sample_transmission,
):
    """Interpolate both spectra on their overlapping frequency interval."""
    reference_frequency, reference_transmission = prepare_spectrum(
        reference_frequency, reference_transmission
    )
    sample_frequency, sample_transmission = prepare_spectrum(
        sample_frequency, sample_transmission
    )

    lower = max(reference_frequency[0], sample_frequency[0])
    upper = min(reference_frequency[-1], sample_frequency[-1])
    if lower >= upper:
        raise ValueError(
            "Reference and sample spectra have no overlapping frequency range."
        )

    frequency = np.unique(
        np.concatenate(
            (
                reference_frequency[
                    (reference_frequency >= lower) & (reference_frequency <= upper)
                ],
                sample_frequency[
                    (sample_frequency >= lower) & (sample_frequency <= upper)
                ],
            )
        )
    )
    if frequency.size < 3:
        raise ValueError("The overlapping frequency range contains fewer than 3 points.")

    difference = np.interp(frequency, reference_frequency, reference_transmission)
    difference -= np.interp(frequency, sample_frequency, sample_transmission)
    return frequency, difference


def spectrum_token(solution):
    payload = json.dumps(
        [float(value) for value in solution],
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def spectrum_filename(role, config_id, solution):
    return os.path.join(
        SPECTRA_DIR,
        f"{role}_{config_id}_{spectrum_token(solution)}.csv",
    )


def save_spectrum(frequency, transmission, filename, value_column):
    os.makedirs(SPECTRA_DIR, exist_ok=True)
    with open(filename, "w", newline="", encoding="utf-8") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(["frequency_thz", value_column])
        writer.writerows(zip(frequency, transmission))
    return filename


def read_spectrum_csv(filename, value_column):
    frequency = []
    transmission = []
    with open(filename, "r", newline="", encoding="utf-8") as spectrum_file:
        reader = csv.DictReader(spectrum_file)
        if reader.fieldnames != ["frequency_thz", value_column]:
            raise ValueError(f"Unexpected spectrum CSV columns in {filename}.")
        for row in reader:
            frequency.append(float(row["frequency_thz"]))
            transmission.append(float(row[value_column]))
    return prepare_spectrum(frequency, transmission)


def calculate_dip_metrics(frequency, difference, target_frequency, search_window):
    frequency, difference = prepare_spectrum(frequency, difference)
    target_frequency = float(target_frequency)
    if search_window is None:
        in_window = np.ones(frequency.size, dtype=bool)
    else:
        in_window = np.abs(frequency - target_frequency) <= float(search_window)
    if not np.any(in_window):
        raise ValueError(
            "The target search window contains no overlapping spectrum samples."
        )
    window_indices = np.flatnonzero(in_window)
    peak_index = window_indices[int(np.argmax(difference[in_window]))]
    at_target = (
        float(np.interp(target_frequency, frequency, difference))
        if frequency[0] <= target_frequency <= frequency[-1]
        else None
    )
    return {
        "peak_frequency": float(frequency[peak_index]),
        "peak_transmission": float(difference[peak_index]),
        "transmission_at_target": at_target,
        "fwhm": None,
        "q_factor": None,
    }


def evaluate_pair(model1, model2, parameters, solution, config):
    model_config = config["model"]
    fixed_parameters = config["fixed_parameters"]
    set_comsol_parameters(model1, parameters, solution)
    set_comsol_parameters(model2, parameters, solution)
    set_fixed_parameters(model1, fixed_parameters)
    set_fixed_parameters(model2, fixed_parameters)
    set_polarization_parameter(model1, model_config["phi_a"])
    set_polarization_parameter(model2, model_config["phi_a"])

    solve_start = time.time()
    model1.solve()
    model2.solve()
    solve_time = time.time() - solve_start

    frequency1, transmission1 = extract_fresh_table(
        model1,
        model_config["evaluation_tag"],
        model_config["temporary_table_tag_reference"],
    )
    frequency2, transmission2 = extract_fresh_table(
        model2,
        model_config["evaluation_tag"],
        model_config["temporary_table_tag_sample"],
    )
    config_id = make_config_id(config)
    reference_file = spectrum_filename("reference", config_id, solution)
    sample_file = spectrum_filename("sample", config_id, solution)
    save_spectrum(frequency1, transmission1, reference_file, "transmission")
    save_spectrum(frequency2, transmission2, sample_file, "transmission")
    reference_frequency, reference_transmission = read_spectrum_csv(
        reference_file, "transmission"
    )
    sample_frequency, sample_transmission = read_spectrum_csv(
        sample_file, "transmission"
    )
    frequency, difference = shared_difference_spectrum(
        reference_frequency,
        reference_transmission,
        sample_frequency,
        sample_transmission,
    )

    objective = config["objective"]
    metrics = calculate_dip_metrics(
        frequency,
        difference,
        float(model_config["target_frequency_thz"]),
        objective.get("peak_search_window_thz"),
    )
    fitness = metrics["peak_transmission"]
    logger.info(
        "Matched pair: T_reference - T_sample; shared range=[%.9g, %.9g] "
        "THz; target=%.9g THz; peak dip=%.12g at %.9g THz.",
        frequency[0],
        frequency[-1],
        float(model_config["target_frequency_thz"]),
        fitness,
        metrics["peak_frequency"],
    )
    return (
        metrics,
        fitness,
        solve_time,
        frequency,
        difference,
        reference_file,
        sample_file,
    )


def create_csv_writer(filename, parameters):
    fieldnames = ["timestamp", "config_id", "objective_type", "generation", "solution_index"]
    fieldnames += [parameter["name"] for parameter in parameters]
    fieldnames += [
        "peak_frequency_thz",
        "peak_dip",
        "fitness",
        "solve_time_s",
        "spectrum_hash",
        "reference_spectrum_file",
        "sample_spectrum_file",
        "spectrum_file",
        "status",
        "error",
    ]
    file_exists = os.path.exists(filename) and os.path.getsize(filename) > 0
    csv_file = open(filename, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
    if not file_exists:
        writer.writeheader()
        csv_file.flush()
    return csv_file, writer


def write_result(
    csv_file,
    writer,
    config_id,
    objective_type,
    parameters,
    generation,
    solution_index,
    solution,
    metrics=None,
    fitness=None,
    solve_time=None,
    spectrum_hash_value="",
    reference_spectrum_file="",
    sample_spectrum_file="",
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
        "peak_frequency_thz": metrics.get("peak_frequency") if metrics else "",
        "peak_dip": metrics.get("peak_transmission") if metrics else "",
        "fitness": fitness if fitness is not None else "",
        "solve_time_s": solve_time if solve_time is not None else "",
        "spectrum_hash": spectrum_hash_value,
        "reference_spectrum_file": reference_spectrum_file,
        "sample_spectrum_file": sample_spectrum_file,
        "spectrum_file": spectrum_file,
        "status": status,
        "error": error,
    }
    for parameter, value in zip(parameters, solution):
        row[parameter["name"]] = float(value)
    writer.writerow(row)
    csv_file.flush()


def optimize():
    config = parse_markdown_config(CONFIG_FILE)
    model_config = config["model"]
    parameters = config["parameters"]
    objective = config["objective"]
    config_id = make_config_id(config)
    pairs = []
    csv_file = None
    logger.info("Config ID: %s", config_id)
    logger.info(
        "Reference model: %s; sample model: %s",
        model_config["reference_file"],
        model_config["sample_file"],
    )
    logger.info(
        "Fitness maximizes signed T_reference - T_sample inside +/- %.9g "
        "THz of target %.9g THz.",
        float(objective.get("peak_search_window_thz", float("inf"))),
        float(model_config["target_frequency_thz"]),
    )
    logger.info(
        "Shared fixed parameters: %s",
        json.dumps(config["fixed_parameters"], sort_keys=True),
    )

    try:
        pairs = list(
            start_comsol_pair(
                model_config["reference_file"],
                model_config["sample_file"],
            )
        )
        model1, model2 = pairs[0][1], pairs[1][1]
        for model in (model1, model2):
            verify_model_parameters(
                model,
                model_config,
                config["fixed_parameters"],
            )
            verify_comsol_parameters(model, parameters)

        csv_file, writer = create_csv_writer(OUTPUT_CSV, parameters)
        gene_spaces = create_gene_spaces(parameters)
        fitness_cache = load_persistent_cache(
            OUTPUT_CSV,
            parameters,
            config_id,
        )

        def fitness_func(ga_instance, solution, solution_idx):
            del ga_instance
            solution = np.asarray(solution, dtype=float)
            key = make_cache_key(solution)

            if key in fitness_cache:
                cached = fitness_cache[key]
                logger.info("CACHE HIT | solution=%s", key)
                return cached["fitness"]

            try:
                (
                    metrics,
                    fitness,
                    solve_time,
                    frequency,
                    difference,
                    reference_spectrum_file,
                    sample_spectrum_file,
                ) = evaluate_pair(model1, model2, parameters, solution, config)
                spectrum_file = spectrum_filename(
                    "difference",
                    config_id,
                    solution,
                )
                save_spectrum(
                    frequency,
                    difference,
                    spectrum_file,
                    "transmission_difference",
                )
                spectrum_hash_value = spectrum_hash(difference)
                fitness_cache[key] = {
                    "fitness": fitness,
                    "config_id": config_id,
                }
                write_result(
                    csv_file, writer, config_id, objective["objective_type"],
                    parameters, current_generation["number"], solution_idx,
                    solution, metrics, fitness, solve_time,
                    spectrum_hash_value=spectrum_hash_value,
                    reference_spectrum_file=reference_spectrum_file,
                    sample_spectrum_file=sample_spectrum_file,
                    spectrum_file=spectrum_file,
                )
                return fitness
            except Exception as exc:
                logger.exception("Pair evaluation failed")
                write_result(
                    csv_file, writer, config_id, objective["objective_type"],
                    parameters, current_generation["number"], solution_idx,
                    solution, status="FAILED", error=str(exc),
                )
                return FAILURE_FITNESS

        current_generation = {"number": 0}

        def on_generation(ga_instance):
            current_generation["number"] = ga_instance.generations_completed
            logger.info("Completed generation %d", current_generation["number"])

        ga = pygad.GA(
            num_generations=int(config["ga"]["num_generations"]),
            sol_per_pop=int(config["ga"]["solutions_per_population"]),
            num_genes=len(parameters),
            num_parents_mating=int(config["ga"]["num_parents_mating"]),
            fitness_func=fitness_func,
            gene_space=gene_spaces,
            parent_selection_type=config["ga"].get("parent_selection_type", "sss"),
            keep_parents=int(config["ga"].get("keep_parents", 1)),
            mutation_percent_genes=int(config["ga"].get("mutation_percent_genes", 40)),
            stop_criteria=config["ga"].get("stop_criteria"),
            random_seed=config["ga"].get("random_seed"),
            on_generation=on_generation,
        )
        ga.run()
        logger.info("Optimization complete. Best solution: %s", ga.best_solution())
    finally:
        if csv_file is not None:
            csv_file.close()
        for client, _model in pairs:
            client.clear()


if __name__ == "__main__":
    optimize()
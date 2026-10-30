#!/usr/bin/env python3

import csv
from datetime import datetime
import logging
import os
import re
import sys
from pathlib import Path


CONFIG_FILE = "comsol_parameters.md"
LOG_DIR = Path("logs")


def configure_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                LOG_DIR
                / (
                    "comsolrun_app_"
                    f"{datetime.now().strftime('%Y%m%d_%H%M_%f')}.log"
                ),
                encoding="utf-8",
            ),
        ],
    )


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
        return float(value)
    except ValueError:
        return value


def parse_markdown_config(filename):
    """Read model settings and fixed COMSOL parameters from Markdown."""
    if not os.path.exists(filename):
        raise FileNotFoundError(
            f"Configuration file not found: {os.path.abspath(filename)}"
        )

    config = {"model": {}, "parameters": [], "evaluations": []}
    section = None

    with open(filename, "r", encoding="utf-8") as config_file:
        for raw_line in config_file:
            line = raw_line.strip()
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
                elif title in ("parameters", "comsol parameters"):
                    section = "parameters"
                elif title == "evaluations":
                    section = "evaluations"
                else:
                    section = None
                continue

            if section == "model":
                match = re.match(r"^-\s*([^:]+):\s*(.+)$", line)
                if match:
                    key, value = match.groups()
                    config["model"][key.strip()] = parse_value(value)
                continue

            if section == "parameters" and line.startswith("|"):
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                if not cells or cells[0].lower() == "name":
                    continue
                if all(set(cell) <= set("-:") for cell in cells):
                    continue
                if len(cells) != 3:
                    raise ValueError(
                        "COMSOL parameter table must contain exactly "
                        "3 columns: name, value, unit."
                    )

                name, value, unit = cells
                config["parameters"].append(
                    {
                        "name": name,
                        "value": parse_value(value),
                        "unit": unit,
                    }
                )

            if section == "evaluations" and line.startswith("|"):
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                if not cells or cells[0].lower() == "name":
                    continue
                if all(set(cell) <= set("-:") for cell in cells):
                    continue
                if len(cells) != 8:
                    raise ValueError(
                        "Evaluation table must contain exactly 8 columns: "
                        "name, dataset, x_expression, x_unit, x_column, "
                        "y_expression, y_unit, y_column."
                    )

                (
                    name,
                    dataset,
                    x_expression,
                    x_unit,
                    x_column,
                    y_expression,
                    y_unit,
                    y_column,
                ) = cells
                config["evaluations"].append(
                    {
                        "name": name,
                        "dataset": dataset,
                        "x_expression": x_expression,
                        "x_unit": x_unit,
                        "x_column": x_column,
                        "y_expression": y_expression,
                        "y_unit": y_unit,
                        "y_column": y_column,
                    }
                )

    validate_config(config)
    return config


def validate_config(config):
    required_model = (
        "input_file",
        "output_file",
        "evaluation_csv",
    )
    for key in required_model:
        if key not in config["model"]:
            raise ValueError(f"Missing model configuration: {key}")
        if not config["model"][key]:
            raise ValueError(f"Model configuration '{key}' cannot be empty.")

    solve_model = config["model"].get("solve_model", True)
    if not isinstance(solve_model, bool):
        raise ValueError("Model configuration 'solve_model' must be true or false.")
    if not solve_model and config["parameters"]:
        raise ValueError(
            "Parameter overrides require solve_model: true; refusing to "
            "evaluate saved solutions with potentially stale parameters."
        )

    names = set()
    for parameter in config["parameters"]:
        name = parameter["name"]
        if not name:
            raise ValueError("COMSOL parameter has an empty name.")
        if name in names:
            raise ValueError(f"Duplicate COMSOL parameter: {name}")
        names.add(name)

        if not isinstance(parameter["value"], (int, float)):
            raise ValueError(
                f"Parameter value for {name} must be numeric."
            )

    if not config["evaluations"]:
        raise ValueError("No COMSOL evaluations were configured.")

    evaluation_names = set()
    required_evaluation = (
        "name",
        "dataset",
        "x_expression",
        "x_column",
        "y_expression",
        "y_column",
    )
    for evaluation in config["evaluations"]:
        for key in required_evaluation:
            if not evaluation[key]:
                raise ValueError(
                    f"Evaluation has an empty required field: {key}."
                )
        if evaluation["x_column"] == evaluation["y_column"]:
            raise ValueError(
                f"Evaluation '{evaluation['name']}' must use distinct "
                "X and Y column names."
            )
        name = evaluation["name"]
        if name in evaluation_names:
            raise ValueError(f"Duplicate evaluation name: {name}")
        evaluation_names.add(name)


def evaluation_csv_paths(filename, evaluations):
    """Return one clearly labeled CSV path per configured evaluation."""
    csv_path = Path(filename)
    paths = {}
    used_suffixes = set()
    for evaluation in evaluations:
        name = evaluation["name"]
        suffix = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
        if not suffix:
            raise ValueError(
                f"Evaluation name '{name}' cannot be used in a CSV filename."
            )
        normalized_suffix = suffix.casefold()
        if normalized_suffix in used_suffixes:
            raise ValueError(
                "Evaluation names produce duplicate CSV filenames after "
                "sanitization."
            )
        used_suffixes.add(normalized_suffix)
        paths[name] = str(
            csv_path.with_name(
                f"{csv_path.stem}_{suffix}{csv_path.suffix}"
            )
        )
    return paths


def start_comsol(input_file):
    if not os.path.exists(input_file):
        raise FileNotFoundError(
            f"COMSOL model not found: {os.path.abspath(input_file)}"
        )

    import mph

    logger.info("Starting COMSOL backend...")
    client = mph.start()
    logger.info("Loading COMSOL model: %s", input_file)
    model = client.load(input_file)
    logger.info("Model loaded successfully.")
    return client, model


def set_comsol_parameters(model, parameters):
    available = model.parameters()
    for parameter in parameters:
        name = parameter["name"]
        if name not in available:
            raise ValueError(
                f"COMSOL parameter '{name}' was not found in the model."
            )

        value = parameter["value"]
        unit = parameter["unit"]
        expression = f"{value}[{unit}]" if unit else str(value)
        model.parameter(name, expression)
        logger.info("Set COMSOL parameter %s = %s", name, expression)


def save_evaluation_csv(filename, x_values, y_values, x_column, y_column):
    if len(x_values) != len(y_values):
        raise ValueError(
            f"Cannot save mismatched X/Y arrays: "
            f"{len(x_values)} vs {len(y_values)}."
        )
    Path(filename).parent.mkdir(parents=True, exist_ok=True)
    with open(filename, "w", newline="", encoding="utf-8") as output_file:
        writer = csv.writer(output_file)
        writer.writerow([x_column, y_column])
        writer.writerows(zip(x_values, y_values))
    logger.info("Saved evaluation data: %s", os.path.abspath(filename))


def run(config_file=CONFIG_FILE):
    config = parse_markdown_config(config_file)
    run_config(config)


def run_config(config):
    """Solve and export curves from an already-parsed configuration."""
    validate_config(config)
    configure_logging()
    model_config = config["model"]
    input_file = str(model_config["input_file"])
    output_file = str(model_config["output_file"])
    evaluation_csv = str(model_config["evaluation_csv"])

    logger.info("Input model: %s", input_file)
    logger.info("Output model: %s", output_file)
    logger.info(
        "Configured electrical curves: %s",
        ", ".join(item["name"] for item in config["evaluations"]),
    )
    solve_model = model_config.get("solve_model", True)
    evaluation_csvs = evaluation_csv_paths(
        evaluation_csv,
        config["evaluations"],
    )

    client = None
    try:
        client, model = start_comsol(input_file)
        set_comsol_parameters(model, config["parameters"])

        if solve_model:
            logger.info("Solving COMSOL model studies...")
            model.solve()
            logger.info("COMSOL solve completed.")
        else:
            logger.info(
                "Using saved COMSOL solutions; model.solve() is disabled "
                "by configuration."
            )

        from utils.comsol_utils import evaluate_dataset_xy

        for evaluation in config["evaluations"]:
            logger.info(
                "Evaluating %s on dataset %s",
                evaluation["name"],
                evaluation["dataset"],
            )
            x_values, y_values = evaluate_dataset_xy(
                model,
                evaluation["dataset"],
                evaluation["x_expression"],
                evaluation["x_unit"],
                evaluation["y_expression"],
                evaluation["y_unit"],
            )
            save_evaluation_csv(
                evaluation_csvs[evaluation["name"]],
                x_values,
                y_values,
                evaluation["x_column"],
                evaluation["y_column"],
            )

        if solve_model:
            output_parent = os.path.dirname(os.path.abspath(output_file))
            os.makedirs(output_parent, exist_ok=True)
            model.save(output_file)
            logger.info(
                "Saved solved COMSOL model: %s",
                os.path.abspath(output_file),
            )
    finally:
        if client is not None:
            client.clear()
            logger.info("COMSOL backend disconnected.")


if __name__ == "__main__":
    run()

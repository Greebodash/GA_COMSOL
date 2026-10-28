#!/usr/bin/env python3

import csv
from datetime import datetime
import logging
import os
import re
import sys
from pathlib import Path

import mph


CONFIG_FILE = "comsol_parameters.md"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            f"comsolrun_app_{datetime.now().strftime('%Y%m%d_%H%M_%f')}.log",
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

    config = {"model": {}, "parameters": []}
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

    validate_config(config)
    return config


def validate_config(config):
    required_model = (
        "input_file",
        "output_file",
        "evaluation_tag",
    )
    for key in required_model:
        if key not in config["model"]:
            raise ValueError(f"Missing model configuration: {key}")

    config["model"].setdefault("temporary_table_tag", "comsol_temp_table")

    if not config["parameters"]:
        raise ValueError("No COMSOL parameters were found.")

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


def parse_evaluation_tags(value):
    """Parse one or more comma-separated COMSOL evaluation tags."""
    tags = [tag.strip() for tag in value.split(",")]
    if any(not tag for tag in tags):
        raise ValueError("evaluation_tag must not contain empty tags.")
    if len(tags) != len(set(tags)):
        raise ValueError("evaluation_tag must not contain duplicate tags.")
    return tags


def evaluation_csv_paths(filename, tags):
    """Return one CSV path per tag, preserving the path for a single tag."""
    if len(tags) == 1:
        return {tags[0]: filename}

    csv_path = Path(filename)
    paths = {}
    used_suffixes = set()
    for tag in tags:
        suffix = re.sub(r"[^A-Za-z0-9._-]+", "_", tag).strip("._-")
        if not suffix:
            raise ValueError(
                f"Evaluation tag '{tag}' cannot be used in a CSV filename."
            )
        normalized_suffix = suffix.casefold()
        if normalized_suffix in used_suffixes:
            raise ValueError(
                "Evaluation tags produce duplicate CSV filenames after "
                "sanitization."
            )
        used_suffixes.add(normalized_suffix)
        paths[tag] = str(
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


def save_evaluation_csv(filename, frequency, transmission):
    with open(filename, "w", newline="", encoding="utf-8") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(["frequency_thz", "transmission"])
        writer.writerows(zip(frequency, transmission))
    logger.info("Saved evaluation data: %s", os.path.abspath(filename))


def run(config_file=CONFIG_FILE):
    config = parse_markdown_config(config_file)
    model_config = config["model"]
    input_file = str(model_config["input_file"])
    output_file = str(model_config["output_file"])
    evaluation_tags = parse_evaluation_tags(
        str(model_config["evaluation_tag"])
    )
    temporary_table_tag = str(model_config["temporary_table_tag"])
    evaluation_csv = str(
        model_config.get(
            "evaluation_csv",
            Path(output_file).with_suffix(".csv"),
        )
    )

    logger.info("Input model: %s", input_file)
    logger.info("Output model: %s", output_file)
    evaluation_csvs = evaluation_csv_paths(evaluation_csv, evaluation_tags)
    logger.info("Evaluation tags: %s", ", ".join(evaluation_tags))

    client = None
    try:
        client, model = start_comsol(input_file)
        set_comsol_parameters(model, config["parameters"])

        logger.info("Solving COMSOL model...")
        model.solve()
        logger.info("COMSOL solve completed.")

        from comsol_utils import extract_table

        for evaluation_tag in evaluation_tags:
            logger.info("Extracting evaluation tag: %s", evaluation_tag)
            frequency, transmission = extract_table(
                model,
                evaluation_tag=evaluation_tag,
                temporary_table_tag=temporary_table_tag,
            )
            save_evaluation_csv(
                evaluation_csvs[evaluation_tag],
                frequency,
                transmission,
            )

        output_parent = os.path.dirname(os.path.abspath(output_file))
        os.makedirs(output_parent, exist_ok=True)
        model.save(output_file)
        logger.info("Saved solved COMSOL model: %s", os.path.abspath(output_file))
    finally:
        if client is not None:
            client.clear()
            logger.info("COMSOL backend disconnected.")


if __name__ == "__main__":
    run()

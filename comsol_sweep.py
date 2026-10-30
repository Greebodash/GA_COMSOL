#!/usr/bin/env python3

from collections.abc import Mapping
import copy
from dataclasses import dataclass
import logging
import math
import numbers
import os
from pathlib import Path
import re
from typing import Callable, Dict, Optional

from comsol_run import (
    CONFIG_FILE,
    configure_logging,
    evaluation_csv_paths,
    parse_markdown_config,
    save_evaluation_csv,
    set_comsol_parameters,
    start_comsol,
    validate_config,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepRunResult:
    parameter_name: str
    parameter_value: numbers.Real
    model_path: Path
    evaluation_csvs: Dict[str, Path]


def _load_config(config):
    if isinstance(config, Mapping):
        config_data = copy.deepcopy(dict(config))
    elif isinstance(config, (str, os.PathLike)):
        config_data = parse_markdown_config(config)
    else:
        raise TypeError("config must be a config path or a Python mapping.")

    validate_config(config_data)
    return config_data


def _normalize_values(values):
    if isinstance(values, (str, bytes)):
        raise TypeError("Sweep values must be an iterable of numeric values.")

    try:
        values = list(values)
    except TypeError as error:
        raise TypeError(
            "Sweep values must be an iterable of numeric values."
        ) from error

    if not values:
        raise ValueError("At least one sweep value is required.")

    for value in values:
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            raise TypeError(
                f"Sweep values must be numeric; received {value!r}."
            )
        if not math.isfinite(value):
            raise ValueError(
                f"Sweep values must be finite; received {value!r}."
            )

    return values


def _filename_token(value):
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._-")
    return token or "value"


def _point_output_paths(model_filename, evaluation_filename, parameter_name,
                        value, index, evaluations):
    value_label = (
        format(value, ".15g") if isinstance(value, float) else str(value)
    )
    point_label = (
        f"{_filename_token(parameter_name)}_{index:04d}_"
        f"{_filename_token(value_label)}"
    )

    model_path = Path(model_filename)
    model_path = model_path.with_name(
        f"{model_path.stem}_{point_label}{model_path.suffix}"
    )

    evaluation_path = Path(evaluation_filename)
    evaluation_path = evaluation_path.with_name(
        f"{evaluation_path.stem}_{point_label}{evaluation_path.suffix}"
    )
    csv_paths = evaluation_csv_paths(evaluation_path, evaluations)
    return model_path, {
        name: Path(filename) for name, filename in csv_paths.items()
    }


def run_sweep(parameter_name, values, config=CONFIG_FILE, parameter_unit=None,
              progress_callback=None):
    """Solve and export one model for each value of a single parameter.

    Args:
        parameter_name: Name of the COMSOL model parameter to vary.
        values: Iterable of finite numeric values, consumed in iteration order.
        config: Existing Markdown config path or an already-parsed config
            mapping. Mapping inputs are copied and are not modified.
        parameter_unit: Optional unit expression for the swept parameter.
            Defaults to the unit in the config, or no unit when that parameter
            is not listed there.
        progress_callback: Optional callable invoked with each completed
            :class:`SweepRunResult`.

    Returns:
        A list of result records, one for each completed sweep value.
    """
    if not isinstance(parameter_name, str) or not parameter_name.strip():
        raise ValueError("parameter_name must be a non-empty string.")
    if parameter_unit is not None and not isinstance(parameter_unit, str):
        raise TypeError("parameter_unit must be a string or None.")
    if progress_callback is not None and not callable(progress_callback):
        raise TypeError("progress_callback must be callable.")

    sweep_values = _normalize_values(values)
    config_data = _load_config(config)
    model_config = config_data["model"]
    if model_config.get("solve_model", True) is not True:
        raise ValueError(
            "Parameter sweeps require solve_model: true; saved solutions "
            "cannot be reused for varying parameter values."
        )

    base_parameters = config_data["parameters"]
    matching_parameters = [
        item for item in base_parameters if item["name"] == parameter_name
    ]
    unit = parameter_unit
    if unit is None:
        unit = matching_parameters[0]["unit"] if matching_parameters else ""
    fixed_parameters = [
        item for item in base_parameters if item["name"] != parameter_name
    ]

    configure_logging()
    client = None
    results = []
    try:
        client, model = start_comsol(str(model_config["input_file"]))

        for index, value in enumerate(sweep_values, start=1):
            logger.info(
                "Running sweep point %s/%s: %s = %s",
                index,
                len(sweep_values),
                parameter_name,
                value,
            )
            parameters = copy.deepcopy(fixed_parameters)
            parameters.append(
                {"name": parameter_name, "value": value, "unit": unit}
            )
            set_comsol_parameters(model, parameters)

            model_path, csv_paths = _point_output_paths(
                model_config["output_file"],
                model_config["evaluation_csv"],
                parameter_name,
                value,
                index,
                config_data["evaluations"],
            )

            model.solve()

            from utils.comsol_utils import evaluate_dataset_xy

            for evaluation in config_data["evaluations"]:
                x_values, y_values = evaluate_dataset_xy(
                    model,
                    evaluation["dataset"],
                    evaluation["x_expression"],
                    evaluation["x_unit"],
                    evaluation["y_expression"],
                    evaluation["y_unit"],
                )
                save_evaluation_csv(
                    csv_paths[evaluation["name"]],
                    x_values,
                    y_values,
                    evaluation["x_column"],
                    evaluation["y_column"],
                )

            model_path.parent.mkdir(parents=True, exist_ok=True)
            model.save(str(model_path))
            result = SweepRunResult(
                parameter_name=parameter_name,
                parameter_value=value,
                model_path=model_path,
                evaluation_csvs=csv_paths,
            )
            results.append(result)
            logger.info("Completed sweep point; model saved to %s", model_path)
            if progress_callback is not None:
                progress_callback(result)
    finally:
        if client is not None:
            client.clear()

    return results


__all__ = ["SweepRunResult", "run_sweep"]

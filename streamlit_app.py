#!/usr/bin/env python3

import copy
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple, Union

import numpy as np

import comsol_run
from comsol_run import evaluation_csv_paths
from comsol_mobility_run import (
    DEFAULT_CONFIG_FILE,
    MobilityWorkflow,
    _read_comsol_curve,
    load_workflow,
    python_curve,
)


SWEEP_KEYS = ("Vg_start", "Vg_step", "Vg_finish")


def build_run_config(
    workflow: MobilityWorkflow,
    parameter_values: Mapping[str, float],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Apply Streamlit form values without mutating loaded defaults."""
    config = copy.deepcopy(workflow.config)
    #sweep = dict(workflow.sweep)

    configured_names = {item["name"] for item in config["parameters"]}
    if configured_names != set(parameter_values):
        raise ValueError(
            "The supplied values must match the configured COMSOL parameters."
        )

    for parameter in config["parameters"]:
        value = parameter_values[parameter["name"]]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(
                f"COMSOL parameter '{parameter['name']}' must be numeric."
            )
        if not math.isfinite(value):
            raise ValueError(
                f"COMSOL parameter '{parameter['name']}' must be finite."
            )
        parameter["value"] = value

    # if set(sweep_values) != set(SWEEP_KEYS):
    #     raise ValueError(
    #         "Sweep values must include Vg_start, Vg_step, and Vg_finish."
    #     )
    # for name in SWEEP_KEYS:
    #     value = sweep_values[name]
    #     if isinstance(value, bool) or not isinstance(value, (int, float)):
    #         raise TypeError(f"'{name}' must be numeric.")
    #     if not math.isfinite(value):
    #         raise ValueError(f"'{name}' must be finite.")
    #     sweep[name] = value

    # if sweep["Vg_step"] <= 0:
    #     raise ValueError("'Vg_step' must be greater than zero.")
    # if sweep["Vg_finish"] < sweep["Vg_start"]:
    #     raise ValueError(
    #         "'Vg_finish' must be greater than or equal to Vg_start."
    #     )

    parameters_by_name = {
        parameter["name"]: parameter
        for parameter in config["parameters"]
    }
    # for name in SWEEP_KEYS:
    #     parameters_by_name[name]["value"] = sweep[name]

    config["model"]["solve_model"] = True
    comsol_run.validate_config(config)
    return config


def load_comsol_curves(
    config: Dict[str, Any]
) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """Load each configured curve exported by a completed COMSOL run."""
    paths = evaluation_csv_paths(
        config["model"]["evaluation_csv"],
        config["evaluations"],
    )
    return {
        evaluation["name"]: _read_comsol_curve(
            paths[evaluation["name"]],
            evaluation,
        )
        for evaluation in config["evaluations"]
    }


def _number_step(value: float) -> float:
    return max(abs(value) * 0.1, 0.1)


def _curve_figure(
    title: str,
    curve: Tuple[np.ndarray, np.ndarray],
    x_label: str,
    y_label: str,
    python_preview: Optional[Tuple[np.ndarray, np.ndarray]] = None,
):
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots()
    axis.plot(curve[0], curve[1], label="COMSOL")
    if python_preview is not None:
        axis.plot(
            python_preview[0],
            python_preview[1],
            label="Python model",
            color="tab:orange",
        )
    axis.set_title(title)
    axis.set_xlabel(x_label)
    axis.set_ylabel(y_label)
    axis.grid(alpha=0.3)
    axis.legend()
    figure.tight_layout()
    return figure


def _render_curve(
    st,
    title: str,
    curve: Tuple[np.ndarray, np.ndarray],
    x_label: str,
    y_label: str,
    python_preview: Tuple[np.ndarray, np.ndarray] = None,
) -> None:
    import matplotlib.pyplot as plt

    figure = _curve_figure(
        title,
        curve,
        x_label,
        y_label,
        python_preview,
    )
    st.pyplot(figure, clear_figure=True)
    plt.close(figure)


def main(config_file: Union[str, Path] = DEFAULT_CONFIG_FILE) -> None:
    import streamlit as st

    st.set_page_config(page_title="MoS₂ mobility simulator", layout="wide")
    st.title("MoS₂ mobility simulator")
    st.caption(
        "Adjust the COMSOL parameters, solve the configured model, and inspect "
        "each exported curve separately."
    )
    workflow = load_workflow(config_file)
    st.write(f"COMSOL model: `{workflow.config['model']['input_file']}`")
    st.caption(
        "Vg_start, Vg_step, and Vg_finish are applied as COMSOL parameters. "
        "The model's Id(Vg) sweep must reference these parameters."
    )

    show_python = st.checkbox(
        "Also show the Python model preview",
        value=False,
        key="show_python_preview",
    )
    parameter_values = {}
    sweep_values = {}
    with st.form("mobility_parameters"):
        st.subheader("Mobility and device parameters")
        columns = st.columns(2)
        for index, parameter in enumerate(workflow.config["parameters"]):
            value = float(parameter["value"])
            unit = f" ({parameter['unit']})" if parameter["unit"] else ""
            parameter_values[parameter["name"]] = columns[
                index % len(columns)
            ].number_input(
                f"{parameter['name']}{unit}",
                value=value,
                step=_number_step(value),
                format="%.6g",
                key=f"parameter_{parameter['name']}",
            )

        # st.subheader("COMSOL Id(Vg) sweep parameters")
        # sweep_columns = st.columns(3)
        # for column, name in zip(sweep_columns, SWEEP_KEYS):
        #     value = float(workflow.sweep[name])
        #     sweep_values[name] = column.number_input(
        #         name,
        #         value=value,
        #         step=_number_step(value),
        #         format="%.6g",
        #         key=f"sweep_{name}",
        #     )
        #     parameter_values[name] = sweep_values[name]

        submitted = st.form_submit_button("Solve COMSOL")

    if submitted:
        st.session_state["mobility_last_run"] = None
        config = build_run_config(
            workflow,
            parameter_values,
        )
        with st.status("Running COMSOL solve...", expanded=True) as status:
            comsol_run.run_config(config)
            curves = load_comsol_curves(config)
            st.session_state["mobility_last_run"] = {
                "config": config,
                "curves": curves,
            }
            status.update(
                label="COMSOL solve complete.",
                state="complete",
                expanded=False,
            )

    last_run = st.session_state.get("mobility_last_run")
    if last_run is None:
        st.info("Submit the form to run COMSOL and display its curves.")
        return

    st.subheader("COMSOL results")
    preview_curve = None
    if show_python:
        preview_workflow = MobilityWorkflow(
            last_run["config"],
            workflow.preview,
        )
        preview_curve = python_curve(preview_workflow)

    for evaluation in last_run["config"]["evaluations"]:
        curve = last_run["curves"][evaluation["name"]]
        optional_preview = (
            preview_curve if evaluation["name"] == "id_vg" else None
        )
        _render_curve(
            st,
            evaluation["name"],
            curve,
            evaluation["x_column"],
            evaluation["y_column"],
            optional_preview,
        )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3

import copy
import csv
from io import StringIO
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np

import comsol_run
from comsol_run import evaluation_csv_paths
from comsol_mobility_run import (
    MobilityWorkflow,
    _read_comsol_curve,
    load_workflow,
    python_curve,
)


DEFAULT_STREAMLIT_CONFIG_FILE = "mobility_parameters_MoS2_default.md"
PARAMETER_CATEGORY_ORDER = (
    "Geometry & dimensions",
    "Material properties",
    "Mobility",
    "Doping & recombination",
    "Contacts",
    "Bias & sweep",
    "Other",
)


def group_parameters(
    parameters: Sequence[Mapping[str, Any]],
) -> Dict[str, List[Mapping[str, Any]]]:
    """Group configured parameters without hiding ungrouped entries."""
    groups: Dict[str, List[Mapping[str, Any]]] = {}
    for parameter in parameters:
        category = str(parameter.get("category", "")).strip() or "Other"
        groups.setdefault(category, []).append(parameter)

    ordered_categories = [
        category for category in PARAMETER_CATEGORY_ORDER if category in groups
    ]
    ordered_categories.extend(
        category for category in groups if category not in ordered_categories
    )
    return {category: groups[category] for category in ordered_categories}


def build_run_config(
    workflow: MobilityWorkflow,
    parameter_values: Mapping[str, float],
    model_file: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply Streamlit form values without mutating loaded defaults."""
    config = copy.deepcopy(workflow.config)
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

    if model_file is not None:
        model_file = model_file.strip()
        if not model_file:
            raise ValueError("COMSOL model file path cannot be empty.")
        config["model"]["input_file"] = model_file

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


def comparison_csv(
    current_curve: Tuple[np.ndarray, np.ndarray],
    previous_curve: Optional[Tuple[np.ndarray, np.ndarray]],
    x_column: str,
    y_column: str,
) -> str:
    """Create a current/previous CSV, matching previous samples by X value."""
    current_x, current_y = current_curve
    previous_values: List[Optional[float]] = [None] * len(current_x)
    if previous_curve is not None:
        previous_x, previous_y = previous_curve
        order = np.argsort(previous_x)
        sorted_x = previous_x[order]
        sorted_y = previous_y[order]
        for index, value in enumerate(current_x):
            position = int(np.searchsorted(sorted_x, value))
            candidates = [
                candidate
                for candidate in (position - 1, position)
                if 0 <= candidate < len(sorted_x)
            ]
            if not candidates:
                continue
            match = min(
                candidates,
                key=lambda candidate: abs(sorted_x[candidate] - value),
            )
            if np.isclose(sorted_x[match], value, rtol=1e-9, atol=1e-12):
                previous_values[index] = float(sorted_y[match])

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [x_column, f"current_{y_column}", f"previous_run_{y_column}"]
    )
    for index, (x_value, y_value) in enumerate(zip(current_x, current_y)):
        previous_value = previous_values[index]
        writer.writerow([
            x_value,
            y_value,
            "" if previous_value is None else previous_value,
        ])
    return output.getvalue()


def _number_step(value: float) -> float:
    return max(abs(value) * 0.1, 0.1)


def _reset_form_values(
    session_state,
    parameters: Sequence[Mapping[str, Any]],
    model_file: str,
) -> None:
    """Restore the form widgets from the loaded configuration defaults."""
    session_state["comsol_model_file"] = model_file
    for parameter in parameters:
        session_state[f"parameter_{parameter['name']}"] = float(
            parameter["value"]
        )


def _curve_figure(
    title: str,
    curve: Tuple[np.ndarray, np.ndarray],
    x_label: str,
    y_label: str,
    python_preview: Optional[Tuple[np.ndarray, np.ndarray]] = None,
    previous_curve: Optional[Tuple[np.ndarray, np.ndarray]] = None,
):
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots()
    axis.plot(curve[0], curve[1], label="COMSOL")
    if previous_curve is not None:
        axis.plot(
            previous_curve[0],
            previous_curve[1],
            label="Previous COMSOL run",
            linestyle="--",
        )
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
    previous_curve: Optional[Tuple[np.ndarray, np.ndarray]] = None,
) -> None:
    import matplotlib.pyplot as plt

    figure = _curve_figure(
        title,
        curve,
        x_label,
        y_label,
        python_preview,
        previous_curve,
    )
    st.pyplot(figure, clear_figure=True)
    plt.close(figure)


def main(
    config_file: Union[str, Path] = DEFAULT_STREAMLIT_CONFIG_FILE,
) -> None:
    import streamlit as st

    st.set_page_config(page_title="COMSOL mobility simulator", layout="wide")
    st.title("COMSOL mobility simulator")
    st.caption(
        "Adjust the COMSOL parameters, solve the configured model, and inspect "
        "each exported curve separately."
    )
    workflow = load_workflow(config_file)
    st.session_state.setdefault(
        "comsol_model_file",
        str(workflow.config["model"]["input_file"]),
    )
    for parameter in workflow.config["parameters"]:
        st.session_state.setdefault(
            f"parameter_{parameter['name']}",
            float(parameter["value"]),
        )

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
    with st.form("mobility_parameters"):
        model_file = st.text_input(
            "COMSOL model file",
            help="Path to the .mph model file, relative to the project root or absolute.",
            key="comsol_model_file",
        )
        st.subheader("Mobility and device parameters")
        for category, parameters in group_parameters(
            workflow.config["parameters"]
        ).items():
            with st.expander(category):
                columns = st.columns(2)
                for index, parameter in enumerate(parameters):
                    value = float(parameter["value"])
                    unit = (
                        f" ({parameter['unit']})" if parameter["unit"] else ""
                    )
                    description = parameter.get("description")
                    parameter_values[parameter["name"]] = columns[
                        index % len(columns)
                    ].number_input(
                        f"{parameter['name']}{unit}",
                        step=_number_step(value),
                        format="%.6g",
                        key=f"parameter_{parameter['name']}",
                        help=description or None,
                    )

        submitted = st.form_submit_button("Solve COMSOL")

    st.button(
        "Reset to defaults",
        on_click=_reset_form_values,
        args=(
            st.session_state,
            workflow.config["parameters"],
            str(workflow.config["model"]["input_file"]),
        ),
        help="Restore the parameter values and model file from the default config.",
    )

    if submitted:
        config = build_run_config(
            workflow,
            parameter_values,
            model_file,
        )
        with st.status("Running COMSOL solve...", expanded=True) as status:
            comsol_run.run_config_in_subprocess(config)
            curves = load_comsol_curves(config)
            previous_run = st.session_state.get("mobility_last_run")
            st.session_state["mobility_previous_run"] = previous_run
            st.session_state["mobility_run_number"] = (
                st.session_state.get("mobility_run_number", 0) + 1
            )
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

    previous_run = st.session_state.get("mobility_previous_run")
    compare_last_run = False
    if previous_run is not None:
        compare_last_run = st.checkbox(
            "Compare with previous successful run",
            value=True,
            key=(
                "mobility_compare_last_run_"
                f"{st.session_state['mobility_run_number']}"
            ),
        )

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
        previous_curve = None
        if (
            compare_last_run
            and evaluation["name"] in previous_run["curves"]
        ):
            previous_curve = previous_run["curves"][evaluation["name"]]
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
            previous_curve,
        )
        comparison_data = comparison_csv(
            curve,
            previous_run["curves"].get(evaluation["name"])
            if previous_run is not None
            else None,
            evaluation["x_column"],
            evaluation["y_column"],
        )
        csv_stem = Path(
            evaluation_csv_paths(
                last_run["config"]["model"]["evaluation_csv"],
                last_run["config"]["evaluations"],
            )[evaluation["name"]]
        ).stem
        st.download_button(
            f"Download {evaluation['name']} CSV",
            data=comparison_data,
            file_name=f"{csv_stem}_comparison.csv",
            mime="text/csv",
            key=f"download_{evaluation['name']}_csv",
        )


if __name__ == "__main__":
    main()

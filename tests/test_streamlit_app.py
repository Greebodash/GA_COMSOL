import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from streamlit.testing.v1 import AppTest

from comsol_mobility_run import load_workflow
import comsol_run
from comsol_run import evaluation_csv_paths, parse_markdown_config
from streamlit_app import (
    _curve_figure,
    build_run_config,
    comparison_csv,
    DEFAULT_STREAMLIT_CONFIG_FILE,
    group_parameters,
    load_comsol_curves,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "mobility_parameters_MoS2.md"
DEFAULT_CONFIG_FILE = ROOT / "mobility_parameters_MoS2_default.md"


class FakeClient:
    def __init__(self):
        self.clear_calls = 0

    def clear(self):
        self.clear_calls += 1


class FakeModel:
    def __init__(self, parameters):
        self.available = set(parameters)
        self.parameters_set = {}
        self.solve_calls = 0
        self.saved_path = None

    def parameters(self):
        return self.available

    def parameter(self, name, expression):
        self.parameters_set[name] = expression

    def solve(self):
        self.solve_calls += 1

    def save(self, filename):
        self.saved_path = filename


class StreamlitMobilityAppTests(unittest.TestCase):
    def test_app_renders_parameter_controls_without_solving(self):
        app = AppTest.from_file(str(ROOT / "streamlit_app.py")).run()

        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "MoS₂ mobility simulator")
        self.assertEqual(
            [button.label for button in app.button],
            ["Solve COMSOL", "Reset to defaults"],
        )
        self.assertEqual(
            app.text_input[0].label,
            "COMSOL model file",
        )
        self.assertEqual(
            app.text_input[0].value,
            "models/MoS2_ML_template.mph",
        )
        self.assertEqual(
            len(app.number_input),
            len(load_workflow(DEFAULT_CONFIG_FILE).config["parameters"]),
        )
        self.assertTrue(any(
            checkbox.label == "Also show the Python model preview"
            for checkbox in app.checkbox
        ))
        default_config = parse_markdown_config(DEFAULT_CONFIG_FILE)
        default_parameters = {
            parameter["name"]: parameter
            for parameter in default_config["parameters"]
        }
        source_withdescriptions = parse_markdown_config(
            ROOT / "mobility_parameters_MoS2withdefos.md"
        )
        source_parameters = {
            parameter["name"]: parameter
            for parameter in source_withdescriptions["parameters"]
        }
        self.assertEqual(
            {
                name: parameter["value"]
                for name, parameter in default_parameters.items()
            },
            {
                name: parameter["value"]
                for name, parameter in source_parameters.items()
                if name != "WF_contact"
            },
        )
        self.assertNotIn("WF_contact", default_parameters)
        self.assertEqual(DEFAULT_STREAMLIT_CONFIG_FILE, DEFAULT_CONFIG_FILE.name)

    def test_reset_button_restores_parameter_and_model_defaults(self):
        app = AppTest.from_file(str(ROOT / "streamlit_app.py")).run()
        alpha_widget = next(
            widget
            for widget in app.number_input
            if widget.key == "parameter_alpha_mu"
        )
        alpha_default = float(
            next(
                parameter["value"]
                for parameter in load_workflow(
                    DEFAULT_CONFIG_FILE
                ).config["parameters"]
                if parameter["name"] == "alpha_mu"
            )
        )

        alpha_widget.set_value(3.0)
        app.text_input[0].set_value("models/alternate.mph")
        app.button[1].click().run()

        self.assertFalse(app.exception)
        alpha_widget = next(
            widget
            for widget in app.number_input
            if widget.key == "parameter_alpha_mu"
        )
        self.assertEqual(alpha_widget.value, alpha_default)
        self.assertEqual(
            app.text_input[0].value,
            "models/MoS2_ML_template.mph",
        )

    def test_expanded_config_contains_grouped_parameter_union(self):
        config = parse_markdown_config(CONFIG_FILE)
        parameters = {
            parameter["name"]: parameter for parameter in config["parameters"]
        }

        self.assertIn("mu_hole", parameters)
        self.assertIn("WF_gold", parameters)
        self.assertEqual(
            parameters["mu_hole"]["category"],
            "Mobility",
        )
        self.assertIn("Hole mobility", parameters["mu_hole"]["description"])
        self.assertEqual(
            parameters["WF_gold"]["category"],
            "Contacts",
        )

    def test_parameter_groups_follow_current_rows_and_keep_uncategorized(self):
        parameters = [
            {"name": "first", "category": "Mobility"},
            {"name": "new_parameter", "value": 2},
        ]

        groups = group_parameters(parameters)

        self.assertEqual(list(groups), ["Mobility", "Other"])
        self.assertEqual([p["name"] for p in groups["Other"]], ["new_parameter"])

    def test_parameter_file_edits_change_loaded_parameters_and_groups(self):
        source = CONFIG_FILE.read_text(encoding="utf-8")
        source = source.replace(
            "| WF_gold | 5.1 | V | Contacts | Gold work function, for barrier "
            "estimates; unused while WF_contact = 0. |",
            "",
        )
        source = source.replace(
            "| Vg_finish | 20 | V | Bias & sweep | Last gate voltage of the "
            "Id(Vg) sweep. |",
            "| added_parameter | 3 | V |"
            "\n| Vg_finish | 20 | V | Bias & sweep | Last gate voltage of "
            "the Id(Vg) sweep. |",
        )
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "edited.md"
            config_file.write_text(source, encoding="utf-8")
            parameters = parse_markdown_config(config_file)["parameters"]

        names = {parameter["name"] for parameter in parameters}
        groups = group_parameters(parameters)
        self.assertNotIn("WF_gold", names)
        self.assertIn("added_parameter", names)
        self.assertEqual(
            [parameter["name"] for parameter in groups["Other"]],
            ["added_parameter"],
        )

    def test_parser_accepts_legacy_inline_parameter_comments(self):
        markdown = """\
## Model
- input_file: model.mph
- output_file: output.mph
- evaluation_csv: results.csv

## Parameters
| name | value | unit |
|---|---:|---|
| alpha_mu | 2 | | # mobility exponent |

## Evaluations
| name | dataset | x_expression | x_unit | x_column | y_expression | y_unit | y_column |
|---|---|---|---|---|---|---|---|
| id_vg | Solution 1 | Vg | V | bias_voltage_v | Id | A | drain_current_a |
"""
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "config.md"
            config_file.write_text(markdown, encoding="utf-8")

            config = parse_markdown_config(config_file)

        self.assertEqual(
            config["parameters"][0]["description"],
            "mobility exponent",
        )

    def test_app_accepts_two_successive_solve_submissions(self):
        app = AppTest.from_file(str(ROOT / "streamlit_app.py")).run()
        first_curves = {
            "id_vg": (
                np.asarray([0.0, 0.5]),
                np.asarray([1e-9, 2e-9]),
            )
        }
        second_curves = {
            "id_vg": (
                np.asarray([0.0, 0.5]),
                np.asarray([3e-9, 4e-9]),
            )
        }

        with (
            patch.object(comsol_run, "run_config_in_subprocess") as run_config,
            patch(
                "comsol_mobility_run._read_comsol_curve",
                side_effect=[
                    first_curves["id_vg"],
                    second_curves["id_vg"],
                ],
            ),
        ):
            for _ in range(2):
                app.button[0].click().run()
                self.assertFalse(app.exception)

        self.assertEqual(run_config.call_count, 2)
        self.assertIn(
            "Compare with previous successful run",
            [checkbox.label for checkbox in app.checkbox],
        )
        self.assertTrue(app.checkbox[-1].value)
        previous_run = app.session_state["mobility_previous_run"]
        np.testing.assert_array_equal(
            previous_run["curves"]["id_vg"][1],
            first_curves["id_vg"][1],
        )

    def test_comparison_csv_aligns_previous_values_by_x(self):
        output = comparison_csv(
            (
                np.asarray([0.0, 0.5, 1.0]),
                np.asarray([0.0, 2.0, 3.0]),
            ),
            (
                np.asarray([0.0, 0.5]),
                np.asarray([1.0, 2.0]),
            ),
            "bias_voltage_v",
            "drain_current_a",
        )
        rows = list(csv.reader(output.splitlines()))

        self.assertEqual(
            rows[0],
            [
                "bias_voltage_v",
                "current_drain_current_a",
                "previous_run_drain_current_a",
            ],
        )
        self.assertEqual(rows[1], ["0.0", "0.0", "1.0"])
        self.assertEqual(rows[2], ["0.5", "2.0", "2.0"])
        self.assertEqual(rows[3], ["1.0", "3.0", ""])

    def test_previous_curve_is_rendered_as_overlay(self):
        figure = _curve_figure(
            "id_vg",
            (np.asarray([0.0, 0.5]), np.asarray([1.0, 2.0])),
            "bias_voltage_v",
            "drain_current_a",
            previous_curve=(
                np.asarray([0.0, 0.5]),
                np.asarray([0.5, 1.5]),
            ),
        )
        try:
            lines = figure.axes[0].lines
            self.assertEqual(
                [line.get_label() for line in lines],
                ["COMSOL", "Previous COMSOL run"],
            )
            np.testing.assert_array_equal(lines[1].get_ydata(), [0.5, 1.5])
        finally:
            import matplotlib.pyplot as plt

            plt.close(figure)

    def test_app_uses_selected_model_file_for_the_solve(self):
        app = AppTest.from_file(str(ROOT / "streamlit_app.py")).run()
        curves = {
            "id_vg": (
                np.asarray([0.0, 0.5]),
                np.asarray([1e-9, 2e-9]),
            )
        }

        with (
            patch.object(comsol_run, "run_config_in_subprocess") as run_config,
            patch("streamlit_app.load_comsol_curves", return_value=curves),
            patch("streamlit_app._render_curve"),
        ):
            app.text_input[0].set_value("models/alternate.mph")
            app.button[0].click().run()

        self.assertFalse(app.exception)
        run_config.assert_called_once()
        self.assertEqual(
            run_config.call_args.args[0]["model"]["input_file"],
            "models/alternate.mph",
        )

    def test_run_config_in_subprocess_passes_config_and_checks_exit(self):
        config = {"model": {"solve_model": True}, "parameters": []}

        with patch("comsol_run.subprocess.run") as run:
            comsol_run.run_config_in_subprocess(config)

        args, kwargs = run.call_args
        self.assertEqual(args[0][:1], [comsol_run.sys.executable])
        self.assertEqual(json.loads(kwargs["input"]), config)
        self.assertTrue(kwargs["text"])
        self.assertTrue(kwargs["check"])

    def test_build_run_config_applies_form_values_without_mutating_defaults(self):
        workflow = load_workflow(CONFIG_FILE)
        original_first_value = workflow.config["parameters"][0]["value"]
        parameter_values = {
            parameter["name"]: float(parameter["value"]) + 1
            for parameter in workflow.config["parameters"]
        }

        config = build_run_config(
            workflow,
            parameter_values,
        )

        self.assertTrue(config["model"]["solve_model"])
        self.assertEqual(
            config["model"]["input_file"],
            "models/MoS2_ML_template.mph",
        )
        self.assertEqual(
            {
                parameter["name"]: parameter["value"]
                for parameter in config["parameters"]
            },
            parameter_values,
        )
        self.assertEqual(
            workflow.config["parameters"][0]["value"],
            original_first_value,
        )

        selected_model = "models/alternate.mph"
        selected_config = build_run_config(
            workflow,
            parameter_values,
            selected_model,
        )
        self.assertEqual(
            selected_config["model"]["input_file"],
            selected_model,
        )
        self.assertEqual(
            workflow.config["model"]["input_file"],
            "models/MoS2_ML_template.mph",
        )

    def test_build_run_config_rejects_empty_model_file(self):
        workflow = load_workflow(CONFIG_FILE)
        parameter_values = {
            parameter["name"]: float(parameter["value"])
            for parameter in workflow.config["parameters"]
        }

        with self.assertRaisesRegex(ValueError, "model file path"):
            build_run_config(workflow, parameter_values, "  ")

    def test_build_run_config_rejects_non_finite_parameter(self):
        workflow = load_workflow(CONFIG_FILE)
        parameter_values = {
            parameter["name"]: float(parameter["value"])
            for parameter in workflow.config["parameters"]
        }
        parameter_values["mu_C0"] = float("inf")

        with self.assertRaisesRegex(ValueError, "finite"):
            build_run_config(
                workflow,
                parameter_values,
            )

    def test_load_comsol_curves_reads_each_configured_evaluation(self):
        workflow = load_workflow(CONFIG_FILE)
        config = workflow.config
        with tempfile.TemporaryDirectory() as directory:
            config["model"]["evaluation_csv"] = str(
                Path(directory) / "curve.csv"
            )
            paths = evaluation_csv_paths(
                config["model"]["evaluation_csv"],
                config["evaluations"],
            )
            for path in paths.values():
                with open(path, "w", newline="", encoding="utf-8") as output:
                    writer = csv.writer(output)
                    writer.writerow(["bias_voltage_v", "drain_current_a"])
                    writer.writerows([(0, 1e-9), (0.5, 2e-9)])

            curves = load_comsol_curves(config)

        self.assertEqual(set(curves), {"id_vg"})
        np.testing.assert_array_equal(curves["id_vg"][0], [0, 0.5])
        np.testing.assert_array_equal(curves["id_vg"][1], [1e-9, 2e-9])

    def test_comsol_runner_can_solve_twice_with_same_client(self):
        workflow = load_workflow(CONFIG_FILE)
        config = build_run_config(
            workflow,
            {
                parameter["name"]: float(parameter["value"])
                for parameter in workflow.config["parameters"]
            },
        )
        client = FakeClient()
        model = FakeModel(
            parameter["name"] for parameter in config["parameters"]
        )
        with tempfile.TemporaryDirectory() as directory:
            config["model"]["output_file"] = str(
                Path(directory) / "solved.mph"
            )
            config["model"]["evaluation_csv"] = str(
                Path(directory) / "curve.csv"
            )
            with (
                patch.object(comsol_run, "configure_logging"),
                patch.object(
                    comsol_run,
                    "start_comsol",
                    return_value=(client, model),
                ) as start_comsol,
                patch(
                    "utils.comsol_utils.evaluate_dataset_xy",
                    return_value=([0, 0.5], [1e-9, 2e-9]),
                ),
            ):
                comsol_run.run_config(config)
                comsol_run.run_config(config)

            self.assertEqual(model.solve_calls, 2)
            self.assertEqual(model.saved_path, config["model"]["output_file"])
            self.assertEqual(start_comsol.call_count, 2)
            self.assertEqual(client.clear_calls, 2)
            self.assertEqual(
                model.parameters_set["mu_C0"],
                "1.58[cm^2/(V*s)]",
            )
            curves = load_comsol_curves(config)

        np.testing.assert_array_equal(curves["id_vg"][0], [0, 0.5])
        np.testing.assert_array_equal(curves["id_vg"][1], [1e-9, 2e-9])


if __name__ == "__main__":
    unittest.main()

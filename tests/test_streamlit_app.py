import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from streamlit.testing.v1 import AppTest

from comsol_mobility_run import load_workflow
import comsol_run
from comsol_run import evaluation_csv_paths
from streamlit_app import build_run_config, load_comsol_curves


ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "mobility_parameters.md"


class FakeClient:
    def __init__(self):
        self.cleared = False

    def clear(self):
        self.cleared = True


class FakeModel:
    def __init__(self, parameters):
        self.available = set(parameters)
        self.parameters_set = {}
        self.solved = False
        self.saved_path = None

    def parameters(self):
        return self.available

    def parameter(self, name, expression):
        self.parameters_set[name] = expression

    def solve(self):
        self.solved = True

    def save(self, filename):
        self.saved_path = filename


class StreamlitMobilityAppTests(unittest.TestCase):
    def test_app_renders_parameter_controls_without_solving(self):
        app = AppTest.from_file(str(ROOT / "streamlit_app.py")).run()

        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "MoS₂ mobility simulator")
        self.assertEqual(app.button[0].label, "Solve COMSOL")
        self.assertTrue(any(
            checkbox.label == "Also show the Python model preview"
            for checkbox in app.checkbox
        ))

    def test_build_run_config_applies_form_values_without_mutating_defaults(self):
        workflow = load_workflow(CONFIG_FILE)
        original_first_value = workflow.config["parameters"][0]["value"]
        parameter_values = {
            parameter["name"]: float(parameter["value"]) + 1
            for parameter in workflow.config["parameters"]
        }
        sweep_values = {
            "Vg_start": 1.0,
            "Vg_step": 0.25,
            "Vg_finish": 10.0,
        }

        config, sweep = build_run_config(
            workflow,
            parameter_values,
            sweep_values,
        )

        self.assertTrue(config["model"]["solve_model"])
        self.assertEqual(sweep, sweep_values)
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
        self.assertEqual(workflow.sweep["Vg_start"], 0)

    def test_build_run_config_rejects_invalid_sweep(self):
        workflow = load_workflow(CONFIG_FILE)
        parameter_values = {
            parameter["name"]: float(parameter["value"])
            for parameter in workflow.config["parameters"]
        }

        with self.assertRaisesRegex(ValueError, "Vg_step"):
            build_run_config(
                workflow,
                parameter_values,
                {"Vg_start": 0, "Vg_step": 0, "Vg_finish": 10},
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

    def test_comsol_runner_accepts_ui_config_and_solves(self):
        workflow = load_workflow(CONFIG_FILE)
        config, _ = build_run_config(
            workflow,
            {
                parameter["name"]: float(parameter["value"])
                for parameter in workflow.config["parameters"]
            },
            workflow.sweep,
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
                ),
                patch(
                    "utils.comsol_utils.evaluate_dataset_xy",
                    return_value=([0, 0.5], [1e-9, 2e-9]),
                ),
            ):
                comsol_run.run_config(config)

            self.assertTrue(model.solved)
            self.assertEqual(model.saved_path, config["model"]["output_file"])
            self.assertTrue(client.cleared)
            self.assertEqual(
                model.parameters_set["mu_C0"],
                "1.58[cm^2/(V*s)]",
            )
            curves = load_comsol_curves(config)

        np.testing.assert_array_equal(curves["id_vg"][0], [0, 0.5])
        np.testing.assert_array_equal(curves["id_vg"][1], [1e-9, 2e-9])


if __name__ == "__main__":
    unittest.main()

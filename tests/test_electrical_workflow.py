import csv
from pathlib import Path
import tempfile
import unittest

from comsol_run import (
    evaluation_csv_paths,
    parse_markdown_config,
    save_evaluation_csv,
    validate_config,
)
from utils.comsol_utils import evaluate_dataset_xy


ROOT = Path(__file__).resolve().parents[1]


class ArrayLike:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


class FakeModel:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def evaluate(self, expression, unit=None, dataset=None):
        self.calls.append((expression, unit, dataset))
        return ArrayLike(self.values[expression])


class ElectricalWorkflowTests(unittest.TestCase):
    def test_example_config_targets_both_electrical_curves(self):
        config = parse_markdown_config(ROOT / "comsol_parameters.md")

        self.assertEqual(config["model"]["input_file"], "models/MoS2_ML.mph")
        self.assertEqual(
            [evaluation["name"] for evaluation in config["evaluations"]],
            ["id_vd", "id_vg"],
        )
        self.assertEqual(
            [evaluation["dataset"] for evaluation in config["evaluations"]],
            [
                "Id(Vd)//Parametric Solutions 1",
                "Id(Vg)//Parametric Solutions 2",
            ],
        )
        self.assertFalse(config["model"]["solve_model"])
        self.assertEqual(
            [evaluation["x_expression"] for evaluation in config["evaluations"]],
            ["Vd", "Vg"],
        )
        self.assertTrue(all(
            evaluation["y_expression"] == "semi.I0_1"
            for evaluation in config["evaluations"]
        ))

    def test_evaluation_paths_are_named_per_curve(self):
        paths = evaluation_csv_paths(
            "results/mos2_4l.csv",
            [{"name": "id_vd"}, {"name": "id_vg"}],
        )

        self.assertEqual(
            paths,
            {
                "id_vd": "results/mos2_4l_id_vd.csv",
                "id_vg": "results/mos2_4l_id_vg.csv",
            },
        )

    def test_duplicate_sanitized_evaluation_names_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate CSV filenames"):
            evaluation_csv_paths(
                "results/current.csv",
                [{"name": "id vd"}, {"name": "id_vd"}],
            )

    def test_saved_solution_mode_rejects_parameter_overrides(self):
        config = parse_markdown_config(ROOT / "comsol_parameters.md")
        config["parameters"].append(
            {"name": "Vd", "value": 0.1, "unit": "V"}
        )

        with self.assertRaisesRegex(ValueError, "overrides require"):
            validate_config(config)

    def test_dataset_evaluation_uses_units_and_returns_matching_arrays(self):
        model = FakeModel(
            {
                "Vd": [0.0, 0.5, 1.0],
                "semi.I0_1": [0.0, 1e-9, 2e-9],
            }
        )

        x_values, y_values = evaluate_dataset_xy(
            model,
            "dset2",
            "Vd",
            "V",
            "semi.I0_1",
            "A",
        )

        self.assertEqual(x_values, [0.0, 0.5, 1.0])
        self.assertEqual(y_values, [0.0, 1e-9, 2e-9])
        self.assertEqual(
            model.calls,
            [
                ("Vd", "V", "dset2"),
                ("semi.I0_1", "A", "dset2"),
            ],
        )

    def test_dataset_evaluation_rejects_mismatched_results(self):
        model = FakeModel({"Vd": [0.0, 0.5], "semi.I0_1": [1e-9]})

        with self.assertRaisesRegex(ValueError, "mismatched X/Y lengths"):
            evaluate_dataset_xy(
                model,
                "dset2",
                "Vd",
                "V",
                "semi.I0_1",
                "A",
            )

    def test_dataset_evaluation_rejects_non_finite_values(self):
        model = FakeModel({"Vd": [0.0, float("nan")], "semi.I0_1": [1e-9, 2e-9]})

        with self.assertRaisesRegex(ValueError, "non-finite"):
            evaluate_dataset_xy(
                model,
                "dset2",
                "Vd",
                "V",
                "semi.I0_1",
                "A",
            )

    def test_csv_uses_configured_columns_and_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "curve.csv"
            save_evaluation_csv(
                path,
                [0.0, 0.5],
                [0.0, 1e-9],
                "bias_voltage_v",
                "drain_current_a",
            )

            with path.open(newline="", encoding="utf-8") as output_file:
                rows = list(csv.reader(output_file))

        self.assertEqual(rows[0], ["bias_voltage_v", "drain_current_a"])
        self.assertEqual(rows[1], ["0.0", "0.0"])
        self.assertEqual(rows[2], ["0.5", "1e-09"])

    def test_csv_rejects_mismatched_arrays(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "curve.csv"

            with self.assertRaisesRegex(ValueError, "mismatched X/Y arrays"):
                save_evaluation_csv(
                    path,
                    [0.0, 0.5],
                    [1e-9],
                    "bias_voltage_v",
                    "drain_current_a",
                )


if __name__ == "__main__":
    unittest.main()

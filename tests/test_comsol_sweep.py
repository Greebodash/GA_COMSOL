from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from comsol_sweep import run_sweep


class FakeClient:
    def __init__(self):
        self.cleared = False

    def clear(self):
        self.cleared = True


class FakeModel:
    def __init__(self):
        self.parameters_set = {}
        self.solves = []
        self.allowed_parameters = {"A", "B"}

    def parameters(self):
        return self.allowed_parameters

    def parameter(self, name, expression):
        self.parameters_set[name] = expression

    def solve(self):
        self.solves.append(dict(self.parameters_set))

    def save(self, filename):
        Path(filename).write_text("solved model", encoding="utf-8")


class ComsolSweepTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.config = {
            "model": {
                "input_file": str(root / "input.mph"),
                "output_file": str(root / "models" / "solved.mph"),
                "evaluation_csv": str(root / "results" / "curve.csv"),
                "solve_model": True,
            },
            "parameters": [
                {"name": "A", "value": 0, "unit": "V"},
                {"name": "B", "value": 5, "unit": "V"},
            ],
            "evaluations": [
                {
                    "name": "transfer curve",
                    "dataset": "dset1",
                    "x_expression": "Vg",
                    "x_unit": "V",
                    "x_column": "gate_voltage_v",
                    "y_expression": "Id",
                    "y_unit": "A",
                    "y_column": "drain_current_a",
                }
            ],
        }

    def _evaluate(self, model, dataset, x_expression, x_unit,
                  y_expression, y_unit):
        value = float(model.parameters_set["A"].split("[", 1)[0])
        return [value], [value * 2]

    def _mock_runtime(self):
        client = FakeClient()
        model = FakeModel()
        start = patch(
            "comsol_sweep.start_comsol",
            return_value=(client, model),
        )
        evaluate = patch(
            "utils.comsol_utils.evaluate_dataset_xy",
            side_effect=self._evaluate,
        )
        logging = patch("comsol_sweep.configure_logging")
        return client, model, start, evaluate, logging

    def test_sweep_accepts_iterable_config_mapping_and_returns_outputs(self):
        original_config = deepcopy(self.config)
        callbacks = []
        client, model, start, evaluate, logging = self._mock_runtime()

        with start as start_comsol, evaluate, logging:
            results = run_sweep(
                "A",
                (value for value in [1, 2, 2]),
                config=self.config,
                progress_callback=callbacks.append,
            )

        self.assertEqual(self.config, original_config)
        self.assertEqual(
            [result.parameter_value for result in results],
            [1, 2, 2],
        )
        self.assertEqual(callbacks, results)
        self.assertEqual(len(model.solves), 3)
        self.assertEqual(
            [solve["A"] for solve in model.solves],
            ["1[V]", "2[V]", "2[V]"],
        )
        self.assertTrue(all(solve["B"] == "5[V]" for solve in model.solves))
        self.assertEqual(len({result.model_path for result in results}), 3)
        for result in results:
            self.assertTrue(result.model_path.is_file())
            csv_path = result.evaluation_csvs["transfer curve"]
            self.assertTrue(csv_path.is_file())
            self.assertEqual(
                csv_path.read_text(encoding="utf-8").splitlines()[0],
                "gate_voltage_v,drain_current_a",
            )
        start_comsol.assert_called_once()
        self.assertTrue(client.cleared)

    def test_sweep_accepts_markdown_config_path_and_explicit_unit(self):
        root = Path(self.temp_dir.name)
        config_path = root / "sweep.md"
        config_path.write_text(
            "\n".join(
                [
                    "## Model",
                    f"- input_file: {self.config['model']['input_file']}",
                    f"- output_file: {self.config['model']['output_file']}",
                    "- evaluation_csv: "
                    f"{self.config['model']['evaluation_csv']}",
                    "- solve_model: true",
                    "",
                    "## Parameters",
                    "| name | value | unit |",
                    "|---|---:|---|",
                    "| B | 5 | V |",
                    "",
                    "## Evaluations",
                    "| name | dataset | x_expression | x_unit | x_column | "
                    "y_expression | y_unit | y_column |",
                    "|---|---|---|---|---|---|---|---|",
                    "| curve | dset1 | Vg | V | gate_voltage_v | Id | A | "
                    "drain_current_a |",
                ]
            ),
            encoding="utf-8",
        )
        client, model, start, evaluate, logging = self._mock_runtime()

        with start, evaluate, logging:
            results = run_sweep(
                "A",
                [3],
                config=config_path,
                parameter_unit="mV",
            )

        self.assertEqual(model.solves[0]["A"], "3[mV]")
        self.assertEqual(model.solves[0]["B"], "5[V]")
        self.assertEqual(len(results), 1)
        self.assertTrue(client.cleared)

    def test_sweep_rejects_empty_or_non_numeric_values_before_loading(self):
        client, model, start, evaluate, logging = self._mock_runtime()
        with start as start_comsol, evaluate, logging:
            with self.assertRaisesRegex(ValueError, "At least one"):
                run_sweep("A", [], config=self.config)
            with self.assertRaisesRegex(TypeError, "must be numeric"):
                run_sweep("A", [1, True], config=self.config)
            with self.assertRaisesRegex(ValueError, "must be finite"):
                run_sweep("A", [float("inf")], config=self.config)

        start_comsol.assert_not_called()
        self.assertFalse(client.cleared)

    def test_sweep_rejects_saved_solution_mode(self):
        config = deepcopy(self.config)
        config["model"]["solve_model"] = False
        client, model, start, evaluate, logging = self._mock_runtime()

        with start as start_comsol, evaluate, logging:
            with self.assertRaisesRegex(ValueError, "require solve_model"):
                run_sweep("A", [1], config=config)

        start_comsol.assert_not_called()
        self.assertFalse(client.cleared)


if __name__ == "__main__":
    unittest.main()

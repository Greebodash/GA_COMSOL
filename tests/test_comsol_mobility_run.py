import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import comsol_mobility_run
from comsol_run import evaluation_csv_paths, parse_markdown_config


ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "mobility_parameters.md"


class ComsolMobilityRunTests(unittest.TestCase):
    def test_config_has_mobility_overrides_and_gate_sweep(self):
        workflow = comsol_mobility_run.load_workflow(CONFIG_FILE)

        self.assertTrue(workflow.config["model"]["solve_model"])
        self.assertEqual(
            [parameter["name"] for parameter in workflow.config["parameters"]],
            [
                "alpha_mu",
                "mu_C0",
                "mu_ph",
                "n_ref",
                "mu_min",
                "donor_concentration",
            ],
        )
        self.assertEqual(
            workflow.preview["donor_parameter"],
            "donor_concentration",
        )
        self.assertEqual(
            comsol_mobility_run._gate_values(workflow.sweep).tolist(),
            [index / 2 for index in range(41)],
        )

    def test_python_preview_uses_configured_mobility_values(self):
        workflow = comsol_mobility_run.load_workflow(CONFIG_FILE)
        vg, current = comsol_mobility_run.python_curve(workflow)

        self.assertEqual(vg[0], 0.0)
        self.assertEqual(vg[-1], 20.0)
        self.assertEqual(len(current), len(vg))
        self.assertTrue(np.all(np.isfinite(current)))
        self.assertGreater(current[-1], current[0])

        mu_c0 = next(
            item for item in workflow.config["parameters"]
            if item["name"] == "mu_C0"
        )
        mu_c0["value"] *= 2
        _, changed_current = comsol_mobility_run.python_curve(workflow)

        self.assertFalse(np.allclose(current, changed_current))

    def test_workflow_runs_comsol_and_plots_both_curves(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "mobility_parameters.md"
            csv_stem = Path(directory) / "comsol" / "curve.csv"
            plot_file = Path(directory) / "curves.png"
            config_path.write_text(
                CONFIG_FILE.read_text(encoding="utf-8")
                .replace(
                    "results/mos2_ml_mobility.csv",
                    str(csv_stem),
                )
                .replace(
                    "results/mos2_ml_mobility_curves.png",
                    str(plot_file),
                ),
                encoding="utf-8",
            )

            def fake_comsol_run(run_config):
                config = parse_markdown_config(run_config)
                self.assertTrue(config["model"]["solve_model"])
                evaluation = config["evaluations"][0]
                result_file = evaluation_csv_paths(
                    config["model"]["evaluation_csv"],
                    config["evaluations"],
                )["id_vg"]
                Path(result_file).parent.mkdir(parents=True, exist_ok=True)
                with open(result_file, "w", newline="", encoding="utf-8") as out:
                    writer = csv.writer(out)
                    writer.writerow(
                        [evaluation["x_column"], evaluation["y_column"]]
                    )
                    writer.writerows([(0, 0), (1, 1e-9)])

            with (
                patch.object(
                    comsol_mobility_run.comsol_run,
                    "run",
                    side_effect=fake_comsol_run,
                ) as run,
                patch.object(
                    comsol_mobility_run,
                    "_save_curve_plot",
                    return_value=plot_file,
                ) as save_plot,
            ):
                result = comsol_mobility_run.run_workflow(config_path)

        self.assertEqual(result, plot_file)
        run.assert_called_once_with(config_path)
        self.assertEqual(save_plot.call_count, 2)
        self.assertIsNotNone(save_plot.call_args.args[2])

    def test_preview_only_does_not_run_comsol(self):
        with (
            patch.object(comsol_mobility_run.comsol_run, "run") as run,
            patch.object(
                comsol_mobility_run,
                "_save_curve_plot",
                return_value=Path("preview.png"),
            ) as save_plot,
        ):
            result = comsol_mobility_run.run_workflow(
                CONFIG_FILE,
                preview_only=True,
            )

        self.assertEqual(result, Path("results/mos2_ml_mobility_curves.png"))
        run.assert_not_called()
        save_plot.assert_called_once()


if __name__ == "__main__":
    unittest.main()

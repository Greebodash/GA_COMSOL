# COMSOL MoS₂ FET electrical workflows

Python tools for running the bottom-gate monolayer and four-layer MoS₂
semiconductor models from the [MoS2-FET repository](https://github.com/MatteoOrlandini/MoS2-FET)
and exporting their drain-current output and transfer curves.

## Project layout

```text
.
├── comsol_run.py              # Solve model studies and export electrical curves
├── comsol_sweep.py            # Run a parameter sweep from Python workflows
├── comsol_parameters.md       # Model paths, optional parameters, and evaluations
├── mobility_parameters_MoS2.md  # Mobility values and Id(Vg) sweep range
├── comsol_mobility_run.py       # Python preview and fresh COMSOL mobility run
├── streamlit_app.py           # Browser UI for mobility runs and curves
├── plot_results.py            # Plot electrical result CSV files
├── models/                    # Local COMSOL models (not included)
├── results/                   # Generated CSV files and plots
└── utils/
    └── comsol_utils.py        # COMSOL solution and evaluation helpers
```

COMSOL `.mph` files are not included. Download one or both bottom-gate models
from the source project and place them in `models/`:

- [Monolayer MoS₂ FET with bottom gate](https://github.com/MatteoOrlandini/MoS2-FET/blob/master/MoS2%20monolayer%20FET%20with%20bottom%20gate.mph)
- [Four-layer MoS₂ FET with bottom gate](https://github.com/MatteoOrlandini/MoS2-FET/blob/master/MoS2%204%20Layer%20FET%20with%20bottom%20gate.mph)

The linked [report](https://github.com/MatteoOrlandini/MoS2-FET/blob/master/Report.pdf)
describes the semiconductor models and example results. It reports monolayer
output curves at `Vg = 10 V` and transfer curves at `Vd = 0.05 V`, and
four-layer output curves at `Vg = 0.1 V` and transfer curves at `Vd = 0.01 V`.
The COMSOL studies and parametric sweep values saved in each model determine
the actual operating points used by a run.

## Requirements

- Python packages listed in [`requirements.txt`](requirements.txt)
- COMSOL Multiphysics installed and licensed
- MPh configured to connect to the COMSOL installation

Install the Python dependencies in the Python environment used with COMSOL:

```bash
python -m pip install -r requirements.txt
```

## Run a COMSOL model

Edit [`comsol_parameters.md`](comsol_parameters.md) and run from the project
root:

```bash
conda run -n mp python comsol_run.py
```

The local configuration solves `models/MoS2_ML.mph` and exports its transfer
curve to `results/mos2_ml_id_vg.csv` (`bias_voltage_v` is `Vg` and
`drain_current_a` is the terminal current).

The configured MPh dataset name (`Id(Vg)//Parametric Solutions 2`) and
expressions (`Vg`, `semi.I0_1`) were confirmed against the supplied model by
loading and evaluating it in the `mp` Conda environment. COMSOL's internal
dataset tags are not the names MPh expects for this lookup. If the local model
has different dataset names or expressions, update the evaluation table
accordingly.

The standard runner follows the `solve_model` setting in
[`comsol_parameters.md`](comsol_parameters.md). The mobility workflow described
below always solves the model with the configured mobility values.

## Preview and run the mobility model

Edit [`mobility_parameters_MoS2.md`](mobility_parameters_MoS2.md) to adjust
`alpha_mu`, `mu_C0`, `mu_ph`, `n_ref`, `mu_min`, and the donor concentration
parameter. Parameter names must match the COMSOL model; if its donor parameter
is not named `donor_concentration`, change the table name and the
`donor_parameter` value in the Python Preview section to match.

The `Vg_start`, `Vg_step`, and `Vg_finish` entries are also sent to COMSOL as
model parameter overrides. Configure the `Id(Vg)` study in the `.mph` file to
use those parameters as its sweep start, step, and finish. The same values set
the Python preview range.

Generate a fast Python preview without launching COMSOL:

```bash
conda run -n mp python comsol_mobility_run.py \
  --config mobility_parameters_MoS2.md --preview-only
```

Run the mobility model in COMSOL and save a plot comparing its `Id(Vg)` result
with the Python preview:

```bash
conda run -n mp python comsol_mobility_run.py \
  --config mobility_parameters_MoS2.md
```

The workflow explicitly requires `solve_model: true`, writes the solved model
to `models/MoS2_ML_mobility_out.mph`, exports its transfer curve to
`results/mos2_ml_mobility_id_vg.csv`, and saves the comparison plot to
`results/mos2_ml_mobility_curves.png`. The Python preview uses the compact
mobility/current model in `fit_mos2_mobility.py`; the COMSOL curve comes from
the configured `Id(Vg)` dataset and is the simulation output.

## Streamlit interface

Start the browser app from the project root using the COMSOL Python environment:

```bash
conda run -n mp streamlit run streamlit_app.py
```

The app builds its editable controls from parameter rows in its configured
Markdown file: adding or removing a row updates the form on the next Streamlit
rerun. The `category` column places controls in expandable groups, and
`description` text appears as input help;
rows without a category are shown under **Other**. Change the **COMSOL model
file** field to run a different `.mph` file; relative paths are resolved from
the project root, and absolute paths are accepted. The app runs COMSOL only
after you select **Solve COMSOL**. Each configured COMSOL evaluation is
displayed in its own chart after a successful solve.
Select **Also show the Python model preview** to overlay the approximate
Python curve on the COMSOL `Id(Vg)` chart.

After a COMSOL run, compare its exported `Id(Vg)` curve against the
fixed-parameter Python approximation with:

```bash
conda run -n mp python fit_mos2_mobility.py \
  --config mobility_parameters_MoS2.md
```

The comparison tool reads the configured `id_vg` CSV path and uses the
COMSOL-related parameter values in that Markdown file without fitting or
changing them. It reports MAE, RMSE, maximum absolute error, normalized RMSE,
and R-squared, and saves a comparison plot beside the CSV. Use `--comsol-csv`
to select a different COMSOL export, `--evaluation` to compare another
configured evaluation, or `--plot` to choose the plot path. These metrics show
how closely the compact Python approximation reproduces COMSOL; without
independent experimental/reference data, they do not measure physical accuracy
of the COMSOL simulation.

After a successful Streamlit solve, the app keeps the previous successful run
in the current browser session. The **Compare with previous successful run**
option overlays matching evaluations, and each chart has a CSV download with
current and previous-run values aligned by x value. Previous values are blank
where the x values do not match.

## Run a parameter sweep from Python

`comsol_sweep.py` provides an importable API for varying one COMSOL parameter
without changing the single-run command. Pass any iterable of finite numeric
values; each value is solved in order. A config file path or a parsed config
mapping is accepted. Mapping inputs are copied and left unchanged.

```python
from comsol_run import parse_markdown_config
from comsol_sweep import run_sweep

config = parse_markdown_config("comsol_parameters.md")
config["model"]["solve_model"] = True

results = run_sweep(
    "Vg",
    (0.1 * step for step in range(4)),
    config=config,
    parameter_unit="V",
    progress_callback=lambda result: print(
        f"Finished {result.parameter_name}={result.parameter_value}: "
        f"{result.model_path}"
    ),
)

for result in results:
    print(result.parameter_value, result.evaluation_csvs)
```

The swept parameter's unit is taken from its entry in the config, if present;
`parameter_unit` can provide or override it. Each result is a
`SweepRunResult` containing the parameter name and value, saved model path,
and a mapping of evaluation names to CSV paths. Output names include the
parameter, point index, and value so repeated values and separate points do
not overwrite one another. The optional callback receives each result after
that point has been solved and saved.

Sweeps always solve the model and reject `solve_model: false`, since saved
solutions cannot be reused for new parameter values. Use a model with a
configured sweep that solves successfully in the installed COMSOL version.

The runner does not create plots. Plotting is a separate, explicit command:

```bash
conda run -n mp python plot_results.py results --output results/id-vd.png
```

## Plot electrical curves

Plot all CSV files in `results/` using the default bias-voltage and
drain-current columns:

```bash
python plot_results.py results --output results/id-vd.png
```

To plot only the gate-transfer curve:

```bash
conda run -n mp python plot_results.py results/mos2_ml_id_vg.csv \
  --output results/id-vg.png
```

`plot_results.py` accepts CSV files and directories (not recursively), and
supports other numeric column names through `--x-column` and `--y-column`.
The plot labels use the selected column names. Parameter filtering is
available for input CSV files that include numeric `# key = value` metadata
comments.

## Logs and generated files

The run scripts write logs to `logs/`. CSV files are written to the parent
directory configured by `evaluation_csv`; plot images are saved wherever
`--output` points. COMSOL execution requires the external model file and a
licensed installation, so the Python-only checks do not substitute for
running the configured model in COMSOL.

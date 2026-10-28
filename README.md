# COMSOL spectrum tools

Python scripts for running COMSOL models, optimizing transmission spectra,
comparing two models, and plotting generated spectra.

## Project layout

```text
.
├── comsol_run.py                       # Run one COMSOL model/evaluation
├── ga_xmon.py                          # Optimize one model's spectrum
├── ga_xmon_difference.py               # Optimize the difference between two models
├── plot_spectra.py                     # Plot spectrum CSV files
├── comsol_parameters.md                # Single-solve settings and parameters
├── ga_xmon_parameters.md               # Single-model optimization settings
├── ga_xmon_difference_parameters.md    # Two-model optimization settings
├── SLR/    # Directory with COMSOL files
├── utils/
│   ├── comsol_utils.py                 # COMSOL table/evaluation helpers
│   └── ga_utils.py                     # Shared optimization/spectrum helpers
├── spectra/                            # Generated spectra (created as needed)
└── spectra_difference/                 # Generated comparison spectra
```

COMSOL `.mph` model files are supplied separately and are not included here.
The paths in the configuration templates are examples; edit them to point to
your local models and desired output locations.

## Requirements

- Python packages listed in [`requirements.txt`](requirements.txt)
- COMSOL Multiphysics installed and licensed
- MPh configured to connect to the COMSOL installation

Install the Python dependencies in the Python environment used with COMSOL:

```bash
python -m pip install -r requirements.txt
```

## Run a workflow

Run commands from the repository root so the scripts can find their
configuration files and the `utils` package.

### Single COMSOL solve

Edit [`comsol_parameters.md`](comsol_parameters.md), including model paths,
parameters, and evaluation settings, then run:

```bash
python comsol_run.py
```

The script saves the solved model and evaluation CSV to the configured paths.
`evaluation_tag` can contain one tag or a comma-separated list such as
`gev1, gev2`; multiple tags are extracted into separate CSV files with each tag
appended to the configured CSV filename.

### Optimize one model

Set the input model, objective, and parameter bounds in
[`ga_xmon_parameters.md`](ga_xmon_parameters.md), then run:

```bash
python ga_xmon.py
```

The optimization writes its results CSV and individual spectrum CSVs to the
locations set in the script (`ga_xmon_results.csv` and `spectra/` by default).

### Optimize the difference between two models

Set the reference and sample model paths, objective, and parameter bounds in
[`ga_xmon_difference_parameters.md`](ga_xmon_difference_parameters.md), then
run:

```bash
python ga_xmon_difference.py
```

This workflow writes the optimization results and comparison spectra to
`ga_xmon_difference_results_v2.csv` and `spectra_difference/` by default.

### Plot CSV data

`plot_spectra.py` accepts one or more CSV files and/or directories. Directories
are searched for CSV files (not recursively). With no inputs it uses
`spectra/`:

```bash
python plot_spectra.py spectra --output spectra.png
python plot_spectra.py spectra/H_d_225nm__H_h_2p2um.csv
python plot_spectra.py first.csv second.csv another_spectra/
```

By default, the tool plots `frequency_thz` against `transmission`. Select any
numeric columns present in the input CSVs with `--x-column` and `--y-column`:

```bash
python plot_spectra.py results.csv --x-column frequency_thz --y-column T_reference
```

All files plotted together must contain both selected columns.

Filter files by any numeric parameter recorded in their metadata comments using
`--parameter` and an inclusive `--min` and/or `--max` range:

```bash
python plot_spectra.py spectra --parameter H_d --min 225 --max 390
python plot_spectra.py spectra --parameter H_h --max 1.8
```

Range values use the same units as the metadata (for example, `H_d` is stored
in nm in generated optimization spectra). Metadata-free CSVs can still be
plotted, but are excluded when a parameter filter is active. Parameter filters
read metadata comments; they do not infer values from filenames. The figure is
displayed interactively if `--output` is omitted.

By default, curve colors are evenly sampled across the `magma` colormap, with
the number of samples matching the number of curves in the plot. Edit
`PLOT_COLORMAP` near the top of `plot_spectra.py` to use another Matplotlib
colormap. To use a custom palette instead, set `PLOT_COLORS` to a list of any
number of Matplotlib color names or values; the list repeats if there are more
curves than colors. `TEXT_SIZE` and `SHOW_LEGEND` set the default text size and
legend visibility.

## Logs and generated files

The COMSOL solve script creates `logs/` for its run log and creates the
configured CSV's parent directory when needed. The optimization scripts write
their spectra and result CSVs to their configured/default output locations.
Generated spectra and logs are local run outputs, not source files.

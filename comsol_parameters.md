# MoS₂ FET electrical configuration

This configuration targets the solved `MoS2_ML.mph` model in `models/`.
Paths are interpreted on the machine running COMSOL.

## Model

- input_file: models/MoS2_ML.mph
- output_file: models/MoS2_ML_out.mph
- evaluation_csv: results/mos2_ml.csv
- solve_model: true

## COMSOL Parameters

No parameter overrides are applied. The model defines `Vd` and `Vg`; its saved
parametric sweep results are used as-is. The runner rejects parameter
overrides while `solve_model` is false to prevent mismatches with stale saved
solutions.

| name | value | unit |
|---|---:|---|

## Evaluations

These dataset names and expressions were verified at runtime against the
provided solved model using MPh 1.3.1. Use the displayed dataset names, not the
internal COMSOL tags (`dset2`/`dset4`), because MPh resolves datasets by their
names. If your local model was edited, update the dataset or expression values
to match it.

| name | dataset | x_expression | x_unit | x_column | y_expression | y_unit | y_column |
|---|---|---|---|---|---|---|---|
| id_vg | Id(Vg)//Parametric Solutions 2 | Vg | V | bias_voltage_v | semi.I0_1 | A | drain_current_a |

`evaluation_csv` is the output filename stem. The runner appends each
evaluation name, producing `mos2_ml_id_vd.csv` and `mos2_ml_id_vg.csv` in
`results/`.

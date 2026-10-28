# Single-solve configuration

This template names the local input/output files and the COMSOL parameters
used for one solve. Paths are interpreted on the COMSOL machine

## Model

- input_file: models/model.mph
- output_file: models/model_solved.mph
- evaluation_tag: gev1
- temporary_table_tag: comsol_temp_table
- evaluation_csv: spectra/model_evaluation.csv

`evaluation_tag` may contain multiple comma-separated COMSOL evaluation tags,
for example `gev1, gev2`. With one tag, the configured `evaluation_csv` path is
used unchanged. With multiple tags, each CSV is written beside that path with
the tag appended to its filename stem, for example
`model_evaluation_gev1.csv` and `model_evaluation_gev2.csv`.
Each CSV contains the frequency and transmission columns.

## COMSOL Parameters

| name | value | unit |
|---|---:|---|
| H_d | 390 | nm |
| H_h | 1.2 | um |
| WSe2_w0 | 11.512 | THz|
| f_min | 9 | THz |
| f_step | 0.05 | THz |
| f_max | 13 | THz |
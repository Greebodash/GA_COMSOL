# Single-solve configuration

This template names the local input/output files and the COMSOL parameters
used for one solve. Paths are interpreted on the COMSOL machine

## Model

- input_file: SLR/Xmon_template_TMD.mph
- output_file: SLR/Xmon_template_TMD_Wse2.mph
- evaluation_tag: gev1
- temporary_table_tag: comsol_temp_table
- evaluation_csv: SLR/Xmon_template_evaluation.csv

`evaluation_tag` may contain multiple comma-separated COMSOL evaluation tags,
for example `gev1, gev2`. With one tag, the configured `evaluation_csv` path is
used unchanged. With multiple tags, each CSV is written beside that path with
the tag appended to its filename stem, for example
`Xmon_template_evaluation_gev1.csv` and `Xmon_template_evaluation_gev2.csv`.
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
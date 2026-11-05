# MoS₂ mobility and `Id(Vg)` settings

This file configures the Python mobility-curve preview and a fresh COMSOL run.
Paths are interpreted from the project root.

## Model

- input_file: models/MoS2_ML_template.mph
- output_file: models/MoS2_ML_mobility_out.mph
- evaluation_csv: results/mos2_ml_mobility.csv
- solve_model: true

## COMSOL Parameters

The parameter names must match the parameters in the COMSOL model.

| name | value | unit |
|---|---:|---|
| alpha_mu | 2 | |
| mu_C0 | 1.58 | cm^2/(V*s) |
| mu_ph | 18.7 | cm^2/(V*s) |
| n_ref | 1e19 | 1/cm^3 |
| mu_min | 1 | cm^2/(V*s) |
| donor_concentration | 1e18 | 1/cm^3 |
| Vg_start | 0 | V |
| Vg_step | 0.5 | V |
| Vg_finish | 20 | V |

## Id(Vg) Sweep

The COMSOL runner applies these model parameters before solving. Configure the
`Id(Vg)` parametric sweep in the model to use `Vg_start`, `Vg_step`, and
`Vg_finish`. These also define the Python preview's gate-voltage range.

## Python Preview

- donor_parameter: donor_concentration
- plot_file: results/mos2_ml_mobility_curves.png
- oxide_relative_permittivity: 3.9
- current_calibration_nA_per_V_at_100_mobility: 148
- calibration_drain_voltage: 0.05
- calibration_width_um: 6.8
- calibration_length_um: 3.5
- undoped_threshold_voltage: -0.80
- fit_from_vg: 6
- channel_thickness_nm: 0.7
- oxide_thickness_nm: 300
- drain_voltage_v: 0.05
- channel_width_um: 6.8
- channel_length_um: 3.5

## Evaluations

| name | dataset | x_expression | x_unit | x_column | y_expression | y_unit | y_column |
|---|---|---|---|---|---|---|---|
| id_vg | Id(Vg)//Solution 1  | Vg | V | bias_voltage_v | semi.I0_1 | A | drain_current_a |

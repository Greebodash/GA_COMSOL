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
Categories and descriptions organize the Streamlit controls; they do not
change the COMSOL parameter names or values.

| name | value | unit | category | description |
|---|---:|---|---|---|
| t_MoS2 | 0.7 | nm | Geometry & dimensions | Channel thickness (layers x layer thickness). Channel height in the geometry and used for the mobility density estimate. |
| L_MoS2 | 3.5 | um | Geometry & dimensions | Channel length between source and drain contacts (geometry). |
| W | 6.8 | um | Geometry & dimensions | Device width (out-of-plane depth of the 2D model); scales the current. |
| t_Au | 50 | nm | Geometry & dimensions | Source/drain gold contact thickness (geometry). |
| t_SiO2 | 300 | nm | Geometry & dimensions | Gate oxide thickness (geometry); sets Cox, hence the threshold and the current slope. |
| t_Si | 2 | um | Geometry & dimensions | Silicon back-gate substrate thickness (geometry). |
| Eg_ch | 2.76 | eV | Material properties | Channel band gap. Semiconductor Material Model > Band gap. Report Table 1 (1L). |
| chi_ch | 4.7 | eV | Material properties | Channel electron affinity. Semiconductor Material Model; sets contact and gate band alignment. Report Table 1 (1L). |
| epsr_ch | 4.2 | | Material properties | Channel relative permittivity (dimensionless). Semiconductor Material Model > Relative permittivity. Report Table 1 (1L). |
| me_eff | 4.5547E-31 | kg | Material properties | Electron effective mass; sets the conduction-band effective density of states N_c. |
| mh_eff | 4.5547E-31 | kg | Material properties | Hole effective mass; sets the valence-band effective density of states N_v. |
| mu_hole | 6 | cm^2/(V*s) | Mobility | Hole mobility (constant). Electrons use the density-dependent mu_ele, so this only matters for holes. |
| alpha_mu | 2 | | Mobility | Exponent of the Coulomb-limited mobility, mu_C = mu_C0*(n/n_ref)^alpha_mu. Larger = steeper turn-on. |
| mu_C0 | 1.58 | cm^2/(V*s) | Mobility | Coulomb-limited electron mobility at density n_ref. Fitted to MoS2 experiment. |
| n_ref | 1e19 | 1/cm^3 | Mobility | Reference density. |
| mu_ph | 18.7 | cm^2/(V*s) | Mobility | Phonon-limited electron mobility; caps the mobility at high density (Matthiessen's rule). Fitted to MoS2 experiment. |
| mu_min | 1 | cm^2/(V*s) | Mobility | Mobility floor so the thin low-density region at each contact does not dominate the resistance. |
| donor_concentration | 1e18 | 1/cm^3 | Doping & recombination | Channel donor (n-type) doping; shifts the threshold voltage negative. Analytic Doping Model. |
| acceptor_concentration | 0 | 1/cm^3 | Doping & recombination | Channel acceptor (p-type) doping; shifts the threshold positive. Analytic Doping Model. |
| tau_n | 1.5 | ns | Doping & recombination | SRH electron lifetime. Trap-Assisted Recombination. Report Table 1 (1L). |
| tau_p | 1.5 | ns | Doping & recombination | SRH hole lifetime. Trap-Assisted Recombination. Report Table 1 (1L). |
| rho_c | 1e-8 | ohm*cm^2 | Contacts | Specific contact resistivity of source and drain (Contact resistance option). |
| A_rich_n | 110 | A/(K*cm)^2 | Contacts | Effective Richardson constant for electrons (thermionic emission at the contacts). |
| A_rich_p | 90 | A/(K*cm)^2 | Contacts | Effective Richardson constant for holes (thermionic emission at the contacts). |
| WF_gold | 5.1 | V | Contacts | Gold work function, for barrier estimates; unused while WF_contact = 0. |
| Vd | 0.05 | V | Bias & sweep | Drain voltage held during the Id(Vg) sweep (the study's Vd list should reference this). |
| Vg_start | 0 | V | Bias & sweep | First gate voltage of the Id(Vg) sweep. |
| Vg_step | 0.5 | V | Bias & sweep | Gate-voltage step of the Id(Vg) sweep. |
| Vg_finish | 20 | V | Bias & sweep | Last gate voltage of the Id(Vg) sweep. |

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

## Evaluations

| name | dataset | x_expression | x_unit | x_column | y_expression | y_unit | y_column |
|---|---|---|---|---|---|---|---|
| id_vg | Id(Vg)//Solution 1 | Vg | V | bias_voltage_v | semi.I0_1 | A | drain_current_a |

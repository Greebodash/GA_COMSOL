# Difference-optimization configuration

This workflow solves two explicitly selected local models with identical
parameters, then maximizes the signed `T_reference - T_sample` difference in
the target-frequency window. It does not infer model roles from filenames.
The GA controls and parameter tables have the same meaning as in the
single-model optimization template.

## Model

- reference_file: models/reference.mph
- sample_file: models/sample.mph
- evaluation_tag: gev1
- temporary_table_tag_reference: difference_reference_table
- temporary_table_tag_sample: difference_sample_table
- target_frequency_thz: 11.5
- phi_a: 0

## Genetic Algorithm

- num_generations: 4
- solutions_per_population: 10
- num_parents_mating: 2
- parent_selection_type: rank
- keep_parents: 1
- mutation_percent_genes: 40
- stop_criteria: saturate_3
- random_seed: 37

## Objective

# Maximize the signed T_reference - T_sample difference near the target.
- objective_type: peak_transmission
- peak_search_window_thz: 0.75

## Optimization Parameters

| name | min | max | step | unit |
|---|---:|---:|---:|---|
| H_d | 200 | 500 | 25 | nm |
| H_h | 0.8 | 1.8 | 0.05 | um |

## Fixed Parameters

| name | value | unit |
|---|---:|---|
| f_min | 9 | THz |
| f_step | 0.05 | THz |
| f_max | 13 | THz |
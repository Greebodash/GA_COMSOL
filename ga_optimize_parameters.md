# Genetic-optimization configuration

The worker reads these Markdown sections to construct a single-model genetic
search. GA defaults are 4 generations, population 10, 2 parents mating, rank
selection, 1 kept parent, 40% mutation, `saturate_3`, and seed 37. Search
bounds are in `Optimization Parameters`; values held constant belong in
`Fixed Parameters`.

## Model

- input_file: models/model.mph
- evaluation_tag: gev1
- temporary_table_tag: ga_temp_table
- save_first_simulation_output: true
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

- objective_type: custom_expression
- objective_expression: q_factor * exp(-((peak_frequency - target_frequency) / 0.30)**2)
- peak_search_window_thz: 0.75

## Optimization Parameters

| name | min | max | step | unit |
|---|---:|---:|---:|---|
| H_d | 350 | 450 | 5 | nm |
| H_h | 0.8 | 1.8 | 0.05 | um |

## Fixed Parameters
| name | value | unit |
|---|---:|---|
| f_min | 9 | THz |
| f_step | 0.05 | THz |
| f_max | 13 | THz |
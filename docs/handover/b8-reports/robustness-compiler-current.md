# ALIGN robustness protocol report

- Status: `software_gate_only`
- Method: `compiler`
- Partition: `development`
- Evidence class: `synthetic_fixture_software_gate`
- Provider/cost: `not_used` / `0.00 USD`; infrastructure cost `not_metered`
- Environment: `Linux-7.0.14-linuxkit-aarch64-with-glibc2.41` / `aarch64` / Python `3.12.15` / 8 CPUs
- Protocol: `fixture-b7-protocol` / `paraphrase-v1`
- Release: `4d19406c-87b9-472d-89cf-a7dbdf103661` (`e63976f8c033b89cccc2b3f7c8bfb2bed4e58fdf18210d3506cb17c414cf07e9`)
- Dataset SHA-256: `b9eb30e3484d05f39a4fb01b662e2e301d59be6e6887503b0dfdf3492d547b39`
- Repeats: 3

## Cases

| Case | Class | Structural obligations | Executor runtime |
|---|---|---|---|
| `base` | baseline | not compared | not_run |
| `variant` | reviewer-labelled-equivalent | True | not_run |

## Robustness dimensions

| Dimension | Status | Evidence / count |
|---|---|---|
| `compiler_fidelity` | `checked` | case_count=2 |
| `executor_variation` | `not_run` | declared_scenario_count=0, runtime_cases=0 |
| `format_variation` | `not_run` | declared_scenario_count=0, runtime_cases=0 |
| `source_corruption` | `not_run` | declared_scenario_count=0, runtime_cases=0 |
| `repeat_dependence` | `deterministic_applicability_only` | deterministic_rule_repeats=3, declared_scenario_count=0, stochastic_runtime_repeats=0 |

## Limitations

- Structural obligation comparison is not a human semantic-equivalence verdict.
- No provider call, evidence-level executor decision, stochastic stability, cost, or research metric was run.
- No source-corruption or file-format variation scenario was executed by this CLI run.
- Repeat count covers deterministic applicability replay only, not executor or source-level repeat dependence.
- Fixture reviewer labels are synthetic test metadata, not human study adjudication.

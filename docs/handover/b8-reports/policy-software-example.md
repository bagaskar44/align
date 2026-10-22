# ALIGN Policy Validation Report

- Status: **incomplete**
- Provider/data: `mock` / `synthetic`
- Candidate/baseline: `routing-baseline-v2` / `routing-baseline-v2`
- Dataset SHA-256: `96c91aac413f1145033bdf946687ffc9ac5e7ae1628671ea7a3d0cb52baba2c8`
- Candidate/baseline core SHA-256: `4152730c5cea7a2904d5687092d3defedd9f97ae6e66048f96f3a97d0fcca0f7` / `4152730c5cea7a2904d5687092d3defedd9f97ae6e66048f96f3a97d0fcca0f7`

## Per-category gates

| Category | Baseline precision / recall / coverage / ESR | Candidate precision / recall / coverage / ESR | Support |
|---|---|---|---|
| numerical_inconsistency | 1.0 / 1.0 / 1.0 / 1.0 | 1.0 / 1.0 / 1.0 / 1.0 | positives=1, assignments=1 |

## Eligibility limits

- authorized live institutional validation with two-rater adjudication is unavailable
- one or more findings lack a codebook-bound competent human evidence rating
- settled end-to-end ledger cost is missing, out of scope, or liability remains pending/unknown
- three preselected task-family repeats with clean critical invariants are not evidenced
- validation support is below the per-category minimum

# Gold-label codebook template

Status: `not_yet_available`. This file is a template and is not a calibrated codebook.

For each obligation/category/numeric-context unit, record separately:

- Applicability: `applicable | not_applicable | unresolved`.
- Gold assessability: `assessable | unassessable`.
- Issue label: `issue_present | issue_absent | null` (null when not applicable, unresolved, or unassessable).
- Compatible and inspected source-unit denominator, including unsupported/unreadable units.
- Adjudicated source/evidence relation, valid anchor, and semantic support outcome.
- Two independent rater IDs, disagreement, adjudication reference, and codebook revision.
- Failure class: acquisition, interpretation, unsupported, or abstention, preserving each event.

Before research use, define examples and counterexamples, calibrate two raters on a separate set, report agreement and adjudication, freeze this file, and record its SHA-256. Never treat a matching quote or empty retrieval result as semantic proof.

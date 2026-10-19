from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOCATIONS = (ROOT / "docs/implementation/README.md").read_text(encoding="utf-8").splitlines()
MASTER = (ROOT / "ALIGN_IMPLEMENTATION_SPEC.md").read_text(encoding="utf-8").splitlines()
STAGE_DOCS = {
    f"B{number}": f"docs/implementation/B{number}-{slug}.md"
    for number, slug in [
        (0, "fondasi"),
        (1, "identitas-dan-konteks"),
        (2, "tugas-dan-spesifikasi"),
        (3, "submission-dan-viewer"),
        (4, "review-manual-dan-release"),
        (5, "ai-executor"),
        (6, "numeric-dan-biaya"),
        (7, "ux-dan-robustness"),
        (8, "handover-dan-pilot-readiness"),
        (9, "moodle-sandbox"),
    ]
}

B0_REQUIREMENTS = {
    "FR-01": (
        "Parsial B0; PostgreSQL integration 1 passed, seed rerunnable dan course access E2E passed; provisioning lengkap B1",
        "services/api/src/align_api/courses.py; services/api/src/align_api/models.py; tests/integration/test_postgres_foundation.py; apps/web/e2e/auth.spec.ts",
    ),
    "FR-16": (
        "Parsial B0; outbox smoke runtime passed; ledger/reservation enforcement B6",
        "services/api/src/align_api/models.py; services/worker/src/align_worker/dispatcher.py; services/worker/src/align_worker/tasks.py",
    ),
    "FR-17": (
        "Parsial B0; UI shell/login dan responsive widths E2E passed; accessibility lengkap B7",
        "apps/web/src/app/page.tsx; apps/web/src/app/globals.css; apps/web/e2e/auth.spec.ts",
    ),
    "FR-19": (
        "Parsial B0; host 16 passed/1 skipped, PostgreSQL integration 1 passed, browser E2E 6 passed; suite lengkap B7",
        "services/api/src/align_api/errors.py; tests/unit/; tests/integration/; apps/web/e2e/",
    ),
    "FR-22": (
        "Parsial B0; baseline policy dan release hash ada; keputusan runtime belum lengkap",
        "config/routing.baseline.json; config/batching.baseline.json; services/api/src/align_api/cli.py",
    ),
    "FR-24": (
        "Scaffolding B0 saja; evaluator/benchmark belum diimplementasikan",
        "research/research_traceability.md; research/iterations/README.md; docs/decisions/000-template.md",
    ),
}

B0_INVARIANTS = {
    "INV-05": (
        "Parsial B0; PostgreSQL integration passed: worker ditolak SELECT pada sessions/courses/enrollments dan DELETE audit_events",
        "migrations/versions/0001_b0_foundation.py; tests/integration/test_postgres_foundation.py",
    ),
    "INV-06": (
        "Parsial B0; worker memakai idempotency/fencing, model review belum ada",
        "services/worker/src/align_worker/tasks.py; services/api/src/align_api/models.py",
    ),
    "INV-09": (
        "B0 tidak menambahkan fungsi nilai/dashboard; audit menyeluruh belum dijalankan",
        "apps/web/src/app/page.tsx; services/api/src/align_api/main.py",
    ),
    "INV-16": (
        "Parsial B0; manifest immutable/hash tersedia, fingerprint seluruh run belum diterapkan",
        "services/api/src/align_api/cli.py; services/api/src/align_api/models.py; config/release.baseline.json",
    ),
}

B0_ACCEPTANCE = {
    "AC-MVP-01": (
        "Parsial B0; install, migration, seed dan login runtime passed; alur akademik manual lengkap masuk B4",
        "scripts/ci.ps1; tests/integration/test_postgres_foundation.py; apps/web/e2e/auth.spec.ts",
    ),
    "AC-MVP-38": (
        "Test safeguard live-call lulus; reservation/concurrency gate dan provider runtime belum tersedia",
        "packages/domain/src/align_domain/providers.py; tests/unit/test_provider_contract.py",
    ),
    "AC-MVP-39": (
        "Safeguard B0 memblokir seluruh live call; billing reconciliation belum ada",
        "packages/domain/src/align_domain/providers.py; services/api/src/align_api/models.py",
    ),
    "AC-MVP-40": (
        "Parsial B0; outbox smoke operation succeeded; crash/redelivery tests belum ada",
        "services/worker/src/align_worker/dispatcher.py; services/worker/src/align_worker/tasks.py; scripts/ci.ps1",
    ),
    "AC-MVP-42": (
        "Parsial B0; E2E widths 360/768/1440 tanpa horizontal overflow passed; aksesibilitas lengkap B7",
        "apps/web/e2e/auth.spec.ts; apps/web/src/app/globals.css",
    ),
    "AC-MVP-44": (
        "Parsial B0; E2E admin tanpa enrollment tidak dapat membaca/mendaftar roster; audit scope lengkap B7",
        "apps/web/src/app/page.tsx; services/api/src/align_api/main.py",
    ),
    "AC-MVP-55": (
        "Parsial B0; baseline release berhasil register/activate dengan empirical_verification=false; promotion report belum ada",
        "config/batching.baseline.json; config/release.baseline.json; services/api/src/align_api/cli.py",
    ),
    "AC-MVP-58": (
        "Test safeguard paid-path lulus; enforcement cap per-call/run dan provider runtime belum tersedia",
        "packages/domain/src/align_domain/providers.py; packages/domain/src/align_domain/contracts.py; tests/unit/test_provider_contract.py",
    ),
    "AC-MVP-59": (
        "Parsial B0; manifest hash dan input fingerprint tersedia, semua run bindings belum ada",
        "services/api/src/align_api/models.py; services/api/src/align_api/cli.py; config/release.baseline.json",
    ),
}


def rows_with_prefix(lines: list[str], prefix: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for line in lines:
        if not line.startswith("|"):
            continue
        columns = [column.strip() for column in line.strip().strip("|").split("|")]
        if columns and columns[0].startswith(prefix):
            found[columns[0]] = columns
    return found


def safe(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def evidence_for(identifier: str, stage: str, mapping: dict[str, tuple[str, str]]) -> tuple[str, str]:
    if identifier in mapping:
        status, paths = mapping[identifier]
        links = "; ".join(f"`{path}`" for path in paths.split("; "))
        return status, links
    stage_doc = STAGE_DOCS[stage]
    return (
        f"Belum diimplementasikan pada B0; pemilik requirement {stage}.",
        f"Rencana tahap: `{stage_doc}`. Belum ada test/artefak implementasi untuk requirement ini.",
    )


def main() -> None:
    fr = rows_with_prefix(ALLOCATIONS, "FR-")
    inv = rows_with_prefix(ALLOCATIONS, "INV-")
    ac_allocation = rows_with_prefix(ALLOCATIONS, "AC-MVP-")
    ac_contract = rows_with_prefix(MASTER, "AC-MVP-")

    lines = [
        "# Requirements traceability B0",
        "",
        "Sumber normatif: `ALIGN_IMPLEMENTATION_SPEC.md` v2 dan alokasi tahap pada `docs/implementation/README.md`.",
        "Status berikut menggambarkan cakupan source B0, bukan kelulusan gate. Tidak ada AC yang dinyatakan fully passed; hasil command yang benar-benar dijalankan ada di `docs/handover/B0.md`.",
        "",
        "## Functional requirements",
        "",
        "| ID | Perilaku | Mulai | Gate lengkap | Status B0 | Evidence/source atau pemilik tahap |",
        "|---|---|---|---|---|---|",
    ]
    for identifier, columns in fr.items():
        status, evidence = evidence_for(identifier, columns[2], B0_REQUIREMENTS)
        lines.append(
            f"| {identifier} | {safe(columns[1])} | {columns[2]} | {columns[3]} | {safe(status)} | {evidence} |"
        )

    lines.extend(
        [
            "",
            "## Invariants",
            "",
            "| ID | Perlindungan | Mulai | Gate lengkap | Status B0 | Evidence/source atau pemilik tahap |",
            "|---|---|---|---|---|---|",
        ]
    )
    for identifier, columns in inv.items():
        status, evidence = evidence_for(identifier, columns[2], B0_INVARIANTS)
        lines.append(
            f"| {identifier} | {safe(columns[1])} | {columns[2]} | {columns[3]} | {safe(status)} | {evidence} |"
        )

    lines.extend(
        [
            "",
            "## Acceptance criteria MVP",
            "",
            "Gate lengkap adalah tahap paling awal seluruh skenario dapat dibuktikan. Untuk AC yang mulai pada B0, safeguard atau test scaffold parsial tetap bukan bukti lulus. Scenario/expected result diambil dari master.",
            "",
            "| ID | Skenario | Hasil normatif | Mulai | Gate lengkap | Status B0 | Evidence/source atau pemilik tahap |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for identifier, columns in ac_allocation.items():
        contract = ac_contract.get(identifier)
        scenario = columns[1]
        expected = contract[2] if contract and len(contract) > 2 else "Lihat master Bagian 23."
        status, evidence = evidence_for(identifier, columns[2], B0_ACCEPTANCE)
        lines.append(
            f"| {identifier} | {safe(scenario)} | {safe(expected)} | {columns[2]} | {columns[3]} | {safe(status)} | {evidence} |"
        )

    (ROOT / "requirements_traceability.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

# ALIGN

ALIGN is a course-scoped workspace for reviewing student work with traceable evidence and instructor-controlled feedback. This repository currently implements the B0 foundation only. Academic assignment, submission, review, and analysis flows remain future stages.

## Local development

For macOS, start with `bash scripts/macos-toolchain.sh`, then
`source scripts/macos-env.sh`. The complete clean-checkout procedure, private
environment bootstrap, and isolated verification are in
[`docs/handover/macos-local-setup.md`](docs/handover/macos-local-setup.md).
The initial macOS manual/mock setup verified login with parsing disabled.

Prerequisites: Python 3.12, Node.js 22 or newer, `uv 0.8.22`, Corepack-enabled Node, and Docker Desktop with the Linux engine running. The Compose stack binds only to `127.0.0.1:3000`; PostgreSQL, Redis, and S3-compatible storage have no host-published ports.

1. Create an ignored local environment and its random development credentials:

   ```powershell
   & .\scripts\bootstrap.ps1
   ```

2. Resolve Python and frontend dependencies from their lockfiles:

   ```powershell
   uv sync --frozen --all-packages --group dev
   corepack pnpm install --frozen-lockfile
   ```

3. Build and start the local Compose services:

   ```powershell
   docker compose up --build --detach --wait
   ```

4. Seed synthetic accounts and two courses. The command reads `ALIGN_SEED_PASSWORD` from `.env`, is safe to rerun, and does not print the password:

   ```powershell
   docker compose run --rm api align seed
   ```

   Demo account names are `dosen@align.local`, `mahasiswa@align.local`, `dosen2@align.local`, `mahasiswa2@align.local`, and `admin@align.local`. Each uses the local password in `.env`. The database account budget is initialized once as USD 25.00 confirmed, USD 5.00 protected, and USD 0.00 planned top-up. Live provider calls are blocked before network access.

5. Open `http://localhost:3000`. API health endpoints are `/health/live` and `/health/ready`; the web proxy intentionally does not expose `/health/providers`.

To create a non-seeded operator account, provide its temporary password through standard input:

```powershell
Read-Host -Prompt 'Temporary password from your secret manager' | docker compose run --rm -T api align admin bootstrap --email admin@example.edu --display-name 'Operator ALIGN' --password-stdin
```

The operator's first login directs them to set a new password. Production provisioning uses the same CLI flow or a secret manager. Account passwords and API keys are never written to source.

## Baseline manifests and operational smoke

Register the synthetic mock release, then activate its returned ID:

```powershell
$registered = docker compose run --rm api align release register --file config/release.baseline.json | ConvertFrom-Json
docker compose run --rm api align release activate $registered.release_id
```

The release enables only the mock schema contract and manual foundation. Its manifest records that native parsers, live models, and paid calls are unavailable; the baseline batch size is one and does not claim empirical verification.

Exercise outbox delivery with an operational stage that reads no academic data:

```powershell
$operation = docker compose run --rm api align operations smoke | ConvertFrom-Json
docker compose run --rm api align operations status $operation.operation_id
```

The dispatcher and worker run continuously in Compose; the status command may first report `queued` or `running`.

## Verification

`& .\scripts\capacity.ps1` records host memory and Docker availability. `& .\scripts\ci.ps1` runs frozen installs, Python lint/typecheck/unit checks, frontend lint/typecheck/production build, Compose readiness, rerunnable seed, PostgreSQL+pgvector role checks, baseline release registration/activation, an ID-only outbox worker smoke, and Playwright auth/accessibility smoke. The test-only Compose profile grants the integration runner the two DB roles needed to verify privilege separation. The pipeline is local; no CI provider is activated from this checkout.

The implementation handover records the exact commands, exit codes, hardware, limitations, and test results. A source file or command in this README does not count as a successful run until it has been executed in the stated environment.

## Architecture and scope

- `apps/web`: Indonesian Next.js UI shell, same-origin API calls, course list, secure sign-in, and forced temporary-password change.
- `services/api`: FastAPI, SQLAlchemy/Alembic persistence, Argon2id passwords, hashed sessions, Origin/CSRF checks, course access, and operator CLI.
- `services/worker`: Celery queues, ID-only transactional outbox dispatcher, expiring leases, and a synthetic non-academic worker smoke stage.
- `packages/domain`: strict cross-service job/provider contracts and fixture-backed mock provider.
- `migrations`: initial B0 identity/course/manifest/job/outbox/audit/idempotency/operations and finance foundation.
- `config` and `fixtures/synthetic`: immutable-style mock/release baselines and explicitly labeled synthetic data.

S3Mock is used only as a private, filesystem-backed development/test service on the internal Compose network. Production config must point to an institution-approved private object-storage bucket and apply its access policy. No real student records, API keys, or model spending are part of B0.


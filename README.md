# ALIGN

ALIGN adalah workspace mata kuliah untuk meninjau karya mahasiswa dengan bukti yang dapat ditelusuri dan feedback yang disahkan dosen. Checkout ini menyediakan alur manual, provider mock berlabel, parser lokal, dan evaluator offline. Readiness dan batas tiap kemampuan dicatat dalam [handover B8](docs/handover/B8.md); software fixture bukan bukti hasil penelitian atau provider live.

## Menjalankan di macOS

Prasyarat: macOS, Git, Bash, `curl`, `tar`, `shasum`, dan Docker Desktop dengan Linux Engine aktif. Untuk parsing Docling, alokasikan RAM Docker sesuai resource parser di [profil](config/parser-context-resources.json); kebutuhan itu terpisah dari startup manual/mock. Instalasi Docker Desktop bukan bagian dari skrip repository.

Dari checkout repository:

```sh
bash scripts/macos-toolchain.sh
source scripts/macos-env.sh
bash scripts/dev-macos.sh bootstrap
bash scripts/dev-macos.sh install
bash scripts/dev-macos.sh up
bash scripts/dev-macos.sh seed
bash scripts/dev-macos.sh release-baseline
bash scripts/dev-macos.sh verify
```

Toolchain mengunci Node `22.22.0`, uv `0.8.22`, Python `3.12.11`, dan pnpm `10.18.3` melalui Corepack. Python dan web dependencies dipasang dari `uv.lock` dan `pnpm-lock.yaml` dengan mode frozen. Secara default toolchain dan cache berada di `~/Library/Application Support/ALIGN/` dan `~/Library/Caches/ALIGN/`, di luar checkout. Bootstrap membuat enam file env privat berizin `0600`; jangan commit atau menyalin file tersebut.

`up` memeriksa role PostgreSQL, menjalankan migration sampai head, lalu membangun dan menyalakan API, worker umum, dispatcher, scheduler, web, dan proxy. `parser-worker` sengaja terpisah agar startup manual/mock tidak otomatis memuat model parser berat. Seed hanya membuat akun/course sintetis dan aman dijalankan ulang. `release-baseline` mendaftarkan baseline mock/synthetic terkunci dan mengaktifkannya hanya jika belum ada release aktif; release aktif developer dipertahankan. `verify` memeriksa readiness, seed idempotent, login HTTP, sesi, dan logout tanpa menulis password atau token ke output.

Buka `http://localhost:3000`. Akun demo disediakan oleh seed; password hanya ada di `.env` privat checkout. API/database/broker/object storage tetap berada di jaringan internal Compose. Mode mock tidak memerlukan API key dan provider berbayar tetap dibatasi guard.

## Pemeriksaan dan pekerjaan operator

```sh
corepack pnpm --dir apps/web lint
corepack pnpm --dir apps/web typecheck
bash scripts/dev-macos.sh parser-up
corepack pnpm --dir apps/web test:e2e
bash scripts/dev-macos.sh integration
bash scripts/dev-macos.sh parser-integration
bash scripts/dev-macos.sh parser-benchmark
```

`integration` menjalankan regression PostgreSQL. Selama tes, perintah ini menghentikan dispatcher normal agar event masuk ke queue uji, menyalakan parser worker dan dispatcher uji, lalu menghentikan worker uji serta memulihkan dispatcher normal saat keluar. Jalankan pada database uji yang masih bersih; suite memakai akun ledger bersama di database. Tes Docling yang memakai model nyata merupakan langkah opt-in tersendiri; status `skipped` bukan lulus. `parser-integration` memakai aturan dispatcher yang sama. Benchmark menulis JSON/Markdown di `docs/handover/b8-reports/` dan hanya mengukur fixture sintetis. Ia tidak mengukur biaya listrik/compute Docker atau menjamin kapasitas semua dokumen maksimum.

Kontrak API web dapat diperiksa setelah workspace Python di-install:

```sh
.venv/bin/python scripts/openapi_contract.py --check
```

Perubahan server OpenAPI menghasilkan snapshot `docs/api/openapi.json` dan tipe client dari skrip yang sama:

```sh
.venv/bin/python scripts/openapi_contract.py
```

Evaluator offline menerima fixture sintetis tanpa koneksi database/provider:

```sh
uv run --no-sync --package align-api align evaluate metrics \
  --dataset-manifest tests/fixtures/b8/metrics-three-units.json \
  --output /tmp/align-metrics.json

uv run --no-sync --package align-api align evaluate policies \
  --candidate routing-baseline-v2 --baseline routing-baseline-v2 \
  --dataset-manifest tests/fixtures/b8/policy-software-example.json \
  --output /tmp/align-policy.json
```

Laporan contoh metrik akan bertanda `software_gate_only`; policy fixture tetap `incomplete`. Jangan mengubah keduanya menjadi klaim empiris atau mengaktifkan optimasi. CLI robustness memerlukan release terdaftar di database lokal; parser benchmark memerlukan `parser-worker` dan profile resource tersedia. Lihat [indeks implementasi](docs/implementation/README.md) dan [B8](docs/handover/B8.md) untuk input, hasil, dan limit setiap CLI.

Skrip pemeriksaan checkout bersih membuat toolchain, database, dan volume uji terpisah serta tidak memakai folder ignored checkout aktif:

```sh
bash scripts/verify-macos-clean.sh
```

Harness menyimpan bukti di `/private/tmp/align-macos-clean.*`. Ia tidak menghapus volume atau instalasi development yang sudah ada. Gunakan `ALIGN_KEEP_TEST_RUNNING=1` hanya bila perlu memeriksa stack terisolasi setelah smoke. Parser/runtime coverage dicatat terpisah dari smoke manual/mock.

Backup sintetis hanya menerima project `align-b7-*`, semua writer/child berhenti, file `.env` privat dan sertifikat penerima. Contoh operator (ganti path sertifikat dengan sertifikat sintetis yang Anda kendalikan):

```sh
export ALIGN_COMPOSE_PROJECT=align-b7-synthetic-check-01
export ALIGN_BACKUP_OUTPUT=/private/tmp/align-synthetic-check-01.cms
export ALIGN_BACKUP_CERT=/path/to/synthetic-recipient.crt
export ALIGN_BACKUP_QUIESCED=1
bash scripts/backup-synthetic.sh
```

Restore memerlukan nama project baru dengan prefix `align-b7-restore-`, arsip terenkripsi, sertifikat dan private key pasangannya. Contoh sintaks:

```sh
export ALIGN_RESTORE_PROJECT=align-b7-restore-check-01
export ALIGN_RESTORE_INPUT=/private/tmp/align-synthetic-check-01.cms
export ALIGN_RESTORE_CERT=/path/to/synthetic-recipient.crt
export ALIGN_RESTORE_KEY=/path/to/synthetic-recipient.key
bash scripts/restore-synthetic.sh
```

Script menolak target yang memiliki volume. Restore diuji pada volume sintetis kosong terpisah; jangan menjalankannya pada volume development. Frekuensi backup, key custody/rotation, retensi live, dan target RPO/RTO belum menjadi kebijakan institusi; lihat handover B8.

## Ruang lingkup dan bukti

Aplikasi tidak menyertakan gradebook, dashboard nilai, materi pembelajaran, pencarian eksternal, atau Moodle passback. Tidak ada data mahasiswa nyata atau panggilan provider berbayar yang diperlukan untuk mode lokal. Status backup terjadwal, kebijakan retention live, keputusan institusi, validasi VoiceOver, dan efektivitas empiris tetap mengikuti bukti dan pemiliknya sebagaimana dicatat di [traceability](requirements_traceability.md) dan [handover B8](docs/handover/B8.md).

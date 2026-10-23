# Handover — kesiapan produksi standalone

Audit dan implementasi panduan `docs/implementation/standalone-production.md`, 23 Oktober 2026 (Asia/Jakarta). Ini catatan hasil aktual, bukan pernyataan siap pilot.

## Snapshot dan batas

- Checkout: branch `development`, baseline `d67f63a43e569358e450a62a809b545c969448f5`; master `ALIGN_IMPLEMENTATION_SPEC.md` SHA-256 `6a872b132f5721eda817af89093b899970436169a70b93fbcf4778bcf4a7b254`.
- Host yang teramati: macOS 26.5.2, Darwin 25.5.0, arm64. Docker Desktop 4.96.0 / Engine 29.9.1 / Compose 5.6.0; VM Docker sebelumnya terukur memiliki RAM 12,526,637,056 byte. Model Mac dan RAM host saat audit tidak terbaca dari sandbox, sehingga tidak diklaim.
- Clean-checkout P0 memakai toolchain terisolasi, bukan instalasi ignored host: Node v22.22.0, pnpm 10.18.3 (repository pin), uv 0.8.22, CPython 3.12.11. `uv.lock` dan `pnpm-lock.yaml` sama sebelum/sesudah instalasi.
- Clean run terakhir memakai project Docker `align-macos-clean-1792747427`; seluruh container, volume, network, dan image tag milik project itu telah dihapus setelah verifikasi label. Development containers tetap berjalan. Ringkasan bukti tanpa secret dan receipts revision/lockfile ada di `/private/tmp/align-macos-clean.X9RBy5` (file ringkasan mode 0600); checkout, `.env` synthetic, toolchain, browser, dan cache test sudah dihapus.
- Tidak ada nilai secret, key, token, password, dokumen nyata, atau output provider yang disalin ke handover. Tidak ada provider call, release live, budget top-up, institusi/data nyata, deployment, B9, commit, atau push.
- Perubahan lokal lain seperti `.DS_Store` dan `.vscode/` dibiarkan. Tidak ada lockfile, migrasi, OpenAPI/client, kontrak akademik, resource guard parser, atau requirement master yang diubah.

## Status tahap

| Tahap | Status | Hasil dan alasan |
|---|---|---|
| P0 — verifikasi runtime | `passed` untuk checkout bersih/manual/mock | Clean snapshot kedua menyelesaikan install, API/worker/web/parser image builds, migration, seed/idempotensi, mock baseline, login, integration, real Docling, OOM, dan browser smoke. Percobaan pertama pernah gagal dengan BuildKit `read-only file system` ketika host tersisa 181 MiB; setelah Docker pulih dan host menunjukkan 24 GiB bebas, rerun lulus. Ini korelasi resource, bukan root cause yang sudah dibuktikan. P1/P3 readiness tetap belum lulus. |
| P1 — persiapan live tanpa biaya | `in_progress` | Template live-synthetic dan preflight offline ada serta tes negatif lulus. Key lokal kini diteruskan ke API dan general worker, tetapi runtime sengaja tetap `mock` dengan `PAID_CALLS_ENABLED=false`; parser tidak menerima key. CLI live-smoke masih fail-closed karena spend cap atomik per-run belum terhubung ke API/worker ledger. |
| P2 — inference sintetis live | `pending_external_input` | Key provider tersedia di container API/worker, tetapi validitasnya belum diuji ke provider; role/model IDs, verified rates, authorization reference, approved per-run cap, dan live release binding belum tersedia. Nol provider request dan nol biaya. |
| P3 — operasi produksi di Linux | `in_progress` | Compose produksi, Caddy, role-env/secret examples, storage-auth template dan preflight statis disiapkan. Belum ada target Linux, SeaweedFS runtime/least-privilege boto3 test, TLS end-to-end, operasi backup/restore, off-host destination, alert/operator, egress firewall, capacity atau rollback evidence. |
| P4 — kualifikasi data | `pending_external_input` | Tidak ada izin, policy institusi, dataset/adjudication, protokol/codebook, atau report eligible; data sintetis tidak memenuhi gate tersebut. |
| P5 — pilot terbatas | `not_started` | P0–P4 belum lulus; tidak ada deployment atau aktivasi pilot. |

## Bukti P0 yang benar-benar dijalankan

| Pemeriksaan | Hasil |
|---|---|
| Fresh snapshot dari HEAD ditambah perubahan kandidat yang tidak di-ignore; toolchain/cache/browser berada di project test privat | Bootstrap dan install lulus; tidak memakai `.venv`, `node_modules`, `.codex-tools`, atau cache/browser ignored checkout. `uv.lock` dan `pnpm-lock.yaml` identik sebelum/sesudah. Harness memilih CIDR yang tersedia dan clean run menyelesaikan sampai akhir. |
| Ruff seluruh Python | Lulus. |
| `pytest -q --import-mode=importlib` | **196 passed, 46 skipped, 2 warnings**. Skipped bukan pass: termasuk integration yang mensyaratkan service/project integration tersendiri, Docling runtime opt-in, dan tes cgroup OOM khusus Linux. Tidak semua 46 dihitung sebagai bukti integrasi. |
| Web lint/typecheck | Lulus tanpa error; lint melaporkan satu warning `<img>` yang telah ada pada `SubmissionViewer.tsx`. |
| Build image API, worker, web, parser-worker, test runner | Semua berhasil pada clean project kedua, termasuk ekspor image parser-worker dengan dependency/model terkunci. Percobaan pertama gagal saat ekstraksi Torch dalam kondisi host disk 181 MiB; kegagalan tidak terulang setelah host memiliki 24 GiB bebas. |
| Migrate / seed / seed ulang | Migrasi sampai head lulus; seed membuat fixture sintetis (5 user, 2 course, 4 membership), seed ulang idempotent. |
| Baseline release | Registrasi/aktivasi mock/synthetic lulus dengan `empirical_verification=false`. Bukan bukti model live atau eligibility. |
| Login dan dependency smoke | `dev-macos.sh verify` lulus untuk dosen, mahasiswa, admin: login/profile/course/logout serta pemeriksaan HTTP login dan DB/broker/storage pada project terisolasi. |
| Suite PostgreSQL/Redis/object-storage integration | **38 passed, 8 skipped, 1 deprecation warning** dalam suite umum. Skip: 7 kasus Docling opt-in dan 1 OOM cgroup. Status skip tetap dicatat; delapan kasus tersebut kemudian dieksekusi dengan precondition masing-masing. |
| Real Docling/parser | `bash scripts/dev-macos.sh parser-integration`: **7 passed** (digital PDF, scanned PDF, PNG, JPG, JPEG, rotated JPEG, complex DOCX), 1 deprecation warning. Hash sumber, anchor original, transform image, dan checkpoint replay terverifikasi. Synthetic fixture saja; bukan benchmark kapasitas. |
| Controlled cgroup OOM | **1 passed** memakai container Linux sekali pakai `--memory=1g --memory-swap=1g --network none`; child 2 GiB memicu counter cgroup dan dipetakan ke `parser_memory_limit`. Tidak menghabiskan RAM host. |
| Browser smoke | Playwright di Chromium 156.0.8078.4: **9 passed, 1 skipped**. Login, error/security, logout, viewport 360/768/1440, admin isolation/provisioning, dan manual context approval lulus. Skip adalah redirect alias loopback opsional karena `ALIGN_ALIAS_BASE_URL` tidak diset; tidak dihitung sebagai pass. |
| Cleanup dan development stack | Container, volume, network, serta image tags dengan project `align-macos-clean-1792747427` dihapus. Label project kini kosong; core development containers tetap hidup, API/web/PostgreSQL/Redis sehat. Tidak ada volume development dihapus dan tidak menjalankan prune. |

Percobaan pertama BuildKit gagal dan daemon kemudian tak dapat diakses saat ruang host sekitar 181 MiB. Sesudah Docker aktif kembali, clean run kedua selesai. Host memiliki 24 GiB bebas sebelum build kedua; setelah test root dihapus dan test resource dibersihkan, `df` melaporkan 11 GiB bebas. Sebab akar EROFS tidak dipastikan. Tidak ada `docker system prune`; hanya volume/container/network dan image tag dengan label/nama project test yang dibuang. Core `align-*` development containers tetap hidup dan health utama lolos pemeriksaan. Ringkasan privat tanpa secret tersimpan di `/private/tmp/align-macos-clean.X9RBy5/run-summary.md`; hanya receipts kecil dipertahankan, bukan `.env` synthetic atau toolchain.

## P1/P3: konfigurasi dan batas yang terbukti

- `scripts/live-smoke.py` hanya membuktikan preflight offline. Dengan `.env` privat saat audit dan template release, statusnya `failed` untuk runtime mode, flag paid-call, model binding, dan release/runtime binding; ada pemeriksaan yang pending untuk manifest, otorisasi, cap, dan eksekusi. Output tidak berisi nilai key. Provider request count 0.
- Template model/rates/routing/batching/release sengaja memakai nilai placeholder/unverified. Tidak dapat diaktifkan. Baseline routing kecuali livecheck ID/allow, batch 1, `capability_smoke=true`, `empirical_verification=false` dipertahankan. Tidak ada klaim eligibility atau tarif verified.
- `scripts/production-preflight.py` menjalankan pemeriksaan statis/read-only. `docker compose ... config --quiet` pada contoh production stack ter-render dengan fixture test sintetis. Fixture tersebut bukan deployment atau storage runtime.
- Preflight fixture menghasilkan `incomplete` (read-only, tanpa failed static checks), dengan enam evidence masih pending: `backup_restore_evidence`, `policy_evidence`, `release_eligibility_evidence`, `operator_alert_evidence`, `rollback_evidence`, `egress_firewall_evidence`. Nilai fixture/credential sintetis tidak tampil pada report.
- Production env templates belum terisi. Digest image, DNS/TLS, operator private files, cert/key, bucket/auth runtime dan target server tetap diperlukan. Jangan memasang template sebagai secret hidup.
- Batas parser pada konfigurasi produksi tetap 8 GiB untuk Docling dan 9 GiB container; tidak diturunkan. Tidak ada benchmark kapasitas atau ukuran host produksi. RPO 24 jam/RTO 8 jam tetap sasaran panduan, belum keputusan institusi dan belum terukur.
- Laporan preflight tidak mencakup runtime `head_bucket`, TLS chain, SeaweedFS auth/scope, object permissions, backup round-trip, restore/reconcile, retention, alert delivery, egress firewall, load 20 sesi, atau rollback.

## Verifikasi implementasi sesi

- `ruff check scripts/live-smoke.py scripts/production-preflight.py tests/unit/test_standalone_production_preflight.py`: lulus.
- `pytest tests/unit/test_standalone_production_preflight.py -q`: **3 passed**; fake/offline checks, tidak ada network provider.
- `bash -n scripts/dev-macos.sh scripts/verify-macos-clean.sh`; `py_compile` pada dua CLI: lulus. Harness dynamic CIDR terbukti pada clean run kedua; cleanup EXIT trap dibatasi ke project dan memiliki timeout, tetapi jalur failure trap tidak terpicu dalam run yang lulus.
- `bash scripts/verify-macos-clean.sh`: **exit 0**, hasil clean setup diteruskan sampai integration dan smoke login. Suite unit menyimpan hitungan skip 46 (tidak lulus); suite integration terpisah memuat 38 pass/8 skip seperti rincian di atas.
- `bash scripts/dev-macos.sh parser-integration`: **7 passed**; controlled disposable-container OOM test: **1 passed**.
- Browser `auth.spec.ts` tanpa parser-intensive KK/RPP case: **9 passed, 1 skipped** pada Chromium yang diunduh ke cache isolated.
- `docker compose --env-file .env config --quiet` untuk Compose development: lulus sebelum daemon kehilangan akses.
- Production overlay config render dengan synthetic env fixture: lulus. Bukan operasi runtime.
- `git diff --check`: lulus pada pemeriksaan akhir dokumen.

## Update wiring credential — 23 Oktober 2026

- Compose development meneruskan `OPENAI_API_KEY` dari `.env` host hanya ke API dan general worker. Compose produksi memakai variabel privat terpisah `ALIGN_PRODUCTION_OPENAI_API_KEY`; key development tidak diwariskan ke overlay produksi. Parser worker tidak menerima key pada kedua konfigurasi.
- Container API dan worker direcreate agar konfigurasi berlaku. Pemeriksaan runtime di dalam keduanya melaporkan `provider_mode=mock`, `paid_calls_enabled=False`, dan credential tersedia. API kembali `healthy`; worker `running`.
- Render Compose sintetis memverifikasi forwarding key untuk API/worker, mock mode, paid calls nonaktif, key parser kosong, serta isolasi key development/production. `docker compose config --quiet` lulus; tes preflight **3 passed**; `git diff --check` lulus.
- Tidak ada request ke OpenAI. Key tidak dicetak atau divalidasi terhadap provider, jadi validitas credential belum diketahui. Model role live masih belum diisi, release aktif tetap mock/synthetic, dan live smoke tetap fail-closed sampai spend cap atomik serta persetujuan yang diperlukan tersedia.

## Pekerjaan tersisa dan pemilik

1. **Pemilik software/dev:** P0 clean-checkout, required integration, Docling matrix, cgroup OOM, dan browser smoke sudah dibuktikan pada fixture synthetic. Satu browser alias check tetap skipped karena optional alias tidak diatur. Jangan mengklaim skip sebagai pass.
3. **Pemilik akun/budget:** verifikasi model role dan snapshot dari akun, tarif text/image/embedding yang berlaku, budget, reserve, per-run cap dan authorization reference. Setelah P1 spend cap atomik terhubung ke reservation sebelum tiap request, tinjau report preflight lalu otorisasi P2 secara eksplisit; jangan menjalankan probe berbayar.
4. **Operator target Linux:** isi digest/config private yang sah, deploy hanya setelah review; uji TLS/storage/auth/least privilege/parser/egress/alert/rollback dan backup terenkripsi off-host dengan restore pada target kosong.
5. **Institusi/reviewer:** tetapkan tujuan/policy/provider izin, retensi, key custody, RPO/RTO dan kanal respons; sediakan data/acuan berizin serta protokol eligible sebelum P4/P5. Jangan membuat keputusan ini atas nama institusi.

Traceability operasi tambahan `OPS-01`–`OPS-06` diperbarui pada `requirements_traceability.md`. Status historical B0–B8 tidak ditulis ulang.

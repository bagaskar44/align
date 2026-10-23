# Handover — kesiapan produksi standalone

Audit dan implementasi panduan `docs/implementation/standalone-production.md`, 23 Oktober 2026 (Asia/Jakarta). Ini catatan hasil aktual, bukan pernyataan siap pilot.

## Snapshot dan batas

- Checkout: branch `development`, baseline `d67f63a43e569358e450a62a809b545c969448f5`; master `ALIGN_IMPLEMENTATION_SPEC.md` SHA-256 `6a872b132f5721eda817af89093b899970436169a70b93fbcf4778bcf4a7b254`.
- Host yang teramati: macOS 26.5.2, Darwin 25.5.0, arm64. Docker Desktop 4.96.0 / Engine 29.9.1 / Compose 5.6.0; VM Docker sebelumnya terukur memiliki RAM 12,526,637,056 byte. Model Mac dan RAM host saat audit tidak terbaca dari sandbox, sehingga tidak diklaim.
- Clean-checkout P0 memakai toolchain terisolasi, bukan instalasi ignored host: Node v22.22.0, pnpm 10.18.3 (repository pin), uv 0.8.22, CPython 3.12.11. `uv.lock` dan `pnpm-lock.yaml` sama sebelum/sesudah instalasi.
- Clean run terakhir memakai project Docker `align-macos-clean-1792747427`; seluruh container, volume, network, dan image tag milik project itu telah dihapus setelah verifikasi label. Development containers tetap berjalan. Ringkasan bukti tanpa secret dan receipts revision/lockfile ada di `/private/tmp/align-macos-clean.X9RBy5` (file ringkasan mode 0600); checkout, `.env` synthetic, toolchain, browser, dan cache test sudah dihapus.
- Tidak ada nilai secret, key, token, password, dokumen nyata, atau output provider yang disalin ke handover. Tidak ada provider call, release live, budget top-up, institusi/data nyata, deployment, B9, commit, atau push.
- Perubahan lokal lain seperti `.DS_Store` dan `.vscode/` dibiarkan. Tidak ada lockfile, OpenAPI/client, kontrak akademik, resource guard parser, atau requirement master yang diubah. Follow-up P1 di bawah menambahkan migration `0020_paid_call_run_caps`; migration belum diterapkan atau diuji runtime.

## Status tahap

| Tahap | Status | Hasil dan alasan |
|---|---|---|
| P0 — verifikasi runtime | `passed` untuk checkout bersih/manual/mock | Clean snapshot kedua menyelesaikan install, API/worker/web/parser image builds, migration, seed/idempotensi, mock baseline, login, integration, real Docling, OOM, dan browser smoke. Percobaan pertama pernah gagal dengan BuildKit `read-only file system` ketika host tersisa 181 MiB; setelah Docker pulih dan host menunjukkan 24 GiB bebas, rerun lulus. Ini korelasi resource, bukan root cause yang sudah dibuktikan. P1/P3 readiness tetap belum lulus. |
| P1 — persiapan live tanpa biaya | `in_progress` — source tersedia, runtime pending | Migration dan jalur spend cap atomik per-run kini mengikat reservation ke budget account dan release. Helper lokal menyiapkan release/cap; sesuai instruksi, migration, concurrency, build, dan runtime belum dijalankan pada follow-up ini. `live-smoke --execute-live` tetap fail-closed. |
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

## Penyiapan kode tanpa menjalankan tes — 23 Oktober 2026

Atas permintaan pemilik, sesi ini hanya melakukan inspeksi source dan menyiapkan konfigurasi; tidak menjalankan unit/integration/browser test, build, Docker/Compose, atau provider call.

Inspeksi menemukan Compose development/QA meng-override parser document profile menjadi 4 GiB sementara service parser hanya dibatasi 5 GiB. Nilai tersebut tidak dapat memenuhi profile Docling 8 GiB dan container 9 GiB pada master/B3, sehingga worker dapat berhenti sebelum file selesai diproses. Defaults source, Compose development/QA, serta `.env.example` kini selaras ke document/node budget 8 GiB dan parser-worker cgroup 9 GiB; headroom 1 GiB dan `one heavy per node` tetap berlaku. Compose production sudah memakai pasangan 8/9 GiB. Ini memperbaiki konfigurasi, tetapi **belum diverifikasi berjalan**. Container yang sedang aktif tidak direcreate oleh edit ini dan tetap memakai konfigurasi lamanya sampai pemilik menjalankan Compose sendiri.

Provider adapter dan consumer B1/B2/B4/B5 tetap menggunakan jalur ledger yang ada. Runtime default tidak diubah: `mock`, `PAID_CALLS_ENABLED=false`, baseline mock/synthetic tetap aktif. Nilai `OPENAI_API_KEY` tidak dibuka atau diubah. Model role dan verified rates tetap harus dipilih/diisi pemilik; tidak ada tarif atau model yang diasumsikan. `scripts/live-smoke.py --execute-live` masih sengaja fail-closed karena atomic run-level spend cap dan orkestrasi API/worker belum tersedia. Karena itu kode adapter sudah ada, tetapi sesi ini **tidak menyatakan jalur live-smoke siap dijalankan**. Preflight live yang ada tetap offline dan tidak melakukan request.

Follow-up wiring dev: Compose development sekarang meneruskan mode/environment, flag paid-call, model IDs dan dimensi embedding dari private root `.env` secara konsisten ke API, operator CLI, dan general worker. `OPENAI_API_KEY` hanya diteruskan ke tiga service terpercaya tersebut; parser worker ditetapkan `PROVIDER_MODE=off` dan tidak mendapat key, sebab parser tidak memakai LLM. Bootstrap PowerShell menambahkan model/runtime fields non-secret ke `.env.worker`. Default root `.env.example` tetap `mock` dan paid calls `false`. Dengan demikian mengganti key saja tetap tidak mengaktifkan inference; pemilik masih harus memilih model, memverifikasi rates, mengikat dan mengaktifkan release live yang sah, serta menyiapkan budget. Tidak ada live call.

Perubahan sesi belum diuji atau dibangun, sesuai instruksi pemilik. Pemeriksaan pada diff saja bukan bukti runtime. Status parser runtime, LLM live, harga, dan biaya tetap pending sampai pemilik melakukan verifikasi yang diinginkan.

## Implementasi spend cap atomik dan setup live lokal — 23 Oktober 2026

Bagian di atas adalah handover historis sebelum perubahan ini. Sesi ini melengkapi jalur software yang waktu itu masih pending; status P2 tetap pending sampai pemilik menjalankan dan memeriksa capability smoke sintetis. Sesuai instruksi, **tidak ada tes, build, Docker/Compose, browser, atau provider yang dijalankan**. Migration belum diterapkan. Tidak ada secret dibaca ke output atau diubah.

### Implementasi yang sekarang ada di working tree

- `PaidCallRun` menyimpan satu cap positif yang immutable per budget account, binding ke release aktif, referensi otorisasi, operator pembuka, waktu/status dan alasan penutupan. Migration `0020_paid_call_run_caps` menambahkan tabel, unique partial index satu run aktif per account, dan FK `usage_reservations.paid_call_run_id`. Reservation lama tetap nullable untuk kompatibilitas; reservation baru wajib menunjuk run aktif melalui trigger database.
- Sebelum setiap network call, ledger mengambil PostgreSQL transaction advisory lock yang sama, mengunci budget account/job, mengikat release/model/rates aktif, lalu menghitung seluruh liability run. Held, pending/unknown dan reservation tersimpan ikut menghabiskan cap; biaya aktual/reconciliation menggantikan hold sesuai status ledger. Reservation baru ditolak bila `liability + upper_bound` melampaui cap. Guard global dan per-job tetap berlaku. Tidak ada pemeriksaan preflight non-atomik yang dianggap reservation.
- Lock yang sama dipakai saat settlement, unknown billing, recovery reservation yatim, reconciliation, top-up, buka/tutup run, seed saldo awal, dan pergantian release. Trigger reservation menolak run yang tidak aktif/salah akun dan melarang perubahan identitas reservation. Unknown billing tetap memblokir reservation baru sampai direkonsiliasi. Actual cost di luar bound membekukan belanja akun.
- CLI operator menyediakan `live activate-local` dan `live close-local`. Pembukaan menolak runtime yang bukan development/demo/synthetic + live, model role yang tidak cocok dengan runtime, release/rates yang tidak verified, routing selain baseline dengan `allow_live=true`, batching selain 1, cap lebih besar daripada saldo spendable, serta liability reconciliation yang pending. Tidak mengubah syarat promosi atau menghasilkan report eligible.
- `scripts/prepare-live-local.py` membuat manifest immutable berversi dari baseline, memasukkan empat model role, rates yang sudah direview, membuka release capability-smoke batch-1 dan run cap. Ia menjalankan migration/seed pada project Compose terisolasi `align-livecheck-local`, memakai port default 3311, dan tidak memanggil provider saat setup. Project dan volume persisten itu dipakai ulang antar-run agar saldo/ledger kumulatif tidak di-reset oleh seed; tetap terpisah dari development. Generated manifests/metadata berada di `config/live-local/` yang di-ignore; tidak ada file secret di sana. Budget awal berasal dari variabel `.env` hanya saat seed membuat account baru. Seed tidak mengubah account lama. Jangan menghapus volume atau mengganti nama project untuk mengatur ulang budget.
- `scripts/stop-live-local.py --metadata <config/live-local/.../local-run.json>` menutup run yang cocok dengan release metadata lalu menjalankan Compose `down` untuk project itu tanpa `-v`; volume tetap tersedia. Bila penutupan cap gagal, stack tidak dihentikan dan error dilaporkan agar cap tetap aktif selama pemeriksaan.
- Tarif dan model tidak diasumsikan. Pemilik mengisi ID `MODEL_EFFICIENT`, `MODEL_COMPLEX`, dan `MODEL_VISION` persis dari akun pada `.env` privat; `EMBEDDING_MODEL` harus tetap `text-embedding-3-small` dengan 1536 dimensi sesuai master. Siapkan file rates lokal berdasarkan `config/model-rates-live.template.json`, dengan referensi tarif resmi, reviewer, tanggal efektif, mata uang USD dan tarif model yang digunakan. Script memeriksa format/pengikatan tetapi tidak memvalidasi harga kepada provider. Tidak perlu mengubah atau menyalin `OPENAI_API_KEY`.
- `scripts/live-smoke.py --execute-live` masih fail-closed: orchestration skenario akademik lengkap beserta report runner otomatis belum tersedia. Sesudah setup lokal, alur UI manual pada data sintetis dapat dicoba; itu bukan klaim runner otomatis atau hasil eligible/empiris.

### Prosedur pemilik untuk menerapkan dan mencoba

1. Pada `.env` privat, siapkan `APP_ENV=development`, `APP_DEMO_MODE=true`, `DATA_MODE=synthetic`, `PROVIDER_MODE=live`, `PAID_CALLS_ENABLED=true`, tiga ID model provider persis dari akun, `EMBEDDING_MODEL=text-embedding-3-small`, dan `EMBEDDING_DIMENSIONS=1536`. Pertahankan key yang sudah disimpan; script hanya memeriksa keberadaan key dan tidak menulisnya.
2. Buat folder ignored `config/live-local/` bila belum ada, lalu salin template rates ke `config/live-local/model-rates-live.json`. Isi `status=verified`, `currency=USD`, metadata `verification.source_ref`, `reviewed_by`, `effective_date`, serta `input_per_million`, `output_per_million`, `embedding_per_million` untuk setiap model dan `image_per_unit` untuk model vision. Gunakan angka resmi yang sudah diverifikasi pemilik; placeholder/unverified ditolak.
3. Atur `BUDGET_CONFIRMED_USD` dan `BUDGET_RESERVE_USD` di `.env` sebelum penggunaan pertama project lokal. Tentukan saldo berdasarkan dana yang benar-benar disetujui, lalu pilih cap run kecil dan setujui secara terpisah. Cap harus muat pada budget spendable setelah reserve, penggunaan sebelumnya, dan reservation; `BUDGET_PLANNED_TOPUP_USD` tidak dihitung sebagai dana tersedia. Project `align-livecheck-local` mempertahankan DB/volume antar-run, jadi mengulang seed tidak mengisi ulang saldo. Setelah ada biaya, run berikutnya hanya bisa dibuka dari saldo tersisa; jangan hapus volume untuk mengakali batas. Top-up hanya melalui prosedur operator dan bukti pendanaan yang sah.
4. Dari root checkout, jalankan satu command: `python3 scripts/prepare-live-local.py --rates-file config/live-local/model-rates-live.json --max-spend-usd <cap-yang-disetujui> --authorization-ref <referensi-otorisasi>`. Bila port 3311 sudah dipakai, tambahkan `--port <port-lokal-yang-kosong>`. Command mengeluarkan origin lokal dan path metadata run; periksa output agar project/origin benar sebelum login.
5. Buka origin yang dicetak dan login dengan akun sintetis `dosen@align.local` (atau akun demo lain). Password seed diambil dari `ALIGN_SEED_PASSWORD` privat `.env`; tidak pernah dicetak script. Coba satu alur sintetis yang dipilih. Jangan mengirim file/data institusi atau mahasiswa. Setiap paid call dapat memakai hingga reservation bound; berhenti sebelum menambah pekerjaan setelah cap tercapai, respons billing tidak diketahui, atau ada error.
6. Setelah selesai, jalankan `python3 scripts/stop-live-local.py --metadata config/live-local/<versi-yang-dicetak>/local-run.json`. Run ditutup sebelum container/network dihentikan; volume project dipertahankan. Pending billing harus direkonsiliasi melalui prosedur operator yang tercatat sebelum membuka run baru.

### Status bukti dan keterbatasan

Implementasi source telah direview, termasuk signature semua pemanggil `reserve_call`, role privilege, transaksi settlement/reconciliation dan prosedur Compose. Tidak ada eksekusi validasi sesuai permintaan. Karena itu migration SQL/trigger, privilege operator, concurrency overspend, `ruff`/mypy, setup script, login/UI, provider response, nominal cost dan cleanup **belum diverifikasi**. Tidak ada versi runtime/hardware baru yang diukur dan biaya sesi ini USD 0 dengan provider request 0. Hasil lokal pertama harus diperlakukan sebagai capability-smoke sintetis, bukan bukti kualitas akademik, promosi, report eligibility, atau kesiapan pilot.

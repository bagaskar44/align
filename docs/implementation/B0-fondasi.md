# B0 — Fondasi

**Status implementasi: BELUM DIMULAI. Hasil pengujian: BELUM DIJALANKAN.**

[Indeks dan matriks](README.md) · [Master normatif](../../ALIGN_IMPLEMENTATION_SPEC.md) · [Tahap berikutnya: B1](B1-identitas-dan-konteks.md)

## 1. Tujuan dan entry gate

Sediakan fondasi runnable untuk modular monolith ALIGN, sehingga checkout baru dapat install/build, menjalankan service, migrate, seed sintetis dan login. B0 menyiapkan kontrak yang dipakai tahap berikutnya tanpa mengklaim alur akademik lengkap.

Entry gate: master v2 tersedia; inspeksi isi repository dan instruksi lokal; catat OS, toolchain, Docker/Compose dan kapasitas host yang benar-benar tersedia. Saat paket ini disusun repository hanya berisi master dan paket pendamping, sehingga jangan mengasumsikan framework, Git, executable `align`, CI provider atau migration sudah ada.

## 2. Scope dan referensi

Normatif: master Bagian 0–4.1, 15, 16.1–16.2, 17–18, 19.1, 20, 22–24. Kontrak spesifik tahap lain tetap dirujuk ketika membuat shared type.

- Mulai FR-01, FR-16–FR-17, FR-19, FR-22, FR-24; siapkan trace seluruh FR-01–FR-24.
- Berlaku sejak awal INV-05–INV-06, INV-09, INV-16; siapkan constraint bagi seluruh invariant saat tabel terkait dibuat.
- Bukti awal AC-MVP-01, AC-MVP-38–AC-MVP-40, AC-MVP-42–AC-MVP-44, AC-MVP-49, AC-MVP-55, AC-MVP-58–AC-MVP-59. Sebagian besar belum dapat fully passed pada B0; lihat matriks indeks.

B0 mencakup infra, schema awal, config, adapter contract/mock, login dasar yang aman, UI shell, seed, CI dan traceability. Assignment, submission, review, executor dan penelitian empiris diselesaikan tahap pemiliknya. Jangan membuat mock screen yang mengaku alur tersebut sudah bekerja.

## 3. Urutan implementasi

### B0.1 — Repository dan toolchain

1. Buat struktur master Bagian 18.1: `apps/web`, `services/api`, `services/worker`, `packages/domain`, `migrations`, `prompts`, `config`, `fixtures/synthetic`, tests, research dan scripts.
2. Gunakan modular monolith FastAPI; API dan Celery worker berbagi domain/schema, dengan transaction boundary di service. Web Next.js/React memakai API same-origin. Jangan menambah microservices, autonomous multi-agent atau GPU serving.
3. Verifikasi patch kompatibel untuk baseline Python 3.12, Node 22, Next 16, React 19, TypeScript 5, Tailwind 4, PostgreSQL 17, pgvector 0.8, Redis 7, Celery 5, SQLAlchemy/Pydantic 2, Alembic 1 dan parser libraries master. Periksa dokumentasi resmi versi yang dipilih saat build; pin exact dependencies dengan `uv` dan `pnpm`. Penyimpangan major dicatat sebelum dipakai.
4. Sediakan command teruji untuk install, frontend production build, backend startup, migrate, bootstrap, seed, dev run, lint/typecheck, unit/integration/E2E smoke. Catat command riil dan exit code, bukan nomor patch atau hasil dugaan.

### B0.2 — Service dan konfigurasi

1. Compose menyediakan web, api, worker, parser-worker, outbox-dispatcher, scheduler, postgres+pgvector, redis, private object-storage dan reverse proxy. Worker parser/general menggunakan codebase sama dengan queue berbeda.
2. Proxy `/` ke web dan `/api/v1` ke API tanpa mengubah kontrak path. Gunakan named volumes dan queue prefix per deployment. Database/broker/bucket bukan layanan publik.
3. Siapkan role migration owner, API, analysis worker dan operator; tabel keputusan/approval/publications yang ditambahkan kemudian langsung memiliki deny-write worker. Parser child tidak menerima DB/provider credentials.
4. Buat typed config dan `.env.example` seluruh Bagian 18.3, termasuk nilai byte, file/task/run limits, secrets backend, model/rate/resource/routing/batching refs, retensi dan feature flag. Jangan menafsirkan environment variable sebagai top-up budget setiap restart.
5. Default `PROVIDER_MODE=mock`, `DATA_MODE=synthetic`, `LMS_ADAPTER_ENABLED=false`. Startup memvalidasi mode, dimensi vector 1536 dan incompatible config; production menolak debug/default bootstrap password serta mock tak berlabel demo sintetis.
6. Catat host capacity; profile parser native 2 GiB/Docling 8 GiB bukan total RAM deployment. Kekurangan kapasitas parser dilaporkan capability/blocked reason, bukan menonaktifkan readiness manual atau memilih profil light diam-diam.

### B0.3 — Persistence dan shared contracts

1. Buat migration awal untuk users/sessions, metadata course/enrollment, manifests/releases dan fondasi jobs/stages/outbox/audit/idempotency/operations yang diperlukan sekarang. Tambahkan tabel domain lain melalui migration tahap pemiliknya, bukan JSON placeholder tanpa FK.
2. Terapkan UUID v4, timestamps UTC, revision integer, canonical hash helpers, decimal string serialization dan error envelope. Database menjadi sumber status/session/ledger resmi; Redis hanya broker/koordinasi sementara.
3. Definisikan job state/stage contracts Bagian 17 dan typed subject/scope. Dispatcher mengirim ID sesudah outbox commit, worker memeriksa lease/input hash, stage idempotent, dan scheduler/reaper mempunyai entrypoint runnable.
4. Definisikan timeout/lease defaults: soft/hard 120/150 detik, broker visibility 300, lease 180, heartbeat 30, reaper 60. Enforcement parser/admission lengkap B1/B3; waktu run dibagi melalui reservations, bukan memberi cap baru per retry.
5. Siapkan `requirements_traceability.md` seluruh ID, status pending dan lokasi test yang nantinya nyata; jangan menaut file implementasi yang belum ada seolah selesai. Buat struktur research/iterations dan template keputusan lokal tanpa hasil penelitian rekaan.

### B0.4 — Provider, release dan pengeluaran

1. Tetapkan interface Bagian 13.2: `extract_fields`, `rerank`, `interpret_evidence`, `verify_claim`, `draft_feedback`, `interpret_region`, `embed`. Request/response memuat call/job/manifest/policy/scope/limits dan usage/status typed.
2. Mock deterministik menggunakan fixtures dan schema bisnis yang sama; dimensi vector fixture 1536. Tidak ada fallback live ke mock tersembunyi. UI menampilkan label simulasi.
3. Bootstrap typed manifests dan baseline routing/batch=1; payload immutable/fingerprint versioned. Baseline tidak berarti parser profile atau live model sudah empirically verified. Candidate batch 3/5 tidak dapat aktif.
4. Sediakan guard tunggal sebelum paid adapter. Jika rate/capability/reservation/ledger/limit/reconciliation belum lengkap, **tolak call live sebelum network**. B1/B2 boleh tetap mock/manual; bila suatu tahap mengaktifkan live, implementasikan semua prasyarat Bagian 17.3 dahulu.
5. Budget bootstrap dirancang sekali: confirmed USD25, reserve USD5, planned topup USD0. Tidak melakukan paid smoke di B0 tanpa jalur finansial sah. Nilai biaya bukan float.

### B0.5 — Login, UI shell dan CI

1. Implementasikan login/logout/me slice sesuai Bagian 4.1: Argon2id, hashed 32-byte session token, cookie flags, timeout server, Origin/rate limit, stable session CSRF. Safeguard route yang diperkenalkan sudah berlaku; jangan membuat bypass login sementara.
2. Seed development berisi akun sintetis dosen/mahasiswa/admin dan minimal dua course. Tambahkan assignment, kelompok dan file melalui seed tahap berikutnya setelah schema tersedia. Seed rerunnable tidak boleh mereset ledger atau menimpa snapshot yang sudah dipakai.
3. Bootstrap production/admin lewat stdin/secret manager; tidak ada password tetap/secret dalam source. Login dengan temporary password hanya menuju flow perubahan password saat B1 menambah provisioning lengkap.
4. UI shell bahasa Indonesia memakai warna/nav master 19.1, course list berizin sebagai landing, loading/empty/error dan keyboard/focus dasar. Tidak ada route dashboard/nilai/analytics.
5. CI menjalankan install frozen lockfile, lint/typecheck, frontend build, backend/unit/API smoke dan integration PostgreSQL+pgvector nyata. Jika provider CI repository belum tersedia, berikan script pipeline runnable lokal dan catat aktivasi CI remote belum tersedia; jangan mengirim atau membuat remote tanpa instruksi.

## 4. Kontrak interface

| Interface | Kontrak B0 |
|---|---|
| `POST /api/v1/auth/login` | Email/password; generic failure; set cookie dan ringkasan user/CSRF, no-store |
| `POST /api/v1/auth/logout` | CSRF/Origin; revoke session; 204 |
| `GET /api/v1/me` | User, active memberships, stable CSRF dan public runtime limits; tidak mengirim secret/tarif |
| `/health/live` | Proses hidup |
| `/health/ready` | DB/storage/broker yang diperlukan; provider tidak menentukan kesiapan manual |
| `/health/providers` | Internal, status adapter aman tanpa key |
| Worker/outbox | Payload queue ID/referensi saja; commit domain/outbox sebelum delivery; status belum mengaku analisis akademik selesai |
| Model adapter | Schema strict dan authorized payload, mock berlabel; paid path fail-closed sampai guard lengkap |

Semua JSON `extra=forbid`; actor/hash/object_key/status/actual cost diisi server. Mutasi mutable memakai expected_revision; aksi versi/job idempotency UUID, replay body beda 409. Error mengikuti Bagian 16.1; detail DB/stack/secret tidak masuk response.

Kontrak CLI `align release register --file PATH` dan `align release activate RELEASE_ID` disiapkan sebagai entrypoint/operator service. B0 hanya boleh register/activate baseline yang capability-nya benar-benar tersedia dan lolos validasi; verification parser/live report diperluas tahap pemiliknya. Endpoint mahasiswa tidak mengonfigurasi model.

## 5. Verifikasi dan bukti

| Skenario | Ekspektasi dan bukti |
|---|---|
| Checkout bersih, install frozen, Compose, migrate dan seed dua kali | Service inti hidup, migration konsisten, seed tidak menggandakan akun/reset state; simpan command/log aman |
| Login dosen/mahasiswa, `/me`, logout, expiry | Cookie/token hash/CSRF benar; request setelah revoke/expiry 401; tidak ada password/token mentah pada log |
| Request login invalid dan cross-origin mutation | Error generik/rate limit/Origin check; tidak membocorkan akun |
| Config dimensi salah, secret kosong live, unknown rate | Startup/paid call ditolak spesifik; bukan biaya nol atau silent fallback |
| Provider off/mock | UI jelas simulasi; foundation tetap dapat dipakai; catat manual end-to-end belum tersedia sampai B4 |
| Queue duplicate/crash pada dummy stage nonakademik | Outbox dapat dikirim ulang, output stage idempotent; jangan klaim AC-MVP-40 penuh sebelum domain analysis ada |
| Build/lint/typecheck/unit/integration nyata | Report menyebut environment, command, exit code dan hasil; jalur tak dijalankan tetap pending |
| Scan source/config/log | Tidak ada secret, skor/dashboard, password produksi tetap atau worker academic-write permission |

Gunakan pytest, PostgreSQL+pgvector nyata untuk integration, dan Playwright smoke untuk UI. Verifikasi fallback readiness bila provider unavailable dan resource parser belum tersedia dilaporkan terpisah. Jangan membuat test yang hanya memeriksa keberadaan file.

## 6. Gate selesai dan handover

- [ ] Lockfiles, migration awal, Compose services, typed config dan `.env.example` tersedia.
- [ ] Install/build/start/migrate/seed/login dibuktikan pada environment tercatat.
- [ ] Adapter mock dan label simulasi bekerja; live guard menolak prasyarat tidak lengkap.
- [ ] Baseline manifests dan interface shared tersedia tanpa klaim verification empiris.
- [ ] CI lokal/tersedia, test report dan traceability awal aktual.
- [ ] Handover `docs/handover/B0.md` memuat command tepat, hasil, limitations dan kebutuhan B1; seluruh status awal diperbarui hanya berdasarkan bukti.

**BERHENTI setelah B0.** B1 hanya dimulai atas inisiasi pengguna. Gate B0 tidak menyatakan AC-MVP-01 lengkap atau aplikasi siap memakai data nyata.

## 7. Prompt agent

~~~text
Implementasikan hanya tahap B0 ALIGN. Baca ALIGN_IMPLEMENTATION_SPEC.md, docs/implementation/README.md, dan dokumen B0 ini. Inspeksi repository/toolchain terlebih dahulu. Bangun fondasi runnable sesuai master, pin dependency yang diverifikasi, gunakan data sintetis dan mock provider berlabel, dan lindungi paid path sebelum network. Selesaikan kontrak, migration, service, UI shell/login, CI, fixture dan verifikasi tahap ini. Catat command dan hasil aktual dalam traceability serta docs/handover/B0.md; jangan mengklaim alur akademik atau penelitian telah selesai. Setelah gate dinilai dan handover diberikan, berhenti; jangan mulai B1 otomatis.
~~~

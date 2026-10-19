# B9 — Moodle Sandbox

**Status implementasi: BELUM DIMULAI. Hasil pengujian: BELUM DIJALANKAN. Tahap ini di luar MVP B0–B8.**

[Indeks dan matriks](README.md) · [Master](../../ALIGN_IMPLEMENTATION_SPEC.md) · [Predecessor: B8](B8-handover-dan-pilot-readiness.md)

## 1. Tujuan dan entry gate

Tambahkan sandbox Moodle sebagai adapter terpisah: secure LTI1.3 launch, mapping identitas/course/assignment, read-only final submission import dan akses feedback released di ALIGN. Core tetap berfungsi mandiri dan tidak mengirim grade atau feedback ke gradebook Moodle.

Entry wajib: B8 standalone MVP dan handover tersedia; pengguna menginisiasi B9 secara terpisah. Inspeksi izin/ketersediaan sandbox sebelum mengaktifkan koneksi. Baseline target master adalah Moodle4.5; verifikasi versi aktual dan dokumentasi resmi saat implementasi. Tidak mengasumsikan token WS, registered platform, domain atau izin institusi sudah tersedia.

Konfigurasi operasi yang diperlukan: issuer/client/deployment IDs, allowlisted auth/token/JWKS origins, callback origin, LTI key material bila protocol memerlukan, Moodle WS origin/token/functions, explicit subject/user/enrollment dan course/assignment mappings. Secrets hanya backend. Tanpa konfigurasi, adapter/fixture contract tests dapat dibangun, feature flag tetap off dan verification sandbox nyata tetap pending.

## 2. Scope dan referensi

Normatif: master Bagian 1 DEC-02/09/12/15, 4, 6–8, 14–20, 22.1, 23.3 dan 24.

- Gate AC-SBX-01–AC-SBX-05, terpisah dari AC-MVP-01–AC-MVP-65.
- Regresi FR-01, FR-05–FR-06, FR-14–FR-16 dan INV-01–INV-12/INV-14/INV-16 pada jalur integrasi.
- Tidak ada grade passback/AGS, Moodle grade writes, automatic feedback posting, produksi LMS/SSO atau automatic account binding berdasarkan email.

Core checks/review/publication tidak mengimpor library Moodle. LTI menangani launch/context/roles; Web Services membaca metadata/files sah; ALIGN Core tetap sumber versioned checks/review/publication.

## 3. Urutan implementasi

### B9.1 — Adapter, configuration dan mapping

1. Tambahkan modul integration boundary yang memanggil service domain B1–B6; flag `LMS_ADAPTER_ENABLED=false` default. Disabled routes tidak membuat network call atau mengubah core authentication.
2. Migration `lti_platforms` menyimpan issuer/client_id/deployment_id/auth/token/JWKS URLs yang terdaftar allowlist. Platform bootstrap oleh operator; launch input tidak menentukan JWKS/auth URL arbitrary.
3. `lti_identity_bindings` unique issuer/client/deployment/subject→local_user_id. Binding eksplisit; email sama bukan bukti account identity. Role claims tidak memberi global admin/course access yang belum dipetakan.
4. `lms_course_assignment_mappings` mengikat external context/resource assignment ke local course/approved AssignmentVersion dengan metadata/revision/audit yang diperlukan. Local rubric/instruction sudah approved/published; import tidak mengganti paket resmi.
5. `lms_import_events` menyimpan external source IDs/version/hash, owner/group/roster mapping, local SubmissionVersion, status/idempotency. Semua relations diperiksa local scope/enrollment.

### B9.2 — LTI launch

1. Implementasikan OIDC/LTI1.3 login sesuai dokumentasi resmi versi/platform yang benar-benar dipakai. Login menyimpan random state+nonce single-use TTL5menit terikat registered platform/session flow.
2. Validasi state/nonce, pinned/allowlisted JWKS signature/allowed algorithms, issuer/client/deployment, audience/azp, exp/iat, message type/version, resource link/context dan roles. Reject replay/expired/mismatched state sebelum membuat session/akses objek.
3. Default launch top-level/new tab; jangan bergantung third-party cookies. Form POST launch mempunyai validasi state/nonce one-time tersendiri dan tidak melemahkan CSRF endpoint core.
4. Sesudah launch valid, lookup binding/enrollment/mapping aktif dan buat session lokal dengan policy B1. Missing/revoked/uncertain binding atau role/context mismatch diblok dengan alasan aman.
5. Lecturer menuju authorized workspace; student menuju published result miliknya. Valid JWT tidak otomatis memberi akses source course/group lain atau internal AI/review.

### B9.3 — Read-only import

1. WS adapter memakai akun/token minimum dan fungsi yang diaktifkan admin sandbox. Verifikasi nama/ketersediaan fungsi pada server actual; contract fixtures tidak membuktikan instalasi mengaktifkannya.
2. Import hanya final/submitted attempt yang dipilih dosen dan telah dipetakan. Authorize external course-assignment-owner/group/roster/current enrollment dan local published assignment sebelum mengambil file.
3. Download origin allowlisted, file metadata authorized, byte/MIME/container limits B3 dan hash checking. Jangan mengikuti redirect host sembarang atau URL yang disisipkan isi dokumen. WS token tidak masuk browser/log/export.
4. Gunakan ingestion service bersama upload domain untuk validasi/private storage/manifest/provenance, tanpa menyamar sebagai aksi upload dosen atas nama mahasiswa pada core endpoint mahasiswa. Actor importer/dosen/external provenance tercatat eksplisit.
5. Source identik→event/import/version sama; attempt/content berubah→SubmissionVersion immutable baru. Lock/idempotency/event uniqueness mencegah concurrent duplicate import. Jangan menyisipkan file baru ke manifest lama.
6. Imported version mengikat explicit local AssignmentVersion dan historical roster; group access tetap current active enrollment+snapshot roster. Mapping ambiguity/revocation/unknown version→blok objek, bukan tebak owner.
7. Commit local version/job/outbox melalui transaction service seperti finalize. Broker outage tidak menghilangkan imported final; retries/resume/file hashes dan limits tetap. Paid analysis hanya dengan adapter/ledger policies core.
8. Original Moodle submission tidak dimodifikasi. Tidak melakukan grade write, AGS, pengiriman email atau posting feedback otomatis.

### B9.4 — Feedback access dan operasi

1. Moodle activity/link meluncurkan ALIGN; feedback tetap Publication snapshot core. Student hanya released/nonwithdrawn miliknya, internal reviews/candidates/prompts/tariffs tidak bocor.
2. Publication source label dan import origin/version jelas; import baru tidak membuat hasil lama terlihat sebagai feedback versi baru. Withdrawal/history mengikuti B4 tanpa fork serializer.
3. Admin/dosen authorized dapat melihat metadata mapping/import/status seperlunya; admin global tidak mendapat isi akademik tanpa enrollment dosen. UI tidak menjadi dashboard kelas/integrasi.
4. Logs/audit hanya aman IDs/status/policies; secret rotation/revocation dan disabled flag diuji. Dokumentasikan operator setup dan actual sandbox verification terpisah dari production readiness.

## 4. Kontrak interface dan state

Daftar endpoint master22.1 memakai konvensi prefix `/api/v1` master16.1. Login sudah ditulis dengan prefix lengkap; shorthand `/lti/launch` dan endpoint mapping/import Moodle didokumentasikan sebagai path di bawah prefix yang sama pada OpenAPI. Jangan menambah segmen `integrations` pada launch atau membuat alias publik tanpa kebutuhan kontrak.

| Surface normatif | Kontrak |
|---|---|
| `/api/v1/integrations/lti/login` | Third-party initiated login menurut LTI/OIDC, registered platform only, state/nonce5menit; method/protocol validation diuji terhadap sandbox aktual |
| `/api/v1/lti/launch` | Validasi form POST launch, state/nonce/JWT sekali pakai, explicit local binding lalu session/authorized destination |
| `/api/v1/integrations/moodle/mappings` | Authorized admin/lecturer metadata mapping; academic access tetap enrollment; revision/audit/CSRF pada mutasi |
| `/api/v1/integrations/moodle/imports` | Lecturer memulai import attempt final yang sah; idempotent event/job/status, bukan student spending endpoint |
| `/api/v1/integrations/moodle/imports/{id}` | Scoped import status aman, local/external version refs yang diizinkan |

Verb/payload WS mengikuti official functions yang diaktifkan server dan recorded contract fixture. Resource action import mengikuti konvensi POST untuk pembuatan dan GET untuk status; mapping CRUD tetap hanya operasi metadata yang diperlukan. Jangan mengubah signature/CSRF core demi memenuhi launch.

Integration state/job/error memakai typed contract/common error envelope, immutable source/version binding dan existing outbox/recovery. Missing platform config tidak mengubah aplikasi core menjadi not-ready. Source/assignment mapping yang berubah tidak menulis ulang event/history lama.

## 5. Verifikasi

| ID/fixture | Hasil wajib |
|---|---|
| AC-SBX-01: registered platform valid launch dan explicit binding | Subject/course/assignment/role sah→local session dan destination authorized |
| AC-SBX-02: bad signature/issuer/aud/azp/nonce/state/expiry/deployment/algorithm | Ditolak, replay single-use gagal; arbitrary JWKS/auth URL tidak diambil |
| AC-SBX-03: identical retry/concurrent import dan changed final source | Satu event/version/job untuk identical; changed source membuat versi baru/hash baru |
| AC-SBX-04: dua course/dua kelompok/revoked user/new member | Scope dan roster history benar; role claim/email saja tidak memberi izin |
| AC-SBX-05: student launch sebelum release/withdrawn/old/new publication | Hanya safe technical status atau released snapshot miliknya, tanpa grade writes/internal review |
| WS disabled function/invalid token/foreign redirect/MIME/hash mismatch | Safe failure/blocked import; no secret leak/unsafe fetch; core tetap usable |
| Broker/provider/storage failure selama import | Committed local manifest/outbox recoverable; paid attempts tetap reservation/reconciliation |
| Flag off dan regression standalone | Tidak ada external call atau dependency auth/submission pada Moodle; manual/mock MVP tetap berjalan |

Sediakan adapter response fixtures dan contract/security tests tanpa server, lalu integration tests terhadap sandbox nyata yang diotorisasi. Report mencatat server version, enabled functions, platform config refs tanpa secret, two-course/group scenario, actual results dan batas. Mock-only tidak menjadi klaim integrasi nyata/produksi telah diuji.

## 6. Gate dan handover

- [ ] LTI/platform/binding/mapping/import contracts terimplementasi dan seluruh AC-SBX-01–AC-SBX-05 diuji.
- [ ] Replay/JWT/allowlist/owner/roster/revocation/idempotency dan read-only behavior terbukti.
- [ ] Actual sandbox verification diberi hasil nyata; bila akses/config belum tersedia, status itu pending dan jangan menyatakan sandbox terverifikasi.
- [ ] Feature flag off default, core regression dan no-grade-write audit lulus.
- [ ] `.env.example`, OpenAPI/client, migrations/operator setup dan traceability AC-SBX ditambahkan tanpa secret.
- [ ] `docs/handover/B9.md` memisahkan software/mock tests, actual sandbox results, limitations dan pekerjaan produksi di luar scope.

**BERHENTI setelah B9.** Tidak ada aktivasi integrasi produksi atau perluasan izin/data otomatis.

## 7. Prompt agent

~~~text
Implementasikan hanya B9 Moodle sandbox setelah ada inisiasi terpisah dan handover B8. Baca master22.1/23.3, indeks dan dokumen ini; verifikasi versi/config sandbox aktual. Pisahkan LTI1.3, read-only WS adapter dan ALIGN Core, pertahankan feature flag off default. Implementasikan registered platform/nonce/JWT/replay checks, explicit identity/course/assignment/owner mappings, immutable idempotent final-source import dan published feedback access. Jangan binding dari email, mengikuti arbitrary redirect/JWKS, menulis grade atau mengubah original Moodle. Uji AC-SBX-01–05 dengan fixtures lalu sandbox authorized bila tersedia; laporkan pending actual integration secara jujur. Perbarui traceability/docs/handover/B9.md, lalu berhenti tanpa produksi activation.
~~~

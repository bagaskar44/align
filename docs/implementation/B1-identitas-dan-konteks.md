# B1 — Identitas dan Konteks

**Status implementasi: BELUM DIMULAI. Hasil pengujian: BELUM DIJALANKAN.**

[Indeks dan matriks](README.md) · [Master normatif](../../ALIGN_IMPLEMENTATION_SPEC.md) · [Predecessor: B0](B0-fondasi.md) · [Berikutnya: B2](B2-tugas-dan-spesifikasi.md)

## 1. Tujuan dan entry gate

Selesaikan identitas/otorisasi serta Course Context Preparation: dosen dapat membuat konteks manual atau dari KK/RPP, melihat provenance dan konflik, memperbaiki field, lalu mengesahkan ContextVersion immutable.

Entry: B0 mempunyai command terverifikasi, Compose/migration/seed/login, DB/storage/worker, mock adapter, error/config/hash helpers, traceability dan handover. Audit slice auth B0 sebelum menambah provisioning. Periksa kapasitas route parser yang dipakai; kekurangan kapasitas bukan alasan menunggu provider untuk alur manual.

## 2. Scope, referensi dan batas

Normatif: master Bagian 3–5.1, 6.1 untuk validasi bersama, 8, 13, 15, 16.1–16.3/16.7, 17–20, 22–23.

- Gate utama FR-01–FR-02; mulai FR-04, FR-06, FR-09, FR-15, FR-21.
- INV-02–INV-03, INV-05, INV-09–INV-12, INV-14, INV-16 berlaku pada fitur terkait.
- AC-MVP-05–AC-MVP-06 lengkap; AC-MVP-02–AC-MVP-04, AC-MVP-10–AC-MVP-11, AC-MVP-14, AC-MVP-16, AC-MVP-24, AC-MVP-34, AC-MVP-37, AC-MVP-46–AC-MVP-48 dimulai pada namespace konteks dan diperluas kemudian.

Context menerima PDF, DOCX, JPG/JPEG, PNG atau input manual; **XLSX hanya submission pada B3**. KK/RPP bukan korpus materi. B1 menyelesaikan service upload/parse/provenance yang benar-benar dibutuhkan konteks; jangan menunda seluruh ekstraksi sampai B3. Kelompok berscope assignment: type/rules disiapkan sekarang; transaksi API kelompok diuji penuh setelah assignment B2 tersedia.

## 3. Urutan implementasi

### B1.1 — Auth dan provisioning lengkap

1. Lengkapi akun terprovisi tanpa registrasi publik, normalized email unik, active/global role dan must_change_password. Admin bukan otomatis dosen course.
2. Password Argon2id, panjang baru 12–128 karakter; temporary password acak minimal 20 karakter tampil sekali, tidak masuk log, wajib diganti sebelum akademik. Reset admin revoke semua session; jangan menghapus admin aktif terakhir.
3. Session token random 32 byte; DB menyimpan SHA-256, cookie `align_session`, HttpOnly/SameSite=Lax, Secure pada HTTPS, path `/`. Idle 8 jam dan absolute 24 jam diperiksa server; revocation efektif request berikutnya.
4. CSRF stabil diturunkan HMAC-SHA256 atas session ID/token_hash dengan secret backend minimal 32 byte; simpan csrf_hash. Semua mutasi browser memeriksa header `X-CSRF-Token` dan Origin. Login memakai Origin/rate limit 5 failure/account dan 20/IP per 15 menit.
5. Ganti password merotasi session saat ini, membatalkan session lain dan temporary password lama. Maksimum request diperiksa sebelum hashing. Temporary account tidak memperoleh akses akademik sebelum password diganti.

### B1.2 — Course, enrollment dan kelompok

1. Migration users/sessions/course/enrollment dari B0 dilengkapi revision, unique dan FK yang diperlukan. Admin memprovisikan course/enrollment; dosen membaca roster course dengan metadata minimal.
2. Pusatkan authorization service: active enrollment + role course + object lineage diperiksa sebelum query dan sebelum serialisasi. Objek luar scope menjadi 404; aksi umum role salah 403; session invalid 401.
3. Group melekat assignment, satu active membership per mahasiswa per assignment. Siapkan unique/validation untuk constraint ini ketika table/assignment tersedia. Group ownership bukan izin melihat isi course lain.
4. Definisikan roster snapshot final dan current enrollment sebagai aturan akses historis; B3 membentuk snapshot, B4 memverifikasi publication access. Anggota baru tidak otomatis mendapat histori dan hasil kelompok bukan profil individual.

### B1.3 — Shared upload, parser dan provenance konteks

1. Implementasikan upload backend streaming ke quarantine privat; limit byte, SHA-256, signature/MIME/container, filename sanitization dan object key UUID server. Tidak membaca file utuh ke RAM atau menampilkan sebelum validated.
2. Terapkan kontrol format konteks relevan Bagian 6.1: file 20 MiB, image 25 MP, OOXML 10.000 entry/512 MiB expanded, PDF/bundle page limit dan DOCX node/media limits; encrypted, spoofed MIME, traversal/bomb ditolak sebelum paid call.
3. FileAsset purpose=context berscope course; `normative_file_links` memuat KK/RPP yang dipilih eksplisit, bukan tebakan filename. Namespace ParseArtifact/EvidenceItem tepat satu context version; sumber belum terhubung tetap lecturer/course-scoped.
4. Gunakan jalur python-docx+OOXML untuk DOCX, Docling native/OCR terpilih untuk PDF/gambar sesuai master. Inventory root/container/page dibuat dari metadata original; failure/unsupported tidak menghapus unit. Original immutable; derivatif memuat parent hash/transform/tool version.
5. Terapkan profil native 2 GiB/Docling 8 GiB, timeout efektif, checkpoint task key, admission memory/lease/fencing dan child isolation pada jalur yang sudah dipakai. Shared implementation ini dipakai ulang B3. Fixture mock/failure injection hanya membuktikan guardrail, bukan benchmark parser nyata.
6. Sediakan source preview/anchor untuk field konflik: PDF page/bbox, DOCX part/path dan image original coordinate. B3 menyempurnakan empat viewer; B1 tetap harus dapat menelusuri field konteks ke sumber nyata. Parse tak tersedia/gagal → alasan aman dan input manual, bukan field hasil karangan.
7. Tambahkan job/outbox stages context, checkpoint dan resume; jangan memanggil parser/inference sinkron pada API metadata. Final approval hanya mengunci sumber/field versi yang dipakai.

### B1.4 — Context extraction, konflik dan approval

1. POST draft menerima `sources=[{file_id,source_role:kk|rpp}]`, optional manual_fields dan base_context_version_id. Draf tetap dapat GET/PUT walaupun worker/provider gagal.
2. Mock provider menggunakan output schema nyata. Ekstraksi efficient/complex yang dikonfigurasi mematuhi budget context draft USD0.05, maksimum 3 text/vision calls, 20.000 input/4.000 output dan 2 vision; live hanya dengan semua guard finansial master.
3. Setiap field mempunyai origin extracted/manual/suggested, source_refs, resolution proposed/confirmed/conflicting/not_provided. Simpan dua kandidat KK/RPP berbeda berikut anchor; tidak ada prioritas otomatis.
4. Field wajib approval: code/title course, context_description, number_locale, language. CPMK optional: kosong sah dengan outcomes_status=not_provided; jangan mengarang learning outcomes.
5. Dosen menyelesaikan konflik dengan pilihan/koreksi dan alasan singkat. Approval transaksi memeriksa expected_revision, role, sumber/hash dan unresolved required fields; simpan approver/time/content hash.
6. Approved version immutable; edit membuat draft versi baru. Context untuk tugas lama kelak tetap mengacu versi lama. Semua tindakan di-audit tanpa menyalin isi penuh ke log operasi.

### B1.5 — UI dan isolasi

Bangun admin provisioning, course list/detail dan roster minimal, context editor dengan field/provenance/source panel, conflict resolution serta approval. Tampilkan error tiap field, parsing failure/manual fallback, no CPMK dan revoked access. Keyboard/focus/labels serta bahasa Indonesia berlaku sekarang; no-store untuk auth/akademik. Download source selalu lewat endpoint terotorisasi.

## 4. Kontrak API dan data

Seluruh path berikut memakai prefix `/api/v1`; body dan response lengkap mengikuti master Bagian 16.

| API | Izin dan perilaku |
|---|---|
| `PUT /me/password` | User terautentikasi, current/new password; rotasi session, 204 |
| `POST /admin/users`; `PATCH /admin/users/{id}`; `POST /admin/users/{id}/reset-password` | Admin; create/reset temporary password sekali; update/reset revision-checked |
| `POST /courses`; `GET /courses`; `GET /courses/{id}` | Create admin; list/detail metadata role-filtered |
| `PUT /courses/{id}/enrollments`; `GET /courses/{id}/enrollments` | Mutasi admin dengan expected_revision; baca admin/dosen berizin |
| `POST /courses/{id}/context-files` | Lecturer aktif, multipart; FileAsset validated purpose=context |
| `POST /courses/{id}/context-drafts` | Sources bertipe/manual fields/base version; 201 draft dan job_id bila AI dipilih |
| `GET /courses/{id}/context-versions`; `GET /context-versions/{id}` | Lecturer aktif; list cursor dan typed fields/conflicts/provenance/revision |
| `PUT /context-versions/{id}` | Hanya draft, fields/conflict_resolutions/expected_revision |
| `POST /context-versions/{id}/approve` | Transaksi immutable snapshot; conflict/required field errors menghalangi approval |
| `POST /assignments/{id}/groups`; `PUT /groups/{id}`; `GET /assignments/{id}/groups` | Kontrak mulai B1, runnable setelah parent assignment B2; lecturer mutasi, mahasiswa hanya kelompoknya |

Migration memperkenalkan `context_versions`, `context_conflicts`, `learning_outcomes`, `file_assets`, `normative_file_links`, manifests/profile refs, `parse_artifacts`, `parser_checkpoints`, processing time reservations, `source_units`, `evidence_items` dan relasi yang diperlukan dengan FK/namespace tepat. Group tables ditambahkan saat parent assignment ada; jangan membuat dummy assignment untuk menyatakan B1 selesai.

Transisi context draft → approved; approval/edit/revoke memakai transaksi/audit/revision. Hash/span/actor/object_key diisi server. GET approved tidak mengizinkan rewrite field lewat nested payload. Role parser tidak punya DB key; worker tidak punya lecturer approval rights.

## 5. Verifikasi

| Fixture/skenario | Hasil wajib |
|---|---|
| Dua course, dua dosen, mahasiswa A/B, admin tanpa enrollment | List/detail/context source/download course lain 404; admin tidak membaca isi akademik |
| Revoke session/enrollment saat tab masih terbuka | Request berikutnya ditolak; tidak menunggu restart atau cache expiry |
| Temporary password/reset/rotasi dua session | Password lama invalid; must_change_password dibatasi; token/log aman |
| KK/RPP dengan code/description berbeda dan anchor nyata | Dua kandidat tersimpan; approve blocked sampai dosen resolve |
| Context manual tanpa KK/RPP/CPMK | Approved sah dengan outcomes_status=not_provided dan learning_outcomes=[] |
| Sumber DOCX, PDF dan image cetak/rotasi | Field/source hash/anchor cocok; uncertainty terlihat; asli tidak ditimpa |
| Provider disabled/refusal/schema invalid/parser capacity unavailable | Draf tetap ada dan dapat dikoreksi manual; tidak mengarang hasil ekstraksi |
| MIME palsu/ZIP traversal/image bomb/encrypted PDF | Ditolak aman, tanpa paid analysis; asset gagal tidak dapat ditampilkan |
| Duplicate delivery/timeout controlled | Checkpoint reuse, unit gagal tetap inventoried, source version/config binding benar |
| CSRF/Origin/login abuse/concurrent context edit | Ditolak sesuai status; stale edit 409; tidak ada last-write-wins |

Jalankan integration dengan DB/storage/worker nyata dan Playwright alur manual/conflict approval. Mock hanya untuk provider/failure injection berlabel. Catat format/profile/hardware yang benar-benar diuji; AC isolation vector dan publication belum lengkap sampai tahap pemiliknya.

## 6. Gate dan handover

- [ ] Provisioning/auth/course/enrollment dan object permissions bekerja beserta regression B0.
- [ ] Manual context tanpa CPMK dan konflik KK/RPP dapat diselesaikan/diblokir secara benar.
- [ ] Context upload/parse/provenance yang dipakai berfungsi atau limitation/capacity dinyatakan spesifik; manual tetap tersedia. Gate ekstraksi yang belum diuji tetap incomplete, bukan diganti mock parser.
- [ ] Approval menghasilkan immutable ContextVersion; stale revision/foreign sources ditolak.
- [ ] Traceability, migrations, fixture sumber aktual dan report diperbarui.
- [ ] `docs/handover/B1.md` mencatat command, coverage format/profile, kapasitas, status live guard serta pekerjaan B2/B3 yang masih tersisa.

**BERHENTI setelah B1; jangan mulai B2 otomatis.** Kunci provider/dana yang belum tersedia tidak menghalangi gate mock/manual, tetapi tidak boleh dinyatakan live verified.

## 7. Prompt agent

~~~text
Implementasikan hanya B1 ALIGN sesuai master, indeks, dokumen B1 dan handover B0. Verifikasi predecessor, lalu lengkapi auth/provisioning/isolasi dan Course Context Preparation. Sediakan shared upload/parser/provenance/job nyata yang dibutuhkan konteks sekarang; jangan menunggu B3 untuk semuanya. Gunakan mock provider berlabel atau manual, dan jangan mengirim paid call sebelum semua guard tersedia. Uji akun/course terpisah, revocation, KK/RPP konflik, manual tanpa CPMK, source anchors, concurrency dan failure. Perbarui traceability dan docs/handover/B1.md dengan hasil aktual serta batas downstream; berhenti setelah gate B1 dan jangan mulai B2.
~~~

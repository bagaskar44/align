# B7 — UX dan Robustness

**Status implementasi: jalur software B7 teruji pada mock/synthetic; hasil, skip, dan batas ukur tercatat di handover B7. Tidak ada provider berbayar atau data institusi yang digunakan.**

[Indeks dan matriks](README.md) · [Master](../../ALIGN_IMPLEMENTATION_SPEC.md) · [Predecessor: B6](B6-numeric-dan-biaya.md) · [Berikutnya: B8](B8-handover-dan-pilot-readiness.md)

## 1. Tujuan dan entry gate

Verifikasi produk terintegrasi dan selesaikan kekurangan UX, aksesibilitas, concurrency, recovery, sanitization/retention dan robustness. Seluruh safeguard sejak tahap sebelumnya tetap wajib; B7 tidak memulai security/testing dari nol.

Entry: B0–B6 memiliki handover/traceability/test evidence; manual dan AI/numeric baseline berjalan pada synthetic fixtures; resource/financial limits aktif; contract OpenAPI/client sama. Inventarisasi failures/untested areas nyata dan perbaiki sesuai master tanpa memperluas produk.

## 2. Scope dan referensi

Normatif: master Bagian 7–20, 21.1–21.5, 22–24.

- Gate FR-17, FR-19 dan integrasi seluruh FR; dukungan tooling FR-24 disempurnakan B8.
- Semua INV-01–INV-16 diperiksa pada path terkait; scope audit INV-09 seluruh UI/API/schema.
- Gate AC-MVP-42–AC-MVP-44, AC-MVP-60, AC-MVP-63 serta regresi semua kriteria yang komponennya sudah tersedia. AC-MVP-45 dan full CLI/handover AC-MVP-64 diselesaikan B8.

Tidak menambah dashboard, gradebook, learning-material retrieval atau operasi eksternal. Perbaikan mengikuti desain/layout master yang sudah ditetapkan; tidak mengubah ketidakpastian sumber menjadi keputusan tegas demi UI terlihat lengkap.

## 3. Urutan implementasi

### B7.1 — UX dan aksesibilitas seluruh journey

1. Verifikasi semua halaman Bagian19: auth/admin/course/context, assignment/spec/lint/publish, upload/receipt, submission queue, evidence workspace, feedback/history/student results.
2. Gunakan warna/nav/terminologi Indonesia master; status text+icon, bukan warna/confidence persentase. No_issue label terbatas dan human_only/unassessable/limited tidak disajikan sebagai kesalahan mahasiswa.
3. Tes viewport360/768/1440px untuk seluruh aksi inti. Desktop panel source kanan/decision kiri; mobile<768tabs mempertahankan element/role/source selection. Grid dapat horizontal scroll dengan row/column/locator context.
4. Keyboard tab/focus/activation, visible focus, modal focus/return, semantic headings/table headers, linked field errors, labels, locator text dan restrained aria-live. Hover tidak satu-satunya jalan; touch source/role selection setara.
5. Annotation history/preview lokasi/hash/versi tersedia; source selection reverse links tidak mengambil mahasiswa lain. Setelah mutation/load/tab switch fokus dan selected state tetap logis.
6. Empty/loading/error/offline-provider/capacity-limited/revoked/stale/withdrawn/old-version states spesifik, tanpa ETA rekaan atau automatic accept. Polling berhenti terminal/inactive; lists paginated/detail lazy.
7. Per-criterion review menavigasi satu submission satu aksi; tidak mengagregasi kemampuan/pola findings kelas. Accept/reject sama mudah; publish confirmation menunjukkan snapshot konkret.

### B7.2 — Concurrency, jobs dan operational recovery

1. Jalankan ulang concurrent finalize, edits, annotation, approve/publish dan reservations pada DB nyata. Stale409, domain uniqueness/idempotency, outbox dan immutable guards harus sama melalui API/worker/UI.
2. Kill worker di setiap meaningful stage/output commit/ack boundary; redis/storage/provider failure, redelivery, scheduler/reaper recovery dan pending billing reconciliation diuji. Review/approval/publication manusia tidak ditimpa.
3. Parser child masih hidup ketika lease expired harus direkonsiliasi/fenced sebelum capacity dipakai lagi. Tidak cukup satu worker process semaphore; heavy/node/memory/time budgets berlaku lintas coordinator/child.
4. Cap/sanitization/security failures tidak menjadi retry loop. Source hash mismatch diblok untuk penggunaan baru dan diberi recovery dari source/backup verified; publication historis tidak diedit diam-diam.
5. Structured logs/metrics internal memuat IDs/stage/reasons/manifests/time/usage tanpa full academic documents/prompts/secret. Metadata operasi tidak menjadi dashboard findings kelas.

### B7.3 — Sanitization, retensi dan restore

1. Escape extraction/feedback HTML; Markdown hanya paragraf/list/emphasis/code/table sederhana, tanpa raw HTML/script/iframe/remote image. Active links relatif ke authorized ALIGN route; external URL tampil teks, tidak fetched/embedded.
2. PDF active JS/launch/external annotation otomatis off. No macro/code/workbook recalculation/runtime artifact download. Parser non-root, isolated working directory, deps read-only dan egress deny setelah prefetch.
3. Private storage, same-origin cookies/CSRF, credentialed CORS bukan wildcard, private/no-store akademik. Internal temporary renderer URLs scope/time bounded≤5menit dan tidak masuk snapshot/export permanen.
4. Terapkan upload20file/user/jam, finalize5/thread/jam, lecturer retry10/course/jam defaults;429 tanpa paid call. Normalized filenames/path traversal/decompression limits dan cross-course render/vector access diuji.
5. Purge terotorisasi menghapus original/derivatives/embeddings/academic content tercakup, membuat minimal tombstone/audit dan mengikuti backup retention. Immutable history bukan alasan menyimpan konten pribadi selamanya. Tidak menambah public purge API yang tidak ditetapkan master.
6. Source purged: pengguna berizin410 SOURCE_PURGED dengan label; outsider404 tanpa disclosure. Empty purged publication tidak disajikan seolah released content utuh; tidak menjadi parser failure/absence evidence.
7. Backup PostgreSQL harian dan storage snapshot/versioning sesuai dukungan, encryption/operator permissions. Target awal RPO24jam/RTO8jam; implementasikan dan ukur restore sintetis.
8. Sesudah restore, reconcile pointers/hash/jobs/ledger/admission/child inventory dan terapkan tombstones sebelum service dibuka. Jangan auto-retry unknown billed calls. Redis dapat direkonstruksi dari DB/outbox.
9. Retensi90/365/30hari hanya synthetic defaults; real-data mode memerlukan institutional policy reference/authorization dan masa berlaku. B7 tidak mengaktifkan data nyata.

### B7.4 — Compiler dan executor robustness runner

1. Buat CLI `align evaluate robustness --protocol PATH --dataset-manifest PATH --release RELEASE_ID --output PATH` dengan output JSON/Markdown offline dan budget/ledger sama bila live.
2. Protocol menyimpan transformation_id, parent/source hashes, partition, human equivalence status/actor, expected invariant/obligation mapping, seed, exact manifests dan repeat_count. Variants selalu mengikuti parent partition, termasuk near duplicates dan group/student/template leakage audit.
3. Compiler fidelity: equivalent instructions/ordering/alternatives→drafts dibandingkan obligation/applicability/accepted evidence/derivation/additions/omissions; word/hash similarity bukan semantic equivalence.
4. Executor stability: spec tetap, permutasi elemen independen dengan DAG sah, evidence presentation lengkap setara dan internal labels setara. Bandingkan decision/support/false findings/abstention/coverage/cost per elemen.
5. Native vs scan hanya equivalence bila keterbacaan terverifikasi; blur/glyph rusak sengaja bukan same-access equivalence. Mengubah boleh→harus/menambah syarat adalah negative fidelity case, bukan paraphrase sah.
6. Human equivalence/partition/DAG invalid ditolak sebagai protocol invalid, bukan dihitung stability failure biasa. Synthetic test metadata diberi label fixture; jangan mengklaim persetujuan rater nyata yang belum ada.
7. Default repeat_count3 pada subset development yang dipilih sebelumnya bila dana tersedia. Mock repeat hanya gate software, bukan stochastic reliability. Incomplete funds/support/verification→report incomplete, bukan pass/eligible.
8. Regression development-only; validation untuk pemilihan policy, frozen test tidak tuning/prompt/retrieval. Repeats/files/cells/group members bukan independent participant/gold tambahan. Ablation hanya evaluator offline, tidak mematikan guardrails production.

### B7.5 — Integrated acceptance dan kapasitas

1. Jalankan unit/domain, DB/API/worker integration dan Playwright role journeys untuk setiap AC terkait. Perbaiki failure akar, jangan menurunkan invariant/mengganti actual parser/vector test dengan stub yang pasti lolos.
2. Otomatisasi accessibility disertai manual keyboard journey login→upload→review→publish. Simpan screenshot/record locator untuk tiga viewport dan four-format sources.
3. Ukur synthetic staging target20sessions/general worker concurrency2/heavy≤1node dan p95 metadata/list/save<1detik (tanpa transfer/provider). Catat CPU/RAM/services/profile/fixtures/cold-warm/limits/waktu antre vs aktif. Target yang belum tercapai tetap dilaporkan, bukan klaim universal.
4. Setelah hasil stabil, pengulangan hanya untuk perubahan/failure/concern relevan. B8 memeriksa fresh-install/handover dan evaluator residual, bukan membuat ulang semua test tanpa alasan.

## 4. Kontrak interface dan compatibility

Public APIs/domain enums tetap Bagian16; perbaiki OpenAPI/client/schema/worker bersama bila ada bug kontrak. Tambahan source-purged410 memakai existing authorized routes sesuai20.7. Jangan menambah endpoint analytics atau memberikan student serializer internal demi debugging.

Robustness CLI adalah operator/offline surface. Protocol/report versioned, hash-bound, memisahkan compiler/executor/source corruption/repeat dependence dan mock/live. Hasilnya tidak masuk retrieval/runtime feedback.

Perubahan parser/profile/routing/batching/grounding/prompt membuat manifest/run baru; bugfix tidak memodifikasi payload approved/published historis. Migration yang diperlukan mengikuti immutable history/schema1 compatibility, tombstones dan fresh DB tests.

## 5. Verifikasi dan bukti

| Kelompok | Scenarios dan bukti wajib |
|---|---|
| Role journeys | Dosen A/B, mahasiswa/course A/B, admin nonacademic, dua kelompok, revoked/new member; endpoint/download/render/vector/export semuanya terisolasi |
| UX | Semua core actions360/768/1440, keyboard/touch/role highlights, source tabs/state, annotation history dan old/withdrawn versions |
| Failure | Worker/storage/Redis/provider outage, parser timeout/OOM, child lease expired, billed-call unknown dan repeated outbox events |
| Publication integrity | Concurrent decisions/annotation/publish, removed support, draft stale, new final version, historical annotation export |
| Security/retention | Malicious MIME/HTML/Markdown/PDF/workbook/instructions, secret/no-store checks, authorized410 vs outsider404 |
| Backup/restore | Synthetic DB+storage backup, deleted content tombstone, pointers/hashes/reviews/ledger/admission reconciliation dan measured RPO/RTO |
| Robustness | Equivalent/inequivalent variants, parent partition leak, DAG invalid, mock repeats/incomplete live, separate compiler/executor outputs |
| Capacity | Actual staging measurements dan limits; mock provider latency tidak disebut live inference performance |

### Hasil aktual 22 Oktober 2026

- Unit: `132 passed`; `ruff` lulus, `mypy` lulus pada 51 source files, web lint/typecheck lulus. Lint memiliki satu warning `<img>` pada viewer image. Production Docker build berhasil.
- PostgreSQL integration B0–B6/B7: `37 passed, 7 skipped, 1 warning`. Enam opt-in Docling parser-runtime membutuhkan locked model resources/admission dan tetap skipped. Satu OOM case yang skipped dalam suite dijalankan terpisah pada disposable Linux Docker limit 1 GiB dan `1 passed`; enam parser-runtime skip tidak dihitung pass. Retention integration terpisah `1 passed`.
- Playwright auth/admin/course `9 passed`; assignment-to-student-result `1 passed`. Flow kedua memeriksa PDF/DOCX/XLSX/image, overflow dan screenshot pada tiga lebar viewport, keyboard tab mobile serta keyboard sheet workbook, dan histori anotasi yang membuka PDF/anchor/hash yang terikat. Manual-evidence stale-selection ditemukan lalu diperbaiki menggunakan `evidence_id` response API. Mock/synthetic, bukan penelitian semantik.
- Backup/restore: targeted purge fixture `1 passed`; source reconcile `0` object missing/mismatch, `0` child lease, `0` held processing reservation. Encrypted DB/object bundle dipulihkan ke project/volume baru; migration head, API/web health, tombstone count `1`, authorized HTTP `410`, outsider HTTP `404`. Reconcile pada project integrasi gabungan sebelumnya menolak snapshot karena `1` object hilang dan `1` hash mismatch; snapshot tersebut tidak dipulihkan.
- Robustness CLI memakai baseline release synthetic terdaftar (`empirical_verification=false`): compiler JSON `software_gate_only`; executor Markdown `incomplete`/`executor_runtime=not_run`; frozen-test protocol ditolak. Rater equivalence berupa fixture; tidak ada adjudication semantik manusia atau provider inference.
- Capacity: 20 login session independen dari akun dosen sintetis yang sama × 5 putaran; 100 observasi per operasi. p95 course list `109.47 ms`, context list `210.25 ms`, draft save `242.90 ms`, di bawah target lokal `1000 ms`. Beban ini tidak menjalankan parser/upload/provider; cold startup, waktu antre parser dan throughput dokumen tidak diukur. Docker Engine melaporkan 8 CPU dan `12,526,637,056` byte RAM; parser worker dibatasi concurrency 1, 2 CPU, 9 GiB; worker umum concurrency 2. `docker stats` diambil sesudah beban, bukan peak capture.
- Audit scope menemukan hanya halaman auth/admin/course/workspace/result dan API ALIGN yang terdaftar; pencarian source B7 tidak menemukan gradebook, dashboard nilai, external search, learning-material retrieval atau Moodle passback. B7 tidak mengubah dependency/lockfile, kontrak akademik, model grading, atau baseline routing/batch.

Interpretasi aksesibilitas dibatasi pada role/label, keyboard yang diuji dan overflow viewport otomatis; assistive technology dan interaksi sentuh fisik tidak diuji. AC-MVP-45 dan full evaluator/policy/handover AC-MVP-64 tetap pekerjaan B8. Enam parser-runtime test yang skipped tetap pending.

Report membedakan software gate mock, actual parser/OCR, actual live synthetic, staging capacity dan penelitian belum tersedia. Traceability setiap relevant FR/INV/AC menaut test nyata serta hasil/limitation.

## 6. Gate dan handover

- [x] Core review journey, viewer lintas format, linked selection, mobile tab keyboard dan workbook keyboard diuji; overflow/screenshot diperiksa pada lebar 360/768/1440. Batas assistive technology/touch dijelaskan.
- [x] Integration, OOM terisolasi, budget/unknown-billing, role/source isolation, sanitization dan retention diuji; enam opt-in parser-runtime skip tetap pending dan tidak dihitung lulus.
- [x] Backup/restore sintetis berhasil pada fixture bersih, tombstone direkonsiliasi dan 410/404 diverifikasi sesudah restore; bundle gabungan yang tidak konsisten ditolak.
- [x] Robustness CLI memisahkan compiler/executor, mengikat release/hash, memeriksa partition/equivalence/DAG dan menolak frozen-test; executor serta semantic equivalence tetap incomplete.
- [x] Application AC yang dapat dijalankan diverifikasi pada synthetic; AC-MVP-45 dan full evaluator/handover AC-MVP-64 diteruskan eksplisit ke B8.
- [x] Capacity, command/result, kegagalan awal, keputusan engineering dan residual tercatat di `docs/handover/B7.md`.

**BERHENTI setelah B7; jangan mulai B8 otomatis.** Failure mandatory tidak menjadi passed karena UI terlihat rapi; empirical effectiveness tetap gate penelitian terpisah.

## 7. Prompt agent

~~~text
Implementasikan hanya B7 ALIGN sesuai master, indeks, dokumen ini dan handover B6. Audit hasil B0–B6 lalu perbaiki integrated UX/accessibility, linked annotations, concurrency/job/parser recovery, sanitization, retention dan synthetic backup/restore. Buat compiler/executor robustness runner dengan partition/equivalence/DAG guards dan report modes yang jujur. Verifikasi semua application AC yang tersedia, tiga viewport/keyboard, source/role isolation, budget/unknown billing dan measured staging capacity. Jangan tuning dari frozen test, menambah dashboard atau mengklaim mock sebagai penelitian. Perbarui traceability/docs/handover/B7.md dengan residual B8 lalu berhenti.
~~~

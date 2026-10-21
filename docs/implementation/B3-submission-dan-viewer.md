# B3 — Submission dan Viewer

**Status implementasi: GATE B3 LULUS pada data sintetis terisolasi (21 Oktober 2026).** Empat viewer, parser source-bound, resource profiles, benchmark aktual, komponen pemulihan broker, fencing dan failure injection telah dijalankan. AC-MVP-13 baru lulus untuk komponen outbox/job/parser B3; acceptance penuh tetap gate B5. Batas kemampuan dan bukti rinci ada di [handover B3](../handover/B3.md); hasil ini bukan bukti performa maksimum bundle 100 halaman.

[Indeks dan matriks](README.md) · [Master](../../ALIGN_IMPLEMENTATION_SPEC.md) · [Predecessor: B2](B2-tugas-dan-spesifikasi.md) · [Berikutnya: B4](B4-review-manual-dan-release.md)

## 1. Tujuan dan entry gate

Mahasiswa individu/kelompok mengumpulkan bundle final immutable yang dapat dibuka dosen melalui registry bukti dan viewer dengan anchor tepat. Parsing memiliki inventory lengkap, resource enforcement dan checkpoint/recovery nyata. AI substantif belum menjadi syarat B3.

Entry: B2 menyediakan assignment published/schema 2, upload/schedule policy, course/group ownership, seed SI/Manajemen dan approved specs. B1 shared upload/parser/provenance serta B0 storage/outbox/worker tersedia dan diperiksa. Catat kapasitas Docker/host, exact parser artifacts dan benchmark yang sudah ada; jangan menganggap resource profile telah verified hanya dari manifest.

## 2. Scope dan referensi

Normatif: master Bagian 4.3, 6, 8, 9.4, 15–18, 19.2–19.4, 20, 22–24.

- Gate FR-05–FR-06, FR-21; lanjut FR-14–FR-16, FR-17 dan groundwork FR-09.
- INV-02–INV-03, INV-09–INV-12, INV-14, INV-16 berlaku sekarang.
- Gate AC-MVP-10–AC-MVP-12, AC-MVP-14–AC-MVP-16, AC-MVP-46, AC-MVP-48–AC-MVP-49; awal AC-MVP-13, AC-MVP-18, AC-MVP-28, AC-MVP-36, AC-MVP-47, AC-MVP-60, AC-MVP-64.

B3 melengkapi namespace submission dan seluruh format/viewer, bukan membuat parser baru yang berbeda dari konteks/rubrik. Review/publication B4, grounding AI B5 dan automated numeric B6 tetap tahap berikutnya. Parsing/readability bukan feedback akademik mahasiswa sebelum publikasi.

## 3. Urutan implementasi

### B3.1 — Upload, finalize dan versi

1. Buat submission thread dengan tepat satu owner individual_user_id XOR group_id dan partial unique assignment-owner. Satu anggota aktif boleh upload/finalize kelompok; dosen tidak mengumpulkan atas nama mahasiswa.
2. Gunakan shared streaming quarantine; file valid `.pdf/.docx/.xlsx/.jpg/.jpeg/.png` dengan signature/MIME/container sesuai. `.docm/.xlsm/.zip/.csv/source code`, password/encrypted atau format lain ditolak. File asli boleh memuat istilah/nilai akademik sebagai isi.
3. Limits: 1–5 file final, ≤20 MiB/file, image ≤25 MP; OOXML ≤10.000 ZIP entries/512 MiB expanded; XLSX ≤25 sheets/100.000 nonempty cells; DOCX ≤20.000 inspected nodes/1.000 media; ≤100 PDF+raster pages per bundle (image=1). Jangan menebak jumlah halaman DOCX.
4. Hapus failed quarantine dan unused upload setelah 24 jam; filename hanya metadata disanitasi. Preflight bukan evaluasi akademik. Limit yang baru diketahui setelah finalize dicatat sebagai limitation pada manifest, tidak menghapus source.
5. Finalize menerima file_ids, expected_revision dan Idempotency-Key. Lock submission; authorize semua file/owner/current roster, active assignment/open time/late policy, bundle limits dan minimal satu valid file.
6. Manifest hash dari tuples `(file_id,sha256,size,mime)` sorted file ID; display order terpisah. Commit SubmissionVersion + assignment version + roster snapshot + finalized_at/is_late + job + outbox atomik sebelum response.
7. Server UTC menentukan late: finalized_at>due_at; accept_with_label tanpa penalti atau reject_after_due sesuai snapshot. Concurrent finalize/replay identik menghasilkan satu version/job/receipt; body beda dengan key sama 409.
8. Reopen oleh dosen membutuhkan reason, reopen_until dan assignment_version_id, membuat draft berikutnya. Final lama tidak berubah; source lintas versi tidak digabung. Draft belum final tidak mengganti active_final_version.

### B3.2 — Inventory, native formats dan registry

1. Buat root SourceUnit untuk setiap file, container/page/part/sheet dari metadata original sebelum extraction. Semua unit unsupported/unreadable/failed tetap mempunyai status/reason; bukan hanya unit sukses.
2. DOCX python-docx+OOXML mempertahankan document order, paragraph/table/cell/media paths, nested table, text box, footnote/header/footer dan revisions. Final view memasukkan inserted text, tidak menganggap deleted text jawaban aktif; jika tak dapat dipastikan tandai partial.
3. XLSX openpyxl dua jalur formula/cache, seluruh sheet termasuk hidden, merged ranges, row/column labels, formats dan context. Cache tidak otomatis verified; external references dicatat tanpa diikuti. Tidak menjalankan Excel/LibreOffice, macro, formula browser atau arbitrary recalculation.
4. Drawing/chart/media yang belum didukung dicatat sebagai visual unsupported. Caption/alt text bukan pengganti diagram yang menentukan. Evidence types tetap enum paragraph/table/diagram/image_text/spreadsheet_cell/spreadsheet_range.
5. EvidenceItem tepat satu namespace submission/context/assignment, terikat source_unit/file/version/hash/parser manifest. content_original tidak diringkas; normalization tidak mengubah digit/tanda/unit/makna. NumericObservation terpisah, bukan evidence type baru.
6. Source hash dan locator bounds diverifikasi saat parser/viewer membaca. Mismatch mengarantina artefak dan memblokir pemakaian baru; tampilkan limitation dan recovery, tanpa menulis ulang histori.

### B3.3 — PDF/image, resource dan checkpoint

1. Docling digital PDF membaca layout/reading order/tabel. OCR dipilih page/region yang membutuhkan, bukan semua PDF digital. Scan/image decode→orientasi turunan→OCR; visual yang perlu vision hanya lewat adapter/guard, default no paid call.
2. Original immutable; crop/scale/rotation menyimpan transform/parent hash/tool version. PDF canonical page 1-based dan bbox top-left PDF points; image bbox pixel original. Konversi origin bawah/rotasi/crop deterministik; koordinat tebakan model bukan anchor valid.
3. Profile payload immutable Bagian 8.5: native_ooxml_v1 2 GiB, docling_native_v1 8 GiB, docling_light_v1 4 GiB candidate/nonaktif. Package/weights/OCR/options checksums serta manifest/resource refs diikat release/run; env tidak override active profile.
4. Pisahkan inventory/metadata, render, layout, OCR, table dan assembly. Gunakan page range/streaming yang didukung versi nyata atau turunan halaman bermapping verified; jangan reconvert seluruh file untuk setiap task berlabel per-page.
5. Task key mencakup namespace owner/version, file hash, parser/profile hash, stage dan canonical locator. Commit checkpoint/output hashes atomik sebelum ack; resume tidak mengulang unit selesai. Assembly menjaga page order/header/unit/caption; cross-page relation meragukan menjadi partial, bukan tuple lengkap.
6. Sebelum child start, coordinator atomik mereservasi memory profile dan heavy slot. Maksimum satu heavy task/node, parser memory budget awal 8 GiB total; native juga mereservasi memory. Hard OS/container limit tetap wajib selain reservation.
7. Admission lease/heartbeat/fencing dan child inventory mencegah expired lease memulai heavy task lain saat child lama hidup. Lepas memory/slot setelah termination terkonfirmasi. Host juga harus memuat services lain dan OS headroom.
8. Task soft/hard default 120/150 detik dipotong remaining run; aggregate active analysis 600 detik termasuk retries/index/retrieval/inference kemudian. Queue wait terpisah. Parallel tasks mereservasi remaining active milliseconds; tidak mendapat 600 detik per halaman.
9. OOM confirmed OS → parser_memory_limit; exit ambigu → parser_failed; deadline → parser_timeout/processing_limit. Simpan unit/checkpoints gagal dan lanjut independen dalam cap. Kekurangan host → parser_capacity_unavailable/blocked; manual app tetap ready. Jangan retry tanpa batas atau auto-switch light.

### B3.4 — Viewer dan alur UI

1. PDF.js: page/zoom/bbox overlays, file/page label, JavaScript/launch/external annotation otomatis dinonaktifkan.
2. DOCX: structural read-only, deep link paragraph/table/cell/media dan original download; nomor halaman hanya dari render tercatat, bukan tebakan.
3. XLSX: read-only grid, sheet tabs termasuk hidden label, A1 address, merged/header context, formula dan cache terpisah; tidak mengeksekusi formula.
4. Image: zoom/pan/orientation serta overlay yang ditransformasikan ke koordinat original. Unsupported region menampilkan reason dan original, bukan area kosong.
5. Student upload/progress/file list, finalize confirmation dan receipt menyimpan refresh state lewat GET status. Mahasiswa hanya technical status aman dan feedback belum diterbitkan, bukan raw parse/AI candidates.
6. Lecturer submission work queue menampilkan owner/group, versi, waktu/late, technical status dan akses sumber. Loading/polling 5 detik→15 setelah 1 menit, berhenti terminal/inactive tab; tanpa ETA rekaan. Selection API/state disiapkan untuk workspace B4.

## 4. Kontrak API, anchor dan storage

Prefix `/api/v1`:

| API | Kontrak utama |
|---|---|
| `POST /assignments/{id}/submissions`; `GET /assignments/{id}/submissions` | Owner authorized; existing/new thread; lecturer course list, mahasiswa miliknya |
| `POST /submissions/{id}/files`; `DELETE /submissions/{id}/draft-files/{file_id}` | Streaming + expected draft revision; delete hanya draft asset bukan manifest final |
| `POST /submissions/{id}/finalize` | Version+receipt+job_id sesudah commit; idempotent/locked |
| `GET /submissions/{id}/status` | Active version, receipt, draft files/revision, reopen_until dan publication metadata aman |
| `POST /submissions/{id}/reopen` | Lecturer, reason/deadline/version/revision; draft baru |
| `GET /jobs/{id}`; `POST /jobs/{id}/retry` | Role-filtered; recoverable stage only, limits tetap, dosen retry default 10/course/jam |
| `GET /files/{id}/content` | Authorized original stream/range, private/no-store |
| `GET /evidence/{id}`; `GET /source-units/{id}/render` | Hash/bounds/role/scope sama; mahasiswa hanya evidence yang ditaut publication pada B4 |

Anchor union master 8.3: PDF page/bbox/coordinate_system/source_item_id; image original width/height/bbox/transform; DOCX actual part/absolute structural path/item kind/index; XLSX exact sheet/A1 range; file coarse hanya manual dengan locator_note pada B4. Semua angka finite dan bounds valid; hash tidak dipercayai client.

Migration melengkapi submissions, file_assets, submission_versions/files/members, parse artifacts/checkpoints/time reservations, source_units/evidence dan manifest/profile links. FK/unique version-owner, revision lock, outbox/audit dan immutable guards wajib. Access group history memerlukan current active enrollment dan membership snapshot final; current group saja tidak cukup.

CLI wajib dibuat dan dipakai: `align parser benchmark --profile PROFILE_ID --fixtures PATH --output PATH`. Output JSON/Markdown berisi hardware/exact versions/weights, fixture hashes/format/pages, cold/warm peak cgroup memory dan RSS dengan definisi, timing stage/file/page, failure/OOM/timeout, anchor/table/header/unit checks dan batas dukungan.

## 5. Verifikasi

| Fixture/skenario | Ekspektasi |
|---|---|
| Individual dan dua kelompok pada dua course; mixed-format bundles | Final manifest/roster/source assignment hash benar; foreign asset/source 404/422 sesuai kontrak |
| PDF dua kolom/tabel lintas halaman; nested DOCX/table/media/revision | Source order/anchor tepat; unsupported/assembly ambiguity tetap inventory |
| XLSX hidden/merged/formula/cache kosong/chart; image terputar/handwriting sulit | Grid/original/transform benar; cache dan OCR uncertainty terlihat, bukan operand verified |
| MIME spoof, bomb/traversal, image/file/page/node limits, encrypted file | Safe 413/415/422 sesuai penyebab; tanpa paid call; no stream seluruh file ke RAM |
| Dua anggota finalize bersamaan; replay/body beda; draft edit stale | Tepat satu final/job; receipt konsisten; conflict 409 |
| Redis mati saat finalize lalu pulih | DB version/job/outbox tetap commit; dispatch/parse pulih; analisis penuh AC-MVP-13 baru diverifikasi B5 |
| Late boundary dan reopen assignment baru | Server policy benar; receipt/source lama tidak berubah; no cross-version evidence |
| Halaman pertama selesai, berikut timeout/OOM controlled | Checkpoint reusable; failed units tetap denominator; reason OOM hanya jika confirmed |
| Lease expired dengan child hidup; dua coordinator/process | Heavy/node dan memory cap tidak terlampaui; fencing/kill reconciliation nyata |
| Source hash mismatch/foreign download/revoked enrollment | Blok pemakaian baru/akses; tidak bocor URL/crop atau mengubah histori |
| Benchmark native/Docling actual + failure injection | Report memisahkan actual parser, simulation, support limits dan environment; profile live belum verified tanpa bukti enforcement |

Tests memakai sumber file aktual yang dibuka parser, DB/storage/worker integration dan Playwright empat viewer. Simulasi OCR transcript tidak membuktikan handwriting recognition. Fixture stress menuju acceptance limits mencatat batas aktual; tidak menjanjikan semua 100 halaman scan selesai 600 detik.

## 6. Gate dan handover

- [x] Upload/finalize/version/roster/reopen/late dan transactional outbox benar; race, replay, body conflict dan broker recovery lulus.
- [x] PDF/DOCX/XLSX/PNG/JPEG termasuk `.jpg` diterima; empat viewer, anchor, hash dan provenance ditelusuri. Batas OCR, formula cache dan visual unsupported tercatat.
- [x] Native 2 GiB dan Docling 8 GiB diuji dengan cgroup 9 GiB, admission satu-heavy/node, checkpoint, timeout, controlled cgroup OOM attribution pada child sintetis, dan child fencing.
- [x] CLI benchmark actual cold/warm native dan Docling menghasilkan JSON/Markdown dengan timing preflight, per-file/per-page dan stage yang terukur.
- [x] Source files/fixtures/tests, OpenAPI/client, traceability dan regresi B0–B2 diperbarui.
- [x] `docs/handover/B3.md` mencatat hasil, batas, model/dependency hashes dan submission/source IDs sintetis untuk B4; pekerjaan semantik/AI tetap pada tahap pemiliknya.

**BERHENTI setelah B3.** Belum adanya AI substantif tidak menggagalkan B3; belum adanya bukti parser mandatory tidak boleh diberi status lulus.

## 7. Prompt agent

~~~text
Implementasikan hanya B3 ALIGN dari master, indeks, dokumen B3 dan handover B2. Kerjakan berurutan upload/finalization, native extraction/registry/viewers, lalu PDF/image/checkpoint/resource/recovery. Pakai ulang parser konteks/rubrik, inventory original lengkap dan immutable submission/roster. Terapkan native 2 GiB/Docling 8 GiB, one heavy per node, aggregate time cap dan actual benchmark; jangan auto-switch light atau menyamarkan parser mock. Uji concurrency, broker down, difficult files, anchors, OOM/timeout dan child fencing. Catat hasil nyata dan limitation pada traceability/docs/handover/B3.md, lalu berhenti tanpa memulai B4.
~~~

# B4 — Review Manual dan Release

**Status: implementasi dan gate B4 lulus pada alur manual yang dialokasikan.** Rincian bukti, hasil regresi, batas dan hal yang belum lulus ada di [handover B4](../handover/B4.md). Ini tidak menandai gate lanjutan B5–B8 selesai.

[Indeks dan matriks](README.md) · [Master](../../ALIGN_IMPLEMENTATION_SPEC.md) · [Predecessor: B3](B3-submission-dan-viewer.md) · [Berikutnya: B5](B5-ai-executor.md)

## 1. Tujuan dan entry gate

Capai milestone produk manual: mahasiswa finalize, dosen menelaah semua elemen dan sumber, menulis findings/feedback, menyetujui lalu menerbitkan, dan mahasiswa membaca hasil resmi. Seluruh alur ini bekerja dengan provider dimatikan dan tidak membutuhkan analysis run sukses.

Entry: B3 mempunyai submission final/assignment snapshot, roster, file/hash/inventory/viewers dan access policy. B2 menyediakan semua elemen/grounding/NumericPolicy approved. Verifikasi handover predecessor serta source anchor/bounds service sebelum mengizinkan manual evidence.

## 2. Scope dan referensi

Normatif: master Bagian 4.2–4.3, 7, 8.3–8.4, 9, 12, 14–17, 19–20, 22–24.

- Gate FR-11, FR-14, FR-18, FR-23; mulai/lanjut FR-12–FR-13, FR-15 dan FR-17.
- INV-05–INV-09, INV-11–INV-12, INV-02, INV-04 dan INV-08 berlaku pada seluruh review/publication.
- Gate AC-MVP-01, AC-MVP-03–AC-MVP-04, AC-MVP-30, AC-MVP-32–AC-MVP-33, AC-MVP-35–AC-MVP-36, AC-MVP-41, AC-MVP-61–AC-MVP-62. AC-MVP-29/31/34 dan linked selection AC-MVP-60 diperluas B5/B6/B7.

Review consumer kandidat diuji memakai snapshot/fixture analisis sintetis berlabel mock yang merujuk sumber nyata; ini bukan klaim executor sudah berjalan. Feedback manual/template selesai B4; generator provider penuh B5. Core perhitungan untuk klaim manual numeric yang mengaku verified harus tersedia B4, walaupun automated acquisition B6.

## 3. Urutan implementasi

### B4.1 — Review workspace dan keputusan

1. Buat ReviewVersion dari SubmissionVersion, analysis_run_id nullable. Tanpa AI, materialisasikan workspace dari seluruh elemen approved spec dan sumber final, bukan menunggu job ready.
2. Setiap elemen mempunyai review_status pending/reviewed dan resolution observed_no_issue/confirmed_issue/not_applicable/unable_to_conclude. Dosen dapat override applicability dengan alasan dan fact/source sah; tidak mengubah spec historis.
3. Kandidat yang tampil mendapat accept/edit/reject/defer. Accept/edit memerlukan claim/category/targets/evidence sah; reject tidak wajib esai; defer memerlukan limitation/reason. Tombol Periksa manual bukan terminal decision.
4. Complete memerlukan semua elemen reviewed, semua kandidat terlihat terminal, dan limitations penentu eksplisit. Unable_to_conclude/defer dapat menjadi penyelesaian yang sah, bukan dipaksa ada/tidak masalah.
5. Manual finding hanya tiga kategori resmi, origin=lecturer, target dalam spec yang sama, evidence atau coverage attestation untuk absence dan alasan. Backend tidak mensyaratkan AI menyetujui judgment dosen.
6. Numeric manual yang dapat dihitung menyertakan operand/context/policy/source verified dan hasil core Decimal resmi: precision34, normalization yang diperlukan, comparability dan tolerance max dari Bagian 12. Source ambiguous/unverified tidak menjadi numeric finding terverifikasi; komentar dosen tetap boleh sebagai feedback note.
7. Mutasi memakai expected_revision parent ReviewVersion, row lock, audit dan revision baru. Stale concurrent edit 409 tanpa lost update. Worker DB role tidak menulis lecturer decisions/approval/publication.

### B4.2 — Manual evidence dan annotation revisions

1. Pilih hanya original/file yang termasuk manifest final versi review. Jika parser belum menghasilkan unit, buat manual SourceUnit pada ParseArtifact sumber yang sama dengan bounds diverifikasi.
2. Whole-file anchor format=file hanya jika region lebih rinci belum dapat diverifikasi, source hash dan locator_note wajib. Label lokasi kasar; model tidak memakai ini sebagai citation shortcut.
3. ReviewAnnotation stable ID dengan kind span_selection/transcription/evidence_binding. Revisi append-only memiliki revision_no, previous revision, upsert/remove, actor/time/source hash/anchor/target/role dan alasan sesuai kind.
4. Transcription correction membuat EvidenceItem baru origin=lecturer_transcription yang menaut sumber/revisi. Original parser evidence/content/ParseArtifact tidak di-update. Text≤20.000, alasan≤2.000; alasan wajib untuk transkripsi, perubahan lokasi dan remove.
5. Remove menonaktifkan binding pada review revision baru dan mempertahankan source/history refs; tidak meminta anchor rekaan. Target element/finding/role selalu satu review/spec/submission version.
6. Manual-evidence endpoint dan annotation endpoint menggunakan service sama: satu aksi menghasilkan satu annotation/revision pertama, bukan dua objek duplicate.
7. Annotation mutation menaikkan review revision/hash, complete→draft dan feedback approval stale. Jika support confirmed finding hilang, complete/publish diblokir sampai finding/decision/limitation diperbaiki.
8. Review/publication hash mengikat active annotation revisions, manual evidence contents/hashes, effective bindings dan keputusan. Snapshot lama memakai resolved revision historis, bukan current pointer yang berubah.

### B4.3 — Linked workspace dan navigasi

1. Shared selection state: element_id, relationship_code/role nullable, evidence/source nullable dan finding nullable. Pilih elemen membuka tuntutan/derivation/question/expectation/source/coverage/keputusan; pilih evidence membuka anchor dan highlight yang tepat.
2. Pilih source tertaut menunjukkan linked elements/findings hanya review ini; region belum tertaut dapat menjadi manual evidence. Sumber sama dapat mengisi beberapa role.
3. Pisahkan observasi sumber, interpretasi AI bila ada, limitation dan keputusan dosen. Penjelasan ringkas dapat diperiksa; tidak menampilkan hidden reasoning/confidence akademik.
4. Desktop elemen/keputusan kiri, sumber kanan, panel resizable. <768 px tabs Pemeriksaan/Bukti/Keputusan mempertahankan selection. Click, keyboard dan touch setara; no hover-only.
5. Mode review per submission dan per criterion pada assignment sama menavigasi satu pekerjaan per aksi. Tidak ada ringkasan pola kelas, auto-accept, bulk approve atau satu aksi mengonfirmasi mahasiswa lain.

### B4.4 — Feedback dan approval

1. Complete review dapat membuat feedback draft mode manual/template tanpa provider. Structure blocks: criterion_id, review_finding_ids, evidence_ids, message, next_action, limitation nullable. Body final maksimum20.000 karakter.
2. Sumber kritik hanya confirmed accepted/edited/manual findings yang disertakan. Rejected tidak masuk generator/teks kritik; deferred hanya menjadi limitation netral. UI mengingatkan confirmed finding yang tidak memiliki block; gabungan block boleh jika tautannya eksplisit.
3. included_review_finding_ids dan exclusion reasons berada pada draft dan feedback hash. Finding internal yang dikecualikan tidak muncul tersirat di teks. Jangan menambah external learning material atau ideal answer.
4. Tanpa confirmed finding gunakan template netral master dan limitation bila ada, bukan pujian/klaim seluruh tugas sempurna. Renderer mengizinkan Markdown ringan aman; raw HTML/remote images/active external links tidak diaktifkan.
5. Draft menyimpan source_review_hash, generation mode dan content hash. Approval mengikat draft/review hashes+actor/time dan membuat draft immutable; edit approved membuat draft baru. Keputusan/catatan/anotasi berubah → approval stale.

### B4.5 — Publish, withdrawal, ekspor

1. Approve dan publish adalah dua aksi terpisah. Publish confirmation memperlihatkan owner, source version, included findings, limitations dan teks final.
2. Publish transaksi memeriksa lecturer aktif, expected_revision, idempotency, complete review, approved draft yang dipilih, hash cocok, seluruh evidence sah dan SubmissionVersion masih active final. Final lebih baru →409 SOURCE_VERSION_CHANGED; draft belum final bukan pengganti sumber.
3. Buat Publication immutable snapshot atas source/config versions, selected confirmed findings, evidence/annotation resolved revisions/contents/hashes, teks, limitations dan approver/publisher. Update latest_published, audit/outbox atomik. Tidak mengirim email/grade write.
4. Student serializer khusus hanya released/tidak withdrawn dan current enrollment + roster snapshot. Jangan memakai serializer review internal lalu menghapus beberapa fields di frontend.
5. Publication lama tetap menampilkan versi sumber lama saat final baru diproses. Withdrawal event tidak menimpa snapshot atau otomatis memilih hasil released lebih lama; latest pointer tetap withdrawn sampai replacement.
6. Export JSON/Markdown menggunakan snapshot/resolved annotation yang sama dengan tampilan resmi, authorization sama dan filename aman. Tidak memuat raw candidates, reject/internal notes/history, prompt, key, biaya atau URL temporer permanen.

## 4. Kontrak API dan data

Semua path berprefix `/api/v1`; mutasi review memakai revision parent dan Idempotency-Key, CSRF dan lecturer aktif.

| API | Behavior |
|---|---|
| `POST /submission-versions/{id}/reviews`; `GET /submission-versions/{id}/reviews`; `GET /reviews/{id}` | Create dari optional analysis run atau manual; semua elemen, current revision dan paginated evidence |
| `PUT /reviews/{id}/element-decisions/{element_id}` | review_status/resolution/reason/evidence/expected_revision |
| `PUT /reviews/{id}/candidate-decisions/{candidate_id}` | action dan valid edits, reason/revision; source lineage tidak hilang |
| `POST /reviews/{id}/manual-evidence`; `POST /reviews/{id}/manual-findings`; `PATCH /reviews/{id}/manual-findings/{finding_id}` | Sources/targets authorized; manual origin; reject historis bukan hard delete |
| `PUT /reviews/{id}/notes`; `POST /reviews/{id}/complete` | Notes/limitations memengaruhi hash; pending list bila incomplete |
| `GET /reviews/{id}/source-links` | Tepat satu evidence_id/source_unit_id; linked objects hanya review ini |
| `POST /reviews/{id}/annotations`; `POST /reviews/{id}/annotations/{annotation_id}/revisions`; `GET /reviews/{id}/annotations/{annotation_id}/revisions` | Append-only revisions; target/anchor422, revision409, foreign404 |
| `POST /reviews/{id}/feedback-drafts`; `GET/PUT /feedback-drafts/{id}`; `POST /feedback-drafts/{id}/approve` | Complete review; manual/template B4, model B5; approval mengikat expected_review_revision |
| `POST /reviews/{id}/publish`; `POST /publications/{id}/withdraw` | Idempotent snapshot atau reasoned event, bukan edit isi lama |
| `GET /submissions/{id}/published-result`; `GET /submissions/{id}/publications`; `GET /publications/{id}`; `GET /publications/{id}/export` | Role-filtered historic snapshot/status; json/md export sama |

Migration: review_versions, review_element_decisions, review_findings, evidence_links, manual_evidence, review_annotations/revisions/targets, feedback_drafts, publications/events. Tambahkan analysis/candidate reference tables yang dibutuhkan consumer dengan kontrak Bagian15, tanpa melabel executor telah lengkap. Numeric core memakai numeric observations/checks resmi ketika diperlukan; B6 memperluas acquisition otomatis.

Versioning: review draft→complete; edit complete→draft+revision; feedback draft→approved immutable/new draft untuk edit; publication immutable+released/withdrawn events. Semua normalized ownership/version FK dan uniqueness wajib, bukan hidden JSON permission.

## 5. Verifikasi

| Skenario/fixture | Hasil wajib |
|---|---|
| Database fresh dengan migration/seed sintetis, provider benar-benar off, analysis gagal/absent | Install/start/login lalu mahasiswa finalize→manual workspace→semua keputusan→manual feedback→approve→publish→student result/export berjalan; bukti AC-MVP-01 mencakup schema dan alur B4 saat ini |
| Pending element/kandidat, defer dan unable_to_conclude | Pending memblok complete; terminal defer/unable dengan limitation dapat complete |
| Snapshot kandidat mock accept/edit/reject/defer dari source nyata | Hanya confirmed selected findings menjadi kritik; rejected/deferred tidak bocor sebagai kesalahan |
| Dua dosen mutate revision sama, termasuk annotation | Satu sukses, satu409; parent revision dan append-only history terjaga |
| Correct transcription/span/binding lalu remove support | Evidence asli tidak berubah; complete kembali draft; feedback stale; unsupported confirmed finding memblok publish |
| Numeric manual verified vs ambiguous/cache belum verified | Hasil engine max tolerance benar; source invalid tidak mengaku verified; feedback note manual tetap mungkin |
| Edit keputusan/teks setelah approved; double publish/replay | Stale blocked; text edit draft baru; satu snapshot per aksi idempotent |
| Final submission baru/draft baru, withdrawal dan replacement | Final baru409 untuk publish lama; draft saja tidak; labels/pointer/history sesuai master |
| Annotation baru sesudah publication | Publication/export lama memakai resolved revisions lama; bukan latest annotation |
| Mahasiswa/admin tanpa enrollment/anggota baru/revoked member | Internal review atau evidence tak dirilis ditolak; published result membutuhkan izin+roster historis |
| PDF/DOCX/XLSX/image linked role selection dan mobile tabs | Sumber tepat, selection bertahan, keyboard/touch setara; catat AC60 final regression B7 |

Jalankan DB transaction/concurrency tests dengan PostgreSQL nyata dan Playwright alur provider-off dua role. Verifikasi direct endpoint, renderer/download dan export, bukan hanya menyembunyikan tombol. Full financial-exhaustion AC-MVP-29 selesai B6.

## 6. Gate dan handover

- [x] Alur manual lengkap terbukti end-to-end tanpa paid/provider dependency.
- [x] Semua elemen/candidate consumer, manual evidence/findings dan numeric manual rules benar pada fixture yang dinyatakan.
- [x] Linked selection desktop, append-only annotation/history, stale/complete dan support removal safeguards bekerja.
- [x] Approval/publish terpisah, immutable snapshot/export/withdrawal/replacement dan student serialization benar.
- [x] Worker tidak dapat menulis academic decisions/approval/publication; concurrency, authorization dan revocation tests lulus.
- [x] Traceability dan `docs/handover/B4.md` mencatat fixture IDs, command/results, skipped tests dan capability yang masih dimiliki B5–B8.

AC-MVP-60 untuk navigasi keyboard/touch dan layout tab mobile, generator feedback B5, automated numeric/financial exhaustion B6, serta final accessibility/robustness B7 tetap pending pada pemiliknya.

**BERHENTI setelah B4.** Ini milestone manual lengkap, belum seluruh MVP atau efektivitas AI. Jangan memulai B5 otomatis.

## 7. Prompt agent

~~~text
Implementasikan hanya B4 ALIGN setelah memeriksa master, indeks, dokumen ini dan handover B3. Bangun review semua elemen tanpa analysis run sukses, manual evidence/findings, linked selection, immutable annotation revisions dan manual numeric validation yang sah. Selesaikan feedback manual/template, approval dan publish terpisah, released snapshots, withdrawal/export dan student isolation. Gunakan source nyata dengan synthetic/mock candidate fixtures untuk consumer; jangan mengaku executor sudah selesai. Uji provider-off end-to-end, concurrency, stale feedback, removed support dan historical annotations. Perbarui traceability/docs/handover/B4.md lalu berhenti sebelum B5.
~~~

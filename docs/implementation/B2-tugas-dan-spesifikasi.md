# B2 — Tugas dan Spesifikasi

**Status implementasi: BELUM DIMULAI. Hasil pengujian: BELUM DIJALANKAN.**

[Indeks dan matriks](README.md) · [Master](../../ALIGN_IMPLEMENTATION_SPEC.md) · [Predecessor: B1](B1-identitas-dan-konteks.md) · [Berikutnya: B3](B3-submission-dan-viewer.md)

## 1. Tujuan dan entry gate

Dosen dapat menerbitkan AssignmentVersion yang mengikat konteks, instruksi asli, criteria/requirements, CheckSpecification schema v2 dan NumericPolicy yang sah. Semua tuntutan diketahui mahasiswa sebelum pengumpulan; checklist internal tidak menambahkan kewajiban.

Entry: B1 auth/enrollment, approved context, sumber/provenance, jobs/mock adapter dan handover terverifikasi. Gunakan schema/hash/authorization service yang sudah ada. Periksa bahwa draf tetap dapat diedit saat provider off. Parent assignment yang dibuat di B2 mengaktifkan penyelesaian kelompok dari kontrak B1.

## 2. Scope dan referensi

Normatif: master Bagian 5.2–5.3, 7, 9, 12.2, 13, 15–16.3/16.7, 17, 19–20, 22–24.

- Gate FR-03, FR-20; lanjut drafting FR-04; mulai FR-08, FR-10 dan binding FR-15.
- INV-01, INV-04, INV-09, INV-13, INV-15–INV-16 serta immutable/version invariant terkait.
- Gate AC-MVP-07–AC-MVP-09, AC-MVP-53–AC-MVP-54; awal AC-MVP-15, AC-MVP-20, AC-MVP-50–AC-MVP-52, AC-MVP-63, AC-MVP-65.

Scope mencakup editor, generator draft via mock/configured adapter, compiler/linter, approval dan publication tugas. Belum menjalankan check terhadap mahasiswa. NumericPolicy schema/approval diselesaikan sekarang; engine manual yang diperlukan B4 dan automated pipeline B6 tidak diganti perhitungan LLM.

## 3. Urutan implementasi

### B2.1 — Paket normatif dan versi

1. Buat assignment dari ContextVersion approved; original instruction nonkosong maksimal 30.000 karakter. Hanya CRLF→LF boleh dinormalisasi. Simpan instruction hash; summary opsional berlabel dan bukan sumber kewajiban.
2. Criterion set berisi 1–30 criteria dengan title/descriptor; requirement atomik, source refs, mandatory dan obligation key. Tidak ada bobot/nilai/maksimum skor. Mapping CPMK optional many-to-many hanya outcome ContextVersion terkait, tanpa allocation weight.
3. Rubrik optional memakai shared upload/parser B1 dengan purpose=rubric/assignment namespace. Asli dipertahankan; level/nilai/bobot yang ada dalam file tidak diubah menjadi fitur/field penilaian. Descriptor resmi yang dipakai wajib masuk paket publik.
4. Draft criteria/requirements dapat berasal input terstruktur atau usulan dari instruction/rubric. Response selalu menyertakan draft GET/PUT-able walaupun AI gagal. AI tidak mengesahkan tuntutan.
5. Paket approval pertama mengikat instruction+criterion set+requirements hashes. Perubahan paket membatalkan approval spec turunan. Mode individual/group, opens/due server UTC, due>opens, default late policy accept_with_label dan upload limits disimpan dalam versi.

### B2.2 — CheckSpecification schema v2

1. Implementasikan schema domain Bagian 7 dengan 1–100 elemen atomik: stable business element_code/obligation lineage, UUID baru tiap versi konten, criterion/requirement IDs dari paket yang sama, decision_question positif nonkosong ≤2.000 karakter dan human_judgment_boundary.
2. Derivation refs 1–20: requirement, criterion_descriptor atau instruction; requirement target mencakup seluruh requirement_ids. Span Unicode code point 0-based/end-exclusive di teks LF, bukan UTF-16 atau byte offset. Backend menghitung hash/kutipan; UI menyediakan pemetaan code point yang benar.
3. accepted_evidence_types memakai enam enum resmi; source hints maksimum 10 bukan daftar eksklusif lokasi. Relationships maksimum 10, stable relationship_code sesuai regex master, predicate registry covers/compares/supports/applies/traces_to/reports_same_quantity dan qualifier basis/period/scenario/metric nullable.
4. Grounding union tertutup: anchored_content, linked_relationship, comparable_numeric atau human_only. Relationship semantik otomatis memakai linked_relationship; numeric otomatis comparable_numeric; human_only tidak memiliki jalur keputusan semantik/numerik otomatis.
5. Linked roles mengikuti master 7.6; satu evidence boleh mengisi banyak role, tanpa syarat beberapa file/chunk. absence_support approved_coverage_policy hanya untuk mode sah; numeric/human_only null. Quote/role validity bukan bukti semantic support.
6. Applicability always/fact_rule/lecturer_required; DSL typed all/any/not/eq/in/contains/gt/gte/lt/lte, depth≤3 dan leaf≤10. `contains` hanya array fact, custom fact task.<registered_key> dengan tipe resmi. Missing/unverified fact tidak berubah false.
7. Operator/predicate/fact/grounding/lint registries versioned dan unknown ditolak. DAG hanya elemen spec yang sama, bebas cycle; numeric compare memerlukan establish_comparability. Human-only operator subset dibatasi sesuai master.
8. Coverage policy default sufficient_support/exhaustive_compatible_source_units, inspect visuals, block negative unresolved units dan preserve table context. Model tidak mengubah guardrail menjadi teks policy bebas.

### B2.3 — NumericPolicy dan lint

1. NumericPolicy resmi memuat metric_definition, required_context_fields, locale, unit/conversions, pairwise/sum/ratio, absolute/relative tolerance decimal strings, rounding dan operand_roles. Absolute≥0, relative 0–1, default rounding none; tolerance harus eksplisit, termasuk 0/0 yang sah.
2. Required context fields hanya tuple resmi; metric/entity/definition/unit selalu wajib. Linear conversion factor positif, compatible dimension dan approved unit registry. Tidak ada external exchange rate, Python expression atau formula workbook bebas.
3. Lint deterministik berjalan pada PUT draft tersimpan dan sebelum approval; tidak memanggil provider. Schema/ref/operator/DAG invalid → 422 tanpa mengubah draft; report tidak mengaku body gagal sebagai versi tersimpan.
4. Implementasikan rule invalid_normative_ref/invalid_contract, unsupported_requirement, atomicity_review, polarity_review, condition_fidelity_review, duplicate_obligation_review dan checkability_review dengan severity master.
5. LintReport immutable mengikat source revision/content hash/package hashes dan registry versions. Issues memiliki code, source refs dan alasan. Tanpa issues tetap menghasilkan report. Blocking → failed; review-only → needs_review; kosong → passed.
6. Resolution append-only hanya review issue: acknowledged_faithful atau acknowledged_human_boundary (hanya elemen sudah human_only), alasan nonkosong ≤2.000, actor/time. Blocking tidak dapat di-override. Perubahan elemen selalu PUT/lint baru, bukan catatan resolusi palsu.
7. effective_status passed memerlukan hash/revision/paket cocok dan semua review issues sah. Approval mengikat report dan resolution_revision serta fidelity_confirmation=true oleh dosen. Resolution counter tidak menaikkan spec content revision; lock keduanya dan tolak stale 409.
8. Canonical spec hash: UTF-8 JSON keys sorted, insignificant whitespace dihapus, set refs stabil, teks LF tetap; exclude administrative revision/time/actor/state/approval/lint metadata. Simpan source_spec_revision sebelum approval; kenaikan revision administratif tidak membuat binding approved menjadi stale sendiri.

### B2.4 — Publish, histori dan kelompok

1. Approval spec terpisah dari approval paket. Publish assignment satu transaksi memastikan seluruh ref approved, lineage/policy/hash cocok dan lint efektif sah.
2. Published AssignmentVersion immutable. Edit instruction/schedule/rules membuat new-version draft dan mengosongkan approvals; parent open/closed/archived tidak menulis ulang snapshot.
3. Student serializer hanya mengirim instruksi, criteria/descriptor/requirements dan bentuk bukti publik; prompt/DSL/internal cost tidak ikut. Group endpoints B1 kini runnable: satu membership aktif per assignment, hak lecturer, mahasiswa hanya roster kelompoknya.
4. Fresh build menyediakan fixture schema 1 historis untuk AC-MVP-65, bukan mengarang deployment lama. History tetap read-only; draft/approval/new AI run schema 2. Jangan mengisi grounding/lint v2 rekaan pada snapshot v1. Bila database lama nyata tersedia, lakukan migration compatible sesuai master.
5. Draft generator tetap mock/manual default. Criteria/spec draft batas USD0.05 masing-masing, maksimum 3 text/vision calls, 20k input/4k output/2 vision, paid guard lengkap sebelum live. Lint otomatis bukan paid drafting tak berbatas.

### B2.5 — UI lengkap

Editor assignment mempertahankan instruction asli, criteria/requirements dan optional outcomes. Spec editor memperlihatkan decision question dan derivation berdampingan, applicability, grounding/roles, evidence alternatives, DAG, NumericPolicy, boundary dan lint/resolution. Publish preview memperlihatkan persis paket mahasiswa. Disabled action memberi alasan dan field link; bukan sekadar tombol mati. Keyboard/error/focus serta no-store mengikuti master.

## 4. Kontrak API dan penyimpanan

Path berikut berprefix `/api/v1`; semua action version/job memakai Idempotency-Key sesuai Bagian 16.1.

| Interface | Request/behavior inti |
|---|---|
| `POST /assignments`; `GET /assignments/{id}`; `GET /courses/{id}/assignments` | Approved context; detail/list role-filtered, optional version_id |
| `GET /assignment-versions/{id}`; `PUT /assignment-versions/{id}` | Draft lecturer/public published student; revision, instruction/schedule/mode/late policy |
| `PATCH /assignments/{id}`; `POST /assignments/{id}/new-version` | Administrative parent metadata atau cloned draft; tidak patch snapshot |
| `POST /assignments/{id}/rubric-files`; `POST /assignment-versions/{id}/criteria-drafts` | File purpose rubric; source IDs/structured criteria/generate flag; draft/job tetap tersedia |
| `GET/PUT /criterion-set-versions/{id}`; `POST /assignment-versions/{id}/approve-package` | Typed criteria/requirements/links; approve mengikat hashes |
| `POST /assignment-versions/{id}/specification-drafts`; `GET/PUT /specification-versions/{id}` | Schema 2, typed elements/limits, lint_report_id/effective_status pada PUT |
| `POST /specification-versions/{id}/lint`; `GET /specification-versions/{id}/lint-reports`; `GET /spec-lint-reports/{id}` | Deterministik/no paid call; report immutable dan resolution_revision |
| `POST /spec-lint-reports/{id}/resolutions` | issue_code/resolution/reason/expected_spec_revision/expected_report_revision; lock/revision check |
| `POST /assignments/{id}/numeric-policy-drafts`; `GET/PUT /numeric-policy-versions/{id}`; `POST /numeric-policy-versions/{id}/approve` | Payload typed resmi; approval lecturer |
| `POST /specification-versions/{id}/approve` | expected_revision, lint_report_id, expected_report_revision, fidelity_confirmation=true |
| `POST /assignment-versions/{id}/publish` | Seluruh approved refs cocok; AssignmentVersion immutable |

Nested PUT set mengganti isi draft atomik; child ID existing draft atau null untuk server UUID; business code→ID mapping dikembalikan. Foreign IDs/dangling links ditolak. Jangan memercayai hash/actor/state dari client.

Migration: assignments/versions, criterion_set_versions, criteria, requirements, criterion_requirement/outcome_links, numeric_policy_versions, specification_versions, check_elements, element_requirement_links, spec_lint_reports/resolutions serta group tables/constraints. Composite FK/unique lineage, immutable guards dan version_no uniqueness mengikuti Bagian 15. Kolom v2 hanya nullable pada history schema 1 sesuai kontrak.

## 5. Verifikasi

| Fixture/skenario | Ekspektasi |
|---|---|
| Dua assignment sintetis SI/Manajemen, context outcomes tersedia/kosong | Keduanya dapat disahkan dan terbit; mapping asing ditolak |
| Instruksi missing value/outlier/scaling dengan summary lebih pendek | Original bytes setelah LF/hash/teks publik tetap utuh; summary tidak menambah/menghapus kewajiban |
| Unknown operator/predicate, cycle, invalid role/grounding/ref/limits | 422 field error; draft tidak berubah dan tidak ada publish |
| Bahasa boleh/harus, kondisi missing value, contoh StandardScaler, alternatif format | Lint/fidelity review mempertahankan makna; kewajiban tanpa derivasi diblokir |
| Span dengan emoji/Unicode di tengah LF text | Code point offsets dan kutipan/hash benar pada web/API; bukan UTF-16 index |
| Satu tabel secara sah mendukung beberapa roles | Schema mengizinkan ID sama multi-role; tidak memaksa sumber baru |
| Review-only issue diakui; blocking diakui; reason kosong | Resolusi review sah, blocking/invalid ditolak; fidelity_confirmation tetap wajib |
| PUT setelah lint/resolution; concurrent approve; administrative revision approved | Stale 409; hash binding benar; approved report tidak salah menjadi stale |
| NumericPolicy tolerance kosong/unit salah/relative>1/free expression | Approval ditolak; policy 0/0 eksplisit dan none rounding sah |
| Published package diedit/new-version/group membership conflict | Snapshot lama utuh; draf baru approval kosong; group uniqueness ditegakkan |
| Schema 1 fixture | Histori terbaca tanpa auto-approve/mengisi kontrak rekaan; kreasi v2 menuntut fields/lint |
| Mahasiswa membaca detail/list/export public task | Tidak mendapat internal DSL/prompt/lint resolution/biaya; akses course asing 404 |

Jalankan domain/DB/API tests serta Playwright alur context→draft package→lint resolution→approval→publish→student task. Catat hasil mock/live terpisah. Uji schema/provider failure tidak menghilangkan draft.

## 6. Gate dan handover

- [ ] Paket dan CheckSpecification schema 2 lengkap, berversi, dapat dipublikasikan dan dibaca mahasiswa berizin.
- [ ] NumericPolicy schema/approval dan registry/DSL/DAG constraints tersedia.
- [ ] Lint, resolution, fidelity approval, invalidation dan concurrency sesuai kontrak.
- [ ] Fixtures sumber asli, semantic fidelity negatives dan schema 1 history tersedia; tidak ada grade/dashboard fields.
- [ ] Regresi B0/B1, OpenAPI/client dan traceability diperbarui.
- [ ] `docs/handover/B2.md` menyertakan approved synthetic assignment IDs/versions, manifest hashes, command/results dan dependensi B3/B4/B5.

**BERHENTI setelah B2.** Schema grounding tersedia tidak berarti executor atau semantic verification sudah selesai.

## 7. Prompt agent

~~~text
Implementasikan hanya B2 ALIGN. Baca master v2, indeks, dokumen B2 dan handover B1; inspeksi schema/kontrak nyata. Selesaikan paket normatif, original instructions, criteria/requirements, NumericPolicy, CheckSpecification schema 2, DSL/DAG/grounding/derivation, deterministic lint/resolutions, fidelity approval dan assignment publication. Gunakan mock/manual default dan shared parser B1; jangan menambah kewajiban lewat checklist. Uji invalidation/concurrency/Unicode spans/history schema 1 serta student serialization. Perbarui traceability dan docs/handover/B2.md; berhenti setelah gate, jangan mulai B3.
~~~

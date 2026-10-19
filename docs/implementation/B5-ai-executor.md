# B5 — AI Executor

**Status implementasi: BELUM DIMULAI. Hasil pengujian: BELUM DIJALANKAN.**

[Indeks dan matriks](README.md) · [Master](../../ALIGN_IMPLEMENTATION_SPEC.md) · [Predecessor: B4](B4-review-manual-dan-release.md) · [Berikutnya: B6](B6-numeric-dan-biaya.md)

## 1. Tujuan dan entry gate

Hubungkan executor berbukti ke produk manual tanpa memberi model kewenangan dosen. Sistem menghasilkan catatan untuk seluruh elemen dan kandidat hanya ketika applicability, source/coverage, grounding serta verifikasi kategori lolos. Hasil sah dapat completed_with_limits.

Entry: B4 manual review/publication terbukti tanpa provider; B3 registry/inventory/hash/parser checkpoints; B2 approved schema2/grounding/operator/fact registries; B0 jobs/adapters/manifests. Verifikasi DB worker role tidak dapat menulis keputusan/approval/publication. Perubahan consumer B4 hanya sesuai kontrak master, tanpa overwrite histori.

## 2. Scope dan referensi

Normatif: master Bagian 7–11, 13–18, 19–21.1/21.5, 22–24.

- Gate FR-04, FR-08–FR-09, FR-13; nonnumeric FR-07 dan executor FR-16/FR-22; regresi FR-11–FR-15.
- Gate/lanjut INV-01, INV-03, INV-06–INV-07, INV-10, INV-13–INV-15; semua version/owner/worker invariants tetap berlaku.
- Gate AC-MVP-02, AC-MVP-13, AC-MVP-17–AC-MVP-24, AC-MVP-31, AC-MVP-34, AC-MVP-40, AC-MVP-47, AC-MVP-50–AC-MVP-51, AC-MVP-56–AC-MVP-57, AC-MVP-65. Human-only AC-MVP-52 diuji; numeric acquisition lengkap B6.

Default mock, routing baseline dan semantic batch1. Real provider adapter harus tersedia melalui konfigurasi; live smoke boleh belum dijalankan bila key/dana tidak tersedia, dengan status jelas. Tidak mengaktifkan batch3/5 atau efficient semantic optimization tanpa laporan. Automated numeric orchestration B6; delegate perhitungan verified yang tersedia ke core B4, jangan menggantinya dengan LLM.

## 3. Urutan implementasi

### B5.1 — Run, deterministic execution dan applicability

1. AnalysisRun mengikat SubmissionVersion+approved spec schema2+release immutable. Fingerprint seluruh source/context/criterion/spec/numeric-policy/parser/profile/model/retrieval/rules/prompt/routing/batching/grounding/lint versions; retry sama memakai run sama, config berubah run baru.
2. Stages validate_manifest→parse→build_evidence→index→execute_checks→verify→ready_for_review. Reuse checkpoint/output valid dan source-scope cache; worker tidak membuat ready sebelum element records/candidate transaction sah.
3. Authorize capability dan recheck source hashes/snapshots sebelum operasi. Tidak memuat file di luar final manifest atau materi normatif sebagai jawaban mahasiswa.
4. Topological sort dependencies, resolve always/fact_rule/lecturer_required dari facts deterministic/lecturer_confirmed. Proposed/unavailable facts→unresolved; dependent unresolved→unassessable; elemen independen lanjut. not_applicable→check_status=null dengan reason, bukan success.
5. Operator registry adalah fungsi aplikasi typed, bukan eval/SQL/shell/model tools. Structural operation dipakai bila benar-benar menyelesaikan check; keyword match bukan pengganti semantic requirement.
6. Setiap elemen mencatat applicability/source_coverage/evidence_relationship/check_status terpisah sesuai Bagian9. Tidak ada finding bukan berarti semua tuntutan terpenuhi.

### B5.2 — Retrieval, expansion dan coverage

1. Muat instruksi/descriptor/requirement/context/policy resmi langsung dari ID/version. Paket minimum mempertahankan seluruh kondisi; jika tidak muat limit, unassessable/processing_limit, bukan ringkasan kewajiban baru.
2. Index hanya EvidenceItem namespace submission versi ini. Filter course/assignment/owner/version/purpose/hash/capability **sebelum** BM25/vector ranking; query DB nyata menguji bahwa data course/owner lain tidak menjadi kandidat.
3. Chunk narasi target600 token/overlap80 dengan tokenizer manifest; table rows membawa header/unit/caption. Caption/embedding diagram tidak menggantikan inspeksi visual yang menentukan.
4. BM25 rank_bm25 Unicode lowercase/whitespace tanpa stemming default; exact cosine pgvector1536; top20 masing-masing, RRF k60/rank mulai1, deterministic tie source order/evidence ID. Bukan ts_rank yang diberi nama BM25 dan bukan approximate index default.
5. Rerank efficient maksimum20 fusion candidates; paket awal≤6 chunks ditambah required parent/context, interpret input≤8.000 token. Maksimum2 reformulations/element tetap menghabiskan call/token budget.
6. Semua retrieval/expanded/sent/inspected units dicatat terpisah; top-k kosong memicu allowed exhaustive compatible-source scan. Similarity rendah tidak mengeluarkan unit dari negative denominator.
7. Coverage record per elemen mencatat seluruh compatible units, inspected/excluded/unresolved dan alasan/metode. Exclusion hanya rule struktural/type sah; visual meragukan tetap compatible. Belum dibaca/partial/failed/unreadable/truncated yang menentukan memblok negative gate.
8. Embedding/provider failure boleh structural+BM25 dengan channel status akurat; bukan mengaku hybrid penuh. Positive support sah dapat cukup tanpa membaca semua; absence/gap wajib exhaustive inspection dalam batas atau unassessable.

### B5.3 — Observations, grounding dan claim verification

1. Model menerima authorized normative/evidence payload dan schema, tanpa prior finding narrative saat menghasilkan observations. Student instruction sebagai isi sumber tidak mengubah system/schema/operator/access.
2. Output Bagian9.5: observations codes/evidence IDs/text, support_bindings relationship_code/role/observation_codes/evidence IDs/quotes, relationship_observations, proposed relation, limitations/reasons. Tidak menerima model check_status/publication sebagai perintah.
3. Schema strict, IDs/membership/source version, finite/bounded anchors, Unicode quote span/text, roles/qualifiers dan coverage diverifikasi kode. Quote valid terhadap OCR hanya membuktikan OCR text match, bukan image truth.
4. GroundingEvaluation server mengikat per-elemen mechanical states dan semantic support. Anchored positive memerlukan content binding; linked positive seluruh required roles/relationship observations; evidence yang sama sah multi-role. Numeric merujuk engine records; human_only→human_required/unassessable bila applicable.
5. Full absence boleh bindings kosong/not_found tetapi negative coverage gate wajib benar. Partial gap perlu bindings bagian ada dan coverage bagian hilang. Missing operand numeric tidak berubah missing_requirement.
6. Claim semantic verification terpisah dari interpretation, menilai satu klaim terhadap source/coverage relevan; locator/quotes/roles lengkap belum otomatis supported. Unsupported/insufficient candidate ditahan; observations/limits tetap ditampilkan dosen.
7. Candidate kategori sesuai9.2: mandatory explicit absence→missing_requirement; descriptor observable gap→rubric_coverage_gap; verified engine difference→numerical_inconsistency. Tidak ada quality diagnosis holistic atau AI authorship/plagiarism.
8. Dedup exact key/approved obligation alias, bukan sentence similarity. Nonnumeric version/obligation/signature; numeric policy/context/sorted operands/mode. Merge links/lineage, bukan menghilangkan masalah berbeda pada criterion sama.
9. Simpan ElementCheck untuk semua elemen, termasuk no_issue, not_applicable, human_only dan limited. Akhiri completed_with_limits bila cap/source limitations; tidak menghilangkan unfinished units/checks.

### B5.4 — Adapter, routing dan batch validation

1. Lengkapi adapter nyata lewat OpenAI Responses text/vision dan embedding endpoint sesuai dokumentasi versi resmi saat implementasi. Strict output tetap divalidasi Pydantic/business constraints; refusal/truncation/provider errors disimpan typed.
2. Calls hanya backend worker melalui adapter; parser child/browser tidak memegang API key. Mock fixture menggunakan kontrak sama, label provider_mode=mock; tidak silent fallback live→mock. Tidak ada browser/search tools dalam pipeline.
3. Routing typed baseline Bagian13.5: unauthorized stop; human boundary/dependency unresolved manual; deterministic available pakai kode; unreadable source repair/abstain; insufficient negative coverage inspect; limit abstain; schema repair terbatas; visual vision; complex/cross-source semantic complex langsung.
4. Baseline interpret_evidence/verify_claim semantik lain juga complex; efficient untuk extract_fields/rerank/draft_feedback sederhana. Efficient semantic route hanya policy validated; self-reported confidence bukan dasar tunggal routing/acceptance.
5. RoutingDecision dicatat termasuk tanpa call: feature snapshot/guards/action/role/reason/source/coverage/remaining limits/policy refs/outcome/call links. Null feature tidak diubah false. Release/policy tidak diganti saat run berjalan.
6. Semantic batch1 aktif. Implementasikan response validation per element dan technical grouping contracts maksimum5 untuk test/evaluator: same owner/submission/spec/release/evidence set/task family/model/prompt/grounding, dependencies resolved. Human-only/dependent/Decimal result tidak digabung jadi model judge.
7. Test batch partial/duplicate/foreign IDs memakai synthetic test-only response validator, bukan activation policy candidate pada live. Entry valid diproses terpisah, omitted/invalid tidak menambah coverage/no_issue; retry dalam cap. Physical call hanya sekali ledger, atribusi elemen turunan.
8. Cap awal: 16 text/vision total, vision≤4, input40k/output8k, embedding80k, aggregate active600detik, analysisUSD0.20. Transport retry≤2 per stage backoff2/8+jitter; schema repair≤1; semantic escalation≤1/element. Semua attempts masuk cap; processing/storage terminal saja boleh setelah cap, bukan inference baru.
9. Paid path tetap tertahan jika reservation/rate/usage/reconciliation belum lengkap. Implementasikan prerequisite finansial terlebih dahulu bila ingin live B5; B6 melengkapi seluruh coverage finansial, bukan izin call gratis sebelumnya.

### B5.5 — Integrasi review dan drafting

1. GET analysis mengirim seluruh checks/grounding/support/routing reasons aman bagi dosen; mahasiswa technical status saja. Lecturer memilih membuat ReviewVersion baru dari run baru; rerun tidak menimpa review/annotation/publication lama.
2. Hubungkan field/criteria/spec drafting B1/B2 ke adapter configured dengan approvals dosen tetap wajib dan draft tetap dapat diedit saat failure.
3. Feedback model menerima hanya relevant official fields, selected confirmed findings/evidence/lecturer notes/limitations dari complete review. Tidak retrieval baru atau input daftar rejected criticisms. Default limits USD0.03, ≤2 text calls, input12k/output3k, no vision. Failure→manual/template generation mode akurat.
4. UI menampilkan observations/interpretation/limitations/candidates terpisah dari keputusan dosen, semua elemen terlihat, manual tetap dapat dipakai saat failed/limited. Tidak ada AI publication/autoaccept.

## 4. Kontrak API dan penyimpanan

Prefix `/api/v1`:

| API/service | Kontrak |
|---|---|
| `GET /runtime/releases` | Lecturer active/verified usable releases, tanpa secret/rate internals |
| `POST /submission-versions/{id}/analysis-runs` | Lecturer, approved release_manifest_id, Idempotency-Key;202 job atau existing identical run |
| `GET /analysis-runs/{id}` | Semua checks/candidates/limitations/versions/grounding/bindings dan routing reasons aman |
| `GET /jobs/{id}`; `POST /jobs/{id}/retry` | Scoped status; recoverable same fingerprint/stage/limits, no human overwrite |
| Review/draft APIs B1/B2/B4 | Kontrak tetap; model hanya mengisi draft/kandidat yang diizinkan |
| Provider methods Bagian13.2 | call_id/job_id/task/manifest/policy/element IDs/authorized payload/schema/limits; response usage/status/request ID typed |

Migration menambah retrieval_chunks/chunk_evidence_links/embeddings, analysis_runs/element_checks, coverage_records/inspections, grounding_evaluations/support_bindings/numeric_links, finding_candidates dan seluruh element/requirement/criterion/coverage links, routing_decisions/call_links dan semantic_batch_members. FK/scope/unique mematuhi Bagian15. Candidate invalid boleh internal diagnostic, bukan accept-able finding.

Konfigurasi dimensi1536 harus sama schema; perubahan perlu manifest+migration/reindex, bukan padding/truncating. Cache key source/config/owner/version; tidak lintas mahasiswa walau file hash sama.

## 5. Verifikasi

| Fixture/skenario | Hasil wajib |
|---|---|
| Positive evidence nyata, absence lengkap, descriptor partial | no_issue atau candidate hanya setelah gate sesuai kategori, scope check jelas |
| Top-k kosong tetapi evidence di bagian lain | Expansion/scan menemukan dukungan atau unassessable; bukan langsung missing |
| Unreadable diagram, failed unit, truncated context, call cap | Unit/check tetap ada; negative gate tertahan; elemen independen lanjut |
| Unverified fact, dependent unresolved, human_only | Unresolved/unassessable atau not_applicable hanya dari fact sah; human-only tidak diputus AI |
| Quote valid tetapi irrelevant, missing role/qualifier, satu tabel multi-role | Mechanical vs semantic dibedakan; support parsial ditahan; tabel lengkap diterima tanpa sumber tambahan |
| Dua query masalah sama dan dua masalah berbeda | Dedup tepat, links digabung, berbeda signature tidak hilang |
| ID/course/version asing, quote/schema invalid/refusal/truncation | Ditolak/limited typed; mahasiswa tidak mendapat kandidat/raw payload |
| Source memerintah publish/score/browse/ambil mahasiswa lain | Backend capability/gates tetap; tidak ada perubahan role/requirement/data scope |
| Partial batch validator dua valid+satu omitted/foreign | Valid per-entry saja; invalid/omitted zero added coverage; candidate policy tetap nonaktif |
| Source broken tetapi model confidence tinggi | repair/inspection/abstain; bukan loop strong-model calls atau diterima confidence |
| Crash/redelivery/broker recovery, config changed rerun | Stage reuse aman; all records commit benar; run baru dan review lama immutable |
| Feedback model rejected/deferred input exclusion/provider failure | Kritik hanya confirmed selected; fallback manual/template benar |
| Schema1 history dan permintaan run v2 | History tetap terbaca/manual; new AI memerlukan approved spec2 |

Deterministic software tests memakai actual source fixtures dan mock provider. Scope/vector isolation diuji PostgreSQL+pgvector nyata, bukan mock filter. Live synthetic smoke bila sah melaporkan manifest/usage/cost; tidak tersedia→belum diuji, tanpa klaim precision/recall empiris.

## 6. Gate dan handover

- [ ] Executor/DAG/retrieval/coverage dan all-element persistence bekerja dalam cap.
- [ ] Typed grounding, mechanical+semantic verification, category/dedup dan human-only boundary benar.
- [ ] Baseline routing/batch1 aktif, per-entry validator dan decision logs tersedia; candidate optimization nonaktif.
- [ ] Configurable real adapter dan explicit mock tersedia; live readiness/status jujur, paid prerequisite tidak dilewati.
- [ ] B1/B2 drafting dan B4 model feedback/review consumer terhubung tanpa human-history overwrite.
- [ ] Semua gate AC B5, regresi manual/provider-off, traceability dan `docs/handover/B5.md` memuat actual commands/results/manifest hashes/remaining numeric-financial scope.

**BERHENTI setelah B5; jangan mulai B6 otomatis.** Mock gate correctness bukan hasil efektivitas penelitian atau bukti batch/routing optimization validated.

## 7. Prompt agent

~~~text
Implementasikan hanya B5 ALIGN berdasarkan master, indeks, dokumen ini dan handover B4. Kerjakan deterministic/applicability/DAG, scoped hybrid retrieval/coverage, observations/typed grounding/semantic verification dan dedup, lalu adapter/routing/batch validator serta review/draft integration. Semua elemen dan failed/unreadable units tetap tercatat. Gunakan mock berlabel dan baseline complex semantic routing/batch1; real adapter tersedia via config, paid call wajib guard lengkap. Jangan menerima absence dari top-k kosong atau dukungan dari quote cocok saja. Uji source nyata, DB vector isolation, failure/budget caps dan preserved human history; perbarui traceability/docs/handover/B5.md lalu berhenti.
~~~

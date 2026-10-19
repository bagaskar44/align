# B6 — Numeric dan Biaya

**Status implementasi: BELUM DIMULAI. Hasil pengujian: BELUM DIJALANKAN.**

[Indeks dan matriks](README.md) · [Master](../../ALIGN_IMPLEMENTATION_SPEC.md) · [Predecessor: B5](B5-ai-executor.md) · [Berikutnya: B7](B7-ux-dan-robustness.md)

## 1. Tujuan dan entry gate

Lengkapi automated numeric checks dan integritas operasi berbayar. Sistem hanya memflag besaran sepadan/terverifikasi, menghitung dengan Decimal dan policy resmi, serta menjaga limit/cadangan pada semua call dan retry. Routing/batching/resource releases tidak berubah diam-diam.

Entry: B5 executor/adapter/retrieval/grounding dan manual baseline B4 bekerja; B2 NumericPolicy approved serta core manual Decimal B4 tersedia. Audit paid consumers yang sudah diperkenalkan B1–B5: tidak boleh ada network call yang melewati reservation/rate/limit guard. Jika guard belum lengkap, live tetap disabled hingga slice finansial tahap ini selesai.

## 2. Scope dan referensi

Normatif: master Bagian 7.6, 9–13, 15–18, 20, 21.4, 22–24.

- Gate FR-07, FR-10, FR-12, FR-15–FR-16, FR-22; regresi manual/drafting/publication FR-04/FR-11/FR-13/FR-14.
- Gate INV-04, INV-08, INV-16; semua owner/coverage/human-authority invariants tetap berlaku.
- Gate AC-MVP-25–AC-MVP-29, AC-MVP-37–AC-MVP-39, AC-MVP-52, AC-MVP-55, AC-MVP-58–AC-MVP-59; regresi AC-MVP-40, AC-MVP-56–AC-MVP-57.

Evaluator policy lengkap dan pelaporan penelitian diserahkan B8. B6 wajib menyediakan report schemas, registration/activation enforcement dan deterministic gate tests; ketiadaan validation empiris mempertahankan baseline, bukan memalsukan eligible report.

## 3. Urutan implementasi

### B6.1 — Numeric acquisition dan source quality

1. Lengkapi NumericObservation dari source registry: literal, normalized decimal text, tuple metric/entity/period/scenario/population/definition/assumptions/unit/value/source, policy/element refs dan source quality.
2. `native_literal` hanya literal sumber dengan locator valid. `ocr_verified` memerlukan re-read crop/OCR kedua/vision yang sesuai dan verification ref; ketidaksepakatan→unverified. Lecturer confirmation berlabel terpisah, tidak menulis ulang hasil AI lama.
3. Formula/cache XLSX disimpan terpisah. Cache tidak otomatis operand verified, termasuk cache yang tampak masuk akal. Hanya nilai dikonfirmasi dosen atau dihitung operasi aplikasi resmi dari verified operands dapat digunakan sesuai master.
4. Context yang relevan namun unknown→comparability unresolved; field null hanya bila NumericPolicy menyatakan tidak relevan. metric/entity/definition/unit selalu wajib. Gross/net, accuracy/precision, currency/period/scenario/population berbeda tidak dibandingkan karena angkanya mirip.
5. Missing/invalid/unverified operands numeric→unassessable dengan reason khusus; tidak dikonversi menjadi missing_requirement atau absence candidate.

### B6.2 — Normalisasi dan engine

1. Parser angka mempertahankan literal/tanda/locale/scaling/unit. `1,234` dengan locale ambigu tidak ditebak; mixed unresolved→ambiguous_number_format. Reject NaN/infinity/out-of-range tanpa silent truncation.
2. Konversi hanya linear approved factor decimal positif dan dimension sama: percent↔ratio, ribu/juta↔satuan, satuan registry. Currency exchange eksternal tidak dijalankan; growth percent berbeda dari percentage points.
3. Core shared B4 diperluas untuk pairwise, sum_against_reported dan ratio_against_reported. Selectors/operand IDs disimpan typed/scope-validated, daftar sum lengkap tanpa duplicate; denominator0→unassessable, bukan infinity.
4. Python Decimal context precision34; canonical decimal text adalah sumber ketepatan. Kolom numeric(38,12) hanya indeks pendamping dan boleh null jika tidak dapat menyimpan representasi tanpa perubahan; jangan menghitung dari rounded companion column.
5. Rounding none default; half_even hanya bila policy quantum positif resmi. Simpan before/after rounding dan rule-engine version.
6. Hitung `difference=abs(x-y)`, `allowed_difference=max(epsilon_abs, epsilon_rel*max(abs(x),abs(y)))`, inconsistent hanya jika difference>allowed_difference. Tepat batas konsisten, bukan hasil epsilon_abs+epsilon_rel.
7. Simpan normalized context/operands/operation/result/difference/tolerance/policy/rule manifests. Candidate numeric hanya setelah comparability+source quality+engine gates; deterministic arithmetic tidak memerlukan LLM verifier tambahan.
8. GroundingEvaluation comparable_numeric mengikat numeric_check IDs dan decision_basis numeric_comparison. Human-only numeric tidak dieksekusi otomatis. Dosen melihat operand/lokasi/policy, bukan label nilai akademik.

### B6.3 — Budget, reservation dan usage ledger

1. Database budget account diinisialisasi sekali confirmed_total=25.00USD, reserve_floor=5.00USD, planned_topup=0.00USD. Available sesuai Bagian17.3 mengurangi known spend, held reservations dan pending maximum liability; jangan menghitung liability attempt sama dua kali. Planned topup bukan dana tersedia.
2. Before setiap physical paid call, hitung upper-bound input/output/reasoning/images/tools/rerank/embedding/repair/retry berdasarkan verified model-rate snapshot. Unknown price/billing bound→no call/manual, bukan estimate0.
3. Transaksi lock budget account dan per-job limits, cek global/per-job/call/token/time, buat unique call_id reservation. Commit dahulu baru network; jangan memegang transaction lock selama menunggu provider.
4. Setelah response, simpan provider request ID/actual usage/rates snapshot/actual cost per physical attempt. Settle actual, release sisa yang sah. USD finansial numeric(18,8), reserve dibulatkan ke atas; tidak memakai float.
5. Timeout/crash dengan billing unknown→reservation reconciliation_required dan pending usage; jangan release saldo/anggap gratis. Retry butuh reservation tambahan jika attempt pertama mungkin tertagih. Operator merekonsiliasi dari request ID/billing, bukan mengklaim provider exactly-once.
6. Usage melampaui upper bound membekukan call baru sampai manifest/ledger direkonsiliasi. Seed/restart tidak mengisi ulang budget. Confirmed topup membutuhkan aksi operator/referensi konfirmasi/audit; planned topup tidak mengganti confirmed_total.
7. Setiap attempt punya call_id/reservation/usage sendiri dan logical_call_id+attempt_no. One physical batch call dicatat sekali; per-element attribution eksplisit sebagai turunan, bukan biaya ganda.
8. Terapkan pada semua consumers: context, criteria/spec draft, embeddings, rerank, interpretation/verifier, vision/OCR provider, repair/retry, feedback dan offline evaluator. Tidak ada script key langsung yang menghindari ledger.

### B6.4 — Limits, failure dan release compatibility

1. Analysis cap default USD0.20, ≤16 text/vision calls (vision≤4), input40k/output8k, embeddings80k dan aggregate active600detik termasuk retries. Feedback USD0.03/2text/input12k/output3k/no vision; context/spec drafts masing-masing USD0.05/3calls/input20k/output4k/2vision.
2. Time reservations mencegah parallel tasks memakai sisa run yang sama. Semua parser/general stages dan provider attempts ikut usage/time; antrean tidak dihitung active processing. Limit tidak dinaikkan otomatis demi menyelesaikan batch1.
3. Transport retry2/stage, schema repair1 dan semantic escalation1/element tetap tunduk cap agregat. Completed_with_limits sah; masih catat semua elemen dan lanjut manual. Provider readiness tidak menghalangi health manual.
4. Model/retrieval/prompt/rules/parser-resource/routing/batching/grounding/lint manifests diikat release immutable. Harga/model identifiers/capability smoke diverifikasi pada akun sebenarnya saat live; baseline example tidak diberi verified tanpa bukti.
5. RoutingPolicy/BatchingPolicy schema/master versions dan core hash tepat. Semantic batch1 baseline tetap usable; candidate3/5 tidak dapat diaktifkan live hanya dari `BATCH_MAX_ELEMENTS=5`.
6. Validated optimization membutuhkan immutable PolicyValidationReport hash/core/exact model/prompt/schema/grounding/resource/scope/data mode cocok. State/report metadata tidak masuk policy core hash; promosi membuat manifest validated baru dengan core identik. Rules berubah→report baru.
7. Registration/activation memverifikasi eligibility Bagian21.4; mock report hanya software gate test dan tidak mempromosikan live. Synthetic-only eligible report hanya untuk DATA_MODE=synthetic; real use membutuhkan representatif/berizin dan authorization institusi.
8. Env berbeda dari active release tidak override silently: job incompatible ditolak dengan alasan operasi, operator membuat release cocok. Config/rate/profile change menghasilkan run/fingerprint baru, source-scoped cache invalidated dan human history tetap.

## 4. Interface, persistence dan operasi

API akademik tetap kontrak B5/B4; numeric results menjadi bagian ElementCheck/analysis/review snapshot. Tidak membuat budget endpoint mahasiswa atau dashboard analitik. Operator memakai CLI/service atau tabel admin sederhana dengan permissions metadata yang sah.

| Interface | Kontrak |
|---|---|
| Numeric engine | Typed observations + approved NumericPolicy + source verification; return comparability/check typed, decimal strings dan reason codes |
| Paid adapter guard | Task/attempt/manifest/limits→committed reservation→network→usage/settlement atau reconciliation_required |
| `align release register --file PATH` | Validasi refs/checksums/profile benchmark/policy report dan operator audit |
| `align release activate RELEASE_ID` | Hanya compatible verified baseline/eligible policy release, tanpa state mutation dalam run |
| Operator topup/reconciliation | Amount/ref/actor/reason/audit; exact command diimplementasikan dan dicatat sebelum diklaim verified |
| Evaluator integration B8 | Memanggil service/adapter/ledger sama dan memakai report schema immutable; tidak perlu key untuk software/mock gates |

Migration melengkapi numeric_observations/checks dan grounding_numeric_links, budget_accounts, usage_reservations/entries, routing_call_links/semantic_batch_members, method/release/parser_profile links dan policy_validation_reports. Unique logical attempt/call IDs, nonnegative amounts/time, FK/source-version dan immutable guards sesuai master15. Amount companion tidak menggantikan canonical text numeric.

Report status eligible/ineligible/incomplete, allowed data modes, dataset/protocol/codebook/split hashes, candidate/baseline core hashes, support/metrics/intervals/violations/repeats dan ledger refs mengikuti21.4. Jangan menerima filename mutable saja sebagai bukti eligibility.

## 5. Verifikasi

| Fixture/skenario | Hasil wajib |
|---|---|
| `0,25` vs`25%`, Rp125juta vs125.000.000, minus dan locale campur | Equivalent jika policy/locale jelas; ambiguous tidak ditebak |
| Gross/net, accuracy/precision, entity/period/scenario berbeda | Comparability unresolved/false yang tepat; tidak numeric inconsistency otomatis |
| x100/y107, abs5/relative0.05; tepat batas dan x/y0 | Difference7/allowed5.35→candidate; equality konsisten; no zero division |
| Sum duplicates, ratio denominator0, out-of-range/NaN, rounding policy | Guardrails benar; canonical34precision; no silent companion rounding |
| Formula cache kosong/usang, digit OCR disagreement, human-only numeric | Unassessable/numeric reason benar; tidak absence atau LLM arithmetic |
| Concurrent reservations mendekati USD20 spendable | ReserveUSD5/planned topup tidak terpakai; DB lock mencegah overspend |
| Provider timeout setelah request, crash sesudah billing, retry | Held liability dan reconciliation_required tetap; attempt baru reserve lagi; no free-call assumption |
| Response actual>estimate, restart/seed dan confirmed topup | Freeze/reconcile; saldo tidak reset; topup butuh confirmation ref |
| Batch physical call dengan beberapa elemen dan omitted entry | Ledger satu biaya fisik; scope/coverage per elemen, omitted bukan inspected |
| Candidate3/5/no report/wrong hash/model/resource/data mode | Activate ditolak; baseline1 tetap tersedia; mock tidak mempromosikan live |
| Cap call/vision/token/active time/eskalasi habis | Tidak ada call baru; completed_with_limits+manual workflow tetap dapat selesai |
| Config/rates/parser/routing/grounding berubah | Run/fingerprint baru; cache tepat; publication/review lama tidak berubah |

Jalankan concurrency/reservation/reconciliation pada PostgreSQL nyata. Mock provider menyimulasikan request IDs, usage, unknown billing dan crash; report menyebut software simulation. Actual live synthetic smoke bila dana sah mengikut ledger; ketiadaannya bukan alasan mengarang harga/usage/reliability report.

## 6. Gate dan handover

- [ ] Semua mode numeric dan source/comparability/Decimal gates benar; manual B4 regression lulus.
- [ ] Seluruh paid consumer/evaluator hooks melewati reservation/ledger/limits/reconciliation tunggal.
- [ ] Concurrent/unknown billing/seed/restart/topup tests membuktikan integritas finansial.
- [ ] Immutable manifests/release/report validation dan candidate activation guards tersedia; baseline tetap usable tanpa penelitian.
- [ ] Manual provider-off dan simulated budget-exhausted review→publish berjalan.
- [ ] Traceability, exact command/results/rates verification status, operational limitations dan `docs/handover/B6.md` diperbarui.

**BERHENTI setelah B6; jangan mulai B7 otomatis.** Selesai software biaya bukan bukti initialUSD25 mencukupi seluruh penelitian atau batch3/5 menghemat biaya.

## 7. Prompt agent

~~~text
Implementasikan hanya B6 ALIGN dari master, indeks, dokumen ini dan handover B5. Audit semua paid consumers terlebih dahulu. Lengkapi automated NumericObservation/comparability/locale/unit/Decimal policies memakai core B4, serta global/per-job reservations, usage ledger, retries/unknown billing reconciliation dan limit enforcement. Verifikasi immutable resource/model/routing/batching releases dan report eligibility; tetap baseline/batch1 tanpa laporan empiris sah. Uji angka, concurrency/overspend, pending liability, seed/restart dan provider-off/budget-exhausted manual publication. Catat actual results dan limitations di traceability/docs/handover/B6.md, lalu berhenti sebelum B7.
~~~

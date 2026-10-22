# B8 — Handover dan Pilot Readiness

**Status implementasi per 22 Oktober 2026: software handover B8 dikerjakan dan diuji pada lingkungan sintetis terisolasi; pilot readiness keseluruhan tetap parsial.** AC-MVP-45 lulus contoh software, enam runtime Docling dijalankan 6/6, dan policy/robustness reports mempertahankan status `incomplete` atau `software_gate_only` sesuai bukti. Screen reader/perangkat touch nyata, keputusan institusi, kapasitas maksimum, RPO/RTO operasional, source-corruption robustness dan hasil empiris tetap pending; lihat [handover B8](../handover/B8.md).

[Indeks dan matriks](README.md) · [Master](../../ALIGN_IMPLEMENTATION_SPEC.md) · [Predecessor: B7](B7-ux-dan-robustness.md) · [Tahap terpisah: B9](B9-moodle-sandbox.md)

## 1. Tujuan dan entry gate

Serahkan MVP standalone yang dapat direproduksi, lengkap dengan source/config/contracts/migrations/tests dan bukti kesiapan per kemampuan. Lengkapi offline evaluator dan research traceability tanpa mengarang hasil empiris atau menuntut kredensial live untuk menjalankan baseline software.

Entry: B0–B7 memiliki handover dan hasil test/benchmark aktual, API/web/worker terintegrasi serta manual/provider-off workflow. Periksa residual failures dan untested capabilities dari B7; selesaikan kewajiban software yang masih tersisa sebelum menyatakan MVP selesai.

## 2. Scope dan referensi

Normatif: master Bagian 0, 8.5, 13, 15–18, 20–24, 26.

- Gate FR-24 dan audit FR-01–FR-24/INV-01–INV-16 seluruhnya.
- Gate AC-MVP-45, AC-MVP-64 dan audit bukti AC-MVP-01–AC-MVP-65; regression fresh-install AC-MVP-01 serta AC-MVP-42–AC-MVP-44 bila relevan.
- Sediakan dukungan software/template AC-RES-01–AC-RES-07; **pemenuhan empiris terpisah**, bukan otomatis passed B8.

B9 tidak menjadi dependency handover. Pilot readiness berarti batas operasional/config diketahui, bukan izin otomatis deployment publik/pemrosesan data nyata atau klaim precision/recall/efektivitas pembelajaran tercapai.

## 3. Urutan implementasi

### B8.1 — Audit kontrak dan reproducibility

1. Cocokkan master, OpenAPI server-generated snapshot, Pydantic/domain schema, migrations, worker payload dan generated/contract-tested typed web client. Perbaiki drift pada seluruh consumer, bukan dokumentasi saja.
2. Audit seluruh FR/INV/AC dalam requirements_traceability.md: link actual implementation/test/report, environment/mode dan status. Tidak ada requirement wajib hilang karena hanya dihitung daftar ID.
3. Uji checkout/database/volumes baru terisolasi dengan lockfiles: install→Compose→migrate→bootstrap→seed→login→manual workflow→mock analysis→review→approve→publish→student export. Jangan menghapus volume/workspace pengguna yang sedang dipakai.
4. Verifikasi startup/migration fresh, schema1 history fixture/read/manual compatibility dan new schema2 approval/run. Seed hanya synthetic; frozen/gold dataset tidak dibundel ke runtime seed.
5. Sediakan README dengan command persis yang benar-benar dijalankan untuk install/migrate/bootstrap/seed/dev/smoke/test/build/backup/restore. One-command dev mengorkestrasi entrypoints teruji, bukan menyembunyikan kegagalan.
6. `.env.example` mencakup semua settings/limits Bagian18.3 tanpa secret. Manifest examples dibedakan dari live verified release; model/rates/weights/resource/policy status dan verification evidence tercatat.

### B8.2 — Offline evaluator metrik dan protocol

1. Lengkapi evaluator yang menerima gold/prediction/manifest offline dengan unit `(bundle_version,obligation/check_target,issue_category)` dan numeric context bila relevan. Schema memisahkan applicability, gold_assessability, issue_label dan coverage/evidence relation.
2. issue_label null untuk gold not_applicable/unassessable. Matching satu-ke-satu berdasarkan obligation/category/numeric context dan adjudicated mapping, bukan fuzzy narrative saja. Duplicate prediction tidak menambah TP.
3. Hitung precision=TP/(TP+FP), end-to-end recall=TP/(TP+FN), F1, selective decision coverage/risk, gold-unassessable violation, operational document coverage, source inspection coverage, anchor validity, evidence support rate, false findings/bundle dan biaya penuh. Denominator0→NA sesuai21.2, bukan perfect/0 otomatis.
4. Parser/retrieval/abstention pada gold issue_present tetap FN. Report duplicate burden, modality/support counts dan acquisition/interpretation/unsupported/abstention failures terpisah.
5. Uji contoh3unit master: satu TP, satu FN karena abstain, satu TN→precision1, recall0.5, coverage2/3, risk0. Tidak menghapus abstain untuk menaikkan recall.
6. Implementasikan interval assignment-cluster bootstrap default10.000 resamples/seed20261018, percentile2.5/97.5 dan seluruh units/bundle assignment ikut bersama. Stratify dua frozen-test courses bila memungkinkan; independent assignments<5→descriptive/unstable. Zero-denominator replicates dilaporkan jumlah validnya, bukan diganti perfect.
7. Protocol templates memisahkan pilot60 dan studi utama dev180/validation60/frozen60 course-disjoint; total utama300, dengan pilot360. Derived variants mengikuti parent; audit student/group/template/near-duplicate leakage. Jangan mengarang dataset atau raw survey.
8. Baseline penelitian B0 deterministic rules, B1 direct strong, B2 filtered RAG strong sama dan B3 full ALIGN ditulis dengan prefiks penelitian agar berbeda dari tahap pembangunan. Sediakan manifest/protocol baseline/ablation A1–A4 offline; production guards tidak dapat dimatikan mahasiswa.
9. Target prospektif precision≥0.85/category, recall≥0.70 dan document coverage≥0.80 bukan hasil aktual. Student study target64 unik, lecturer counterbalanced study dan gold two-rater/codebook calibration adalah protocol terpisah, bukan wajib menjalankan studi sebelum handover.

### B8.3 — Parser/policy/robustness CLI dan laporan

Pertahankan command normatif berikut sebagai executable operator/offline, bukan endpoint produk:

~~~text
align parser benchmark --profile PROFILE_ID --fixtures PATH --output PATH
align evaluate policies --candidate MANIFEST_ID --baseline BASELINE_ID --dataset-manifest PATH --output PATH
align evaluate robustness --protocol PATH --dataset-manifest PATH --release RELEASE_ID --output PATH
align release register --file PATH
align release activate RELEASE_ID
~~~

1. Parser CLI B3 memakai parser lokal nyata dan report hardware/package/weights/hash/resource enforcement/cold-warm memory/time/failures/anchors/support. Mock injection terpisah dari actual benchmark. Kekurangan profile/hardware menghasilkan status/limits jelas, bukan benchmark palsu.
2. Robustness CLI B7 memisahkan compiler fidelity, executor variation, source corruption dan repeat dependence. Protocol-equivalence/partition/DAG guards aktif; mock reports tidak mengklaim stochastic reliability.
3. Policy evaluator paired baseline/candidate memakai sumber/spec/model/prompt/schema/limits yang sama kecuali faktor diuji. Semua call melalui adapter/ledger; rate/key/dana tidak lengkap→mock/software mode atau incomplete sesuai pilihan eksplisit, bukan live fallback tak terlihat.
4. Terapkan gate21.4 secara persis: critical invariants tanpa tambahan violation; tiap kategori terdampak≥20 gold positives dan≥20 predicted findings per arm;≥5 independent validation assignments; candidate precision≥max(0.85,baseline), ESR≥baseline, recall≥max(0.70,baseline−0.02), coverage≥baseline−0.02, gold-unassessable violation≤baseline dan mean actual cost≤0.90×baseline.
5. Jalankan3repeats pada subset validation yang dipilih sebelum eksperimen mencakup affected families, semua critical fixtures lulus dan compatible release/core hashes tepat. Selisih0.02 berarti2percentage points absolut. Tidak ada pending usage dianggap0 atau failure dikeluarkan denominator.
6. Inadequate support/repeats/funds/equivalence→incomplete, invariant/metric gate fail→ineligible. Gate point estimate bukan uji statistik noninferiority. Validated report immutable mengikat protocol/dataset/codebook/split/model/prompt/parser/resource/schema hashes, scope/modalities/data modes, numerators/denominators/intervals/repeats dan ledger refs.
7. Synthetic-only report tidak mengizinkan optimization real data. Mock report tidak mempromosikan live; tanpa eligible evidence register/activate mempertahankan baseline routing/batch1. CLI tidak mencetak secret atau membuka gold ke runtime.
8. Semua report JSON dan Markdown menampilkan provider_mode, data_origin, actual/simulated/missing measurements, versions/hardware, complete/incomplete dan limitations. Exit behavior/command usage diverifikasi dan didokumentasikan; incomplete tidak diam-diam dianggap eligible oleh caller.

### B8.4 — DSRM, handover dan kemampuan aktual

1. Buat research/research_traceability.md dan research/iterations dengan template rantai problem→objective→design decision→FR/INV→artifact version→demo→evaluation metric/result→redesign→communication.
2. Enam aktivitas DSRM masing-masing memuat IDs, sumber, versions/hashes dan bukti yang benar-benar ada. Iteration menyimpan hipotesis, before/after change, partition/scenario, hasil aktual dan keputusan lanjut/ubah/hentikan serta freeze consequences.
3. Survei21responden (1dosen/20asisten) tercatat sebagai informasi kebutuhan sesuai master, bukan efektivitas sistem; raw responses yang tidak ada tidak dibuat. Field hasil belum ada memakai not_yet_available+alasan.
4. Siapkan demo sintetis SI dan Manajemen dengan source/review/publication IDs/versions, langkah dan outcome aktual. Demo manual/AI bukan research evaluation atau bukti feedback meningkatkan pemahaman.
5. Report readiness per kemampuan: manual product, mock gate correctness, native parsing/OCR nyata, live synthetic inference, resource enforcement/capacity, optimized routing/batching dan empirical research. Setiap row memuat evidence/limitations; unavailable tidak disulap menjadi passed.
6. Serahkan source, locks, migrations, synthetic seed, env example, Compose, generated OpenAPI/client, tests/reports, version manifest inventory, parser benchmarks/capacity, operator backup/restore/purge/reconciliation instructions dan real limitations sesuai24.

## 4. Interface dan pengujian wajib

Pekerjaan tahap ini tidak menambah academic/public APIs. Report/protocol/evaluator schemas versioned adalah interface offline; strict validation, source/partition separation dan hashed refs tetap wajib. Gunakan DB/service/adapter aplikasi yang sama untuk CLI yang melakukan reservation/activation.

| Skenario | Ekspektasi |
|---|---|
| Fresh checkout tanpa live key/dana tambahan | Manual/mock smoke, seed, build, migrate dan seluruh CLI software modes berjalan dengan status mode jelas |
| Metrik contoh3unit dan denominator0 | Angka normative benar, NA tepat; abstain FN tidak hilang; duplicate tidak mendapat TP ganda |
| Dataset partition invalid/parent variant pindah/gold leaked | Ditolak dengan alasan; tidak menggabungkan frozen test ke tuning/retrieval |
| Bootstrap fewer assignments/zero-denom replicas | Descriptive/unstable/support/valid replicate counts jelas |
| Candidate unggul biaya tetapi turun mutu/coverage/support kurang | Ineligible/incomplete sesuai21.4; baseline tetap aktif |
| Candidate mock/wrong report hash/model/resource/mode | No live promotion; report registration/activation menolak incompatibility |
| Parser benchmark actual vs simulated failure/unsupported handwriting | Report memisahkan measurement nyata, simulation dan unverified capability |
| Robustness no live key/invalid equivalence/repeat limits | Software gate berjalan; invalid protocol bukan stability failure; incomplete tidak eligible |
| OpenAPI/client/migration/schema1 compatibility | Contract tests dan fresh/historical fixture checks konsisten |
| Handover/operator restore commands | Dijalankan pada isolated synthetic environment; hasil, biaya bila ada dan limitations aktual tercatat |

Jalankan final checks yang belum terbukti dan regresi yang terdampak perbaikan. Jangan mengulang seluruh suite stabil tanpa alasan, tetapi jangan memakai report versi lama untuk perubahan yang relevan. Test report bukan klaim lulus hanya karena command tersedia.

## 5. Gate selesai

- [x] B0–B8 dan seluruh FR/INV/AC-MVP mempunyai implementation/test/evidence links; kegagalan/pending operasional atau empiris disebut eksplisit dan tidak dinyatakan selesai.
- [x] Fresh install/migration/seed/start/build/manual/mock/contract tests dibuktikan dari checkout terisolasi.
- [x] README/env/Compose/OpenAPI/client/lockfiles dan operating commands sesuai hasil pemeriksaan.
- [x] Parser, policy dan robustness CLI/report tanpa key berjalan pada mode yang dilaporkan; incomplete/live claims tidak dipromosikan.
- [x] Metrik/denominator/interval/partition guards dan contoh tiga unit lulus software tests.
- [x] DSRM/research templates dan demo sintetis dengan runtime IDs tersedia; hasil empiris yang belum ada tetap pending.
- [x] `docs/handover/B8.md` memuat readiness matrix, commands/results, manifest/version inventory, kapasitas, skips dan limitations; LMS flag tetap off.

**BERHENTI setelah B8. B9 hanya dimulai atas inisiasi terpisah pengguna.** Jangan deploy publik, mengirim pesan, mengaktifkan integrasi atau memakai data nyata sebagai efek samping handover.

## 6. Prompt agent

~~~text
Implementasikan hanya B8 ALIGN. Baca master, indeks, dokumen B8 dan handover B7, lalu selesaikan residual software requirements dan audit FR/INV/AC. Verifikasi fresh-checkout run/build/migrate/seed/manual/mock, OpenAPI-client consistency, README/env/operating commands dan seluruh required handover artifacts. Lengkapi offline metrics/policy evaluator, parser/robustness CLIs dan DSRM templates; uji denominator/partition/eligibility dengan mode jelas tanpa mengarang empirical results. Laporkan readiness per kemampuan, actual results/versions/hardware/cost/limits dalam traceability dan docs/handover/B8.md. Berhenti setelah B8; jangan mulai B9 atau melakukan deployment/integrasi/data nyata otomatis.
~~~

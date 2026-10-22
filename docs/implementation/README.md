# Panduan Implementasi ALIGN per Tahap

Tanggal pembaruan: 22 Oktober 2026 (Asia/Jakarta). Bahasa implementasi produk dan dokumen: Indonesia.

**Status per 22 Oktober 2026: software paths B0–B7 diuji pada fixture mock/synthetic dan PostgreSQL terisolasi; bukti provider nyata, tarif aktual, adjudication semantik manusia dan hasil optimasi empiris belum tersedia.** B2 teruji dengan parser rubrik bersama, editor dan semantic-negative. B3 mencatat upload/finalisasi, registry/viewer empat format, parser native/Docling, checkpoint/recovery, fencing dan benchmark. B4 membuktikan alur provider-off manual sampai hasil resmi mahasiswa, termasuk roster kelompok, concurrency, annotation history, numeric manual checks, withdrawal dan replacement. B5 menghubungkan embedding executor, semantic verification/absence scan, drafting B1/B2, analysis recovery dan feedback UI dengan mock/fake non-network. B6 menambah numeric acquisition, paid-call ledger/reservations/reconciliation dan guard eligibility; baseline masih mock/synthetic batch 1, tanpa panggilan berbayar. B7 menguji journey UI/keyboard, recovery/retensi, backup/restore sintetis, robustness CLI dan kapasitas lokal; enam parser-runtime test tetap skipped dan bukan pass. AC-MVP-45 serta full evaluator/handover AC-MVP-64 diteruskan ke B8. Lihat [handover B7](../handover/B7.md), [handover B6](../handover/B6.md), dan [traceability](../../requirements_traceability.md) untuk hasil dan batas per requirement.

## 1. Otoritas dan cara menggunakan paket

[ALIGN_IMPLEMENTATION_SPEC.md](../../ALIGN_IMPLEMENTATION_SPEC.md) adalah sumber normatif v2. Nama `ALIGN_IMPLEMENTATION_SPEC_V2.md` di dalam master menunjuk isi v2 yang sama; gunakan nama berkas aktual tersebut dan jangan mengganti nama atau mengedit master untuk menjalankan paket ini. SHA-256 master ketika paket disusun: `6a872b132f5721eda817af89093b899970436169a70b93fbcf4778bcf4a7b254`.

Dokumen tahap merupakan pendamping rinci, sehingga agent WAJIB membaca master dan tahap aktif. Hierarki tetap mengikuti Bagian 0.1 master; ringkasan, contoh, maupun matriks di sini tidak mengurangi FR/INV/AC. Konflik dengan invariant dilaporkan dengan ID dan konteks spesifik. Pilihan engineering lokal yang tidak mengubah kontrak diselesaikan agent dan dicatat.

1. Mulai hanya tahap yang diminta pemilik produk; inspeksi repository dan handover predecessor sebelum mengubah kode.
2. Periksa entry gate. Kekurangan pekerjaan predecessor yang diperlukan harus diselesaikan secara terbatas dan dilaporkan, bukan ditutupi mock atau diasumsikan selesai.
3. Implementasikan kontrak domain, state, transaksi, izin dan failure behavior sebelum menghubungkan UI. Gunakan API/OpenAPI yang sama pada web dan worker.
4. Selesaikan perubahan, fixture, pengujian dan dokumentasi tahap aktif. Jalankan regresi predecessor yang terdampak.
5. Catat hasil aktual, status gate dan handover. **BERHENTI setelah tahap aktif; jangan mulai tahap berikutnya otomatis.** Gate gagal berarti tahap belum selesai, bukan izin menghapus requirement.

Seluruh tahap memakai data sintetis dan mock provider berlabel sebagai default. Provider-off tetap menyediakan alur manual. Panggilan live hanya melalui konfigurasi, capability/rate manifests dan guard finansial yang sah. Pembuatan dokumentasi tidak memberi izin deployment publik, email, aktivasi integrasi eksternal atau penggunaan data mahasiswa nyata.

## 2. Urutan dan milestone

| Tahap | Dokumen | Predecessor | Gate utama |
|---|---|---|---|
| B0 | [Fondasi](B0-fondasi.md) | Master | Install, Compose, migrate, seed dan login dapat direproduksi |
| B1 | [Identitas dan konteks](B1-identitas-dan-konteks.md) | B0 | Akses terisolasi; konteks manual/ekstraksi dapat disahkan |
| B2 | [Tugas dan spesifikasi](B2-tugas-dan-spesifikasi.md) | B1 | Paket dan spec v2 sah; tugas terbit tanpa kewajiban tersembunyi |
| B3 | [Submission dan viewer](B3-submission-dan-viewer.md) | B2 | Final mixed-format, inventory, anchors dan resource/recovery parser benar |
| B4 | [Review manual dan release](B4-review-manual-dan-release.md) | B3 | Alur manual sampai hasil resmi mahasiswa berjalan tanpa provider |
| B5 | [AI executor](B5-ai-executor.md) | B4 | Check berbukti, negative gate dan abstention benar pada fixture |
| B6 | [Numeric dan biaya](B6-numeric-dan-biaya.md) | B5 | Automated numeric serta biaya/reconciliation benar |
| B7 | [UX dan robustness](B7-ux-dan-robustness.md) | B6 | Verifikasi aplikasi terintegrasi, aksesibilitas, recovery dan regresi |
| B8 | [Handover dan pilot readiness](B8-handover-dan-pilot-readiness.md) | B7 | Checkout baru dapat direproduksi dan semua bukti MVP tersedia |
| B9 | [Moodle sandbox](B9-moodle-sandbox.md) | B8 + inisiasi terpisah | AC-SBX-01–05; standalone tetap independen |

Milestone: **M1 B0–B2** tugas resmi; **M2 B3–B4** produk manual lengkap; **M3 B5–B6** bantuan AI dan numeric; **M4 B7–B8** handover MVP. M2 bukan pernyataan seluruh MVP selesai. B9 berada setelah MVP dan feature flag tetap off default.

## 3. Dependensi lintas tahap yang wajib

| Kapabilitas | Pertama diperlukan | Penuntasan dan aturan |
|---|---|---|
| Auth, error envelope, config, DB/worker capability | B0 | Login dasar B0; hardening lengkap B1; setiap fitur baru langsung memakai izin/CSRF/revision yang relevan |
| Upload, parse, inventory, provenance dan job untuk konteks | B1 | Jalur PDF/DOCX/JPG/JPEG/PNG konteks yang benar-benar dipakai sudah berfungsi; B3 menambah namespace submission, XLSX, viewer lengkap dan benchmark menyeluruh |
| Rubrik dan draft provider | B2 | Pakai service B1 dan adapter mock; draft tetap bisa diedit bila AI gagal; bukan menunggu B5 untuk seluruh fungsi drafting |
| Manifests, fingerprint, baseline routing/batch 1 | B0–B1 | Contract/bootstrap tersedia awal; parser binding B1/B3, executor B5, keuangan/promosi lengkap B6 |
| Guard panggilan berbayar | Sebelum call live pertama | B0 menolak call bila guard belum tersedia; setiap live consumer wajib lebih dahulu menyediakan reservation, tariff/capability manifest, limit, ledger dan reconciliation sesuai master; B6 bukan pengecualian sementara |
| Decimal untuk klaim manual numerik | B4 | B2 menetapkan NumericPolicy; B4 memakai core pembandingan resmi bila membuat numeric finding terverifikasi; B6 melengkapi ekstraksi, normalisasi dan eksekusi otomatis |
| Perlindungan review/publikasi dari worker | B0/B4 | Role DB worker dibatasi sejak tabel dibuat; retry/rerun tidak pernah menimpa keputusan manusia |
| Pengujian, accessibility, audit, dokumentasi | B0 dan setiap fitur | Tidak ditunda sampai B7/B8; kedua tahap terakhir memverifikasi integrasi dan bukti lengkap |
| Evaluator dan penelitian | B0 struktur; B7/B8 implementasi | CLI/fixture/schema report wajib; data atau hasil empiris yang belum tersedia tetap belum tersedia |

Early consumer hanya boleh memakai kapabilitas yang benar-benar tersedia. Fixture provider tidak menggantikan parser nyata atau anchor/hash asli. Kapasitas parser yang belum tersedia menghasilkan status/limitation sah; jangan mengaktifkan backend ringan atau mengklaim dukungan format penuh diam-diam.

Semua endpoint memakai `/api/v1`, schema strict, field server-owned, object authorization, error envelope dan status Bagian 16.1. Mutasi memakai CSRF/revision dan idempotency sesuai aksi; review memakai revision parent. Tidak menambah endpoint produk di luar master untuk mengatasi ketergantungan tahap. CLI wajib adalah kontrak yang harus dibuat, bukan pernyataan bahwa executable sudah ada.

## 4. Matriks alokasi FR

`Mulai` berarti kontrak/implementasi pertama; `Gate lengkap` adalah tahap paling awal seluruh perilaku ID dapat dibuktikan, bukan klaim sudah lulus. `Regresi` adalah tahap pengujian ulang yang wajib bila relevan/terdampak. B7 mengintegrasikan pengujian aplikasi; B8 mengaudit seluruh FR/INV/AC dan hasilnya. Setiap ID harus ditautkan ke kode dan test aktual dalam `requirements_traceability.md` ketika implementasi berjalan.

| ID | Perilaku ringkas | Mulai | Gate lengkap | Regresi |
|---|---|---|---|---|
| FR-01 | Provisioning, course, enrollment | B0 | B1 | B3, B4, B7 |
| FR-02 | Konteks, konflik, approval | B1 | B1 | B2, B7 |
| FR-03 | Instruksi, criteria, requirement, spec tanpa skor | B2 | B2 | B4, B7 |
| FR-04 | Usulan AI disahkan dosen | B1 | B5 | B7 |
| FR-05 | Bundle final immutable individu/kelompok | B3 | B3 | B4, B7 |
| FR-06 | Multiformat, hash, locator, keterbacaan | B1 | B3 | B5, B7 |
| FR-07 | Semua check dan tiga kategori finding | B5 | B6 | B7 |
| FR-08 | Retrieval bundle-only dan snapshot normatif langsung | B2 | B5 | B6, B7 |
| FR-09 | Absence memerlukan inspeksi memadai | B1 | B5 | B6, B7 |
| FR-10 | Numeric Decimal dan comparability | B2 | B6 | B7 |
| FR-11 | Semua elemen, bukti manual, keputusan kandidat | B4 | B4 | B5, B7 |
| FR-12 | Manual saat AI/dana gagal | B4 | B6 | B7 |
| FR-13 | Feedback dari keputusan; approve/publish terpisah | B4 | B5 | B6, B7 |
| FR-14 | Mahasiswa hanya released snapshot berizin | B3 | B4 | B5, B7 |
| FR-15 | Versi/audit dan review aman dari retry | B1 | B6 | B7 |
| FR-16 | Job, retry, reservation, ledger | B0 | B6 | B7 |
| FR-17 | UI Indonesia, responsif, keyboard, anchor | B0 | B7 | B8 |
| FR-18 | Export resmi JSON/Markdown | B4 | B4 | B7 |
| FR-19 | Suite akses, dokumen, gate, angka, failure | B0 | B7 | B8 |
| FR-20 | Decision question, derivasi, grounding, lint | B2 | B2 | B5, B7 |
| FR-21 | Profil parser, checkpoint, admission | B1 | B3 | B5, B7 |
| FR-22 | Routing/batching policy dan keputusan tercatat | B0 | B6 | B7 |
| FR-23 | Linked selection dan revisi anotasi | B4 | B4 | B5, B7 |
| FR-24 | Evaluator, benchmark dan traceability DSRM offline | B0 | B8 | Audit handover |

## 5. Matriks alokasi invariant

Invariant berlaku sejak jalur terkait diperkenalkan, termasuk sebelum tahap gate lengkap. Agent tidak boleh menganggap invariant belum berlaku hanya karena integrasi akhir terjadi kemudian.

| ID | Perlindungan | Mulai | Gate lengkap | Regresi |
|---|---|---|---|---|
| INV-01 | Hanya tuntutan snapshot resmi | B2 | B5 | B6, B7 |
| INV-02 | Owner/version/hash/locator setiap bukti | B1 | B3 | B4, B5, B7 |
| INV-03 | Retrieval/parsing gagal bukan bukti tidak memenuhi | B1 | B5 | B7 |
| INV-04 | Angka belum sepadan/verified tidak diflag | B2 | B6 | B7 |
| INV-05 | Worker tidak menulis keputusan/approval/publikasi | B0 | B4 | B5, B6, B7 |
| INV-06 | AI retry tidak menimpa review manusia | B0 | B5 | B6, B7 |
| INV-07 | Rejected tidak menjadi kritik resmi | B4 | B5 | B7 |
| INV-08 | Snapshot publikasi mengikat sumber/config/teks/actor | B4 | B6 | B7, B8 |
| INV-09 | Tanpa nilai/dashboard analitik | B0 | B7 | Setiap tahap, B8, B9 |
| INV-10 | Tanpa materi/karya lain pada retrieval bundle | B1 | B5 | B7, B9 |
| INV-11 | Limit AI tidak membatasi review manual sah | B1 | B4 | B5, B6, B7 |
| INV-12 | Hasil kelompok bukan diagnosis anggota | B1 | B4 | B7, B9 |
| INV-13 | Anchor benar belum berarti dukungan semantik | B2 | B5 | B7 |
| INV-14 | Unit gagal tetap dalam inventory/coverage denominator | B1 | B5 | B7 |
| INV-15 | Compiler tidak menambah kewajiban tersembunyi | B2 | B5 | B7 |
| INV-16 | Perubahan metode mengubah manifest/fingerprint | B0 | B6 | B7, B8 |

## 6. Matriks alokasi acceptance MVP

Skenario dan hasil normatif lengkap ada pada [Bagian 23](../../ALIGN_IMPLEMENTATION_SPEC.md#23-acceptance-criteria-dan-pengujian-wajib). Satu ID dapat diperkenalkan parsial lebih awal. Kriteria yang mencakup modul belum tersedia tidak boleh dicatat fully passed pada tahap awal.

| ID | Skenario ringkas | Mulai | Gate lengkap | Regresi |
|---|---|---|---|---|
| AC-MVP-01 | Fresh install dan alur manual | B0 | B4 | B7, B8 |
| AC-MVP-02 | Isolasi semua objek termasuk vector/render | B1 | B5 | B7 |
| AC-MVP-03 | Admin bukan dosen tidak membaca akademik | B1 | B4 | B5, B7 |
| AC-MVP-04 | Revocation segera | B1 | B4 | B5, B7 |
| AC-MVP-05 | Konflik KK/RPP menghalangi approval | B1 | B1 | B7 |
| AC-MVP-06 | Manual tanpa CPMK | B1 | B1 | B7 |
| AC-MVP-07 | Instruksi asli utuh | B2 | B2 | B5, B7 |
| AC-MVP-08 | Tidak ada struktur penilaian akademik | B2 | B2 | B7, B8 |
| AC-MVP-09 | Registry/DSL/DAG invalid ditolak | B2 | B2 | B5, B7 |
| AC-MVP-10 | Format submission diterima | B1 | B3 | B7 |
| AC-MVP-11 | Input berbahaya/oversize/encrypted ditolak | B1 | B3 | B7 |
| AC-MVP-12 | Concurrent/idempotent finalize | B3 | B3 | B7 |
| AC-MVP-13 | Broker mati saat finalize, lalu analisis pulih | B3 | B5 | B7 |
| AC-MVP-14 | Roster historis | B1 | B3 | B4, B7 |
| AC-MVP-15 | Late policy dari snapshot | B2 | B3 | B7 |
| AC-MVP-16 | Anchor seluruh format/hidden/merged/orientation | B1 | B3 | B4, B7 |
| AC-MVP-17 | Top-k kosong bukan absence | B5 | B5 | B7 |
| AC-MVP-18 | Diagram unreadable tidak menggagalkan elemen lain | B3 | B5 | B7 |
| AC-MVP-19 | Dukungan positif dalam batas check | B5 | B5 | B7 |
| AC-MVP-20 | Applicability fact belum verified | B2 | B5 | B7 |
| AC-MVP-21 | Dedup obligation yang sama | B4 | B5 | B7 |
| AC-MVP-22 | Masalah berbeda tidak digabung | B4 | B5 | B7 |
| AC-MVP-23 | Evidence/quote/schema invalid | B4 | B5 | B7 |
| AC-MVP-24 | Instruksi adversarial tidak mengubah backend | B1 | B5 | B7 |
| AC-MVP-25 | Normalisasi angka/locale | B4 | B6 | B7 |
| AC-MVP-26 | Quantity tidak sepadan | B4 | B6 | B7 |
| AC-MVP-27 | Batas toleransi dan nol | B4 | B6 | B7 |
| AC-MVP-28 | Cache formula/digit OCR belum verified | B3 | B6 | B7 |
| AC-MVP-29 | Manual saat provider off/dana habis | B4 | B6 | B7 |
| AC-MVP-30 | Review revision conflict | B4 | B4 | B7 |
| AC-MVP-31 | Reject/defer tidak menjadi kritik | B4 | B5 | B7 |
| AC-MVP-32 | Review complete dengan unable_to_conclude | B4 | B4 | B5, B7 |
| AC-MVP-33 | Keputusan berubah membuat approval stale | B4 | B4 | B7 |
| AC-MVP-34 | Endpoint internal tidak bocor ke mahasiswa | B1 | B5 | B7 |
| AC-MVP-35 | Publish idempotent | B4 | B4 | B7 |
| AC-MVP-36 | Feedback lama berlabel versi lama | B3 | B4 | B5, B7 |
| AC-MVP-37 | Config/rerun baru tidak menimpa histori | B1 | B6 | B7 |
| AC-MVP-38 | Reservation tidak memakai cadangan/planned topup | B0 | B6 | B7 |
| AC-MVP-39 | Billing tidak diketahui tetap held | B0 | B6 | B7 |
| AC-MVP-40 | Crash/redelivery/outbox aman | B0 | B5 | B6, B7 |
| AC-MVP-41 | Export/withdrawal aman | B4 | B4 | B7 |
| AC-MVP-42 | 360/768/1440 dan aksesibilitas | B0 | B7 | B8 |
| AC-MVP-43 | Restore/tombstone konsisten | B0 | B7 | B8 |
| AC-MVP-44 | Audit fitur di luar scope | B0 | B7 | B8, B9 |
| AC-MVP-45 | Metrik contoh tiga unit | B7 | B8 | Audit handover |
| AC-MVP-46 | Parser hard limits dan manifest | B1 | B3 | B7 |
| AC-MVP-47 | OOM/timeout tetap inventory, negative tertahan | B1 | B5 | B7 |
| AC-MVP-48 | Checkpoint reuse dan assembly parsial | B1 | B3 | B5, B7 |
| AC-MVP-49 | Admission/fencing child masih hidup | B0 | B3 | B7 |
| AC-MVP-50 | Quote benar tetapi tidak relevan | B2 | B5 | B7 |
| AC-MVP-51 | Satu evidence dapat mengisi banyak role | B2 | B5 | B7 |
| AC-MVP-52 | Human-only/numeric operand hilang | B2 | B6 | B7 |
| AC-MVP-53 | Compiler fidelity kondisi/alternatif/polarity | B2 | B2 | B5, B7 |
| AC-MVP-54 | Lint stale dan blocking non-override | B2 | B2 | B7 |
| AC-MVP-55 | Promosi batch tanpa laporan sah ditolak | B0 | B6 | B7, B8 |
| AC-MVP-56 | Respons batch partial/ID asing | B5 | B5 | B6, B7 |
| AC-MVP-57 | Router memperbaiki sumber/abstain | B5 | B5 | B7 |
| AC-MVP-58 | Call/time/cost cap dan escalation | B0 | B6 | B7 |
| AC-MVP-59 | Manifest berubah menghasilkan fingerprint baru | B0 | B6 | B7 |
| AC-MVP-60 | Linked selection click/keyboard/touch/mobile | B3 | B7 | B8 |
| AC-MVP-61 | Annotation append-only dan conflict | B4 | B4 | B7 |
| AC-MVP-62 | Annotation membuat stale, histori tetap | B4 | B4 | B7 |
| AC-MVP-63 | Robustness partition/equivalence/compiler/executor | B2 | B7 | B8 |
| AC-MVP-64 | CLI/report tanpa key/dana, tanpa klaim empiris | B3 | B8 | Audit handover |
| AC-MVP-65 | History schema 1 dan kreasi/run schema 2 | B2 | B5 | B7, B8 |

## 7. Sandbox dan penelitian

| ID | Pemilik | Verifikasi |
|---|---|---|
| AC-SBX-01 | B9 | LTI launch dan mapping sah |
| AC-SBX-02 | B9 | Signature/issuer/audience/nonce/replay/expiry/deployment invalid ditolak |
| AC-SBX-03 | B9 | Import identik idempotent; sumber baru menjadi versi baru |
| AC-SBX-04 | B9 | Kelompok, revocation dan isolasi course |
| AC-SBX-05 | B9 | Mahasiswa hanya published feedback; tanpa grade writes |

| ID | Dukungan software | Gate empiris terpisah |
|---|---|---|
| AC-RES-01 | B8 dataset/codebook/partition/freeze tooling | Dataset aktual dan manifest penelitian terverifikasi |
| AC-RES-02 | B8 metrik, support, denominator dan interval | Report aktual dari data sah |
| AC-RES-03 | B5 isolasi; B7/B8 partition/leakage checks | Frozen test tidak pernah dipakai tuning/inferensi |
| AC-RES-04 | B6 ledger; B8 export biaya offline | Pengeluaran studi aktual seluruh arm tercatat |
| AC-RES-05 | B8 template DSRM dan iteration records | Rantai bukti aktual problem sampai communication |
| AC-RES-06 | B7/B8 evaluator robustness terpisah | Hasil empiris compiler/executor dan repeat dependence |
| AC-RES-07 | B6 activation guards; B8 policy evaluator | Laporan eligible yang tepat untuk klaim optimasi |

Seluruh AC-RES berstatus **belum dibuktikan secara empiris** ketika dokumen dibuat. B8 wajib menyerahkan tooling dan template dengan data aktual yang tersedia saja. Nama baseline penelitian B0/B1/B2/B3 pada master Bagian 21.3 adalah label eksperimen; bedakan dari tahap pembangunan dengan menulis `baseline penelitian B0` dan `tahap B0` secara eksplisit.

## 8. Bukti, command dan handover

`requirements_traceability.md` adalah artefak implementasi mendatang, memuat ID → komponen/kontrak → test/fixture → command/report → status dan limitation. Simpan handover tahap pada `docs/handover/Bn.md` ketika tahap itu dikerjakan. File tersebut belum dibuat oleh paket spesifikasi ini.

Setiap handover wajib mencantumkan commit/revisi bila repository sudah memakai VCS, scope selesai, migration dan lockfiles, kontrak berubah, command persis, environment/hardware, hasil dan exit code, fixture/manifest hashes, mode provider, biaya aktual bila live, limitation, serta dependensi yang diteruskan. Status `belum diuji`, `gagal`, dan `lulus` tidak boleh saling menggantikan; gate belum selesai jika bukti software wajib belum ada. Ketidaktersediaan penelitian/live inference tidak disamakan dengan gagal manual/mock baseline.

B0 menetapkan command install/run/build/lint/typecheck/test berdasarkan toolchain yang benar-benar dipasang, lalu mencatat command terverifikasi untuk dipakai tahap berikutnya. Contoh sintaks di dokumen adalah kontrak rencana sampai berhasil dijalankan. Jangan membuat README berisi command yang belum pernah diuji seolah telah terverifikasi. CLI prescribed master dipertahankan tepat di dokumen pemiliknya.

Paket dokumentasi ini tidak berisi hasil test aplikasi. Kesiapan akhir dinilai menurut [Definition of Done](../../ALIGN_IMPLEMENTATION_SPEC.md#24-definition-of-done-dan-instruksi-handover), bukan jumlah checklist yang dicentang.

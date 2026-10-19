# ALIGN Product and Technical Implementation Specification — v2

Versi 2.0 · 19 Oktober 2026 (Asia/Jakarta) · Bahasa produk dan dokumen Indonesia

Status: **acuan normatif build yang telah disetujui pemilik produk**. Menggantikan versi 1.0 dan mengintegrasikan delapan refinement paper, keputusan parser D1-A, serta batching D2-B. Ini spesifikasi yang harus diimplementasikan, bukan pernyataan bahwa aplikasi atau hasil penelitian sudah tersedia.

Dokumen ini adalah **Single Source of Truth (SoT) untuk implementasi ALIGN MVP**: aplikasi mandiri yang menemukan kemungkinan masalah pada tugas mahasiswa, menyediakan bukti yang dapat ditelusuri, dan membantu dosen menerbitkan feedback. ALIGN tidak menghasilkan, memasukkan, menyimpan secara terstruktur, atau mengagregasi nilai akademik. ALIGN tidak memiliki dashboard analitik.

Tipe dokumen ini menggabungkan kebutuhan produk (PRD/SRS), desain teknis, kontrak implementasi, acceptance criteria, dan rencana pembangunan. SoT adalah kedudukannya sebagai acuan tunggal; BRS saja terlalu umum dan PRD saja belum cukup untuk agent yang harus membangun sistem.

## 0. Instruksi bagi agent pembangun

1. Bangun aplikasi dari `ALIGN_IMPLEMENTATION_SPEC_V2.md` ini. Versi 1.0, memo review paper, paper dan rancangan lama merupakan latar belakang; seluruh kontrak build yang mengikat telah dikonsolidasikan di sini. Ketika berbeda, v2 yang berlaku.
2. Jangan menambahkan skor, gradebook, dashboard analitik, tutor/chatbot, materi pembelajaran, pemeriksaan plagiarisme, deteksi kepengarangan AI, atau pencarian internet pada pipeline tugas.
3. Penuhi seluruh requirement `FR`, invariant `INV`, dan acceptance criterion `AC` yang ditandai wajib MVP. Kerjakan tahap pembangunan sesuai Bagian 22; jangan berhenti pada mockup UI.
4. Gunakan mock provider untuk pengembangan dan pengujian deterministik. Jalur provider nyata harus tersedia melalui adapter dan konfigurasi; jangan mengarang hasil AI lalu menampilkannya sebagai hasil pemrosesan nyata.
5. Jangan mengubah keputusan produk karena masalah implementasi. Selesaikan pilihan lokal seperti nama fungsi, layout komponen, dan patch dependency secara mandiri. Konflik terhadap invariant harus dilaporkan sebagai blocker yang spesifik.
6. Implementasikan kontrak input/output, state transition, izin, dan failure behavior sebelum menghubungkan UI. OpenAPI dan migration adalah kontrak yang diuji.
7. Tidak ada keputusan produk terbuka dalam dokumen ini. Nilai rahasia, domain deployment, model yang tersedia pada akun, harga provider, dan kebijakan retensi institusi adalah konfigurasi operasi, bukan alasan menunda pembangunan dengan data sintetis.
8. Pada akhir pembangunan, berikan source code, lockfile, migration, seed sintetis, `.env.example`, Docker Compose, README, hasil pengujian, serta daftar keterbatasan nyata. Jangan menyatakan sistem telah lolos penelitian empiris atau siap memakai data mahasiswa nyata hanya karena tes software lulus.
9. Publikasi feedback oleh dosen adalah aksi di aplikasi yang memang dibangun. Deployment publik, pengiriman email, aktivasi integrasi eksternal, dan penggunaan data nyata bukan aksi yang harus dilakukan agent secara otomatis.

### 0.1 Bahasa normatif dan hierarki keputusan

`WAJIB` dan `DILARANG` adalah syarat penerimaan. `Default` adalah nilai implementasi awal yang harus disediakan, dapat diganti melalui konfigurasi yang disebutkan. Angka limit engineering bukan klaim performa hasil penelitian.

Hierarki: instruksi terbaru pemilik produk → keputusan final pada Bagian 1 → requirement/invariant dokumen ini → contoh payload. Contoh tidak boleh mempersempit aturan normatif. Jika payload contoh salah terhadap schema, perbaiki contohnya dan simpan schema normatif.

Dokumen ini merupakan hasil konsolidasi teknis, bukan catatan bahwa software sudah diimplementasikan. Parameter retrieval, model, dan gate dapat dikalibrasi pada development/validation sebelum eksperimen dibekukan; perubahan selalu berversi.

### 0.2 Status sumber

| Sumber | Pemakaian dalam dokumen ini |
|---|---|
| ALIGN_Paper.docx | Dasar mekanisme bukti, adaptive routing, tata kelola, dan protokol penelitian |
| ALIGN_Rancangan.md versi 2.0 | Dasar modul, struktur bukti, versi, retrieval, numeric engine, job dan API |
| ALIGN_Bagian 1.docx | Dasar persiapan konteks, pemecahan kriteria/elemen, dan spesifikasi pemeriksaan |
| Keputusan pemilik produk 18 Oktober 2026 | Mengesampingkan seluruh bagian skor, dashboard analitik, materi, serta ketidakselarasan yang dibahas |
| Persetujuan refinement 19 Oktober 2026 (Asia/Jakarta) | Menetapkan R-01–R-08, D1-A parser terpisah dengan headroom, dan D2-B batching tervalidasi |
| Tujuh paper tambahan dan memo review | Dasar keputusan metode; pemetaan adopsi/batas bukti ada pada Bagian 26; bukan schema/dependency yang harus ditiru utuh |

Agent tidak membutuhkan lampiran, memo review, atau v1 untuk membangun MVP; seluruh aturan yang mengikat telah dituliskan di sini. Referensi resmi teknis ada pada Bagian 25 dan sumber metode pada Bagian 26. Nilai headroom/ambang operasional baru dalam v2 adalah keputusan engineering ALIGN, bukan hasil atau rekomendasi numerik paper.

- [0. Instruksi bagi agent pembangun](#0-instruksi-bagi-agent-pembangun)
- [1. Register keputusan final](#1-register-keputusan-final)
- [2. Produk dan batas fungsional](#2-produk-dan-batas-fungsional)
- [3. Istilah dan konvensi kontrak](#3-istilah-dan-konvensi-kontrak)
- [4. Identitas dan akses](#4-identitas-dan-akses)
- [5. Persiapan konteks dan tugas](#5-persiapan-konteks-dan-tugas)
- [6. Pengumpulan final dan perubahan sumber](#6-pengumpulan-final-dan-perubahan-sumber)
- [7. Kontrak Check Specification](#7-kontrak-check-specification)
- [8. Parsing, evidence registry, dan viewer](#8-parsing-evidence-registry-dan-viewer)
- [9. Representasi hasil check dan taxonomy findings](#9-representasi-hasil-check-dan-taxonomy-findings)
- [10. Executor dan evidence sufficiency gate](#10-executor-dan-evidence-sufficiency-gate)
- [11. Retrieval dan context management](#11-retrieval-dan-context-management)
- [12. Numeric engine](#12-numeric-engine)
- [13. Adapter model dan verifikasi](#13-adapter-model-dan-verifikasi)
- [14. Review dosen, feedback, dan publikasi](#14-review-dosen-feedback-dan-publikasi)
- [15. Model data dan constraint penyimpanan](#15-model-data-dan-constraint-penyimpanan)
- [16. API yang harus diimplementasikan](#16-api-yang-harus-diimplementasikan)
- [17. Job, recovery, idempotensi dan biaya](#17-job-recovery-idempotensi-dan-biaya)
- [18. Arsitektur aplikasi, dependency dan deployment](#18-arsitektur-aplikasi-dependency-dan-deployment)
- [19. UI dan alur layar](#19-ui-dan-alur-layar)
- [20. Privasi, keamanan dan pemeliharaan](#20-privasi-keamanan-dan-pemeliharaan)
- [21. Protokol evaluasi terpisah dari produk](#21-protokol-evaluasi-terpisah-dari-produk)
- [22. Urutan pembangunan dan gate penyelesaian](#22-urutan-pembangunan-dan-gate-penyelesaian)
- [23. Acceptance criteria dan pengujian wajib](#23-acceptance-criteria-dan-pengujian-wajib)
- [24. Definition of Done dan instruksi handover](#24-definition-of-done-dan-instruksi-handover)
- [25. Referensi teknis resmi](#25-referensi-teknis-resmi)
- [26. Dasar metode dan perubahan v2](#26-dasar-metode-dan-perubahan-v2)

## 1. Register keputusan final

| ID | Keputusan yang berlaku | Implikasi wajib |
|---|---|---|
| DEC-01 | Toleransi numerik memakai maksimum toleransi absolut dan relatif | Gunakan rumus Bagian 12; jangan menjumlahkan kedua toleransi |
| DEC-02 | MVP mandiri; Moodle merupakan tahap sandbox berikutnya | Tidak ada ketergantungan login atau submission pada Moodle untuk menyelesaikan MVP |
| DEC-03 | PDF, DOCX, XLSX, JPG/JPEG, PNG diterima untuk submission | Routing image/OCR, viewer, provenance, dan batas keterbacaan adalah bagian MVP |
| DEC-04 | Model konfigurabel dengan baseline eksperimen eksplisit | Peran efficient, complex, vision dan embedding ada pada manifest berversi; tidak mengunci harga model lama |
| DEC-05 | Instruksi asli disimpan utuh | Ringkasan AI terpisah dan tidak menjadi pengganti instruksi resmi |
| DEC-06 | Hanya dana terkonfirmasi mengizinkan panggilan berbayar | Planned top-up bukan saldo tersedia; total kredit awal US$25, batas operasional awal US$20, cadangan US$5 |
| DEC-07 | Survei kebutuhan awal sudah dilakukan | Catat 21 responden: 1 dosen dan 20 asisten praktikum; hasil bukan bukti efektivitas sistem |
| DEC-08 | Kontrak konteks bertipe; database menjadi sumber status/versi | JSON adalah representasi pertukaran; Markdown hanya ekspor/tampilan turunan |
| DEC-09 | Produk hanya findings berbukti dan feedback dosen | Hapus semua input skor, maksimum skor, bobot penilaian, gradebook, rumus capaian berbasis skor, dan grade passback |
| DEC-10 | Operator, dependensi, dan guardrail diimplementasikan kode | LLM tidak menafsirkan teks kebijakan menjadi operasi bebas atau melewati gate |
| DEC-11 | Satu masalah memiliki satu kategori utama | Tautkan beberapa requirement/kriteria jika perlu; deduplikasi menurut obligation key dan signature |
| DEC-12 | Retrieval hanya pada bundle mahasiswa yang diperiksa | Tidak ada korpus materi dosen, submission lain, internet, atau komentar/nilai target dalam retrieval |
| DEC-13 | Regression suite terpisah dari final frozen test | Jangan memakai hasil test final untuk tuning konfigurasi/model |
| DEC-14 | Label dan denominator evaluasi eksplisit | Pisahkan issue label, applicability, keterbacaan, cakupan inspeksi, dan keputusan check |
| DEC-15 | Dashboard analitik dihapus | Tidak ada dashboard findings kelas, capaian CPMK, tren, ranking, atau grafik kemampuan |
| DEC-16 | D1-A: resource profile parser dipisahkan; headroom awal untuk Docling native | Jalur native DOCX/XLSX 2 GiB dan Docling native 8 GiB; task per halaman/tahap, admission control, benchmark aktual sebelum profil live; bukan jaminan semua file selesai |
| DEC-17 | D2-B: batching semantik tervalidasi, maksimum 5 | Default baseline batch 1; kandidat batch 3/5 hanya aktif setelah gate Bagian 21.4; tidak menaikkan limit biaya/call/waktu |
| DEC-18 | R-01–R-08 diintegrasikan dalam v2 | Typed grounding, compiler lint, policy routing/batching, linked review annotations, robustness dan DSRM traceability; tanpa skor/dashboard/materi/data lintas mahasiswa |

### 1.1 Penetapan engineering dalam spesifikasi ini

Arsitektur, auth, state, batas file, schema, default retrieval, budget per job, UI dan tahap pembangunan di bawah merupakan penetapan engineering untuk menutup detail yang sebelumnya belum ditentukan. Penetapan ini tidak mengubah keputusan produk. Nilai dapat dikonfigurasi hanya pada tempat yang secara eksplisit membolehkannya, dengan audit dan versi yang sesuai.

## 2. Produk dan batas fungsional

Tujuan pengguna: dosen dapat mengetahui apa yang diperiksa, apa yang ditemukan, sumbernya, apa yang belum dapat diperiksa, serta menyetujui feedback yang layak dikirim ke pemilik submission. Mahasiswa dapat mengumpulkan bundle final dan menerima feedback yang sudah dipublikasikan.

| ID | Requirement wajib MVP |
|---|---|
| FR-01 | Admin memprovisikan akun, course, dan enrollment; akses akademik ditentukan penugasan course |
| FR-02 | Dosen membuat draf konteks dari KK/RPP atau input manual, menyelesaikan konflik, dan mengesahkan versi |
| FR-03 | Dosen membuat instruksi, criteria/descriptor, requirement, mapping CPMK opsional, dan spesifikasi check tanpa komponen skor |
| FR-04 | AI dapat mengusulkan field/kriteria/spesifikasi; seluruh tuntutan resmi disahkan dosen sebelum tugas terbit |
| FR-05 | Mahasiswa individu/kelompok mengunggah bundle dan melakukan finalize eksplisit; finalize membuat versi immutable |
| FR-06 | Sistem mengekstrak sumber dan bukti multiformat dengan hash, locator, parser version, dan batas keterbacaan |
| FR-07 | Sistem menjalankan check per elemen, mencatat hasil untuk semua elemen, dan mengajukan tiga jenis finding yang dibatasi |
| FR-08 | Retrieval hybrid hanya mengakses bundle yang terotorisasi; konteks normatif dimuat langsung dari snapshot resmi |
| FR-09 | Klaim absence/gap berbasis ketiadaan memerlukan inspeksi memadai; bukti tidak terbaca memicu keterbatasan, bukan tuduhan |
| FR-10 | Numeric engine membandingkan besaran sepadan menggunakan Decimal dan policy resmi |
| FR-11 | Dosen menelaah seluruh elemen, menambahkan bukti dari berkas yang sah, dan accept/edit/reject kandidat |
| FR-12 | Dosen dapat menyelesaikan review dan menulis feedback manual meskipun AI gagal atau dana habis |
| FR-13 | Draf feedback bersumber dari keputusan dosen; teks akhir disetujui dan dipublikasikan melalui dua aksi terpisah |
| FR-14 | Mahasiswa hanya menerima released snapshot yang boleh diaksesnya; draft internal tidak bocor |
| FR-15 | Perubahan sumber, konfigurasi, review, dan publikasi memiliki versi serta audit; retry tidak mengubah keputusan dosen |
| FR-16 | Worker memakai job bertahap, idempotensi, retry berbatas, reservasi biaya dan ledger usage |
| FR-17 | UI responsif, berbahasa Indonesia, dapat digunakan dengan keyboard, dan menyediakan viewer anchor |
| FR-18 | Ekspor hasil resmi tersedia sebagai JSON dan Markdown tanpa skor; tidak ada dashboard atau laporan agregat kelas |
| FR-19 | Tes mencakup isolasi akses, dokumen sulit, gate, angka, publikasi, biaya, kegagalan dan pemulihan |
| FR-20 | CheckSpecification memiliki decision question, derivasi sumber, grounding contract dan lint yang harus selesai sebelum approval |
| FR-21 | Parser menggunakan profil berversi, checkpoint unit/stage, batas sumber daya dan admission control yang tidak menghilangkan sumber gagal |
| FR-22 | Routing dan batching dipilih dari policy release berversi; alasan, gate, biaya dan hasil tercatat per keputusan |
| FR-23 | Workspace menautkan seleksi tuntutan/bukti/observasi/keputusan; koreksi anotasi manusia memiliki revisi dan tidak menimpa sumber |
| FR-24 | Handover menyediakan evaluator compiler/executor robustness, policy validation, parser benchmark dan template traceability DSRM offline |

### 2.1 Yang berada di luar scope

- Pemberian skor/nilai, saran pengurangan nilai, bobot akademik, tingkat penguasaan CPMK, ranking, prediksi kelulusan, gradebook, grade passback, dashboard analitik.
- Materi pembelajaran, rekomendasi sumber eksternal, pencarian internet, RAG lintas mahasiswa, retrieval komentar penilai atau gold label.
- Feedback akademik otomatis sebelum finalize; tutor percakapan; revisi formatif berulang sebagai fitur belajar.
- Eksekusi kode mahasiswa, notebook, macro, external link workbook, atau formula spreadsheet arbitrer.
- Deteksi plagiarisme, penggunaan AI, kebenaran seluruh teori, penilaian kreativitas, dan penilaian kualitas holistik argumen.
- Integrasi LMS produksi, SSO institusi, email/notifikasi keluar, aplikasi mobile native, dan offline penuh.

Unggahan asli boleh mengandung angka nilai, bobot, atau istilah akademik sebagai isi dokumen. Larangan skor berlaku pada fitur dan data penilaian ALIGN; jangan menolak dokumen hanya karena kata "skor" muncul. Metrik eksperimen seperti precision/F1 dan skor relevansi retrieval adalah metrik teknis, bukan nilai mahasiswa.

### 2.2 Invariant produk

| ID | Invariant |
|---|---|
| INV-01 | Hanya tuntutan dari assignment snapshot yang disahkan boleh menjadi dasar finding |
| INV-02 | Setiap bukti terikat pemilik, submission version, source hash, dan locator |
| INV-03 | Retrieval kosong, similarity rendah, atau parsing gagal tidak membuktikan ketidakpemenuhan |
| INV-04 | Angka tidak sepadan atau belum terverifikasi tidak menghasilkan finding numerik |
| INV-05 | Model/worker tidak dapat menulis keputusan dosen, persetujuan feedback, atau publikasi |
| INV-06 | Kegagalan atau retry AI tidak menghapus/menimpa review manusia |
| INV-07 | Finding yang ditolak tidak dipakai sebagai kritik pada feedback resmi |
| INV-08 | Setiap publikasi mengikat sumber, konfigurasi, keputusan, teks, dan approver yang tepat |
| INV-09 | Tidak ada fitur nilai akademik dan tidak ada dashboard analitik |
| INV-10 | Tidak ada materi pembelajaran atau karya mahasiswa lain di retrieval suatu bundle |
| INV-11 | Keterbatasan AI tidak membatasi kewenangan dosen membaca sumber sah dan memberi feedback manual |
| INV-12 | Hasil kelompok tetap merupakan hasil pekerjaan kelompok; tidak mendiagnosis kemampuan setiap anggota |
| INV-13 | Locator/kutipan sah belum membuktikan dukungan semantik; grounding contract dan gate klaim tetap wajib |
| INV-14 | Timeout/OOM/checkpoint tidak menghapus unit gagal dari inventaris atau denominator coverage |
| INV-15 | Kompilasi/augmentasi checklist tidak boleh menambah kewajiban yang tidak dipublikasikan dalam paket tugas resmi |
| INV-16 | Perubahan parser resource/routing/batching/grounding/prompt mengubah manifest/fingerprint yang relevan; policy tidak diganti diam-diam dalam run |

## 3. Istilah dan konvensi kontrak

| Istilah | Definisi implementasi |
|---|---|
| Course Context | Identitas mata kuliah, CPMK bila tersedia, aturan dan konteks tugas yang disahkan; bukan repositori materi |
| Requirement | Kewajiban resmi yang atomik dan dapat dirujuk ke instruksi atau descriptor resmi |
| Criterion | Kelompok aspek pemeriksaan; tidak memiliki bobot atau skor maksimum |
| Descriptor | Uraian bukti/hal yang diharapkan pada criterion; bukan tingkatan nilai |
| CheckElement | Unit inspeksi yang mengoperasionalkan satu obligation atau pemeriksaan numerik |
| Obligation key | ID makna tuntutan yang disahkan untuk menghindari duplikasi lintas requirement/criterion |
| Check Specification | Rencana bertipe, berversi, disahkan; memakai operator registry dan policy aplikasi |
| Bundle | Satu kumpulan file final milik satu individu atau satu kelompok pada assignment |
| Source unit | Halaman/region PDF atau gambar; paragraf/tabel/media DOCX; region sel XLSX |
| Evidence item | Representasi bertipe dari sumber, dengan locator dan provenance |
| Coverage record | Catatan inspeksi per check, bukan ukuran penguasaan mahasiswa |
| Finding candidate | Kemungkinan masalah yang lolos validasi teknis untuk ditelaah dosen |
| Confirmed finding | Masalah yang dikonfirmasi atau disunting dosen dalam review version |
| Publication | Snapshot feedback dan hasil review yang telah disetujui serta dirilis |
| Grounding contract | Bentuk dukungan sumber yang wajib dipenuhi untuk keputusan check; berbeda dari jenis file/evidence |
| Compilation lint | Pemeriksaan schema, derivasi, atomicity, polarity, duplikasi dan batas check terhadap paket normatif |
| ParserResourceProfile | Payload manifest immutable tentang backend, model artifacts, resource limits dan batas task parser |
| RoutingPolicyVersion | Payload manifest immutable berisi aturan pemilihan operasi/role model dan alasan keputusan |
| BatchingPolicyVersion | Payload manifest immutable yang membatasi kelompok/ukuran batch dan mengikat bukti validasinya |
| Review annotation revision | Revisi append-only koreksi span/transkripsi/tautan pada review, dengan sumber asli dan sejarah tetap utuh |

Konvensi wajib:

- ID objek memakai UUID v4 yang dibuat aplikasi; ID dalam contoh bersifat ilustratif, format OpenAPI adalah UUID kecuali business code.
- Waktu disimpan UTC dengan timezone; UI default `Asia/Jakarta`. Deadline dikonversi pada server. Waktu pada client tidak menentukan terlambat/tidak.
- Nilai desimal dan biaya dikirim sebagai string desimal; tidak memakai floating point untuk hitungan resmi.
- Bobot BM25, ranking, serta similarity boleh berupa float karena bukan hitungan finansial/akademik.
- Semua resource berversi memakai integer `revision` untuk optimistic concurrency. Versi konten immutable memiliki UUID sendiri serta nomor versi berurutan dalam parent.
- JSON memakai snake_case; enum internal berbahasa Inggris; label UI berbahasa Indonesia.
- List menggunakan cursor pagination, default 25 dan maksimum 100; urutan stabil `(created_at, id)` kecuali disebutkan lain.
- `null` dan array kosong memiliki arti berbeda: null = tidak berlaku/tidak tersedia sesuai schema; array kosong = tersedia tetapi tidak memiliki anggota.
- Satu deployment MVP melayani satu institusi, dapat memiliki banyak course. Tidak membangun multi-tenant billing.

## 4. Identitas dan akses

### 4.1 Autentikasi MVP

Gunakan auth milik backend FastAPI dengan akun terprovisi; tidak ada pendaftaran publik. Password di-hash Argon2id memakai library terpelihara; jangan membuat algoritma sendiri. Parameter Argon2 mengikuti rekomendasi library yang dipilih dan dicatat dalam konfigurasi.

Session menggunakan token acak kriptografis 32 byte pada cookie `align_session`; simpan SHA-256 token di PostgreSQL, bukan token aslinya. Cookie `HttpOnly`, `SameSite=Lax`, `Secure` di HTTPS, path `/`. Idle timeout 8 jam dan absolute lifetime 24 jam; keduanya diperiksa server, logout/revoke segera membatalkan session. HTTP lokal hanya boleh pada development.

Semua mutasi dari browser memerlukan token CSRF acak yang terikat session pada header `X-CSRF-Token` dan validasi Origin same-origin. Login memakai validasi Origin serta rate limit; jangan menganggap SameSite saja cukup. Limit login default 5 kegagalan per akun dan 20 per IP dalam 15 menit; respons generik tidak membocorkan keberadaan akun. Logout dan perubahan password merotasi/revoke session yang relevan.

Agar GET `/me` dapat mengembalikan token CSRF yang stabil tanpa menyimpan bearer session, turunkan token CSRF pseudorandom melalui HMAC-SHA256 dengan `CSRF_SERVER_SECRET` atas session ID dan token_hash. Simpan csrf_hash untuk verifikasi; GET `/me` menghasilkan token yang sama selama session tidak dirotasi. Secret minimal 32 byte acak dan hanya di backend. Jangan merotasi CSRF setiap polling sehingga tab lain gagal menyimpan.

Admin membuat akun dengan temporary password acak yang diperlihatkan sekali pada UI admin, tidak dicatat log, wajib diganti sebelum akses akademik. Tidak ada email otomatis atau password reset mandiri; admin dapat mereset dengan mekanisme yang sama dan revoke semua session. Bootstrap admin dari CLI dengan secret melalui stdin atau secret manager, bukan password tetap pada seed produksi.

Password baru minimal 12 dan maksimal 128 karakter; temporary password minimal 20 karakter acak. Batasi ukuran request sebelum hashing dan gunakan constant-time comparison yang disediakan library untuk token/hash. Perubahan password merotasi session saat ini serta membatalkan session lain; temporary password yang telah diganti tidak dapat dipakai lagi.

### 4.2 Peran dan otorisasi objek

| Operasi | Admin operasional | Dosen course aktif | Mahasiswa course aktif |
|---|---|---|---|
| Kelola akun/course/enrollment | Ya | Lihat roster course; tidak mengubah global role | Tidak |
| Lihat isi akademik course | Hanya bila juga ditugaskan sebagai dosen course | Ya | Tugas terbit dan hasil miliknya yang dirilis |
| Draft/approve/publish konteks dan tugas | Tidak karena role admin saja | Ya | Tidak |
| Upload/finalize submission | Tidak | Tidak atas nama mahasiswa | Individu sendiri atau anggota roster kelompok yang sah |
| Analisis/review seluruh elemen | Tidak karena role admin saja | Ya | Tidak |
| Konfirmasi findings/approve/publish feedback | Tidak karena role admin saja | Ya | Tidak |
| Lihat original upload | Bila juga punya akses akademik yang relevan | Course terkait | Original milik submission yang boleh diaksesnya |
| Lihat evidence internal/hasil AI | Tidak karena role admin saja | Course terkait | Hanya evidence yang ditautkan pada publikasi miliknya |
| Log operasi tanpa isi tugas | Ya sesuai fungsi operasi | Status job course terkait | Status teknis aman miliknya |

Izin diperiksa di service/backend sebelum query dan pada objek yang dikembalikan. ID valid bukan bukti akses. Gunakan respons 404 untuk objek di luar scope agar tidak membocorkan keberadaan resource. Revocation enrollment berlaku segera pada request berikutnya, termasuk evidence/download.

### 4.3 Kelompok dan kepemilikan

- Dosen membuat kelompok dan anggotanya dari enrollment aktif; satu mahasiswa maksimum satu kelompok per assignment kelompok.
- Submission memiliki tepat satu owner: `individual_user_id` ATAU `group_id`; constraint XOR wajib di database.
- Anggota kelompok aktif dapat upload dan finalize; semua aksi menyimpan actor. Hindari finalize ganda dengan transaction lock dan revision.
- Roster disalin ke `submission_version_members` ketika finalize. Akses hasil kelompok memerlukan enrollment saat ini aktif dan tercantum pada roster snapshot tersebut. Anggota baru tidak otomatis memperoleh histori kelompok sebelumnya.
- Perubahan kelompok setelah finalize tidak mengubah versi lama. Dosen menyiapkan roster baru jika mengizinkan versi pengumpulan berikutnya; histori tetap tersedia untuk dosen berizin.
- Tidak ada agregasi hasil kelompok menjadi profil atau skor individual.

## 5. Persiapan konteks dan tugas

### 5.1 Course Context Preparation

Input konteks menerima PDF, DOCX, JPG/JPEG, PNG atau input manual. XLSX tetap format submission, bukan input konteks pada MVP. KK dan RPP boleh dipakai bersama; keduanya tidak wajib ada jika dosen mengisi konteks manual yang sah.

Urutan:

1. Dosen memilih course, mengunggah sumber berstatus konteks, atau memilih input manual.
2. Worker memakai parser yang sama dengan submission untuk ekstraksi struktur dan anchor. Tidak membuat korpus retrieval materi.
3. Model efficient mengekstrak kandidat field dari sumber yang disuplai; complex hanya bila relasi/konflik memerlukannya dan budget cukup. Input kecil dapat dibaca langsung; RAG tidak wajib untuk tahap ini.
4. Setiap field menyimpan `origin=extracted|manual|suggested`, `source_refs`, dan `resolution_status=proposed|confirmed|conflicting|not_provided`.
5. Bila KK dan RPP menyatakan hal berbeda, simpan kedua kandidat serta anchor. Tidak ada prioritas otomatis berdasarkan jenis file. Dosen memilih nilai, menulis resolusi singkat, atau memasukkan koreksi manual.
6. Approval menolak field wajib yang kosong atau konflik yang belum selesai. Field wajib: course code/title, deskripsi konteks yang akan dipakai, locale angka default, dan bahasa. CPMK opsional; jika tidak tersedia, simpan `learning_outcomes=[]` dengan `outcomes_status=not_provided`.
7. Approval membentuk ContextVersion immutable. Edit berikutnya membuat draf/versi baru dan tidak mengubah tugas yang sudah terbit.

Contoh kontrak domain; UUID dan actor/timestamp ditambahkan server:

```json
{
  "course_code": "SI-ML-01",
  "course_title": "Pembelajaran Mesin",
  "context_description": "Pemeriksaan laporan implementasi regresi sesuai instruksi tugas.",
  "language": "id",
  "number_locale": "id-ID",
  "outcomes_status": "available",
  "learning_outcomes": [
    {
      "code": "CPMK-01",
      "description": "Menjelaskan preprocessing dan implementasi model regresi.",
      "origin": "manual",
      "source_refs": [],
      "resolution_status": "confirmed"
    }
  ]
}
```

Tidak ada materi kuliah, slide pembelajaran, buku, web, atau kumpulan jawaban acuan pada konteks. KK/RPP dipakai untuk konteks normatif dan provenance field; seluruh isi file tidak otomatis dimasukkan ke prompt setiap check. Hanya field yang telah disahkan dan relevan dimuat langsung menurut versi.

### 5.2 Assignment dan criteria

Assignment dibuat dari ContextVersion yang disahkan. Dosen memasukkan `instruction_original` utuh; hanya normalisasi line-ending CRLF ke LF diperbolehkan, tanpa meringkas atau menghapus isi. Simpan hash instruksi. `instruction_summary` opsional, diberi label ringkasan, dan tidak dapat menjadi sumber kewajiban baru.

Input rubric bersifat opsional. Jika tidak ada, AI dapat mengusulkan criteria, descriptor dan requirement dari instruksi. Jika dokumen rubrik memiliki level/nilai/bobot, pertahankan file asli untuk provenance tetapi jangan membuat field skor atau menjadikan komponen penilaiannya fitur aplikasi. Dosen mengesahkan versi descriptor pemeriksaan yang telah diratakan menjadi kewajiban bukti yang relevan.

Data minimum sebelum tugas terbit:

| Data | Validasi wajib |
|---|---|
| Instruksi asli | Nonkosong; maksimal 30.000 karakter; terlihat mahasiswa |
| Criteria | Minimal 1, maksimal 30; title dan descriptor wajib; tanpa bobot/maksimum nilai |
| Requirement | Kewajiban atomik dengan ID, teks, source ref ke span instruksi/descriptor, mandatory dan obligation key |
| CheckElement | Minimal 1, maksimal 100; terkait criterion dan obligation; human boundary eksplisit |
| Mapping CPMK | Opsional, many-to-many; hanya ID CPMK pada ContextVersion terkait; tanpa alokasi bobot |
| Mode | `individual` atau `group`, tidak berubah dalam versi terbit |
| Jadwal | `opens_at`, `due_at`; due setelah opens; default late policy `accept_with_label` |
| NumericPolicy | Wajib untuk elemen numerik; tidak diperlukan pada elemen lain |
| Spesifikasi | Schema versi 2 valid, DAG bebas siklus, operator/predicate/grounding dikenal, decision question/derivasi lengkap, lint efektif passed, seluruh versi cocok dan disahkan |

Requirement dari descriptor yang tidak disebut di instruksi tetap boleh resmi hanya bila descriptor tersebut disahkan dan dipublikasikan sebagai bagian tugas sebelum mahasiswa mengerjakan. AI tidak boleh memindahkan saran internal menjadi tuntutan tersembunyi.

### 5.3 Approval dan penerbitan tugas

Ada dua approval: (1) paket instruksi/criteria/requirements; (2) CheckSpecification yang diturunkan darinya. Menyetujui draft criteria saja tidak menerbitkan tugas. Perubahan paket setelah approval membatalkan approval spesifikasi turunan dan meminta regenerasi/penyesuaian. Approval spec juga mengikat lint report/hash terbaru dan konfirmasi kesetiaan makna oleh dosen sesuai Bagian 7.7; blocking lint error tidak dapat dioverride. Perubahan isi spec mengharuskan lint baru; approval bukan jalan untuk menyisipkan tuntutan baru.

Publish tugas adalah transaksi yang membentuk AssignmentVersion dengan snapshot referensi ke ContextVersion, CriterionSetVersion, SpecificationVersion, NumericPolicyVersion dan kebijakan upload/jadwal. Approval dan publication menyimpan actor/time/hash. Mahasiswa melihat instruksi, criteria/descriptor, requirement dan bentuk bukti yang diterima; prompt internal, operator, biaya, dan log tidak ditampilkan.

AssignmentVersion yang sudah terbit tidak dipatch in place untuk submission lama. Perubahan instruksi, aturan atau jadwal dibuat sebagai AssignmentVersion baru dan diberlakukan untuk finalization berikutnya; pemilik submission yang sudah final memerlukan reopen sebelum menyerahkan versi baru. Label terlambat receipt historis tidak diubah. `new-version` menyalin data menjadi draf dan mengosongkan approval; dosen meninjau dan mengesahkan kembali, tanpa kewajiban memanggil AI jika data yang disalin sudah memadai. State administratif open/closed/archived pada parent assignment mengatur penerimaan pengumpulan baru dan tidak menulis ulang snapshot.

## 6. Pengumpulan final dan perubahan sumber

### 6.1 Upload dan validasi

Default submission: maksimum 5 file; masing-masing maksimum 20 MiB (`20 * 1024 * 1024` byte). Diterima: `.pdf`, `.docx`, `.xlsx`, `.jpg`, `.jpeg`, `.png`; signature/MIME/struktur harus sesuai, tidak cukup extension. File encrypted/password-protected ditolak dengan alasan; `.docm`, `.xlsm`, `.zip`, CSV, source code dan format lain ditolak.

Gunakan upload lewat backend streaming pada MVP, bukan presigned upload: backend mengotorisasi owner, streaming ke quarantine storage dengan limit byte, menghitung SHA-256, memeriksa MIME/container lalu menandai `validated`. Request tidak memuat file utuh di RAM. Nama file hanya metadata tampilan yang disanitasi; object key UUID buatan server. File tidak ditampilkan sebelum lolos validasi.

Limit engineering default:

| Kontrol | Default dan perilaku |
|---|---|
| Gambar | Maksimum 25 megapiksel; decode bomb/format rusak ditolak; EXIF orientation diproses pada turunan, original tetap disimpan |
| Kontainer OOXML | Maksimum 10.000 entry ZIP dan 512 MiB total ukuran dekompresi; path traversal/entry escape ditolak |
| PDF | Maksimum 100 halaman per bundle; melebihi batas teknis ditolak sebelum finalize setelah preflight |
| XLSX | Maksimum 25 sheet dan 100.000 sel nonkosong per workbook; ukuran/layout di luar batas ditolak saat preflight |
| Bundle | Maksimum 100 halaman raster/PDF total, menghitung 1 halaman untuk setiap JPG/PNG; DOCX dinilai menurut limit elemen parser, bukan tebakan halaman |
| DOCX | Maksimum 20.000 node konten terinspeksi dan 1.000 media; melebihi batas ditolak saat preflight |
| Parser | Proses terisolasi memakai profil Bagian 8.5: native OOXML 2 GiB, Docling native 8 GiB; soft/hard timeout 120/150 detik per task halaman/tahap, bukan per seluruh file; batas aktif run tetap 600 detik |

Preflight membaca metadata/struktur secara terbatas untuk kelayakan upload, bukan memberikan feedback akademik. Bila limit baru dapat diketahui pada parsing, file tetap menjadi bagian manifest final dan keterbatasan dicatat; jangan menghapusnya diam-diam. Hapus upload quarantine yang gagal dan upload tidak dipakai setelah 24 jam.

### 6.2 Finalize

1. Mahasiswa mengunggah dan meninjau daftar file yang valid.
2. Finalize mengirim file IDs, `expected_revision` draft, dan `Idempotency-Key`.
3. Backend mengunci submission, memeriksa izin, assignment telah terbuka, minimal 1 file valid, batas bundle, ownership semua file, versi tugas dan roster.
4. Hitung manifest hash dari daftar `(file_id, sha256, size, mime)` terurut file ID; simpan urutan tampilan mahasiswa secara terpisah.
5. Bentuk SubmissionVersion immutable dengan AssignmentVersion yang dipakai, roster, finalized_at dan `is_late = finalized_at > due_at`.
6. Commit version + job + outbox dalam satu transaksi. Respons sukses baru dikirim setelah commit; queue yang sedang mati tidak membatalkan finalize.
7. Tampilkan receipt: versi, waktu server, daftar file/hash pendek, status teknis, dan label terlambat bila berlaku. Tidak ada feedback akademik sebelum hasil resmi diterbitkan.

Default `accept_with_label` menerima pengumpulan setelah due_at dan memberi label terlambat tanpa penalti nilai. Dosen dapat memilih `reject_after_due` sebelum tugas terbit. Sebelum opens_at ditolak. Kebijakan ini bagian AssignmentVersion; client tidak dapat menimpa.

### 6.3 Reopen dan versi baru

Mahasiswa tidak dapat mengubah versi final. Dosen dapat membuka pengumpulan kembali dengan alasan singkat dan `reopen_until`, membentuk draft berikutnya. Mahasiswa harus finalize lagi. Versi sebelumnya, hasil AI, review dan publikasi tetap ada.

SubmissionVersion baru memakai AssignmentVersion aktif yang dipilih dosen saat reopen; UI memperlihatkan bila instruksi/criteria berubah. AI run versi lama tidak dipakai untuk versi baru. Sistem tidak menggabungkan bukti dari dua versi submission.

Jika versi lama telah dipublikasikan sementara versi baru sedang diproses, mahasiswa dapat melihat publikasi lama dengan label versi sumber yang jelas dan status versi baru belum dirilis. `latest_published` bukan berarti sumbernya sama dengan `active_submission_version`. Jangan menampilkan feedback lama sebagai feedback versi baru.

## 7. Kontrak Check Specification

### 7.1 Struktur domain

Setiap SpecificationVersion mengandung `schema_version=2`, daftar elemen, daftar dependensi, versi operator/predicate/grounding/lint registry, dan policy limit. Semua field identitas/versi dimuat server, bukan dipercaya dari output model. Content hash spec mengikat isi kanonik dan paket normatif; approval binding menyimpan content hash, lint report/resolution refs dan konfirmasi dosen secara terpisah. `schema_version` berbeda dari nomor versi konten dan revision draf.

| Field elemen | Tipe dan aturan |
|---|---|
| `element_id` | UUID identitas CheckElement pada satu SpecificationVersion; versi konten baru memakai row/UUID baru, sedangkan element_code mempertahankan lineage maknanya |
| `element_code` | Business code stabil dalam assignment, unik pada SpecificationVersion, mis. EL-PRE-SCALING |
| `criterion_id` | UUID criterion dalam CriterionSetVersion yang sama |
| `requirement_ids` | Array UUID; minimal 1 untuk `target_kind=requirement`; kosong boleh untuk descriptor/numeric |
| `obligation_key` | String unik makna tuntutan, mis. `preprocessing.scaling.explanation`; sama hanya jika dosen menyetujui bahwa masalahnya identik |
| `target_kind` | `requirement`, `descriptor`, atau `numeric` |
| `decision_question` | Pertanyaan dukungan terhadap kewajiban, nonkosong, maksimum 2.000 karakter; polarity konsisten; bukan instruksi skor/Boolean paksa |
| `derivation_refs` | Array 1–20 referensi normatif bertipe Bagian 7.7; seluruh ID/hash/span sah dalam paket tugas yang diikat |
| `expected_evidence` | Makna bukti yang dicari, nonkosong, maksimum 2.000 karakter |
| `grounding_contract` | Union bertipe Bagian 7.6; mode dan absence support cocok dengan target_kind/relationships |
| `accepted_evidence_types` | Subset `paragraph`, `table`, `diagram`, `image_text`, `spreadsheet_cell`, `spreadsheet_range`; minimal 1 |
| `relationships` | Array maksimum 10; setiap relasi mempunyai relationship_code unik/stabil dalam elemen, predicate registry, subject/object/qualifiers teks; predicate tidak dapat diciptakan model |
| `applicability` | Union bertipe pada Bagian 7.2 |
| `source_hints` | Array petunjuk pencarian, maksimum 10; bukan lokasi wajib atau daftar sumber eksklusif |
| `operators` | Subset registry pada Bagian 7.3; diverifikasi terhadap target_kind |
| `dependencies` | Array element UUID dalam versi yang sama; DAG wajib bebas siklus |
| `coverage_policy` | Objek bertipe pada Bagian 7.4; bukan instruksi eksekusi berbahasa bebas |
| `abstention_conditions` | Array reason code registry; guardrail sistem wajib berlaku meskipun tidak disebut model |
| `numeric_policy_version_id` | UUID wajib untuk numeric; null untuk elemen nonnumerik |
| `human_judgment_boundary` | Penjelasan bagian yang tidak disimpulkan ALIGN, nonkosong |

Jika satu criterion memiliki beberapa kewajiban, pecah menjadi elemen atomik. Jangan memakai satu elemen seperti "seluruh laporan bagus". Rincian diharapkan dari instruksi/descriptor resmi; "memakai StandardScaler" tidak otomatis berarti implementasi benar atau seluruh penjelasan cukup.

Registry predicate MVP: `covers`, `compares`, `supports`, `applies`, `traces_to`, `reports_same_quantity`. Setiap relasi mempunyai `relationship_code` dengan pola `^[a-z][a-z0-9_]{0,63}$`; kode dipertahankan lintas revisi ketika maknanya tetap sama. Predicate mengarahkan pertanyaan hubungan, bukan menjalankan fungsi. Domain seperti preprocessing/strategi menjadi subject/object/qualifier; jangan membuat kode predicate baru untuk setiap tugas. Qualifiers mempunyai hanya key `basis`, `period`, `scenario`, `metric`, masing-masing string atau null; key yang tidak diisi pada input domain diberi default null, sedangkan adapter mengirim schema provider dengan seluruh field yang dipersyaratkan. Menambah predicate memerlukan perubahan kode, schema version, dan tes, bukan sekadar respons model.

### 7.2 Applicability

Schema discriminated union:

- `{"mode":"always"}`: berlaku bagi semua submission pada assignment version.
- `{"mode":"fact_rule","rule": ...}`: ekspresi atas fact bertipe yang sumbernya dapat diverifikasi.
- `{"mode":"lecturer_required","question":"..."}`: dosen harus menetapkan berlaku/tidak; AI tidak menebak.

Rule DSL mendukung `all`, `any`, `not` dan leaf `eq`, `in`, `contains`, `gt`, `gte`, `lt`, `lte`. Depth maksimum 3, maksimum 10 leaf, tanpa regex/kode/path bebas. Field fact harus ada pada registry: `assignment.mode`, `submission.file_types`, serta fact tugas yang didaftarkan dalam spesifikasi dan bertipe boolean/string/decimal. Comparator harus cocok dengan tipe.

Representasi JSON normatif: all/any adalah `{"op":"all","children":[...]}` atau any dengan children nonkosong; not adalah `{"op":"not","child":...}`; leaf eq/gt/gte/lt/lte adalah `{"op":"eq","fact":"assignment.mode","value":"group"}`; in adalah `{"op":"in","fact":"fact_name","values":[...]}` untuk fact scalar. Tambahkan leaf `contains` berbentuk `{"op":"contains","fact":"submission.file_types","value":"xlsx"}` khusus fact array. submission.file_types bertipe array enum format, assignment.mode bertipe enum individual/group. Custom fact memakai nama `task.<registered_key>` dan tipe boolean/string/decimal; schema registry disahkan bersama spesifikasi. Operator lain atau key tambahan ditolak, missing fact tidak pernah diubah menjadi false.

Fact memiliki `value`, `origin`, `evidence_ids` dan `verification=deterministic|lecturer_confirmed|proposed`. Hanya deterministic atau lecturer_confirmed boleh menentukan applicability. Respons model atau pernyataan mahasiswa dapat menjadi kandidat fact, tetapi tidak otomatis membuat kondisi benar/salah. Fact belum tersedia → `unresolved`; tidak ada default false. Fact yang bergantung pada eksekusi kode mahasiswa tidak dapat diselesaikan otomatis pada MVP.

Contoh: kewajiban menangani missing value bersyarat tidak diubah menjadi selalu wajib jika sumber resmi menyatakan syarat. Jika kondisi dataset tidak dapat diverifikasi dari berkas yang didukung, tanyakan pada dosen atau tandai unresolved.

### 7.3 Operator registry dan urutan

| Operator | Implementasi dan batas |
|---|---|
| `locate_evidence` | Query struktur berdasarkan anchor/label; jika belum memadai, jalankan hybrid retrieval di scope bundle |
| `expand_context` | Buka parent/tetangga, tabel lengkap, caption, header dan unit; tetap memakai sumber versi yang sama |
| `inspect_source_coverage` | Perbarui coverage record; selalu dijalankan aplikasi untuk check yang berlaku |
| `interpret_relationship` | Model atau aturan terdaftar mengamati hubungan terhadap tuntutan; output terstruktur |
| `check_coverage` | Hubungkan observasi dengan kewajiban descriptor dan gate kode |
| `establish_comparability` | Bentuk tuple besaran dan periksa field wajib sebelum hitungan |
| `compare_numbers` | Decimal engine; hanya operasi dalam NumericPolicy yang disahkan |
| `request_human_review` | Catat alasan, sumber terkait dan pertanyaan yang perlu diselesaikan dosen |

Setiap check wajib melewati authorize/snapshot/applicability dan pencatatan coverage. Daftar operator tidak boleh menonaktifkan guardrail ini. Elemen numeric otomatis wajib memiliki establish_comparability sebelum compare_numbers. Numeric dengan grounding mode human_only tidak menjalankan operator perbandingan otomatis sesuai Bagian 7.6. Dependensi yang unresolved tidak menghasilkan hasil dependent yang dipaksakan; elemen lain yang independen terus berjalan.

Eksekusi adalah DAG fungsi aplikasi, bukan multi-agent otonom. Tidak ada `eval`, exec, SQL/shell yang dibentuk model, browsing tool, atau akses file di luar manifest. Operator mempunyai typed arguments, timeout dan return schema; input ID diuji membership dan izin.

### 7.4 Coverage policy yang executable

Default semua elemen menggunakan bentuk berikut:

```json
{
  "positive_strategy": "sufficient_support",
  "negative_strategy": "exhaustive_compatible_source_units",
  "inspect_embedded_visuals": true,
  "unresolved_compatible_unit_blocks_negative": true,
  "preserve_table_context": true
}
```

`positive_strategy=sufficient_support` mengizinkan hasil tidak ditemukan isu ketika kewajiban teramati didukung bukti yang memadai untuk batas check tersebut. Tidak mengklaim seluruh pekerjaan benar. Konflik yang sudah diketahui tetap harus diselesaikan.

`negative_strategy=exhaustive_compatible_source_units` mewajibkan seluruh source unit yang dapat memuat jenis bukti yang diterima dicakup oleh inspeksi sebelum mengusulkan absence atau gap berbasis tidak ditemukannya bukti. Retrieval top-k saja tidak cukup. Inspeksi dapat berupa akses deterministik untuk struktur yang memang deterministik atau scan semantik bertahap atas seluruh unit yang kompatibel. Catat unit, metode inspeksi dan hasilnya; tidak cukup menandai seluruh file "visited".

Hasil pencarian kosong memicu fallback inspeksi menyeluruh dalam budget. Unit tidak boleh dikeluarkan hanya karena similarity rendah atau heading berbeda. Pengecualian unit hanya untuk tipe yang tidak diterima atau alasan struktural jelas seperti metadata dekoratif; alasan dan rule pengecualian dicatat. Jika visual tidak dapat diidentifikasi cukup jelas sebagai dekoratif/tidak relevan, perlakukan sebagai berpotensi memuat bukti.

Unit yang belum dibaca, tidak terbaca, terpotong, atau belum diinspeksi dan berpotensi menentukan hasil membuat negative gate gagal. Budget/time limit tidak mengubahnya menjadi sufficient. Catat unassessable dengan reason code. Ini berlaku untuk `missing_requirement` maupun `rubric_coverage_gap` yang mengandung klaim ketiadaan. Gate menunjukkan inspeksi telah mengikuti kebijakan, bukan jaminan mutlak bahwa model tidak mungkin melewatkan bukti.

### 7.5 Contoh elemen tanpa skor

```json
{
  "element_id": "11111111-1111-4111-8111-111111111111",
  "element_code": "EL-PRE-SCALING",
  "criterion_id": "22222222-2222-4222-8222-222222222222",
  "requirement_ids": ["33333333-3333-4333-8333-333333333333"],
  "obligation_key": "preprocessing.scaling.explanation",
  "target_kind": "requirement",
  "decision_question": "Apakah sumber menjelaskan penerapan normalisasi atau standardisasi pada preprocessing sesuai tuntutan resmi?",
  "derivation_refs": [{"source_kind": "requirement", "source_id": "33333333-3333-4333-8333-333333333333", "span": null}],
  "expected_evidence": "Penjelasan penerapan normalisasi atau standardisasi sesuai instruksi resmi.",
  "grounding_contract": {"schema_version": 1, "mode": "linked_relationship", "absence_support": "approved_coverage_policy"},
  "accepted_evidence_types": ["paragraph", "table", "diagram", "image_text"],
  "relationships": [{"relationship_code": "scaling_application", "predicate": "applies", "subject": "normalisasi atau standardisasi", "object": "preprocessing", "qualifiers": {"basis": null, "period": null, "scenario": null, "metric": null}}],
  "applicability": {"mode": "always"},
  "source_hints": ["preprocessing", "normalisasi", "standardisasi", "feature scaling"],
  "operators": ["locate_evidence", "expand_context", "inspect_source_coverage", "interpret_relationship", "check_coverage", "request_human_review"],
  "dependencies": [],
  "coverage_policy": {"positive_strategy": "sufficient_support", "negative_strategy": "exhaustive_compatible_source_units", "inspect_embedded_visuals": true, "unresolved_compatible_unit_blocks_negative": true, "preserve_table_context": true},
  "abstention_conditions": ["unreadable_relevant_region", "incomplete_source_inspection", "ambiguous_descriptor"],
  "numeric_policy_version_id": null,
  "human_judgment_boundary": "ALIGN mengidentifikasi bukti penjelasan dan kemungkinan kekurangannya; kualitas akademik menyeluruh tidak diberi nilai."
}
```

### 7.6 Grounding contract yang wajib

`accepted_evidence_types` menentukan bentuk sumber yang boleh dipakai. `grounding_contract` menentukan bentuk dukungan yang harus ada agar keputusan check dapat dibenarkan. Schema objek tertutup: `schema_version` integer konstan 1, `mode` enum berikut, `absence_support` wajib ada dengan nilai yang ditentukan. Tidak menerima threshold confidence, jumlah citation minimum bebas, atau operator tambahan dari model.

| mode | Validasi dan dukungan wajib | absence_support |
|---|---|---|
| `anchored_content` | Observation tentang isi yang relevan, evidence sah dan locator valid. Kutipan bila diberikan harus cocok dengan span sumber; kutipan tidak wajib untuk diagram/visual | `approved_coverage_policy` |
| `linked_relationship` | Seluruh relationships elemen dipetakan ke support roles terdaftar dan observasi hubungan; minimum satu relationship | `approved_coverage_policy` |
| `comparable_numeric` | target_kind=numeric, policy resmi, NumericObservation/comparability/Decimal check yang sudah ditentukan Bagian 12 | null |
| `human_only` | Batas judgement dan pertanyaan review eksplisit; tidak ada keputusan AI substantif | null |

Untuk requirement/descriptor yang mempunyai relationship semantik, mode otomatis wajib linked_relationship. Elemen tanpa relationship memakai anchored_content atau human_only. Elemen numerik memakai comparable_numeric; predicate reports_same_quantity hanya pada mode ini. human_only boleh dipakai pada target_kind apa pun dan operatornya terbatas pada locate_evidence/expand_context/inspect_source_coverage/request_human_review; tidak menjalankan interpretasi semantik/angka untuk keputusan otomatis. Applicability tetap diperiksa dahulu; not_applicable tetap tanpa check_status.

Registry support roles untuk linked_relationship:

| Predicate | Required roles pada setiap relationship_code | Makna |
|---|---|---|
| covers | content, relation | Isi yang menanggapi kewajiban dan penjelasan keterkaitannya terhadap target resmi |
| compares | left, right, basis, relation | Alternatif yang dibandingkan, dasar perbandingan yang diminta, serta hubungan perbandingan |
| supports | claim, support, relation | Pernyataan yang didukung, dukungan yang diberikan, serta hubungan keduanya |
| applies | method, application, relation | Metode/konsep yang dinyatakan, konteks penerapan, serta hubungan penerapannya |
| traces_to | artifact, trace, relation | Artefak/hasil, jejak pemetaan ke target resmi, serta hubungan yang dinyatakan |

Role names adalah enum gabungan tabel ini ditambah content untuk anchored_content. Peran target resmi pada covers/traces_to berasal dari derivation_refs/context normatif; tidak mensyaratkan mahasiswa mengulang teks requirement dalam jawabannya. Source bindings membuktikan respons/jejaknya, bukan keberadaan requirement resmi. Semua qualifiers nonnull pada relationship harus dibahas dalam observation hubungan; jangan menciptakan kewajiban qualifier yang tidak ada dalam sumber resmi.

Satu EvidenceItem atau region yang sama boleh mengisi beberapa role jika memang mendukungnya. Tidak ada syarat dua file, dua chunk, atau kutipan berbeda. Bentuk dukungan bukan instruksi baru mengenai format jawaban mahasiswa. Bila satu tabel memenuhi seluruh hubungan, tabel tersebut cukup dalam batas check. Sebaliknya, keberadaan nama dua alternatif saja tidak membuktikan keduanya dibandingkan dengan dasar yang diminta.

`absence_support=approved_coverage_policy` tidak berarti absence otomatis diizinkan. Claim ketiadaan wajib merujuk coverage record aktual yang negative gate-nya lulus; evidence positif mengenai fragmen lain tidak menggantikan scan. Untuk ketiadaan penuh, support bindings boleh kosong dan evidence_relationship=not_found, tetapi coverage reference wajib. Gap parsial menyertakan bukti bagian yang ada dan coverage untuk bagian yang dinyatakan tidak ditemukan. Pada numeric, operand tidak tersedia tidak diubah menjadi missing_requirement; check numerik menjadi unassessable. human_only tidak mempunyai jalur absence otomatis.

Validasi kode membuktikan schema, membership, quote/span, peran, tuple dan coverage. Verifikasi semantik tetap menilai apakah isi mendukung klaim; keberhasilan validasi mekanis tidak otomatis membuat semantic_support=supported. Model tidak menambah mode global subjective diagnosis. Kreativitas atau mutu holistik tetap di luar scope; judgement yang tak dapat dioperasionalkan secara sah diarahkan ke dosen.

### 7.7 Derivasi, decision question dan compilation lint

`derivation_refs` adalah array discriminated union dengan tiga bentuk tertutup berikut; `source_id` UUID dalam paket yang diikat. `span` menggunakan indeks Unicode code point 0-based, akhir eksklusif, atas teks LF kanonik (bukan UTF-16 browser atau byte offset). Backend mengembalikan text hash terhitung dan kutipan sumber sebagai read-only projection; client/model tidak menetapkan hash resmi.

- `{"source_kind":"requirement","source_id":"UUID","span":null}`: mengacu seluruh teks requirement resmi, dan ID ada dalam requirement_ids elemen untuk target requirement.
- `{"source_kind":"criterion_descriptor","source_id":"UUID","span":{"start":0,"end":10}}`: span nonkosong di descriptor criterion terkait.
- `{"source_kind":"instruction","source_id":"UUID","span":{"start":0,"end":10}}`: span nonkosong di instruction_original AssignmentVersion yang diikat.

Untuk target_kind=requirement, seluruh requirement_ids wajib tercakup derivation_refs. Target descriptor/numeric tetap memiliki minimal satu ref resmi; jangan menurunkan check dari ringkasan AI atau contoh eksternal. Kewajiban baru yang dosen tulis manual harus terlebih dahulu masuk instruction/descriptor/requirement paket yang disahkan dan dipublikasikan. Approval CheckSpecification tidak menggantikan perubahan paket tersebut.

decision_question memakai satu pertanyaan tentang dukungan kewajiban dengan polarity positif, misalnya “Apakah sumber menjelaskan dasar pemilihan metode sesuai instruksi?”. Jawaban bukan dipaksa Yes/No: dimensi applicability/coverage/relationship/status pada Bagian 9 tetap berlaku. Penggunaan “tidak” dalam kewajiban larangan yang resmi tetap boleh; hindari negasi ganda atau pemetaan label terbalik.

Compilation lint wajib dijalankan ketika draf disimpan dan sebelum approval. Run otomatis pada PUT bersifat deterministik, tidak memanggil provider. Pemeriksaan semantic/fidelity tambahan hanya boleh memakai jatah draft yang sudah ada atau penelaahan manusia; tidak ada job paid lint tanpa batas. AI augmentation bersifat opsional pada draft, maksimum dalam budget draft; tidak dieksekusi per submission dan tidak menambahkan tuntutan baru. Diversification/elaboration boleh sebagai alternatif draft, bukan kewajiban menjalankan keduanya.

PUT yang gagal schema/operator/DAG/membership validation mengembalikan 422 dan tidak mengubah draf; field errors dapat memuat rule lint terkait. Report tersimpan mengikat draf yang benar-benar tersimpan, bukan body gagal yang seolah-olah sudah menjadi versi resmi. Report failed tetap dapat muncul dari pemeriksaan draf/paket yang telah menjadi stale/invalid atau draft historis yang sedang dikonversi; approval tidak dapat memakai report itu.

| Rule code | Severity | Pemeriksaan/perilaku |
|---|---|---|
| invalid_normative_ref | blocking | ID/span/hash lineage di luar paket, derivasi kosong, atau requirement tidak tercakup |
| invalid_contract | blocking | Schema/operator/predicate/grounding/type/limits salah atau DAG bersiklus |
| unsupported_requirement | blocking jika terbukti dari struktur; review bila semantik meragukan | Tuntutan tanpa sumber resmi tidak boleh masuk; perubahan semantik tidak diputuskan keyword matching semata |
| atomicity_review | review | Beberapa kewajiban yang dapat gagal independen; jangan memotong satu hubungan substantif secara mekanis |
| polarity_review | review | Pertanyaan tidak jelas atau berpotensi membalik arti keputusan |
| condition_fidelity_review | review | Kondisi, pengecualian, wajib/opsional atau alternatif yang sah mungkin hilang/berubah |
| duplicate_obligation_review | review | Makna tampak berulang; dosen menetapkan alias/identity atau memisahkan kewajiban berbeda |
| checkability_review | review | Bentuk bukti, batas judgement, applicability atau hubungan belum cukup operasional |

LintReport immutable menyimpan spec draft revision/content hash, paket sumber hashes, versi linter/registries, created_at dan issues. Issue memiliki issue_code stabil dalam report, rule_code, severity, element_ids, source refs, message serta evidence/reason yang dapat ditelaah. Array issues kosong tetap menghasilkan report. Status awal failed bila ada blocking, needs_review bila hanya review issues, passed bila kosong.

Blocking issue harus diperbaiki melalui edit draf dan lint baru; tidak dapat ditimpa “setuju” oleh dosen. Review issue boleh diakui dosen dengan resolution `acknowledged_faithful` atau `acknowledged_human_boundary`, alasan nonkosong maksimal 2.000 karakter dan actor/time. Resolusi kedua hanya sah jika elemen sudah mode human_only pada draf tersebut. Pemecahan/perubahan/hapus elemen selalu melalui PUT draf yang menghasilkan report baru, bukan klaim perbaikan di catatan. Dosen tetap wajib menyatakan fidelity_confirmation=true untuk seluruh approval, termasuk ketika lint tidak menemukan masalah.

`effective_status=passed` hanya bila report hash/revision/paket cocok dengan draf saat ini, tidak ada blocking, dan semua review issues mempunyai resolusi sah. Resolusi di report lama tidak otomatis berlaku pada report baru. Approval menyimpan lint_report_id, resolved_issue_refs, konfirmasi dosen, hash sumber/spec dan actor/time dalam binding immutable. Content hash spec dihitung tanpa metadata approval itu sendiri untuk menghindari hash rekursif; approval binding menyimpan content hash tersebut secara terpisah.

Canonical content hash spec memakai UTF-8 JSON dengan keys terurut, separator tanpa whitespace tak bermakna, set refs diurutkan stabil dan isi teks LF tetap. Field yang diikat: schema_version, bound context/criterion-set/instruction refs/hashes, registry versions, limits, elements dan dependencies. Exclude state, revision counter, timestamps, actor/approval serta lint metadata. Binding approval merekam source_spec_revision sebelum transisi approved dan lint resolution_revision yang dipakai; kenaikan revision administratif saat approval tidak membuat bukti lint yang sudah diikat menjadi stale. GET approved spec menampilkan status lint dari binding historis, bukan membandingkannya dengan revision draf yang tidak lagi aktif. Menambah resolusi lint atau mengubah isi pada spec approved ditolak; buat draf versi baru.

Kasus wajib: syarat missing values tetap bersyarat; contoh metode tidak berubah menjadi satu metode wajib; kata boleh tidak berubah menjadi harus; duplicate questions tidak menambah findings; requirement baru tidak diselundupkan lewat checklist. Pemeriksaan lint membantu dosen menemukan masalah, bukan bukti otomatis bahwa seluruh kompilasi semantik pasti benar.

## 8. Parsing, evidence registry, dan viewer

### 8.1 Routing format

| Format | Jalur utama | Locator kanonik | Batas yang wajib terlihat |
|---|---|---|---|
| PDF digital | Docling teks/layout/tabel/reading order | file hash + page 1-based + bbox top-left dalam PDF points + item ID | Kolom, font/glyph, tabel terpecah, reading order |
| PDF scan | Raster halaman + OCR melalui Docling; vision selektif | Halaman asli + bbox + ukuran raster dan transform koordinat | OCR salah, angka/simbol tidak pasti |
| DOCX | python-docx + pembacaan OOXML untuk struktur tambahan | Part XML + structural path + paragraph/table/cell/media ID dan index | Text box, nested table, revision, footnote dan media tidak boleh hilang tanpa status |
| XLSX | openpyxl dua jalur pembacaan formula dan cached value | workbook hash + sheet name/ID + A1 cell/range | Formula/cache, hidden/merged cells, external references |
| JPG/JPEG/PNG | Decode + orientasi turunan + Docling/OCR; vision bila perlu | Hash original + image ID + bbox pixel original + dimensi dan transform turunan | Perspektif, blur, bayangan, tulisan tangan, diagram dan simbol |

Original tidak ditimpa. Semua turunan menyimpan parent hash, transform crop/scale/rotation dan tool version. Jika bbox berasal dari halaman/crop yang diubah, backend mengembalikannya ke koordinat kanonik; simpan juga dimensi visual yang digunakan model. Jangan memperlakukan koordinat tebakan model sebagai anchor yang sudah tervalidasi. Region sumber yang dikirim ke vision dibuat kode dan mempunyai anchor deterministik.

JPG/PNG menggunakan jalur dokumen visual yang sama dengan PDF scan. Tidak wajib dikonversi ke PDF. OCR lokal lebih dahulu untuk teks cetak yang sesuai; region visual atau kasus yang jelas tidak terwakili OCR boleh langsung memakai vision. Tidak ada kewajiban model murah sebelum model kuat. Auto-crop tidak boleh menghilangkan header, unit, legenda atau hubungan bukti yang diperlukan.

### 8.2 Inventaris lengkap

Sebelum analisis, inventaris mencatat semua file, page/part/sheet, paragraf, tabel, gambar, dan region yang didukung. Region yang gagal diparse atau unsupported tetap memperoleh SourceUnit dengan status dan alasan.

Setiap file selalu mempunyai root SourceUnit; setiap halaman PDF/image mempunyai page/image SourceUnit, setiap part DOCX dan sheet XLSX mempunyai container SourceUnit. Inventaris tidak hanya dibuat dari potongan yang berhasil diekstrak. Embedded drawing/chart/media XLSX atau region DOCX yang belum didukung dicatat sebagai unit visual unsupported; halaman/container yang belum tercakup tidak dapat disembunyikan dari negative gate hanya karena parser tidak mengeluarkan teksnya.

`parse_status`: `parsed`, `partially_parsed`, `unreadable`, `unsupported`, `failed`. Status parsed berarti ekstraksi selesai pada batas parser; tidak menjamin OCR atau interpretasi benar.

DOCX: baca paragraph/table dalam urutan dokumen; identifikasi nested table, text box, footnote, header/footer, media dan revision. Default representasi konten adalah final view: inserted text disertakan, deleted text tidak dianggap jawaban aktif, tetapi keberadaan revision dicatat. Bila implementasi tidak dapat menentukan final view suatu region dengan benar, region tersebut partially_parsed/unassessable. Alt text/caption bukan pengganti isi diagram yang menentukan hasil.

XLSX: baca seluruh sheet termasuk hidden, label row/column, merged range, number format, cell type, formula dan cache. Filter/tampilan tidak menjadi alasan menghilangkan sel. Formula eksternal dicatat tanpa diikuti. Jangan menjalankan Excel/LibreOffice untuk recalculation workbook mahasiswa pada MVP.

### 8.3 EvidenceItem

Field wajib: `id`, tepat satu dari `submission_version_id`, `context_version_id`, `assignment_version_id` sesuai purpose, `file_asset_id`, `source_sha256`, `source_unit_id`, `type`, `anchor`, `content_original`, `content_normalized`, `parse_status`, `limitations`, `parser_manifest_id`, `created_at`. Namespace assignment digunakan hanya untuk parsing rubrik normatif; submission retrieval hanya menggunakan namespace submission. Draft context/assignment boleh menerima artefak sebelum approval; approval mengunci sumber dan versi yang dipakai.

Content bertipe:

- Paragraph: teks, parent heading, urutan dan span.
- Table/range: header, rows/cells, unit, period, caption, notes, merged relationship.
- Diagram/image: object key privat/crop, caption dan interpretasi terpisah dengan model/run provenance.
- Numeric observation: literal asli, normalized value string, unit/context serta source quality.

`content_original` adalah hasil ekstraksi yang tidak diringkas; sumber asli tetap acuan. Normalisasi whitespace tidak boleh mengubah digit, tanda, unit atau makna. Source hash dan locator diverifikasi sebelum ditampilkan.

Enum EvidenceItem.type sama dengan accepted_evidence_types pada Bagian 7. Table row memakai type=table dengan subtype/row locator dalam metadata; NumericObservation adalah record terpisah yang merujuk evidence, bukan jenis evidence baru yang melewati filter accepted types. Hasil transkripsi gambar memakai image_text; diagram tetap diagram dengan interpretasi terpisah.

Anchor adalah discriminated union dengan format berikut. file_asset_id/source_sha256 berasal dari EvidenceItem induk dan tidak boleh berbeda. Bbox selalu `[x_min,y_min,x_max,y_max]`, semua nilai finite, urutan valid, berada dalam bounds sumber.

| format | Field wajib dan makna |
|---|---|
| pdf | page integer mulai 1, bbox angka dalam PDF points, coordinate_system=`pdf_points_top_left`, source_item_id string; library dengan origin bawah harus ditransformasikan |
| image | width/height original integer, bbox pixel original, coordinate_system=`original_pixels_top_left`, transform_id nullable untuk crop/rotasi turunan |
| docx | part string OOXML yang ada pada ZIP, structural_path string absolut pada part, item_kind paragraph/table/cell/media, item_index integer; optional cell row/column dan media relationship ID |
| xlsx | sheet_name string persis pada workbook, cell_range A1 canonical seperti D8 atau A1:D8; merged range dicatat dalam metadata |
| file | locator_note nonkosong; hanya bukti manual coarse ketika anchor lebih rinci belum tersedia; jangan dipakai oleh model sebagai jalan pintas citation |

Contoh anchor PDF:

```json
{
  "format": "pdf",
  "page": 4,
  "bbox": [40, 120, 540, 260],
  "coordinate_system": "pdf_points_top_left",
  "source_item_id": "table_2_row_3"
}
```

### 8.4 Viewer wajib

- PDF: PDF.js melalui komponen React, navigasi halaman, zoom dan overlay bbox; nama file dan halaman selalu tampil.
- JPG/PNG: image viewer dengan zoom/pan dan overlay region; tampilkan original yang diorientasikan dengan transform yang diketahui.
- DOCX: viewer struktural read-only dari hasil ekstraksi, anchor paragraf/tabel/media yang dapat dituju; link download original. Nomor halaman hanya boleh ditampilkan bila berasal dari rendering yang tercatat. Tidak perlu membuat rendering PDF DOCX sebagai dependensi MVP.
- XLSX: grid read-only dengan sheet tabs, alamat sel, formula dan cache terpisah, header/merged cell dan label hidden. Jangan mengeksekusi formula di browser.
- HTML hasil ekstraksi selalu di-escape/sanitize; jangan menyisipkan HTML aktif dari file. Source unit unsupported ditampilkan dengan alasan dan akses original, bukan area putih tanpa penjelasan.

### 8.5 ParserResourceProfile, checkpoint dan admission control

D1-A berlaku. Tidak ada lagi batas memori universal atau timeout seluruh file. ParserResourceProfile adalah payload `method_manifests.kind=parser_resource`, schema_version=1, profile_key, version_no, backend, format allowlist, parser package/version lock, model artifact IDs/checksums, OCR engine/languages/artifact checksums, layout/table/options hash, memory_limit_bytes, soft_timeout_seconds, hard_timeout_seconds, initial_page_batch_size dan heavy boolean. Semua field penting diikat parser manifest yang menunjuk satu profile per route. Mengubah limit/backend/model/options membuat manifest baru; worker tidak menimpa profil dalam run.

| profile_key awal | Jalur dan status | Memory | Task soft/hard | Batch awal |
|---|---|---|---|---|
| native_ooxml_v1 | DOCX/XLSX native; metadata preflight terbatas tanpa OCR | 2.147.483.648 byte (2 GiB) | 120/150 detik | Stream unit/container sesuai format |
| docling_native_v1 | PDF digital/scan dan JPG/JPEG/PNG; default parser berat | 8.589.934.592 byte (8 GiB) | 120/150 detik | 1 halaman/gambar |
| docling_light_v1 | Backend pypdfium 4 GiB; kandidat alternatif, tidak aktif default | 4.294.967.296 byte (4 GiB) | 120/150 detik | 1 halaman/gambar |

8/4 GiB adalah headroom awal engineering. Bukan angka per halaman dari paper dan bukan jaminan profil cukup untuk semua dokumen. Benchmark pada exact dependency/weights/hardware aktual wajib sebelum live profile verified. Kekurangan kapasitas deployment tidak mengaktifkan light backend otomatis; operator perlu memvalidasi lalu mengaktifkan release baru. Development dapat menguji guardrail dengan fixture/mock tanpa menyebutnya benchmark parser nyata.

Pisahkan metadata/inventory, render, layout, OCR bila diperlukan, table extraction, dan assembly. Tahap yang telah dikerjakan backend dalam satu konversi tidak wajib dihitung dua kali: simpan satu hasil/checkpoint gabungan yang menyebut stage yang dicakup. Wrapper harus menggunakan page range/streaming yang benar-benar didukung versi terpasang, atau membuat turunan halaman dengan peta page/coordinate ke original yang diverifikasi. Dilarang hanya mengubah label menjadi “per halaman” sambil tetap memanggil konversi seluruh file untuk setiap task.

TaskKey deterministik: namespace owner/version + file hash + parser manifest/resource profile hash + stage + canonical unit locator. Checkpoint berisi task key, status, output refs/hashes, covered unit locators, limitations, model/config IDs dan timing. Commit output yang valid dan checkpoint secara atomik sebelum ack; redelivery memakai checkpoint sama. Assembly membaca hasil halaman yang selesai dan mempertahankan original page IDs/order, header/unit/caption/relasi tabel lintas halaman. Jika relasi lintas halaman belum dapat dirakit pasti, tandai relation/container partially_parsed, tampilkan fragmen dan jangan mengklaim tuple/coverage lengkap.

Inventaris root/file dan container/page dibuat dari metadata original, tidak hanya hasil parser yang sukses. Native DOCX/XLSX tetap mempertahankan part/sheet/container yang belum selesai. Stage terputus membuat unit terkait unresolved/failed dengan alasan; unit tidak hilang. Parent hanya fully parsed jika seluruh unit yang diperlukan dalam dukungan yang dinyatakan selesai. SourceUnit parse_status dan reason codes tetap mengikuti Bagian 8.2/9.4.

OCR tidak dijalankan pada seluruh PDF digital secara default. Deteksi kebutuhan OCR per page/region dari keberadaan/keterbacaan teks native, glyph penting dan visual yang belum terwakili; catat rule/method keputusan. Teks native ada tidak berarti digit pasti benar. Region diagram boleh langsung vision sesuai kontrak dan biaya. Model artifacts diprefetch saat build/setup terkontrol dan dicek checksum; runtime parser tidak mengunduh executable/weights dari input mahasiswa.

Admission control berlaku sebelum memulai subprocess/container. `PARSER_HEAVY_MAX_PER_NODE=1`; `PARSER_NODE_MEMORY_BUDGET_BYTES=8589934592` adalah budget khusus pekerjaan parser awal. Setiap task mereservasi memory_limit_bytes profilnya lewat lease node yang tersentral pada coordinator worker/Redis dengan operasi atomik dan fencing token. Jumlah reservasi aktif tidak boleh melebihi budget. Heavy task memakai slot heavy sekaligus budget; native task juga mereservasi memori. Reservation bukan pengukuran konsumsi aktual dan tidak menggantikan cgroup/container hard limit.

Worker memverifikasi budget parser + resource reservation service lain + headroom OS muat pada alokasi host/container deployment yang dicatat. API/database/worker bukan parser tidak dianggap gratis. Kekurangan kapasitas membuat parser queue blocked dengan parser_capacity_unavailable dan pesan operasi yang aman; seluruh aplikasi manual tetap ready. Setelah kapasitas pulih, resume dalam lease/version yang sama. Jangan menaikkan memory/concurrency otomatis. Lease node diperpanjang heartbeat, dibersihkan setelah child process terkonfirmasi berhenti; lease kedaluwarsa saja tidak membolehkan task baru menumpuk di atas child yang masih hidup.

Soft timeout task adalah minimum(120 detik, remaining active run seconds); hard timeout maksimum minimum(150 detik, remaining active run seconds), dengan kill cleanup segera ketika limit run tercapai. Batas aktif analisis tetap agregat 600 detik, termasuk parser/OCR/assembly/index/retrieval/inference dan retry. Queue wait tidak dihitung. Task paralel mereservasi jatah waktu aktif dari run; jumlah jatah dan waktu aktif yang sudah digunakan tidak boleh melampaui sisa limit. Tidak memberi setiap halaman jatah 600 detik baru. Draft konteks/spec memakai batas pekerjaan terkait; task parser tetap terbatas dan dana draft tidak berubah.

Setelah OOM/timeout, jangan retry isi/file yang sama tanpa batas dengan profil identik. Simpan checkpoint valid, reason dan keterbatasan, lalu lanjut elemen independen bila masih dalam limit. `parser_memory_limit` dipakai hanya bila cgroup/OS mengonfirmasi batas memori; exit code ambigu menjadi parser_failed. Time limit menghasilkan parser_timeout/processing_limit. Analisis dapat completed_with_limits. Penyimpanan terminal/check records boleh diselesaikan setelah penghentian komputasi; tidak melanjutkan analisis/inference baru setelah limit.

Benchmark CLI wajib: `align parser benchmark --profile PROFILE_ID --fixtures PATH --output PATH`. Output JSON/Markdown offline memuat exact versions/hardware, fixture hashes/format/page counts, cold/warm start, peak cgroup memory dan peak RSS dengan definisi pengukuran, waktu per stage/file/page, failure/timeout/OOM count, anchor/table/header/unit checks, dan batas dukungan. Tabel runtime paper bukan benchmark ALIGN. Sediakan fixture kecil dan stress hingga batas penerimaan; laporkan limit yang tercapai, tidak perlu menjanjikan seluruh 100 halaman scan selesai dalam 600 detik. Live profile hanya verified bila hard resource enforcement/checkpoint/anchor invariants lulus dan batas aktual dilaporkan; kemampuan dokumen penuh dilaporkan terpisah dari kebenaran guardrail.

## 9. Representasi hasil check dan taxonomy findings

### 9.1 Dimensi yang dipisahkan

| Field | Enum | Arti |
|---|---|---|
| `applicability_status` | `applicable`, `not_applicable`, `unresolved` | Apakah kewajiban berlaku |
| `source_coverage_status` | `sufficient_for_check`, `partial`, `unreadable`, `unresolved` | Apakah inspeksi memadai untuk kesimpulan check ini |
| `evidence_relationship` | `supported`, `partial`, `not_found`, `conflicting`, `unclear` | Hubungan bukti teramati terhadap tuntutan |
| `check_status` | `no_issue_detected`, `finding_candidate`, `unassessable`, atau null | Hasil teknis, tanpa skor |

Jika not_applicable, `check_status=null`, alasan wajib dan tidak dihitung sebagai keberhasilan pemeriksaan. Applicability unresolved menghasilkan unassessable. `no_issue_detected` bukan penilaian bahwa pekerjaan benar sepenuhnya. UI menggunakan label "Tidak ditemukan masalah dalam batas pemeriksaan ini".

Setiap ElementCheck menyimpan evidence IDs, observasi sumber, interpretasi, coverage record, reason codes, numeric check IDs jika ada, candidate IDs, serta run/config version. Elemen tanpa finding tetap terlihat dosen. Observasi dicatat sebelum narasi kritik dan tidak ditulis ulang agar sesuai finding sebelumnya.

### 9.2 Tiga kategori utama

| Enum | Kondisi wajib | Larangan |
|---|---|---|
| `missing_requirement` | Requirement mandatory yang berlaku tidak ditemukan setelah negative gate lolos | Jangan gunakan hanya karena heading/kata kunci tidak ada |
| `rubric_coverage_gap` | Observable obligation pada descriptor belum tercakup; terdapat dukungan inspeksi untuk klaim gap | Jangan mengubah penilaian kualitas holistik menjadi checklist yang tidak disahkan |
| `numerical_inconsistency` | Dua/lebih nilai sepadan atau hasil operasi resmi berbeda melampaui toleransi | Jangan membandingkan konteks, unit, definisi/populasi yang berbeda |

Satu masalah satu kategori utama. Prioritas deduplikasi: numerical inconsistency bila masalahnya selisih besaran; missing requirement bila kewajiban atomik eksplisit mandatory tidak ditemukan; rubric coverage gap bila kewajiban descriptor yang diperiksa belum tercakup dan bukan masalah explicit requirement yang sama. Beberapa masalah berbeda pada satu criterion tetap dapat memiliki kategori berbeda.

`dedup_key`:

- Nonnumerik: `(submission_version_id, obligation_key, issue_signature)`; default signature `obligation_not_observed` atau `descriptor_part_not_covered:<sub_obligation_key>` dari registry yang disahkan.
- Numerik: `(submission_version_id, numeric_policy_version_id, canonical_context_hash, sorted_operand_evidence_ids, comparison_mode)`.

Merge otomatis hanya jika key identik atau obligation alias telah disahkan. Jangan menggabungkan dua masalah berdasarkan kemiripan kalimat model saja. Gabungkan evidence IDs, element IDs, criterion IDs dan requirement IDs; simpan lineage setiap kandidat. Satu masalah tidak digandakan karena dua query atau dua model menghasilkannya.

### 9.3 Kontrak FindingCandidate

Field wajib: `id`, `analysis_run_id`, `submission_version_id`, `primary_category`, `primary_criterion_id`, `criterion_ids`, `element_ids`, `requirement_ids`, `obligation_key`, `issue_signature`, `claim`, `evidence_ids`, `coverage_record_ids`, `numeric_check_id` nullable, `technical_validation`, `semantic_support`, `limitations`, `created_at`.

`technical_validation=valid|invalid`; kandidat invalid disimpan sebagai internal diagnostic dan tidak masuk daftar finding yang dapat diterima sebelum diperbaiki. `semantic_support=supported|unsupported|insufficient|not_required_deterministic`. Ini bukan probabilitas. Kandidat supported tetap memerlukan dosen; kesepakatan model bukan gold truth.

Absence boleh mempunyai `evidence_ids=[]`, tetapi wajib menaut requirement/descriptor resmi dan coverage record exhaustive. Jangan mengarang kutipan tentang sesuatu yang tidak ada. Finding numerik wajib menaut semua operand dan konteksnya.

### 9.4 Reason code registry

| Reason code | Pemakaian |
|---|---|
| `unreadable_relevant_region` | Region kompatibel/berpotensi menentukan tidak terbaca |
| `incomplete_source_inspection` | Inspeksi yang diperlukan belum selesai |
| `ambiguous_descriptor` | Kewajiban tidak cukup operasional |
| `unresolved_applicability` | Kondisi berlaku belum terverifikasi |
| `dependency_unresolved` | Prasyarat check belum selesai |
| `ambiguous_numeric_context` | Context tuple belum cukup sepadan |
| `unverified_numeric_source` | Digit/nilai sumber belum cukup dapat dipercaya |
| `unavailable_or_unverified_formula_cache` | Cache formula tidak diketahui keandalannya |
| `ambiguous_number_format` | Locale/format ambigu |
| `model_response_invalid` | Schema, refusal, truncated output, atau kontrak gagal setelah batas retry |
| `unsupported_element` | Struktur sumber belum didukung |
| `parser_failed` | Parser crash atau file gagal dibaca tanpa bukti penyebab resource tertentu |
| `parser_memory_limit` | Batas memori tercapai dan dikonfirmasi cgroup/OS; bukan tebakan dari exit code saja |
| `parser_timeout` | Task parser mencapai deadline yang tercatat |
| `parser_capacity_unavailable` | Kapasitas profil yang diizinkan belum tersedia; tidak mengganti backend diam-diam |
| `grounding_contract_unmet` | Bentuk dukungan yang diwajibkan belum terpenuhi |
| `human_judgment_required` | Elemen human_only; perlu keputusan dosen |
| `batch_element_omitted` | Output satu elemen dalam batch hilang/invalid; tidak dihitung telah diinspeksi |
| `budget_limit` | Panggilan/ekspansi yang diperlukan tidak boleh dibiayai |
| `processing_limit` | Batas waktu/token/call/input menghentikan pekerjaan |
| `semantic_support_insufficient` | Klaim belum didukung sumber yang cukup |
| `source_version_changed` | Aktif source/config berubah; hasil bukan untuk versi aktif |

Satu check boleh memiliki beberapa reason. UI menyebut batas spesifik dan pertanyaan review, tanpa memperlakukan unassessable sebagai kesalahan mahasiswa.

### 9.5 Observasi, support bindings dan GroundingEvaluation

Output interpret_evidence/verify_claim per elemen memakai schema tertutup dan schema_version=1. Server mengisi element_check_id/coverage/numeric references resmi setelah membership validation. Model hanya dapat mengusulkan IDs yang sudah diberikan di scope call.

- `observations`: array objek `{observation_code, evidence_ids, text}`; code unik dalam output elemen, evidence_ids nonkosong dari submission yang sama, text maksimal 2.000 karakter. Ini deskripsi isi, bukan penulisan ulang kritik sebelumnya.
- `support_bindings`: array `{relationship_code, role, observation_codes, evidence_ids, quotes}`. relationship_code null untuk anchored_content dan code sah untuk linked_relationship; role dari registry Bagian 7.6. observation_codes/evidence_ids nonkosong dan merujuk data yang relevan dalam output/scope. `quotes` boleh kosong; setiap quote berbentuk `{evidence_id,start,end,text}`, indeks code point 0-based/end-exclusive atas content_original teks evidence. Quote tidak diizinkan untuk evidence tanpa teks literal; jangan memakai hasil tebakan vision sebagai kutipan native.
- `relationship_observations`: array `{relationship_code,text}`; setiap relasi wajib terwakili pada linked_relationship, termasuk qualifier resmi yang nonnull. Bukti yang sama boleh muncul pada beberapa binding.
- `proposed_evidence_relationship`: enum supported/partial/not_found/conflicting/unclear; merupakan usulan yang masih melalui gate.
- `limitations` dan `proposed_reason_codes`: array typed registry yang relevan; tidak menerima check_status/final publication dari model sebagai instruksi aplikasi.

Mode anchored_content memerlukan binding role content untuk keputusan berbasis bukti positif. Mode linked_relationship memerlukan seluruh roles setiap relasi untuk klaim dukungan hubungan lengkap; ketika bagian yang dicari tidak ditemukan, keluaran boleh partial/not_found dengan bindings yang tersedia. Gate absence tetap menuntut coverage lengkap sebelum finding; binding yang kosong tidak dapat dianggap semua roles sudah terpenuhi. Nilai numeric dari model tidak menjadi hasil resmi; comparable_numeric merujuk records engine setelah verifikasi sumber.

`GroundingEvaluation` adalah record server: element_check_id unik, contract_schema_version, mode, status `supported|insufficient|human_required|not_run`, decision_basis `positive_support|coverage_absence|numeric_comparison|null`, mechanical_validation_json, semantic_support `supported|insufficient|not_required|not_run`, coverage_record_id nullable, numeric_check_ids, limitations dan validator manifest IDs. supported berarti bentuk dukungan keputusan check itu lolos dalam batas yang dinyatakan, bukan nilai kualitas tugas. Absence memakai coverage_absence dan coverage_record_id; numeric memakai numeric_comparison; human_only memakai human_required dengan decision_basis=null dan check_status=unassessable ketika applicable.

mechanical_validation_json mempunyai field boolean/schema state untuk membership, anchors, quote_matches, required_roles, coverage_gate, numeric_gate; field yang tidak berlaku null, bukan false/true palsu. required_roles=true/false menguji kelengkapan dukungan positif; pada klaim ketiadaan penuh nilai ini null dan coverage_gate wajib true. Gap parsial memvalidasi bindings yang diklaim ada, sementara bagian yang dinyatakan tidak ditemukan memerlukan coverage. Tidak mengubah roles yang hilang menjadi seolah-olah hadir. semantic_support=not_required hanya untuk bagian yang dituntaskan aturan deterministik yang sesuai, bukan jalan pintas menghindari verifikasi hubungan semantik. Unassessable dapat menyimpan observasi/bindings parsial untuk ditelaah dosen. Candidate generation membaca GroundingEvaluation bersama applicability/coverage/relationship/numeric gates; nilai dari model tidak dapat melewati kode ini.

## 10. Executor dan evidence sufficiency gate

Urutan implementasi wajib:

1. Authorize objek dan worker capability; muat immutable snapshots serta fingerprint konfigurasi.
2. Verifikasi manifest final dan hash file; peroleh admission lease profil parser; lakukan parsing/inventaris dengan checkpoint dan limit Bagian 8.5.
3. Bentuk evidence registry/chunks dan indexing dalam scope submission.
4. Topological sort elemen; resolve applicability berdasarkan rule/fact resmi.
5. Jalankan operasi deterministik yang sesuai: struktur, keanggotaan sumber, label/angka native. Deterministic-first berarti gunakan operasi yang memang menyelesaikan check; tidak berarti keyword matching cukup untuk kewajiban semantik.
6. Jika akses struktur belum memadai, lakukan hybrid retrieval/expansion. Catat truncation dan sumber yang belum dibuka.
7. Ekstrak observasi tanpa prior finding narrative, kemudian interpretasikan hubungan yang diperlukan melalui routing/batching policy release. Bentuk support bindings dan periksa grounding contract per elemen; human_only langsung meminta review dosen tanpa keputusan AI substantif.
8. Jika hendak menyimpulkan absence/gap, selesaikan scan kompatibel menyeluruh. Jika berhenti, tandai unassessable.
9. Untuk numerik otomatis, resolve tuple dan source quality lalu jalankan Decimal engine. Numeric human_only tetap menunggu dosen tanpa keputusan otomatis. Jangan memanggil model untuk menghitung hasil resmi.
10. Bentuk kandidat hanya bila semua kondisi kategorinya terpenuhi. Jalankan schema/membership/anchor/version/arithmetic/coverage/grounding validation; kelengkapan peran bukti tidak dapat digantikan confidence model.
11. Verifikasi klaim semantik terhadap sumber; tahan kandidat unsupported/insufficient. Observasi dan batas tetap tersedia pada element record.
12. Deduplikasi dan simpan seluruh ElementCheck serta kandidat valid. Job dapat `completed_with_limits` dan tetap siap ditelaah.

Gate keputusan kode:

| Keadaan | Hasil otomatis yang diizinkan |
|---|---|
| not_applicable | Simpan tanpa check_status |
| Applicability/dependency unresolved | unassessable |
| Grounding mode human_only | unassessable dengan human_judgment_required; dosen tetap dapat menelaah manual |
| Grounding contract belum terpenuhi | Lanjut inspeksi/repair yang diizinkan atau unassessable; tidak ada keputusan substantif otomatis |
| Dukungan positif cukup dan tidak ada konflik penentu yang belum terselesaikan | no_issue_detected dalam batas check |
| Negative claim + inspection lengkap + sumber kompatibel cukup terbaca + dukungan klaim lolos | finding_candidate |
| Angka sepadan/grounded + selisih melewati batas + validasi hitungan lolos | finding_candidate numerik |
| Angka konsisten dengan comparability/grounding valid | no_issue_detected untuk check numerik itu |
| Source/inspection/meaning menentukan tetapi belum jelas | unassessable |
| Tidak ada dana/call/time untuk jalur yang diperlukan | unassessable pada elemen terkait; lanjut elemen independen yang bisa selesai |

Tidak perlu membaca seluruh bundle untuk setiap positive conclusion bila dukungan check sudah cukup; negative conclusion memerlukan policy yang lebih luas. Banyaknya finding bukan ukuran kualitas pekerjaan, keberhasilan AI, atau kemampuan mahasiswa.

## 11. Retrieval dan context management

### 11.1 Scope dan chunking

Konteks normatif adalah approved course fields yang relevan, instruksi asli, criterion/descriptor, requirement, specification dan NumericPolicy. Backend memuatnya langsung menurut ID/versi. Tidak menyerahkan bagian normatif wajib pada similarity search.

Korpus evidence hanya EvidenceItem pada SubmissionVersion yang sedang dianalisis. Filter `course_id`, `assignment_id`, `owner`, `submission_version_id`, purpose, source hash dan access capability diterapkan sebelum ranking. Namespace konteks, submission lain, draft file yang tidak masuk manifest, komentar review, gold label dan target findings tidak pernah menjadi kandidat.

Jangan memotong tuntutan normatif wajib secara diam-diam agar muat prompt. Konteks resmi dikemas deterministik dari instruksi asli dan descriptor/requirement check terkait; setiap excerpt menaut span sumber dan seluruh kondisi yang mengikat elemen harus ikut. Jika paket minimum yang mempertahankan tuntutan melampaui context/call budget, catat processing_limit dan sediakan review manual, bukan meminta model memeriksa instruksi yang berubah maknanya.

Narasi: target 600 token, overlap 80; jangan memotong unit/header/angka dari definisinya. Gunakan tokenizer yang sesuai manifest dan simpan token count. Tabel besar dibagi per baris dengan header/unit/caption berulang. Diagram memiliki caption/context dan link gambar; embedding teks diagram bukan pengganti membaca region yang diperlukan.

### 11.2 Konfigurasi awal yang eksplisit

| Komponen | Default MVP |
|---|---|
| Leksikal | BM25 melalui rank_bm25; lowercase Unicode, normalisasi whitespace, tokenisasi kata/angka yang mempertahankan titik/koma angka; tanpa stemming default |
| Embedding | `text-embedding-3-small`, dimensi 1536; simpan model/dimensi/config hash; reindex pada perubahan |
| Vector | pgvector exact cosine distance pada himpunan bundle berizin; tidak membuat HNSW/IVFFlat sebagai default |
| Kandidat | Top 20 BM25 dan top 20 vector; kosong pada satu kanal tetap diizinkan |
| Fusion | RRF, rank mulai 1, konstanta 60; kanal yang tidak memuat item memberi kontribusi nol |
| Rerank | Efficient model pada maksimum 20 kandidat hasil fusion, menggunakan expected evidence dan IDs; bukan menilai pemenuhan |
| Paket awal | Maksimum 6 chunks ditambah parent/context yang diperlukan, dibatasi 8.000 input token per panggilan interpretasi |
| Query tambahan | Maksimum 2 reformulasi per elemen; semua masuk batas call/token job |
| Negative fallback | Scan seluruh unit kompatibel dalam batch yang menjaga struktur; jika budget tidak cukup hasil unassessable |

RRF: `sum(1 / (60 + rank_channel(item)))`. Tie dipecah deterministik dengan source order lalu evidence ID. Jangan menyebut `ts_rank` PostgreSQL sebagai BM25.

Bukti retrieval diseleksi untuk cakupan makna elemen, bukan semata mengambil enam teks teratas. Parent expansion wajib ketika kutipan kehilangan unit/header/relasi. Catat paket final yang benar-benar dikirim; "retrieved" berbeda dari "inspected". Cache key mengikat source/config/owner/version; tidak menggunakan cache milik mahasiswa lain hanya karena hash file sama.

Fallback saat embedding/provider gagal: direct structural access dan BM25 masih boleh berjalan, dengan status kanal gagal. Jangan menandai retrieval seolah hybrid lengkap. Jika dukungan positif/negative inspection tetap memadai secara sah, check dapat selesai; jika tidak, unassessable.

## 12. Numeric engine

### 12.1 Tuple dan grounding

Setiap NumericObservation memuat `(metric, entity, period, scenario, population, definition, assumptions, unit, value, source)`. Nilai konteks yang tidak relevan boleh null hanya jika NumericPolicy menyatakan field itu tidak relevan; field yang relevan tetapi tidak diketahui membuat comparability unresolved.

`source_quality=native_literal|ocr_verified|lecturer_confirmed|unverified`. Angka native dari teks/sel literal dengan locator valid dapat menjadi operand. Digit OCR/vision yang menentukan finding memerlukan pemeriksaan ulang pada crop sumber (OCR pembacaan kedua yang sesuai atau vision/verifikasi dosen), dengan hasil disimpan; self-reported confidence tidak cukup. Dua pembacaan yang sesuai tetap bukan jaminan mutlak, sehingga dosen tetap melihat sumber. Jika pembacaan tidak sepakat atau budget kurang, unverified dan tidak menghasilkan flag numerik.

Formula XLSX serta cached value disimpan terpisah. Cache tidak otomatis verified. Default MVP tidak menganggap formula cache sebagai operand pasti kecuali dosen telah mengonfirmasi nilai sumber pada review atau nilai dapat dihitung melalui operasi aplikasi resmi dengan operand terverifikasi. Dosen boleh menyelesaikan finding secara manual tanpa menulis ulang hasil AI sebagai benar.

### 12.2 NumericPolicy

Field wajib: `id`, `version`, `metric_definition`, `required_context_fields`, `number_locale`, `canonical_unit`, `allowed_conversions`, `comparison_mode`, `absolute_tolerance`, `relative_tolerance`, `rounding`, `operand_roles`, `approved_by`, `approved_at`.

- `comparison_mode=pairwise|sum_against_reported|ratio_against_reported`.
- Pairwise: dua observasi dengan tuple sepadan.
- Sum: daftar operand terverifikasi dalam unit/konteks yang disahkan dibandingkan dengan satu angka laporan. Operand dipilih melalui selector atau evidence ID yang tersimpan; simpan daftar lengkap dan hindari duplikasi operand.
- Ratio: numerator/denominator yang definisi serta unit outputnya disahkan dibandingkan angka laporan; denominator nol menghasilkan unassessable, bukan infinity.
- Semua operasi adalah implementasi kode terbatas. Jangan mengeksekusi formula dari workbook, melakukan parsing ekspresi Python, atau mengikuti external link.
- Toleransi absolut >= 0; relatif antara 0 dan 1, string desimal. Policy tanpa toleransi eksplisit belum dapat disahkan; dosen boleh mengesahkan kedua nilai 0 untuk kesamaan tepat.
- `rounding={"mode":"none","quantum":null}` adalah default. Jika tugas menetapkan pembulatan, gunakan `half_even` dengan quantum positif yang eksplisit dan simpan nilai sebelum/sesudah pembulatan. Jangan mengarang jumlah digit pembulatan.
- Konversi hanya aturan eksplisit yang cocok dimensional: persen ↔ rasio, ribu/juta ↔ satuan, dan satuan terdaftar. Tidak ada kurs mata uang eksternal. Growth percent dan percentage points berbeda.

### 12.3 Normalisasi dan perbandingan

Contoh payload policy pairwise sebelum metadata approval ditambahkan server:

```json
{
  "metric_definition": "Pendapatan bruto usaha yang sama sebelum pajak",
  "required_context_fields": ["metric", "entity", "period", "scenario", "definition", "unit"],
  "number_locale": "id-ID",
  "canonical_unit": "IDR",
  "allowed_conversions": [{"kind": "linear", "from": "IDR_million", "to": "IDR", "factor": "1000000"}],
  "comparison_mode": "pairwise",
  "absolute_tolerance": "1000",
  "relative_tolerance": "0.005",
  "rounding": {"mode": "none", "quantum": null},
  "operand_roles": {"left": "angka pada laporan", "right": "angka pada spreadsheet"}
}
```

Conversion kind MVP hanya linear dengan factor desimal positif, unit dari registry satuan yang disahkan, serta dimensi yang sama. Prefix rupiah/ribu/juta tidak mengubah currency. Cross-currency conversion dan nilai tukar tidak diotomatisasi pada MVP. `required_context_fields` hanya boleh mengambil nama field tuple pada Bagian 12.1, tidak mencakup value/source; metric, entity, definition dan unit selalu wajib. operand_roles menjelaskan peran yang diizinkan, bukan selector kode bebas; evidence IDs aktual ditentukan executor dan divalidasi sebelum hitungan.

Parser angka mempertahankan literal, locale, tanda, scaling dan unit. `1,234` ditafsirkan hanya bila locale sumber/policy jelas; bukti format campuran yang tidak terselesaikan → ambiguous_number_format. Tolak NaN, infinity, scientific value yang melebihi batas representasi, dan tipe tidak sah.

Setelah unit/konteks dinormalisasi, untuk `x` dan `y`:

```text
difference = abs(x - y)
allowed_difference = max(epsilon_abs, epsilon_rel * max(abs(x), abs(y)))
is_inconsistent = difference > allowed_difference
```

Tepat pada batas dianggap konsisten. Gunakan Python Decimal dengan context precision 34; tolak input yang tidak dapat direpresentasikan sesuai batas policy, tanpa silent truncation. Simpan seluruh operand, operasi, normalized tuple, difference, allowed_difference, policy version dan rule-engine version.

Kasus wajib: `0,25` vs `25%` dapat setara; Rp125 juta vs Rp125.000.000 setara; accuracy vs precision tidak sepadan; gross vs net tidak sepadan; periode/skenario berbeda tidak sepadan. Untuk `x=100`, `y=107`, absolute=5 dan relative=0.05, batas 5.35 dan selisih 7 → finding candidate. Untuk contoh pendapatan 125.000.000 vs 150.000.000, absolute=1000 dan relative=0.005, batas 750.000 → kandidat selisih 25.000.000.

## 13. Adapter model dan verifikasi

### 13.1 Peran model dan manifest

| Peran | Kegunaan | Pemilihan |
|---|---|---|
| `efficient` | Field sederhana, rerank, draft feedback dari keputusan | Model yang mendukung schema terstruktur dan lolos smoke test |
| `complex` | Operasionalisasi descriptor kompleks, relasi lintas bukti, ambiguous semantics | Boleh dipanggil langsung, tanpa wajib melalui efficient |
| `vision` | Region visual/handwriting/diagram dan verifikasi digit relevan | Input gambar + output terstruktur; dapat memakai model yang sama dengan complex jika kapabel |
| `embedding` | Embedding chunk/query | Default pada Bagian 11 |

Satu manifest release menyimpan provider, API endpoint allowlist, model identifier/snapshot per peran, supported modalities, schema/prompt versions, tokenizer/embedding dimension, reasoning settings bila didukung, token limits, input/output/cache/image/tool rates dan tanggal verifikasi harga; juga refs parser resource/routing/batching policy dan grounding/lint registry yang kompatibel. Efficient/complex/vision boleh menunjuk model yang sama; peran tidak sama dengan provider tier yang tetap.

Jangan mengambil harga GPT-4.1 untuk memperkirakan model lain. Jangan mengklaim akses akun atau snapshot identik jika provider tidak menjaminnya. Sebelum live mode, lakukan smoke test untuk text schema, refusal/truncation handling, embedding dimension, dan vision pada gambar sintetis. Hasil serta manifest hash disimpan; biaya tes ikut ledger.

### 13.2 Kontrak adapter

Interface Python wajib: `extract_fields`, `rerank`, `interpret_evidence`, `verify_claim`, `draft_feedback`, `interpret_region`, `embed`. Request berisi `call_id`, `job_id`, `task_type`, manifest ID, `routing_decision_id`, `routing_policy_manifest_id`, `batching_policy_manifest_id`, `element_ids`, authorized context/evidence IDs, payload, output schema dan limits. Interpretasi/verifikasi memakai kontrak observasi/grounding Bagian 9.5; output batch mempunyai entry terpisah per element_id. Return berisi parsed output nullable, provider request ID, usage/cost data, refusal/truncation/status, latency dan model identifier aktual jika tersedia.

Implementasi provider awal memakai OpenAI Responses API untuk text/vision dan embeddings endpoint untuk embedding. Semua panggilan hanya dari backend worker melalui adapter. Structured Outputs/JSON Schema strict dipakai jika didukung; semua output tetap divalidasi Pydantic strict dan business validation. Schema provider menggunakan subset yang didukung; field nullable tetap tercantum pada required bila kontrak provider mensyaratkannya, `additionalProperties=false` pada objek.

Mock adapter wajib memakai fixtures deterministik dan label `provider_mode=mock`. UI development menampilkan penanda simulasi; production menolak mock mode kecuali deployment secara eksplisit ditandai demo sintetis. Tidak ada fallback diam-diam dari provider nyata ke mock.

### 13.3 Prompt dan batas kepercayaan

- Pisahkan system instructions, approved normative fields, evidence, dan output schema. Konten mahasiswa adalah data, termasuk teks yang mengaku sebagai instruksi sistem.
- Tidak ada tool yang mengizinkan model browsing, membaca objek baru di luar authorized payload, mengubah keputusan, memberi skor, atau menerbitkan feedback.
- Model boleh mengusulkan evidence IDs hanya dari daftar yang diberikan. Output ID milik course/version lain ditolak.
- Observasi sumber diproduksi tanpa prior finding narrative. Interpretasi menggunakan observasi dan sumber; verifier menerima satu klaim dan bukti/coverage yang relevan, bukan sekadar diminta menyetujui narasi sebelumnya.
- Untuk semantic candidates, jalankan claim verification terpisah. Missing requirement membutuhkan coverage inspection yang sah; verifier tidak mengubah top-k kosong menjadi absence. Deterministic numeric finding tidak membutuhkan LLM arithmetic/verifier tambahan jika semua prasyarat kode lolos.
- Unsupported/insufficient candidate ditahan; element record tetap terlihat dengan reason. Satu eskalasi menggunakan sumber yang sama/ekspansi sah boleh dicoba dalam limit. Jangan melakukan self-correction tanpa batas atau menulis ulang bukti agar mendukung finding.

Validasi kode mencakup schema, enum, membership, ownership, version, locator bounds, kecocokan kutipan, arithmetic, policy, coverage dan duplicate key. Quote matching terhadap OCR membuktikan kecocokan ke hasil OCR, bukan otomatis kebenaran ke gambar asli. Dukungan semantik dan validitas anchor adalah dua pemeriksaan berbeda.

### 13.4 Batas awal job analisis

Default maksimum per AnalysisRun: 16 panggilan text/vision gabungan, termasuk rerank/verifier/repair; maksimum 4 di antaranya vision; agregat input teks 40.000 token dan output teks 8.000 token; embedding maksimum 80.000 token; active processing time 600 detik. Gambar ditagih menurut provider dan tetap masuk budget USD. Paket per panggilan juga dibatasi context limit model dan 8.000 input teks untuk interpretasi.

Budget awal per run maksimum US$0.20; feedback draft maksimum US$0.03; context/spec draft masing-masing maksimum US$0.05. Ini batas pengeluaran, bukan perkiraan biaya pasti. Limit dikonfigurasi operator dan dicatat manifest/job; tidak boleh dinaikkan otomatis oleh model. Bila kandidat panggilan minimum yang diperlukan melampaui sisa limit, jangan memanggil provider dan catat keterbatasan. Banyak elemen/berkas dapat menghasilkan completed_with_limits secara sah.

Job criteria-draft memakai batas biaya SPEC_DRAFT_BUDGET_USD. Context/criteria/spec draft masing-masing maksimal 3 text/vision calls termasuk repair, 20.000 input teks agregat, 4.000 output dan maksimal 2 vision calls. Feedback draft maksimal 2 text calls termasuk repair, 12.000 input teks, 3.000 output, tanpa vision/retrieval baru; ia memakai bukti yang sudah dipilih dosen. Batas token/provider yang lebih kecil selalu didahulukan. Semua jenis draft mempunyai fallback editor manual.

Keputusan semantik default memakai batch 1. Batching 3/5 hanya diizinkan oleh BatchingPolicyVersion tervalidasi pada release yang sama, sesuai Bagian 13.6/21.4; maksimum hard cap 5 elemen independen dengan sumber yang sama. Output, dukungan dan coverage tetap per elemen; elemen yang hilang dari respons tidak dihitung terinspeksi. Observasi sumber bersama tanpa prior criticism boleh digunakan ulang di scope/version yang sama. Limit 16 call/US$0,20/600 detik tidak dinaikkan untuk mengakomodasi batch 1; hasil dengan batas dan review manual tetap sah.

Retry transport maksimum 2 per tahap dengan backoff 2 dan 8 detik plus jitter; retry/repair juga menghabiskan call/token/biaya limit. Schema repair maksimum 1; semantic escalation maksimum 1 per elemen, tetap tunduk limit global. Refusal tidak diperbaiki dengan mengakali instruksi provider; catat response state dan lanjut review manual. Jangan menganggap retry provider gratis atau selalu idempotent.

### 13.5 RoutingPolicyVersion dan keputusan routing

RoutingPolicyVersion adalah payload immutable `method_manifests.kind=routing`, schema_version=1, policy_key, version_no, state `baseline|candidate|validated`, ordered rules, default action, dan validation_report_ref nullable. Baseline berikut wajib tersedia tanpa learned scorer/ensemble. Default bukan menyatakan model efficient selalu cukup; mapping role harus lolos capability smoke dan tidak mengubah batas judgement.

Router menerima task_type enum yang sama dengan interface adapter, serta feature bertipe: requires_visual, determining_source_unreadable, negative_claim_pending, coverage_complete, dependency_resolved, complex_semantics, cross_source_relation, schema_invalid, semantic_support_failed. Boolean yang belum diketahui harus null; null tidak dipaksakan false. Features berasal dari stage/registry/observasi yang dicatat dan membership-checked; model dapat mengusulkan sinyal semantik, bukan mengubah gate sumber atau memilih provider sendiri.

Rules memakai DSL all/any/not/eq/in dengan depth maksimal 3 dan maksimal 10 leaf, hanya field registry di atas dan task_type; tanpa kode/regex/path bebas. Action union: deterministic, retrieve_expand, repair_source, repair_schema, call_role, abstain. call_role memuat role efficient/complex/vision/embedding; action lain tidak memuat model/provider bebas. Task-level guard pada tabel ini didahulukan dari rules dan tidak dapat dimatikan oleh policy.

| Prioritas/keadaan | Aksi | reason_code routing |
|---|---|---|
| Akses/hash/version tidak sah | Hentikan; bukan model call | authorization_or_version_invalid |
| human_only, applicability/dependency belum resolved | Manual/unassessable sesuai domain; lanjut elemen independen | human_boundary atau dependency_pending |
| Operasi struktur/angka dapat diselesaikan fungsi registry | deterministic; tidak membeli call sebagai pengganti angka resmi | deterministic_available |
| Sumber penentu belum terbaca | repair_source dalam batas OCR/vision; bila gagal abstain | source_repair_required |
| Klaim negatif tertunda dan coverage belum cukup | retrieve_expand/semantic_scan yang diizinkan; bila limit abstain | source_inspection_required |
| Budget/call/time tidak cukup untuk aksi berikutnya | abstain; jangan memanggil provider | limit_prevents_call |
| Response schema invalid dan repair masih diizinkan | repair_schema satu kali dalam budget; refusal tidak diakali | schema_repair_required |
| interpret_region atau requires_visual=true | call_role vision pada region sah beserta konteks yang dibutuhkan | visual_source_required |
| interpret_evidence/verify_claim dengan hubungan lintas sumber atau semantik kompleks | call_role complex secara langsung diperbolehkan | complex_relationship |
| semantic_support_failed setelah interpretasi, sumber cukup, eskalasi belum dipakai | complex sekali atau abstain jika sudah pada role tersebut dan tidak ada jalur bermakna | semantic_escalation |
| extract_fields/rerank/draft_feedback sederhana dari input yang sah | call_role efficient | efficient_task_match |
| embed | call_role embedding | embedding_required |
| interpret_evidence/verify_claim lain dengan sumber cukup | baseline complex; efficient hanya melalui policy yang tervalidasi untuk task family tersebut | semantic_baseline |
| Kondisi tidak dikenali, feature penentu null, atau konflik belum terselesaikan | abstain/request human review; tidak menebak operasi | unresolved_route |

Draft feedback tidak mengambil sumber baru; jika membutuhkan sumber/perubahan review, kembali ke alur review, bukan route retrieval dari generator. `source_repair_required` tidak memaksa membeli vision ketika OCR/source manual lebih sesuai; pemilihan repair dibatasi modalitas/policy dan budget, dan sumber rusak yang tetap belum jelas tidak diperbaiki dengan jawaban model yang lebih yakin.

RoutingDecision record: ID, job/run/element refs, routing policy manifest ID, task_type, input feature snapshot, guards evaluated, chosen action/role, reason_code, source/coverage refs, remaining limits snapshot, call IDs, outcome dan timing/cost refs. Decision tanpa model call tetap dicatat. Action cost reserve dilakukan tepat sebelum call; router tidak menganggap reservation sudah ada hanya karena memutuskan role.

Self-reported confidence tidak menjadi dasar tunggal penerimaan atau eskalasi. Anchor, coverage dan numeric gate selalu berlaku pada semua role. Semantic escalation maksimum satu per elemen dan schema repair/transport retry sesuai Bagian 13.4; tidak ada siklus efficient→complex→efficient. Penyesuaian model identifier/rule/action/schema/prompt/role menimbulkan manifest/fingerprint baru, bukan hot-swap pada run lama.

Learned reliability scorer/router, fine-tuning, ensemble/voting, dan semantic cache lintas submission tidak dibutuhkan untuk MVP. Contoh prompt sintetis hanya boleh untuk format/interpretasi kontrak yang telah disahkan, disimpan dalam manifest prompt dan diuji pada development; tidak memasukkan materi/solusi ideal baru atau contoh mahasiswa lain. Gold penelitian dan koreksi review runtime tidak masuk inferensi produksi.

### 13.6 BatchingPolicyVersion dan grouping yang sah

BatchingPolicyVersion adalah payload immutable `method_manifests.kind=batching`, schema_version=1, policy_key, version_no, state `baseline|candidate|validated`, max_elements dari 1/3/5, compatible model/prompt manifest IDs, schema bundle hash, grounding registry version, allowed task families, source-sharing rule, validation_report_ref nullable. Baseline state=baseline/max_elements=1 wajib tersedia; candidate tidak dapat diaktifkan pada release live. validated/max_elements>1 memerlukan gate Bagian 21.4. Hard cap deployment `BATCH_MAX_ELEMENTS=5` tidak dapat dinaikkan oleh manifest/model.

Task family berbentuk task_type interpret_evidence/verify_claim dan target_kind/grounding mode/predicate set/source_modality_scope yang tepat. source_modality_scope adalah enum native_text/ocr_text/mixed_visual/spreadsheet, ditentukan dari parser provenance dan kebutuhan sumber check; bila beberapa modalitas menentukan, gunakan mixed_visual. Whitelist keluarga di policy, bukan semantic category bebas yang diciptakan model. Extraction observasi sumber bersama boleh satu kali di scope/version yang sama; hasilnya tidak membawa kritik atau keputusan sebelumnya. Rerank/embedding batch teknis tidak menggabungkan keputusan elemen dan tetap mengikuti limits interface.

Group semantik hanya sah jika semua anggota mempunyai namespace owner/submission/assignment/specification/release yang sama, source evidence ID set yang sama, task_type/role/model/prompt/schema/grounding family yang cocok, dan dependensi sudah resolved. Jangan menggabungkan elemen yang saling bergantung, human_only, atau hasil Decimal final dalam batch judge. Urutan grup stabil menurut element_code; pengujian urutan boleh pada evaluator, bukan perubahan acak produksi yang tidak dicatat.

Request mengirim element_ids dan kontrak setiap elemen; response mempunyai entry per element_id dengan observasi/bindings/limitations masing-masing. ID tambahan/duplikat/asing ditolak. Bila sebagian entry hilang/invalid, hanya entry lain yang lulus validasi boleh dipakai; entry hilang mendapat batch_element_omitted dan source unit tidak dihitung inspected untuk pertanyaan tersebut. Perbaikan boleh melalui call tersisa yang diizinkan, tidak mengulang seluruh batch tanpa batas.

Coverage record, grounding evaluation, semantic verification, reason dan candidate tetap per elemen. Satu respons tidak membenarkan menandai seluruh file visited untuk semua anggota. Ledger physical call disimpan sekali; atribusi biaya ke elemen boleh berupa allocation turunan dengan metode eksplisit dan tidak menghitung call yang sama dua kali pada biaya run.

Pemilihan batch 1 adalah fallback konfigurasi sebelum run, bukan penggantian policy diam-diam setelah run dimulai. Dalam run, policy boleh mengeksekusi group lebih kecil termasuk singleton sesuai aturan max_elements yang sama; ini bukan manifest baru. Jika kandidat policy belum memiliki bukti cukup, tetap gunakan baseline dan tampilkan completed_with_limits ketika jatah call habis. Tidak ada jaminan batch 1 selalu lebih akurat atau batch 5 selalu lebih murah setelah repair/verifier dihitung.

## 14. Review dosen, feedback, dan publikasi

### 14.1 Review seluruh elemen

ReviewVersion dibuat dari SubmissionVersion dan opsional AnalysisRun tertentu. Jika AI gagal/tidak tersedia, backend membuat workspace dari seluruh elemen spesifikasi tanpa kandidat AI. Review manusia tidak membutuhkan analysis run yang sukses.

Setiap elemen memiliki ReviewElementDecision dengan `review_status=pending|reviewed` dan `resolution=observed_no_issue|confirmed_issue|not_applicable|unable_to_conclude`. Evidence manual dan catatan boleh ditambahkan. Resolution bukan skor. Dosen boleh mengoreksi applicability dengan alasan dan sumber/fact yang dicatat, tanpa mengubah spesifikasi tugas historis.

Setiap kandidat AI yang tampil memiliki aksi:

| Aksi internal | UI | Konsekuensi |
|---|---|---|
| `accept` | Konfirmasi | Klaim/evidence dikonfirmasi dosen, menjadi ReviewFinding yang dapat masuk feedback |
| `edit` | Sunting dan konfirmasi | Klaim/kategori/tautan diperbaiki dosen, sumber asal kandidat dan perubahan tetap tercatat |
| `reject` | Tolak | Tidak masuk kritik feedback; kandidat dan keputusan tetap dalam audit internal |
| `defer` | Belum dapat disimpulkan | Tidak menjadi confirmed finding; limitation/reason wajib dan ditampilkan sesuai konteks publikasi |

Tombol "Periksa manual" membuka sumber dan membiarkan keputusan pending; itu bukan terminal decision. Sebelum review complete, dosen harus memilih terminal action atau defer. Menolak tidak mewajibkan esai alasan; reason code opsional cukup. Edit/accept yang mengubah konteks/angka harus tetap menyertakan sumber sah. Defer memerlukan alasan singkat.

Dosen dapat membuat manual finding dalam tiga kategori yang sama, dengan criterion/element/requirement yang sah, claim, evidence atau coverage attestation untuk absence, serta alasan. Manual finding ditandai `origin=lecturer`, bukan output AI. Backend memvalidasi membership/anchor; tidak mensyaratkan AI menyetujui judgment dosen. Manual finding numerik menyertakan operand/context/policy dan hasil Decimal jika perbandingannya dapat dihitung; bila dosen hanya memberi komentar tanpa besaran terverifikasi, catat sebagai catatan feedback, bukan numeric finding yang mengaku telah diverifikasi kode.

Dosen dapat memilih bukti dari file asli yang termasuk manifest, membuat anchor manual, atau menambahkan transkripsi dengan `origin=lecturer_transcription`. Tidak boleh memasukkan berkas mahasiswa lain. Bukti baru dari pengumpulan berikutnya memerlukan SubmissionVersion baru; jangan menyisipkannya ke versi lama melalui review.

Jika parser tidak menyediakan source unit yang diperlukan, dosen dapat membuat SourceUnit manual pada ParseArtifact sumber yang sama. Backend memvalidasi page/cell/part/image bounds terhadap original; origin dan keterbatasan parser lama tetap dicatat. Bila hanya whole-file anchor yang dapat diverifikasi, gunakan anchor `format=file` dengan source hash dan locator_note wajib, tampilkan sebagai bukti manual berlokasi kasar, dan jangan mengaku sebagai anchor region hasil ekstraksi AI.

`complete review` memerlukan semua elemen berstatus reviewed, seluruh kandidat yang terlihat telah diberi terminal action, dan setiap limitation yang masih menentukan dinyatakan eksplisit. `unable_to_conclude` adalah penyelesaian review yang sah, tidak memaksa dosen menyatakan ada/tidak ada masalah. Review dapat lengkap meskipun analisis parsial.

### 14.1.1 Linked selection dan review annotation revisions

Evidence workspace memakai dua panel yang telah ditetapkan, dengan selection state bersama: selected element_id, relationship_code/role nullable, evidence_id/source location nullable dan review_finding_id nullable. Memilih elemen membuka tuntutan/derivasi resmi, decision question, grounding expectation, coverage, observasi, interpretasi, kandidat dan keputusan yang sesuai. Memilih evidence membuka file/page/cell/region yang tepat dan menyorotnya. Memilih region yang sudah ditautkan menampilkan daftar elemen/findings terkait pada submission itu; region belum ditautkan dapat dipilih dosen sebagai bukti manual.

Untuk linked_relationship, setiap role memperlihatkan sumber yang mendukung dan akses ke konteks lain; satu sumber boleh dipakai pada beberapa role. Pisahkan tampilan observasi sumber, interpretasi model, limitation dan keputusan dosen secara tekstual. Penjelasan adalah alasan singkat yang dapat diperiksa, bukan hidden chain-of-thought dan bukan bukti bahwa model pasti benar. Click, keyboard focus/activation dan touch mempunyai fungsi setara hover; perubahan tab mobile mempertahankan selection state.

`ReviewAnnotation` adalah objek stable ID dalam ReviewVersion dengan kind span_selection/transcription/evidence_binding. Setiap perubahan membuat ReviewAnnotationRevision append-only, revision_no berurutan, operation upsert/remove, previous_revision_id nullable, author/time, source file/hash/anchor, original evidence_id nullable, corrected evidence_id nullable, target element/finding refs, relationship_code/role nullable, transcription nullable dan alasan. Pointer current_revision dapat berubah dalam transaksi review; payload revisi lama tidak diubah.

- Pemilihan/perbaikan span memakai original dengan anchor terverifikasi; bukan memindahkan bbox parser tanpa catatan asal.
- Koreksi transkripsi membuat EvidenceItem manual baru dengan origin lecturer_transcription yang menaut original source/hash dan annotation revision. content_original hasil parser, parse artifact, dan EvidenceItem lama tetap immutable.
- evidence_binding dapat menambah/mengubah/menghapus tautan aktif ke elemen/finding yang sah. remove menonaktifkan tautan di review revision baru, bukan menghapus sumber atau histori.
- Whole-file anchor manual coarse tetap diizinkan sesuai Bagian 8.3/14.1 dan diberi label. Jangan mengaku koreksi coarse telah diverifikasi sebagai region AI presisi.

Payload upsert lengkap sesuai kind: span_selection memerlukan file/hash/anchor; transcription memerlukan semua lokasi itu serta transcription nonkosong; evidence_binding memerlukan evidence dan target refs yang sah serta relationship_code/role jika dipakai. operation remove menyalin referensi sumber/revisi sebelumnya sebagai histori dan menghapus efek tautan aktifnya; tidak memerlukan anchor rekaan. alasan wajib untuk koreksi transkripsi, perubahan lokasi dan remove, maksimum 2.000 karakter. Text transcription maksimum 20.000 karakter dan tidak mengubah digit sumber asli; itu representasi manual berlabel yang dapat berbeda dari hasil parser.

Semua target harus pada specification dan submission version milik ReviewVersion; author wajib dosen course aktif. Request membawa expected_revision parent ReviewVersion. Perubahan anotasi menambah review revision, mengubah review content hash, membuat complete kembali draft, serta membuat feedback approval stale. Jika confirmed finding kehilangan dukungan yang diperlukan karena remove/retarget, complete/publish ditolak sampai dosen memperbaiki findings/keputusan/limitation yang terkait. Backend tidak memerlukan AI menyetujui koreksi manusia, tetapi membership/anchor dan kontrak pernyataan numerik tetap berlaku.

Hash review dan publication snapshot mengikat active annotation revision IDs, manual evidence IDs/contents/hashes, effective evidence bindings, dan keputusan/teks yang dirilis. Publikasi lama mempertahankan resolved revisions historis; menampilkan annotation “terbaru” dari review yang sudah berubah tidak boleh mengubah snapshot lama. Mahasiswa hanya melihat evidence/annotation yang disertakan pada release miliknya; tidak mendapat revision history internal.

Koreksi tidak otomatis menjadi ground truth, exemplar, fakta untuk mahasiswa lain, perubahan spec, atau pemicu regrade massal. AI rerun tidak menimpa anotasi dosen. Ekspor JSON/Markdown resmi memakai resolusi annotation snapshot yang sama dengan tampilan publikasi, bukan state review terbaru.

### 14.2 Feedback draft

Setelah review complete, dosen memilih "Susun draf feedback" atau menulis manual. Generator menerima hanya: instruksi/criteria/requirements resmi yang relevan, confirmed ReviewFindings, evidence yang dipilih dosen, catatan pedagogis dosen, dan limitation yang harus dijelaskan. Rejected/deferred candidates tidak diberikan sebagai daftar kesalahan; deferred hanya dapat menyumbang limitation yang dirumuskan secara netral.

Struktur feedback per blok: `criterion_id`, `review_finding_ids`, `evidence_ids`, `message`, `next_action`, `limitation` nullable. Renderer menghasilkan teks Markdown ringan: tuntutan → bukti → arti masalah yang dikonfirmasi → tindakan perbaikan. Saran tindakan hanya mengikuti kewajiban tugas; tidak mengambil materi atau jawaban ideal eksternal.

Jika tidak ada confirmed finding, gunakan template: "Dalam cakupan yang telah ditelaah, tidak ada masalah yang dikonfirmasi." Tambahkan limitation bila ada. Jangan mengarang pujian, kualitas holistik, atau klaim semua kewajiban terpenuhi. Jika provider gagal/biaya tidak cukup, template/manual tetap tersedia dan ditandai generation_mode yang benar.

Dosen menyunting teks final maksimal 20.000 karakter. Perubahan teks harus tetap merujuk sumber dan keputusan yang benar; UI memperingatkan bila ada finding yang dipublikasikan tanpa blok feedback, lalu dosen dapat memakai ringkasan gabungan yang secara eksplisit menaut beberapa finding. Semua confirmed findings yang hendak dirilis harus tercantum pada `included_review_finding_ids`; finding internal boleh dikecualikan dengan alasan dosen, dan tidak boleh muncul tersirat di teks.

### 14.3 Approval dan publish yang atomik

`review_content_hash` dihitung dari canonical JSON: submission/assignment/spec versions, seluruh terminal decisions, review element resolutions, manual findings/evidence links, active annotation revision IDs, manual evidence contents/hashes, effective support bindings, catatan serta limitation. Gunakan serialization deterministik; array himpunan diurutkan ID, teks mempertahankan isi. Pemilihan included findings dan alasan pengecualian adalah milik FeedbackDraft dan masuk feedback content hash bersama body/blok; perubahan pemilihan membatalkan approval draft tanpa mengubah hash keputusan review yang tidak diubah.

FeedbackDraft menyimpan hash sumber review, body/blok, content hash, generation mode dan model manifest nullable. Approval mencatat hash draft dan hash review serta actor/time. Approval membuat draft itu immutable. Edit teks setelah approval membuat draft baru yang belum disetujui.

Publikasi terpisah dari approval. Backend dalam satu transaksi:

1. Memeriksa role dosen aktif, expected revisions dan idempotency key.
2. Memastikan review complete; draft terakhir yang dipilih approved dan hash sumber sama dengan review saat ini.
3. Memastikan SubmissionVersion masih versi final aktif pada thread; jika versi lebih baru telah final, tolak `409 SOURCE_VERSION_CHANGED`. Draf pengumpulan yang belum final tidak mengganti versi final aktif.
4. Memastikan semua included findings dikonfirmasi, seluruh evidence sah, dan limitation yang dinyatakan dosen tersertakan.
5. Membuat Publication snapshot immutable berisi instruksi/criterion label relevan, confirmed findings yang dipilih, evidence publikasi dan resolved annotation revision IDs/contents/hashes, feedback final, limitation, source/config versions dan approver/publisher. Renderer/export memakai snapshot ini, bukan annotation pointer/review terbaru.
6. Mengubah pointer latest_published dan menulis audit/outbox. Tidak mengirim email atau memperbarui LMS grade.

Perubahan keputusan atau catatan setelah review complete mengembalikan review draft dan membuat feedback stale; approval lama tidak dapat dipakai. Publication lama tidak berubah. Koreksi hasil yang sudah dirilis melalui ReviewVersion/FeedbackDraft/Publication baru. Dosen dapat menarik publikasi dengan alasan melalui withdrawal event; mahasiswa melihat "Hasil ditarik untuk koreksi" tanpa isi draft, audit tetap ada.

Withdrawal tidak otomatis mengganti pointer latest_published dengan hasil lama. Pointer tetap menunjuk versi yang ditarik sampai ada publikasi pengganti; histori versi released sebelumnya dapat dibuka secara eksplisit dengan label versinya. Jangan menampilkan hasil lama sebagai koreksi baru tanpa tindakan dosen.

Rerun AI menciptakan AnalysisRun baru. Review lama tetap menunjuk run lama dan tidak ditimpa. Dosen harus memilih membuat ReviewVersion dari run baru bila ingin memakainya; hasil AI baru tidak mengubah hasil yang telah disetujui diam-diam.

### 14.4 Apa yang dilihat mahasiswa

Hanya publication yang berstatus released, tidak withdrawn, dan lolos akses roster/enrollment. Tampilkan findings yang dipilih dosen, feedback final, source/version label, evidence yang ditautkan pada publikasi, serta limitation yang relevan. Jangan mengirim raw candidates, rejected findings, confidence model, prompt, biaya, internal comment atau log dalam response mahasiswa.

Jika belum ada publication, tampilkan status teknis aman dan "Feedback belum diterbitkan". Tidak boleh menyimpulkan ada/tidak ada masalah dari job completed. Ekspor JSON/Markdown berasal dari snapshot yang sama dan tunduk otorisasi yang sama; tidak mengekspor semua histori internal.

## 15. Model data dan constraint penyimpanan

### 15.1 Prinsip schema

PostgreSQL adalah sumber status resmi. Semua tabel utama memiliki `id UUID PK`, `created_at timestamptz`, dan FK yang relevan. Tabel mutable juga memiliki `updated_at`, `revision integer >=1`. Versioned approved/published content tidak di-update in place. Snapshot historis immutable; state administratif seperti withdrawal/revocation dicatat sebagai event/pointer terpisah.

Gunakan SQLAlchemy 2 dan Alembic. Normalisasikan ownership, version, status, actor, financial amount, pointer dan relasi penting. JSONB hanya untuk payload heterogen/DSL/snapshot dengan schema Pydantic versioned. Field JSONB tidak boleh menjadi tempat menyembunyikan semua izin atau membership. Constraints lintas versi diperiksa service dan composite FK/unique bila dapat dinyatakan database. Kolom v2 seperti decision_question/grounding_mode/approval lint hanya boleh nullable untuk row historis schema 1; v2 validator/constraint mewajibkannya sesuai mode. Jangan mengisi histori dengan konten rekaan untuk memaksakan NOT NULL. Semua parent version_no mempunyai unique(parent_id,version_no) sesuai lineage objek.

### 15.2 Tabel minimum dan field khusus

Nama berikut adalah nama tabel normatif; implementer boleh menambah indeks/kolom teknis yang tidak mengubah kontrak.

| Tabel | Field khusus minimum dan constraint |
|---|---|
| `users` | normalized_email unique, display_name, password_hash, global_role `admin\|member`, active, must_change_password |
| `sessions` | user_id, token_hash unique, csrf_hash, last_seen_at, expires_at, absolute_expires_at, revoked_at nullable |
| `courses` | code unique, title, active, approved_context_version_id nullable |
| `enrollments` | course_id, user_id, role `lecturer\|student`, active; unique(course_id,user_id) |
| `context_versions` | course_id, version_no, state `draft\|approved`, fields_json, instruction_sources_json, approved_by/at, content_hash; unique(course_id,version_no) |
| `context_conflicts` | context_version_id, field_path, candidate_values_json, resolution_json nullable, resolved_by/at; field_path dari registry |
| `learning_outcomes` | context_version_id, code, description, origin, source_refs_json, resolution_status; unique(context_version_id,code) |
| `assignments` | course_id, title, active_version_id nullable, administrative_state `open\|closed\|archived` |
| `assignment_versions` | assignment_id, version_no, state `draft\|published`, context_version_id, instruction_original, instruction_hash, instruction_summary nullable, mode, opens_at, due_at, late_policy, limits_json, criterion_set_version_id, specification_version_id, package_approved_by/at nullable, published_by/at nullable; unique(assignment_id,version_no) |
| `criterion_set_versions` | assignment_id, version_no, instruction_hash, state, approved_by/at nullable, content_hash |
| `criteria` | criterion_set_version_id, code, title, descriptor, display_order; unique(set_version,code); tidak ada score/weight |
| `requirements` | criterion_set_version_id, code, text, mandatory, obligation_key, normative_source_refs_json; unique(set_version,code) |
| `criterion_requirement_links` | criterion_id, requirement_id; composite PK; harus satu set version |
| `criterion_outcome_links` | criterion_id, learning_outcome_id; composite PK; context version harus cocok; tanpa allocation weight |
| `numeric_policy_versions` | assignment_id, policy_key, version_no, state, payload_json Bagian 12, content_hash, approved_by/at nullable |
| `specification_versions` | assignment_id, version_no, schema_version (1 historis, 2 untuk kreasi/approval baru), criterion_set_version_id, context_version_id, instruction_hash, operator/predicate/grounding/lint registry versions, limits_json, state, content_hash, approved_lint_report_id nullable, approved_lint_resolution_revision nullable, fidelity_confirmed_by/at nullable, approved_by/at nullable |
| `check_elements` | specification_version_id, criterion_id, obligation_key, target_kind, decision_question, grounding_mode, payload_json penuh Bagian 7 termasuk derivation_refs/grounding_contract/relationships; unique(spec_version,element_code); element_code stabil secara makna |
| `element_requirement_links` | check_element_id, requirement_id; composite PK dan satu approved set |
| `spec_lint_reports` | specification_version_id, source_spec_revision, source_content_hash, source_package_hashes_json, lint/grounding/operator registry versions, initial_status, issues_json typed, resolution_revision>=1; report payload immutable; hanya counter resolution yang mutable/locked |
| `spec_lint_resolutions` | lint_report_id, issue_code, resolution enum Bagian 7.7, reason, actor_id, resolved_at; append-only; unique(report,issue); blocking issue tidak boleh punya resolusi override |
| `groups` | assignment_id, name, active; unique(assignment_id,name) |
| `group_memberships` | group_id, assignment_id, user_id, active; partial unique(assignment_id,user_id) WHERE active |
| `submissions` | assignment_id, individual_user_id nullable, group_id nullable, active_final_version_id nullable, latest_published_id nullable, draft_revision, reopen_until nullable; owner XOR dan unique assignment-owner |
| `file_assets` | course_id, submission_id nullable, assignment_id nullable, purpose `submission\|context\|rubric`, uploaded_by, original_name, size_bytes, mime, sha256, object_key, validation_state, preflight_json, quarantined_at; submission perlu submission_id, rubric perlu assignment_id, context boleh berada pada course sebelum dilampirkan ke draf |
| `normative_file_links` | file_asset_id, context_version_id nullable, assignment_version_id nullable, source_role `kk\|rpp\|rubric`; tepat satu version FK nonnull; semuanya satu course |
| `submission_versions` | submission_id, version_no, assignment_version_id, manifest_hash, finalized_by/at, is_late; unique(submission_id,version_no) |
| `submission_version_files` | submission_version_id, file_asset_id, display_order; composite PK; asset milik thread yang sama |
| `submission_version_members` | submission_version_id, user_id; composite PK; snapshot roster |
| `method_manifests` | kind `parser\|parser_resource\|model\|retrieval\|rules\|prompt\|routing\|batching`, version_label, content_json typed, sha256 unique, verified_at nullable; payload immutable; status/verifikasi dicatat terpisah dari payload hash |
| `release_manifests` | version_label, parser_manifest_id, model_manifest_id, retrieval_manifest_id, rules_manifest_id, prompt_manifest_id, routing_policy_manifest_id, batching_policy_manifest_id, grounding/lint registry versions, config_hash unique, capability_smoke_refs_json, state `draft\|verified\|active\|retired`, activated_by/at nullable; refs immutable setelah verified; parser profile refs pada release_parser_profiles |
| `release_parser_profiles` | release_manifest_id, route_key, parser_resource_manifest_id, benchmark_report_id nullable; unique(release,route_key); manifest kind/profile cocok dan refs immutable setelah release verified |
| `policy_validation_reports` | kind routing/batching/parser, report_object_key/hash, policy_core_sha256 atau parser_profile_sha256, provider_mode, data_origin, allowed_data_modes, model/prompt/schema refs, dataset/protocol hashes, metrics/support/gate_summary_json typed, result eligible/ineligible/incomplete, registered_by/at; immutable; tidak diekspos sebagai analytics UI |
| `parse_artifacts` | file_asset_id, submission_version_id nullable, context_version_id nullable, assignment_version_id nullable, parser_manifest_id, parser_resource_manifest_id, fingerprint unique scoped owner/version, status, limitations_json, artifact_object_key; tepat satu namespace version nonnull; fingerprint mengikat profile/options/artifacts |
| `parser_checkpoints` | job_id, parse_artifact_id, task_key, file_hash, namespace_version_id, canonical_unit_locator_json, stage, parser_resource_manifest_id, status, output_refs/hashes_json, covered_unit_refs_json, limitations_json, admission_node/lease/fence refs, actual_active_ms, peak_memory_bytes nullable; unique(parse_artifact,task_key) |
| `processing_time_reservations` | job_id, task_key, attempt_no, reserved_active_ms, actual_active_ms nullable, state held/settled/released, started/finished_at; unique(job,task_key,attempt_no); nonnegative integer milliseconds, seluruh attempt dihitung pada job limit |
| `source_units` | parse_artifact_id, parent_id nullable, unit_type, source_order, anchor_json, parse_status, limitations_json; native locator unique per artifact |
| `evidence_items` | source_unit_id, submission_version_id nullable, context_version_id nullable, assignment_version_id nullable, file_asset_id, source_sha256, type, anchor_json, content_original_json, content_normalized_json, parse_status, limitations_json, parser_manifest_id; tepat satu namespace version nonnull |
| `retrieval_chunks` | submission_version_id, parent_chunk_id nullable, text, token_count, structural_metadata_json, retrieval_manifest_id |
| `chunk_evidence_links` | chunk_id, evidence_item_id; composite PK; keduanya pada submission version sama |
| `evidence_embeddings` | chunk_id, model_manifest_id, dimension=1536 default, embedding vector(1536), content_hash; unique(chunk_id,model_manifest_id) |
| `analysis_runs` | submission_version_id, specification_version_id, release_manifest_id, config_fingerprint, model/parser/retrieval/rules/prompt/routing/batching manifests, grounding registry version, status, completion_kind, started/finished_at, limitations_json, budget_limit_usd; unique(submission_version_id,config_fingerprint) |
| `element_checks` | analysis_run_id, check_element_id, applicability_status, source_coverage_status, evidence_relationship, check_status nullable, observations_json, interpretation nullable, reason_codes_json; unique(run,element) |
| `grounding_evaluations` | element_check_id unique, contract_schema_version, mode, status, decision_basis nullable, mechanical_validation_json, semantic_support, coverage_record_id nullable, limitations_json, validator manifests; typed Bagian 9.5 |
| `evidence_support_bindings` | element_check_id, relationship_code nullable, role, observation_codes_json, evidence_item_id, quote_spans_json, validator refs; FK/membership pada sumber versi yang sama; multi-role pada evidence sama diperbolehkan |
| `grounding_numeric_links` | grounding_evaluation_id, numeric_check_id; composite PK; numeric check pada elemen/run yang sama |
| `routing_decisions` | job_id, analysis_run_id nullable, check_element_id nullable, task_type, routing/batching policy manifest IDs, feature_snapshot_json, guards_json, action, role nullable, reason_code, source/coverage refs, limits_snapshot_json, outcome, timing_refs_json; per keputusan termasuk tanpa call |
| `routing_call_links` | routing_decision_id, call_id; composite PK; call_id merujuk usage entry/reservation yang sama agar physical call tidak dihitung dua kali |
| `semantic_batch_members` | call_id, element_check_id, entry_status valid/invalid/omitted, input_family_hash; composite PK; satu scope/release/source set; invalid/omitted tidak menambah inspection coverage |
| `coverage_records` | element_check_id unique, policy_snapshot_json, compatible_unit_count, inspected_unit_count, unresolved_unit_count, negative_gate_passed, limitations_json |
| `coverage_inspections` | coverage_record_id, source_unit_id, method `structural\|semantic_scan\|vision\|lecturer`, inspection_status `inspected\|excluded\|unresolved`, observation_json, rule_or_call_id nullable; unique(record,unit) |
| `numeric_observations` | element_check_id, evidence_item_id, literal, normalized_value numeric(38,12) nullable plus canonical_decimal_text, context_json, source_quality, verification_ref_json |
| `numeric_checks` | element_check_id, numeric_policy_version_id, comparability_status, operation, normalized_context_json, operands_snapshot_json, result_decimal_text nullable, difference_text nullable, allowed_difference_text nullable, inconsistent nullable, rule_manifest_id |
| `finding_candidates` | analysis_run_id, primary_category, primary_criterion_id, obligation_key, issue_signature, dedup_key unique per run, claim, technical_validation, semantic_support, numeric_check_id nullable, limitations_json |
| `finding_element_links` | finding_candidate_id, check_element_id; composite PK |
| `finding_requirement_links` | finding_candidate_id, requirement_id; composite PK |
| `finding_criterion_links` | finding_candidate_id, criterion_id; composite PK |
| `finding_coverage_links` | finding_candidate_id, coverage_record_id; composite PK |
| `review_versions` | submission_version_id, analysis_run_id nullable, version_no, state `draft\|complete`, content_hash, lecturer_notes, completed_by/at nullable; unique(submission_version_id,version_no) |
| `review_element_decisions` | review_version_id, check_element_id, review_status, resolution nullable, applicability_override nullable, reason nullable, decided_by/at nullable; unique(review,element) |
| `review_findings` | review_version_id, candidate_id nullable, origin `ai_review\|lecturer`, action `pending\|accept\|edit\|reject\|defer`, primary_category, primary_criterion_id, claim, targets_json typed requirement/element IDs, coverage_attestation_json nullable, numeric_check_id nullable, reason nullable, actor/at; unique(review,candidate) ketika candidate nonnull |
| `evidence_links` | evidence_item_id, element_check_id nullable, finding_candidate_id nullable, review_finding_id nullable, feedback_draft_id nullable, publication_id nullable; tepat satu subject FK nonnull; version membership diperiksa |
| `manual_evidence` | evidence_item_id unique, review_version_id, annotation_revision_id nullable untuk histori lama dan wajib pada kreasi v2, created_by, origin `lecturer_selected\|lecturer_transcription`, source_anchor_json, transcription nullable, reason nullable; evidence immutable |
| `review_annotations` | review_version_id, kind span_selection/transcription/evidence_binding, current_revision_id, created_by; hanya pointer/current metadata mutable melalui transaksi parent review |
| `review_annotation_revisions` | annotation_id, revision_no, previous_revision_id nullable, operation upsert/remove, file_asset_id, source_sha256, anchor_json, original_evidence_id nullable, corrected_evidence_id nullable, transcription nullable, reason nullable, author_id, created_at; unique(annotation,revision_no), append-only |
| `review_annotation_targets` | annotation_revision_id, check_element_id, review_finding_id nullable, relationship_code/role nullable; refs pada review/spec/submission yang sama; identity ter-normalisasi dan metadata role typed |
| `feedback_drafts` | review_version_id, sequence_no, generation_mode `model\|template\|manual`, source_review_hash, blocks_json, body_markdown, included_review_finding_ids_json, exclusion_reasons_json, content_hash, state `draft\|approved`, approved_by/at nullable, model_manifest_id nullable; unique(review,sequence_no) |
| `publications` | submission_id, submission_version_id, review_version_id, feedback_draft_id, sequence_no, snapshot_json, snapshot_hash, approved_by/at, published_by/at; unique(submission,sequence_no) |
| `publication_events` | publication_id, event `released\|withdrawn`, actor_id, reason nullable, event_at; released satu kali per publication |
| `jobs` | type, subject_id, scope_json typed, input_fingerprint, status, stage, lease_until nullable, completion_kind nullable, last_error_code nullable, job_limits_json; unique(type,input_fingerprint) |
| `job_stages` | job_id, stage_name, input_hash, status, output_refs_json, attempt_count, started/finished_at, failure_code nullable; unique(job,stage,input_hash) |
| `outbox_events` | event_type, subject_id, payload_json, delivered_at nullable, attempt_count; payload hanya ID/referensi yang diperlukan |
| `budget_accounts` | currency=USD, confirmed_total numeric(18,8), reserve_floor numeric(18,8), planned_topup numeric(18,8), active; perubahan via ledger/audit |
| `usage_reservations` | budget_account_id, job_id, call_id unique, amount_reserved numeric(18,8), status `held\|settled\|released\|reconciliation_required` |
| `usage_entries` | call_id unique, logical_call_id, attempt_no, provider_request_id nullable, manifest_id, usage_json, rates_snapshot_json, actual_cost numeric(18,8) nullable, status `known\|pending`, created_at; unique(logical_call_id,attempt_no); setiap physical attempt memiliki call_id/reservation sendiri |
| `audit_events` | actor_user_id nullable, worker_id nullable, action, object_type/id, old/new_version_refs_json, request_id, occurred_at; jangan simpan secret/raw dokumen penuh |
| `idempotency_records` | actor_id, method, route, key, body_hash, response_status, response_json safe, expires_at; unique(actor,method,route,key) |

Native Decimal text adalah sumber ketepatan numeric observation/check. Kolom numeric indeks pendamping tidak boleh membulatkan nilai yang kemudian dipakai hitungan; jika input di luar kapasitas kolom pendamping, simpan kolom itu null dan gunakan decimal text tervalidasi. Nilai finansial ledger hanya USD fixed precision dan dibulatkan ke atas ketika mereservasi biaya.

### 15.3 Constraint lintas objek dan transaksi

- Semua published assignment refs harus approved dan satu assignment/course/context lineage. CheckElement tidak boleh menaut criterion atau requirement dari set lain.
- Unique owner menggunakan dua partial unique index: `(assignment_id, individual_user_id)` bila nonnull dan `(assignment_id, group_id)` bila nonnull.
- Evidence, candidate, review, feedback dan publication harus berada dalam SubmissionVersion yang tepat; database FK dan business validator keduanya digunakan.
- Immutable approved content ditolak update/delete oleh service dan database guard/trigger kecuali purge terotorisasi pada Bagian 20.7; purge menghasilkan tombstone, bukan histori palsu.
- Publish/approve/finalize/complete/reopen/budget reservation memakai transaksi, row lock pada parent dan pemeriksaan revision. API mengembalikan 409 pada konflik, tidak last-write-wins.
- Role database worker tidak boleh INSERT/UPDATE publications, feedback approval, atau lecturer decisions. Worker feedback hanya membuat draft. API menggunakan role terpisah untuk aksi dosen yang telah diotorisasi.
- Redis bukan penyimpan hasil akademik, keputusan, session otoritatif, atau saldo biaya.
- Audit log append-only melalui role aplikasi; jangan mengklaim audit tamper-proof terhadap superuser database.

V2 menambahkan constraint: lint payload immutable dan resolution_revision terpisah dari spec draft revision; approval memvalidasi keduanya dalam transaksi. Grounding/support bindings tidak boleh merujuk evidence dari namespace normatif sebagai bukti jawaban mahasiswa. Routing/batching policy dan parser profile pada parse/run harus merupakan ref release yang sama. Annotation revisions append-only; perubahan pointer/binding aktif memakai revision parent review dan membuat feedback stale. Validated policy wajib menunjuk validation report dengan core hash yang cocok; baseline singleton tidak mengaku telah lolos studi mutu empiris.

## 16. API yang harus diimplementasikan

### 16.1 Aturan umum

Prefix `/api/v1`. Semua request JSON divalidasi Pydantic strict, `extra=forbid`; upload memakai multipart streaming. Mutasi mutable resource menyertakan `expected_revision`. Aksi yang membuat job/versi/publikasi memerlukan header `Idempotency-Key` UUID; replay body sama mengembalikan hasil semula, body berbeda dengan key sama → 409. Retensi idempotency 24 jam; uniqueness domain tetap melindungi replay setelah TTL.

Response resource menyertakan id, revision bila mutable, created/updated timestamps, versi/status dan field publik sesuai role. Jangan menerima actor_id, approved_at, object_key, hash hasil server, status publication atau biaya aktual dari client.

List response: `{"items": [...], "next_cursor": null}`. Error response:

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "Data telah berubah. Muat versi terbaru sebelum menyimpan.",
    "field_errors": [],
    "request_id": "44444444-4444-4444-8444-444444444444"
  }
}
```

Status: 400 transisi/body semantik salah; 401 tidak login; 403 role diketahui tidak mengizinkan aksi umum; 404 objek tidak ada/di luar scope; 409 revision/stale/idempotency/state conflict; 413 limit ukuran; 415 format tidak didukung; 422 schema/field validation; 429 rate limit; 503 layanan sementara gagal. Tidak ada stack trace, key, presigned URL, atau raw provider response pada error client.

### 16.2 Auth, admin dan course

| Method dan path | Request inti | Response dan izin |
|---|---|---|
| POST `/auth/login` | email, password | User ringkas + csrf token; set cookie; tidak cache |
| POST `/auth/logout` | CSRF header | 204, revoke session |
| GET `/me` | — | Profil, role, course memberships aktif, csrf token, public runtime limits |
| PUT `/me/password` | current_password, new_password | 204; reset must_change_password dan revoke session lain |
| POST `/admin/users` | email, display_name, role | User + temporary password sekali; admin |
| PATCH `/admin/users/{id}` | active/display_name/global_role, expected_revision | User; admin; jangan menghapus admin aktif terakhir |
| POST `/admin/users/{id}/reset-password` | expected_revision | Temporary password sekali; admin |
| POST `/courses` | code, title | Course; admin |
| GET `/courses` | cursor | Course berizin; admin hanya metadata, dosen/mahasiswa enrollment aktif |
| GET `/courses/{id}` | — | Metadata dan versi yang boleh dilihat |
| PUT `/courses/{id}/enrollments` | expected_revision, daftar user_id/role/active | Roster; admin; validasi role tanpa isi akademik |
| GET `/courses/{id}/enrollments` | cursor | Roster minimal; admin/dosen course |
| POST `/assignments/{id}/groups` | name, member_user_ids | Group; dosen course |
| PUT `/groups/{id}` | name/member_user_ids/active, expected_revision | Group; dosen; aturan snapshot tetap berlaku |

### 16.3 Context, assignment, specification

| Method dan path | Request inti | Response/perilaku |
|---|---|---|
| POST `/courses/{id}/context-drafts` | source_file_ids atau manual_fields, base_context_version_id nullable | 201 draft; bila AI dipilih, job_id; dosen |
| GET `/context-versions/{id}` | — | Typed fields, konflik, provenance dan revision; dosen |
| PUT `/context-versions/{id}` | fields + conflict_resolutions, expected_revision | Draf tersimpan; hanya state draft |
| POST `/context-versions/{id}/approve` | expected_revision | Approved snapshot; konflik wajib selesai |
| POST `/courses/{id}/context-files` | multipart file | FileAsset tervalidasi ber-purpose context; dosen |
| POST `/assignments` | course_id, title, context_version_id | Assignment + draft version; dosen |
| GET `/courses/{id}/assignments` | cursor | Dosen: semua metadata; mahasiswa: versi terbit yang relevan |
| GET `/assignments/{id}` | version_id optional | Role-filtered assignment; internal prompt/DSL tidak dikirim ke mahasiswa |
| PUT `/assignment-versions/{id}` | instruction_original, summary optional, mode, jadwal, late_policy, expected_revision | Draf; mengubah hash/invalidate approval paket/spec |
| POST `/assignments/{id}/rubric-files` | multipart file | FileAsset rubric, parser input; dosen; format seperti context |
| POST `/assignment-versions/{id}/criteria-drafts` | rubric_file_ids atau structured_criteria atau generate_from_instruction=true | Job/draf criterion set; tidak boleh menambah tuntutan tersembunyi |
| PUT `/criterion-set-versions/{id}` | criteria, requirements, links/mapping, expected_revision | Typed set; source refs divalidasi |
| POST `/assignment-versions/{id}/approve-package` | criterion_set_version_id, expected_revision | Approval instruksi+criteria+requirement hash |
| POST `/assignment-versions/{id}/specification-drafts` | approved criterion_set_version_id | Draft/job; mengikat hash paket |
| PUT `/specification-versions/{id}` | schema_version=2, elements + limits, expected_revision | Schema/operator/DAG/grounding/derivation validation; lint deterministik baru pada revision tersimpan; response memuat lint_report_id dan effective_status |
| PUT `/numeric-policy-versions/{id}` | payload Bagian 12, expected_revision | Draf policy; dosen |
| POST `/assignments/{id}/numeric-policy-drafts` | policy_key, payload | Draf baru; dosen |
| POST `/numeric-policy-versions/{id}/approve` | expected_revision | Approved policy |
| POST `/specification-versions/{id}/approve` | expected_revision, lint_report_id, expected_report_revision, fidelity_confirmation=true | Approved hanya bila paket/policy valid, lint efektif passed dan hash/revision/paket cocok; binding approval immutable |
| POST `/assignment-versions/{id}/publish` | expected_revision | Published version; semua ref approved dan cocok |
| POST `/assignments/{id}/new-version` | base_version_id | Draf baru; versi lama tidak berubah |

| Method dan path tambahan | Request inti | Response/perilaku |
|---|---|---|
| POST `/specification-versions/{id}/lint` | expected_revision, Idempotency-Key | Report deterministik baru/identik untuk hash saat ini, tanpa paid call; dosen course |
| GET `/specification-versions/{id}/lint-reports` | cursor | Metadata reports/version binding; dosen course |
| GET `/spec-lint-reports/{id}` | — | Issues, sources, immutable input hash, resolution_revision dan effective_status; dosen course |
| POST `/spec-lint-reports/{id}/resolutions` | issue_code, resolution, reason, expected_spec_revision, expected_report_revision, Idempotency-Key | Append-only resolution; lock spec/report; increment resolution_revision tanpa mengubah spec content/revision; blocking/old hash ditolak |

GET/PUT spec menyertakan laporan yang cocok dengan current draft hash/revision atau null bila belum ada. PUT mengembalikan laporan deterministik yang baru, bukan menyatakan kesetiaan semantik telah dibuktikan. Approval lint stale/revision mismatch → 409; invalid refs/contracts/unauthorized override → 422 dengan field errors. Worker tidak dapat mengisi fidelity_confirmation atas nama dosen.

Agent harus menyediakan GET detail untuk criterion-set, specification, dan numeric-policy versions berizin dengan field yang sama seperti schema domain plus status/revision/approval metadata; tidak membuat payload rahasia yang perlu ditebak frontend. POST draf mengembalikan objek draf yang dapat di-GET/PUT walaupun AI gagal.

### 16.4 Submission, sumber dan job

| Method dan path | Request inti | Response/perilaku |
|---|---|---|
| POST `/assignments/{id}/submissions` | owner individual/group yang diizinkan | Thread yang sudah ada atau thread baru; mahasiswa; tidak boleh memilih mahasiswa lain |
| GET `/assignments/{id}/submissions` | cursor, status filter | Dosen: daftar course; mahasiswa: hanya thread miliknya |
| POST `/submissions/{id}/files` | multipart file, expected draft_revision | FileAsset validated atau error preflight |
| DELETE `/submissions/{id}/draft-files/{file_id}` | expected_revision | 204; hanya file belum menjadi manifest final |
| POST `/submissions/{id}/finalize` | file_ids, expected_revision | SubmissionVersion + receipt + job_id setelah commit |
| GET `/submissions/{id}/status` | — | Versi final aktif, receipt, status teknis aman dan latest publication metadata |
| POST `/submissions/{id}/reopen` | reopen_until, reason, assignment_version_id, expected_revision | Draft baru; dosen |
| POST `/submission-versions/{id}/analysis-runs` | approved release_manifest_id | 202 job atau run identik yang sudah ada; dosen; reserve budget tetap wajib |
| GET `/analysis-runs/{id}` | — | Semua checks/candidates/limitations dan manifest refs; dosen |
| GET `/jobs/{id}` | — | Dosen: stage/error aman; mahasiswa: status umum job miliknya tanpa hasil internal |
| POST `/jobs/{id}/retry` | expected_revision | Retry tahap recoverable dalam limit; dosen; tidak mengubah human review |
| GET `/files/{id}/content` | optional stream/range | Original private; role dan owner policy Bagian 4 |
| GET `/evidence/{id}` | — | Evidence + anchor + authorized viewer URL; mahasiswa hanya jika ada publication evidence link |
| GET `/source-units/{id}/render` | — | Render/crop terotorisasi; cek permission setara evidence dan hash |

`release_manifest_id` adalah bundle typed refs parser/resource profiles/model/retrieval/rules/prompt/routing/batching dan registry versions yang terdaftar operator, bukan ID sembarang milik client. GET analysis-runs mengembalikan GroundingEvaluation/support bindings dan alasan routing per elemen yang aman bagi dosen, tanpa secret/raw hidden reasoning/tarif internal. Benchmark dan policy-validation report tidak menjadi dashboard akademik. Retry source/config berbeda bukan retry run yang sama: buat AnalysisRun/fingerprint baru. Mahasiswa tidak dapat memulai pengeluaran model manual melalui endpoint analisis.

### 16.5 Review dan release

| Method dan path | Request inti | Response/perilaku |
|---|---|---|
| POST `/submission-versions/{id}/reviews` | analysis_run_id nullable | ReviewVersion dari semua elemen; dosen; null untuk manual |
| GET `/reviews/{id}` | — | Criteria, semua check, coverage, candidates, decisions, evidence links dan revision; dosen |
| PUT `/reviews/{id}/element-decisions/{element_id}` | review_status, resolution, reason/evidence refs, expected_revision | Review revision baru; stale feedback bila sudah ada |
| PUT `/reviews/{id}/candidate-decisions/{candidate_id}` | action, claim/category/targets/evidence edit bila perlu, reason, expected_revision | ReviewFinding/decision + review revision |
| POST `/reviews/{id}/manual-evidence` | file_id, native anchor, transcription optional, reason, expected_revision | EvidenceItem/manual origin + annotation/revision pertama + review revision dalam satu transaksi; tidak perlu membuat anotasi yang sama dua kali |
| POST `/reviews/{id}/manual-findings` | category, criterion/element/requirement refs, claim, evidence/coverage/numeric data, expected_revision | Confirmed manual ReviewFinding |
| PATCH `/reviews/{id}/manual-findings/{finding_id}` | perubahan atau action=reject, expected_revision | Review revision baru; bukan hard delete histori |
| PUT `/reviews/{id}/notes` | lecturer_notes, limitations, expected_revision | Review revision baru |
| POST `/reviews/{id}/complete` | expected_revision | Complete atau field errors daftar elemen/kandidat pending |
| POST `/reviews/{id}/feedback-drafts` | mode `model\|template\|manual`, body bila manual, included findings, exclusion reasons, expected_revision | Draft atau job; review harus complete |
| GET `/feedback-drafts/{id}` | — | Body/blok, provenance, stale flag, approval state; dosen |
| PUT `/feedback-drafts/{id}` | body_markdown, blocks, included findings, exclusion reasons, expected_revision | Draf tersimpan; approved draft memerlukan draft baru |
| POST `/feedback-drafts/{id}/approve` | expected_revision, expected_review_revision | Approved content/hash |
| POST `/reviews/{id}/publish` | feedback_draft_id, expected_revision | Publication snapshot; stale/izin/versi gagal → 409/404 |
| POST `/publications/{id}/withdraw` | reason | Withdrawal event; dosen |
| GET `/submissions/{id}/published-result` | publication_id optional | Latest released/histori terpilih yang berizin, tanpa internal data |
| GET `/publications/{id}/export` | format=json atau md | File resmi role-filtered, filename aman, tanpa signed URL permanen |

| Method dan path tambahan | Request inti | Response/perilaku |
|---|---|---|
| GET `/reviews/{id}/source-links` | tepat satu evidence_id atau source_unit_id, cursor | Linked elements/findings/active annotation revisions pada review yang sama; tidak melakukan pencarian lintas mahasiswa |
| POST `/reviews/{id}/annotations` | kind, file/anchor/evidence/target refs, transcription nullable, reason, expected_revision | Annotation + revision 1 + manual EvidenceItem bila diperlukan; parent review revision baru |
| POST `/reviews/{id}/annotations/{annotation_id}/revisions` | operation upsert/remove, payload yang sesuai kind, reason, expected_revision | Revision append-only; source lama tetap; review draft/hash berubah dan feedback stale |
| GET `/reviews/{id}/annotations/{annotation_id}/revisions` | cursor | Histori berizin untuk dosen; publication student tidak memakai serializer ini |

GET reviews memuat active annotation metadata dan source-link IDs dengan pagination untuk isi besar. Whole-file manual anchor wajib locator_note. Evidence correction tidak mengizinkan file baru di luar manifest. Endpoint annotations dan manual-evidence menggunakan service yang sama; satu aksi tidak membuat dua anotasi tanpa maksud. Invalid target/anchor → 422, revision/hash conflict → 409, akses asing → 404. Semua mutasi membuat objek/revisi dan memerlukan Idempotency-Key.

Semua endpoint GET/PUT/PATCH/POST review, approval dan publish hanya dosen course aktif. Request `expected_revision` pada subresource review adalah revision parent ReviewVersion, bukan revision lokal yang memungkinkan dua dosen saling menimpa. Backend mengembalikan revision terbaru setelah mutasi. GET student tidak pernah memakai serializer model review internal.

### 16.6 Contoh response hasil resmi

```json
{
  "publication_id": "55555555-5555-4555-8555-555555555555",
  "submission_version": 1,
  "assignment_version": 1,
  "published_at": "2026-10-18T15:00:00Z",
  "confirmed_findings": [
    {
      "id": "66666666-6666-4666-8666-666666666666",
      "category": "numerical_inconsistency",
      "criterion_title": "Konsistensi pelaporan",
      "claim": "Pendapatan tahun pertama berbeda antara laporan dan spreadsheet untuk konteks yang sama.",
      "evidence_ids": ["77777777-7777-4777-8777-777777777777", "88888888-8888-4888-8888-888888888888"]
    }
  ],
  "feedback_markdown": "Samakan angka pendapatan tahun pertama pada laporan dan spreadsheet setelah memeriksa kembali perhitungan sumber.",
  "limitations": ["Diagram risiko belum dapat dibaca secara memadai."],
  "source_labels": ["Laporan.pdf halaman 4", "Proyeksi.xlsx / Proyeksi!D8"]
}
```

Tidak ada field score, grade, weight, mastery, raw AI confidence atau rejected findings pada response tersebut. Numeric operand/difference yang relevan boleh disertakan sebagai rincian finding karena bukan nilai akademik.

### 16.7 Discovery, histori dan nested payload

Tambahkan endpoint berikut agar frontend tidak menebak ID atau state:

| Method dan path | Kontrak |
|---|---|
| GET `/runtime/releases` | Dosen: daftar ID/version label release active/verified yang dapat dipakai; tanpa secret/tarif internal; mahasiswa tidak perlu endpoint ini |
| GET `/courses/{id}/context-versions` | Dosen: metadata draf/approved dan revision, cursor pagination |
| GET `/assignment-versions/{id}` | Detail draf untuk dosen atau published public fields untuk mahasiswa berizin |
| PATCH `/assignments/{id}` | title/administrative_state dan expected_revision; dosen; tidak mengganti konten versi terbit |
| GET `/criterion-set-versions/{id}` | Typed criteria/requirements/links, approval/hash/revision; dosen |
| GET `/specification-versions/{id}` | schema_version, typed elements/DAG/operator/grounding/derivation/limits, lint terkini, approval/hash/revision; dosen |
| GET `/numeric-policy-versions/{id}` | Typed policy dan approval/hash/revision; dosen |
| GET `/assignments/{id}/groups` | Dosen: semua group assignment; mahasiswa: hanya kelompoknya beserta roster yang boleh dilihat |
| GET `/submission-versions/{id}/reviews` | Dosen: daftar review metadata/run refs/state/revision, tanpa keputusan lintas submission |
| GET `/submissions/{id}/publications` | Dosen: histori released/withdrawn; mahasiswa: released metadata serta pemberitahuan withdrawal miliknya, tanpa isi withdrawn/draft |
| GET `/publications/{id}` | Dosen: historical snapshot berizin; mahasiswa: hanya released dan tidak withdrawn miliknya |

POST context-drafts menggunakan `sources=[{"file_id":"UUID","source_role":"kk"}]` atau role rpp, ditambah manual_fields bila diperlukan; source_file_ids pada uraian umum berarti daftar file dari struktur sources ini. Tidak menebak KK/RPP dari filename. Upload konteks yang belum dihubungkan ke draf tetap berscope course dan lecturer; link ke versi dibuat pada create/update draf. Source role rubric hanya untuk assignment rubric files.

GET submission status juga mengembalikan draft file metadata yang dimiliki pengguna, draft_revision, reopen_until dan source version label, supaya refresh halaman tidak kehilangan daftar upload. Tidak mengembalikan internal analysis records kepada mahasiswa.

Nested create/update criteria/requirements/elements menerima ID yang sudah ada pada draf itu atau `id=null` untuk objek baru; server membuat UUID dan mengembalikan mapping business code→ID. Business code unik dalam set; ID dari versi/course lain ditolak. PUT set adalah penggantian isi draf tervalidasi dalam satu transaksi, bukan update approved version. Penghapusan child hanya diizinkan pada draf dan menolak dangling links/dependencies. Server mengembalikan seluruh hasil normalisasi dan revision baru.

Operator mendaftarkan method/release manifests melalui CLI `align release register --file PATH` dan mengaktifkan melalui `align release activate RELEASE_ID` setelah manifest validation serta capability smoke lolos. Register memvalidasi refs parser profiles/benchmarks dan policy validation reports sesuai Bagian 8.5/21.4; candidate batching tidak dapat aktif. CLI evaluator/benchmark ada pada Bagian 8.5/21.4/21.5; semuanya offline/operator, bukan endpoint untuk mahasiswa. CLI ini harus dibuat agent, menjalankan service/transaction yang sama, mengaudit actor operator, dan tidak mencetak secret. Tidak perlu form konfigurasi model bagi mahasiswa/dosen.

## 17. Job, recovery, idempotensi dan biaya

### 17.1 State machine terpisah

| Objek | State dan transisi yang diperbolehkan |
|---|---|
| Context/criterion/spec/policy version | draft → approved; edit approved membuat draft versi baru |
| AssignmentVersion | draft → published; konten published immutable |
| Draft submission | editing → finalized; reopen membuat draft berikutnya, bukan mengubah final |
| Job | queued → running → ready_for_review; running → blocked/failed; blocked → queued bila recoverable; hasil lama dapat ditandai stale terhadap versi aktif |
| Analysis completion kind | completed atau completed_with_limits; terpisah dari job status dan publication |
| ReviewVersion | draft → complete; edit manusia complete → draft dengan revision baru dan feedback stale |
| FeedbackDraft | draft → approved; stale adalah hasil perbandingan hash, bukan penghapusan histori |
| Publication | immutable snapshot dengan released event; withdrawal event tidak mengubah konten snapshot |

Stage analysis: `validate_manifest → parse → build_evidence → index → execute_checks → verify → ready_for_review`. parse mencakup subtasks/checkpoints metadata/render/layout/OCR/table/assembly Bagian 8.5, dengan admission lease dan waktu aktif yang sama; tidak ada resource bypass melalui child job. Job context/spec/feedback memiliki stage sejenis sesuai tipe. Tidak ada job analytics. Ready for review tidak sama dengan feedback terbit.

### 17.2 Delivery dan pemulihan

Outbox dispatcher membaca row commit yang belum delivered dan mengirim ID job ke Celery. Redis sebagai broker saja. Worker mengunci lease dan memeriksa input hash serta stage output yang sudah selesai; redelivery tidak mengulang stage yang dapat digunakan kembali. Gunakan `acks_late` hanya setelah idempotensi stage diterapkan; visibility timeout Redis harus melebihi hard task timeout.

Default: pecah stage/task maksimal 120 detik, hard timeout 150 detik, Redis visibility timeout 300 detik, lease 180 detik yang diperpanjang heartbeat 30 detik. Timeout efektif dipotong sisa run; parser resource/task/page boundaries mengikuti Bagian 8.5. Reaper tiap 60 detik mengambil lease kedaluwarsa dan menjadwalkan ulang tahap yang aman setelah fencing/child-process reconciliation. Batas aktif 600 detik adalah agregat seluruh processing/retry per analysis run, terpisah dari waktu antre; processing_time_reservations mencegah task paralel mendapat jatah sisa yang sama. Native parse checkpoints yang sah tidak dijalankan ulang hanya karena stage berikutnya gagal.

Transport sementara dapat retry sesuai Bagian 13. Parser rusak, descriptor ambigu, limit format dan source unreadable bukan alasan retry tak terbatas. `blocked` dipakai untuk dependensi operasional yang bisa dipulihkan, misalnya provider/storage sementara tidak tersedia; reason dan resume_stage wajib. Check yang tidak dapat disimpulkan karena isi sumber cukup dicatat unassessable dan job selesai dengan batas.

Jika proses gagal total, review manual tetap dapat dibuat dari manifest dan specification. Worker tidak boleh menandai ready_for_review sebelum transaksi element records/candidate validation selesai; bila stage analisis berhenti dengan batas, isi record untuk semua elemen yang belum selesai menggunakan reason yang tepat.

Kunci fingerprint analysis: submission manifest hash + bound context/criterion/spec/numeric-policy versions + release manifest hash, termasuk parser resource profile/model/retrieval/prompt/rules/routing/batching manifests dan grounding/lint registry versions. Parser artifact key juga mengikat profile/options/artifact checksums; annotation manusia terikat review/publication hash, bukan diam-diam dimasukkan ke AI run lama. Retry byte/config sama memakai run sama; perubahan konfigurasi memakai run baru. Fingerprint cache tidak membolehkan lintas owner.

### 17.3 Reservasi dan ledger biaya

Initial budget account: confirmed_total=25.00 USD, reserve_floor=5.00 USD, planned_topup=0.00 USD. Saldo yang boleh dipakai = confirmed_total - reserve_floor - known spend - held reservations - pending maximum liability. Penambahan dana hanya lewat aksi operator yang mencatat jumlah dan referensi konfirmasi; planned_topup tampil di konfigurasi operasi saja dan tidak menambah saldo.

Sebelum setiap call berbayar, adapter menghitung upper-bound dari input, output maksimum, gambar/tool dan tarif manifest. Transaksi mengunci budget account, memeriksa budget per job dan global, lalu membuat reservation. Panggilan dilakukan setelah commit reservation. Jika harga atau batas billing yang diperlukan tidak diketahui, nonaktifkan call tersebut dan gunakan manual/unassessable; jangan memperkirakan biaya nol.

Setelah respons, simpan actual usage dan hitung actual cost memakai rates snapshot. Settle reservation ke actual cost dan lepaskan sisanya. Semua rerank, verifier, reasoning yang tertagih, image tokens, embedding, repair, retry dan draft feedback masuk ledger. Jangan menganggap cache/batch discount tersedia tanpa usage/harga yang mendukung.

Jika timeout/crash membuat status provider tidak diketahui, tandai reservation reconciliation_required; jangan langsung melepas saldo atau menganggap call gratis. Retry memerlukan reservation tambahan bila ada kemungkinan call pertama tertagih. Operator merekonsiliasi menggunakan provider request ID/billing; API tidak membuat jaminan exactly-once terhadap tagihan provider. Selisih usage yang melampaui estimasi membekukan call baru sampai manifest/ledger direkonsiliasi.

Tidak tersedia endpoint budget bagi mahasiswa. Konfigurasi dan ledger operasi melalui CLI/operator service atau antarmuka admin tabel sederhana; tidak membuat dashboard analitik produk. Budget habis tidak menghalangi dosen menyelesaikan feedback manual.

### 17.4 Observability

Log terstruktur: request/run/job/call IDs, stage, method manifests, query dan selected source IDs, omitted/truncated unit IDs, limits, latency, retry, failure code, usage, biaya dan aksi dosen. Tambahkan parser profile/checkpoint/admission lease refs, ground contract/gate result, routing decision/policy refs, batch membership/per-element omission, dan annotation revision refs; tidak menulis full feature source content/teks akademik ke log umum. Isi penuh tugas/prompt tidak ditulis ke log umum; artefak pemeriksaan rinci berada pada penyimpanan akademik berizin.

Endpoint `/health/live` hanya status proses; `/health/ready` memeriksa database/storage/broker yang diperlukan. Provider tidak harus sehat untuk readiness aplikasi manual; `/health/providers` internal menampilkan status adapter tanpa secret. Metrik internal latency, queue backlog dan error untuk operator diperbolehkan; tidak ada visualisasi kemampuan mahasiswa atau pola findings kelas pada UI.

## 18. Arsitektur aplikasi, dependency dan deployment

### 18.1 Stack dan batas modul

Gunakan modular monolith FastAPI dengan worker terpisah. Frontend Next.js/React mengakses API same-origin melalui reverse proxy. PostgreSQL+pgvector, Redis dan S3-compatible private storage adalah service pendukung. Tidak perlu microservices, GraphRAG, graph database, multi-agent, fine-tuning atau GPU serving untuk menyelesaikan MVP.

Baseline compatibility yang dipilih untuk implementasi: Python 3.12, Node.js 22, Next.js 16, React 19, TypeScript 5, Tailwind CSS 4, PostgreSQL 17, pgvector 0.8, Redis 7, Celery 5, SQLAlchemy 2, Pydantic 2, Alembic 1, Docling 2, python-docx 1, openpyxl 3, rank_bm25 0.2. Ini baseline engineering, bukan klaim versi terbaru. Agent harus memverifikasi patch yang kompatibel pada tahap B0, mengunci exact version dalam lockfile, dan mencatat penyimpangan major yang memang diperlukan sebelum implementasi bergantung padanya. Jangan melakukan floating upgrade ketika eksperimen dibekukan.

Pakai uv untuk Python lockfile dan pnpm untuk frontend lockfile. Paket pendukung: HTTP client dengan timeout, Argon2 library terpelihara, object-storage client, file signature detector, image decoder dengan decompression-bomb protection, PDF.js wrapper dan test runner. Versi patch dipilih dengan tes install/build nyata; jangan mencantumkan nomor patch tebakan dalam README. Dependency parsing native dan OCR beserta model artifact dipasang saat build/prefetch yang terkontrol, bukan mengunduh model dari URL isi dokumen saat runtime.

Modul backend: `identity`, `courses`, `assignments`, `submissions`, `documents`, `evidence`, `retrieval`, `checks`, `numeric`, `providers`, `review`, `feedback`, `publication`, `operations`. Setiap modul memiliki schemas, services dan repository; transaction boundary berada pada service. Komponen UI tidak memanggil provider model atau storage bucket langsung.

Struktur repository yang disarankan dan menjadi deliverable:

```text
align/
  apps/web/                     Next.js UI, route guards, viewers
  services/api/                  FastAPI app dan modul domain
  services/worker/               Celery entry point dan stage handlers
  packages/domain/               schema/enum Python bersama API dan worker
  migrations/                   Alembic version files
  prompts/                      prompt templates dan version manifests
  config/                       release manifests dan defaults tanpa secrets
  fixtures/synthetic/           sumber mixed-format dan expected records
  tests/unit/
  tests/integration/
  tests/e2e/
  research/                     protokol/offline evaluator; tidak diekspos UI
  scripts/                      bootstrap, seed, smoke, backup/restore
  docker-compose.yml
  .env.example
  README.md
  ALIGN_IMPLEMENTATION_SPEC_V2.md
```

### 18.2 Service Compose dan routing

Development Compose wajib menyediakan `web`, `api`, `worker`, `parser-worker`, `outbox-dispatcher`, `scheduler`, `postgres`, `redis`, `object-storage` dan reverse proxy. parser-worker adalah queue coordinator dari codebase worker yang sama; child parser terisolasi menerima input/output terbatas tanpa database/provider credentials. Queue parser dan queue general dipisahkan; max satu parser berat/node berlaku lintas seluruh proses worker. API dan worker memakai kode domain yang sama; parser dijalankan subprocess/container dengan limit dan egress terkontrol. Object storage development boleh MinIO; gunakan named volumes. Queue diberi prefix deployment agar dua environment tidak berbagi job.

Reverse proxy mengekspos web pada `/` dan API pada `/api/v1`; backend menerima path tersebut tanpa mengubah kontrak. `/health` dapat diproxy internal. Production memakai TLS, satu origin, bucket privat dan network database/broker tidak diekspos ke internet. Frontend tidak berisi API key atau storage credential. Set `Cache-Control: private, no-store` pada halaman/response akademik dan auth; tidak memakai shared cache untuk data mahasiswa.

Database roles terpisah: migration owner, API, analysis worker, feedback worker bila dipisah, dan operator read-only metadata. Parser tidak mendapat database credential atau provider API key; ia menerima file yang dibatasi dan mengembalikan artefak. Local test boleh menjalankan subprocess dengan resource limits, tetapi demo yang mengklaim parser terisolasi harus menguji konfigurasi yang benar-benar dipakai.

### 18.3 Konfigurasi minimum

| Environment/config | Default development dan syarat live |
|---|---|
| `APP_ENV` | development; production menolak debug/default bootstrap password |
| `APP_ORIGIN` | http://localhost:3000; HTTPS origin eksplisit untuk production |
| `DATABASE_URL`, `REDIS_URL` | Service Compose; secret tidak di-commit |
| `S3_ENDPOINT`, `S3_BUCKET`, credentials | Bucket privat development; tidak ada public read |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`, `S3_USE_PATH_STYLE` | Secret backend; region default us-east-1 dan path-style true untuk MinIO lokal; endpoint HTTPS pada production |
| `CSRF_SERVER_SECRET` | Minimal 32 byte acak, tidak di-commit; rotasi membatalkan session yang bergantung padanya |
| `PROVIDER_MODE` | mock; live hanya setelah key dan manifest lengkap |
| `OPENAI_API_KEY` | Kosong pada mock; hanya backend secret pada live |
| `MODEL_EFFICIENT`, `MODEL_COMPLEX`, `MODEL_VISION` | Diisi dari model yang tersedia pada akun, lalu capability smoke test; tidak auto-substitute diam-diam |
| `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` | text-embedding-3-small dan 1536; mock memakai vector fixture dengan dimensi sama |
| `MODEL_RATE_MANIFEST` | File JSON versioned dengan tarif terverifikasi; tanpa rate yang diperlukan, paid call dinonaktifkan |
| `OCR_ENGINE`, `OCR_LANGUAGE` | Tesseract lokal, bahasa Indonesia+Inggris bila traineddata tersedia; startup memeriksa artifact; backend alternatif boleh melalui manifest |
| `BUDGET_CONFIRMED_USD`, `BUDGET_RESERVE_USD` | Inisialisasi sekali 25.00 dan 5.00; setelah itu ledger database yang otoritatif, bukan mengisi ulang saldo tiap restart |
| `MAX_*`, `JOB_*` | Nilai default Bagian 6/13/17, tersimpan pada job/release snapshot |
| `PARSER_PROFILE_NATIVE`, `PARSER_PROFILE_DOCUMENT` | native_ooxml_v1 dan docling_native_v1; IDs/bootstrap keys diselesaikan ke immutable manifest; light tidak otomatis dipilih |
| `PARSER_NATIVE_MEMORY_BYTES`, `PARSER_DOCUMENT_MEMORY_BYTES`, `PARSER_LIGHT_MEMORY_BYTES` | 2147483648, 8589934592, 4294967296; initial profile values, bukan override runtime terhadap release aktif |
| `PARSER_NODE_MEMORY_BUDGET_BYTES`, `PARSER_HEAVY_MAX_PER_NODE` | 8589934592 dan 1; deployer harus menyediakan kapasitas host/OS/service lain yang cukup dan mencatatnya |
| `PARSER_PAGE_BATCH_SIZE`, `PARSER_TASK_SOFT_SECONDS`, `PARSER_TASK_HARD_SECONDS` | 1, 120, 150; hard/soft efektif tidak melewati sisa run; native stream unit sesuai format |
| `ROUTING_POLICY_FILE`, `BATCHING_POLICY_FILE` | config/routing.baseline.json dan config/batching.baseline.json; bootstrap/register menjadi manifest immutable; baseline batch=1 |
| `BATCH_BASELINE_ELEMENTS`, `BATCH_MAX_ELEMENTS` | 1 dan 5; kandidat profile 3/5 perlu validasi; policy tidak menaikkan cap |
| `STAGE_SOFT_SECONDS`, `STAGE_HARD_SECONDS`, `BROKER_VISIBILITY_SECONDS` | 120, 150, 300 |
| `WORKER_LEASE_SECONDS`, `WORKER_HEARTBEAT_SECONDS`, `REAPER_INTERVAL_SECONDS` | 180, 30, 60; lease/fencing tidak mengizinkan child parser ganda |
| `DATA_MODE` | synthetic; real-data mode memerlukan policy config dan institutional authorization recorded |
| `RETENTION_SOURCE_DAYS`, `RETENTION_REVIEW_DAYS`, `BACKUP_RETENTION_DAYS` | 90, 365, 30 untuk data sintetis; live memerlukan nilai institusi eksplisit serta policy reference |
| `LMS_ADAPTER_ENABLED` | false untuk MVP; tidak menyembunyikan ketergantungan LMS dalam core |

Jika live model belum dikonfigurasi, aplikasi tetap dapat dijalankan untuk seluruh alur manual dan demo mock yang ditandai. Jangan menggunakan nama model yang belum diverifikasi hanya karena terdapat pada paper lama. Satu release baseline live dianggap terpilih setelah operator memasukkan identifier yang tersedia, tarif dan capability check lolos; hasilnya menjadi manifest immutable, bukan keputusan tersembunyi di kode.

Setiap proses memperoleh DATABASE_URL untuk role-nya sendiri; Compose tidak memakai satu credential superuser untuk API/worker. `.env.example` menyebut semua limit dengan key konkret: `MAX_FILES_PER_BUNDLE=5`, `MAX_FILE_BYTES=20971520`, `MAX_IMAGE_PIXELS=25000000`, `MAX_PDF_IMAGE_PAGES_PER_BUNDLE=100`, `MAX_XLSX_SHEETS=25`, `MAX_XLSX_NONEMPTY_CELLS=100000`, `MAX_DOCX_NODES=20000`, `MAX_DOCX_MEDIA=1000`, `JOB_MAX_CALLS=16`, `JOB_MAX_VISION_CALLS=4`, `JOB_MAX_TEXT_INPUT_TOKENS=40000`, `JOB_MAX_TEXT_OUTPUT_TOKENS=8000`, `JOB_MAX_EMBEDDING_TOKENS=80000`, `JOB_MAX_ACTIVE_SECONDS=600`, `ANALYSIS_BUDGET_USD=0.20`, `FEEDBACK_BUDGET_USD=0.03`, `CONTEXT_DRAFT_BUDGET_USD=0.05`, `SPEC_DRAFT_BUDGET_USD=0.05`. Limit OOXML dan timeout mengikuti nama field typed config yang dihasilkan agent dan nilainya pada Bagian 6/17.

Config resource/policy dibaca untuk bootstrap release dan diverifikasi terhadap payload manifest aktif. Perbedaan env dengan release tidak menimpa fingerprint diam-diam: worker menolak job yang tidak kompatibel dan meminta operator membuat/menetapkan release yang cocok. `.env.example` juga menyediakan `MAX_OOXML_ENTRIES=10000` dan `MAX_OOXML_EXPANDED_BYTES=536870912`. Secret akun/harga/model yang tersedia tetap konfigurasi operasi; tidak ada produk decision terbuka. Headroom parser di atas tidak menyatakan deployment sekarang sudah memiliki RAM tersebut.

Perubahan dimensi embedding dari 1536 bukan sekadar env toggle: harus mempunyai manifest baru, migration kolom/index yang sesuai dan reindex sumber berizin. Startup menolak dimensi config yang berbeda dari schema database; jangan memotong/padding vector diam-diam.

### 18.4 Performa dan kapasitas awal

Target operasional development/pilot: 20 session pengguna bersamaan, general worker concurrency 2; parser-heavy concurrency maksimum 1 per node melalui coordinator terpisah dan reservation budget Bagian 8.5, bukan 1 per worker. Benchmark dan capacity report mencatat CPU/RAM host, resource service lain, profile/weights, cold/warm memory, timeout dan cakupan; aplikasi manual tidak menunggu seluruh kemampuan AI/profile empiris tersedia. API non-upload tidak melakukan parsing atau inference sinkron. Target p95 metadata/list/save request <1 detik pada environment staging yang dinyatakan, tidak termasuk transfer file/provider. Ukur dan laporkan hasil; ini acceptance target prospektif, bukan janji semua deployment.

Polling job default 5 detik saat halaman aktif, backoff 15 detik setelah 1 menit; hentikan saat terminal atau tab tidak aktif. Tidak perlu websocket untuk MVP. Waktu antre dan processing ditampilkan terpisah bila tersedia; jangan mengarang ETA. Pagination dan sumber detail dimuat on demand agar workspace tidak mengunduh seluruh bundle saat dibuka.

## 19. UI dan alur layar

### 19.1 Navigasi dan gaya

Brand ALIGN; bahasa Indonesia dengan istilah teknis yang perlu tetap jelas. Gunakan tampilan tenang dengan background `#F8FAFC`, teks utama `#0F172A`, aksen `#0F766E`, warning `#B45309`, error `#B91C1C`. Verifikasi contrast pada komponen nyata, jangan mengandalkan warna saja. Font system sans atau font self-hosted; tidak memerlukan CDN atau gambar generatif.

Home setelah login adalah daftar course. Navigation: Mata kuliah → Tugas → Pengumpulan/Review atau Feedback. Tidak ada halaman bernama Dashboard/Analitik, chart kelas, ranking, mastery heatmap, KPI kartu findings, atau route `/analytics`, `/scores`, `/grades`. Daftar pengumpulan/status operasional merupakan work queue, bukan dashboard analitik.

Komponen status selalu memakai teks dan ikon. Gunakan "kandidat temuan", "dikonfirmasi dosen", "belum dapat diperiksa", dan "belum diterbitkan". Jangan menampilkan confidence AI sebagai persentase kepastian akademik atau memakai label lulus/gagal untuk mahasiswa.

### 19.2 Layar dosen

| Layar | Konten dan aksi | Empty/error state wajib |
|---|---|---|
| Course list/detail | Course berizin, tugas, tautan konteks; roster minimal | Belum ditugaskan; akses dicabut |
| Context editor | Panel field, source anchors, konflik KK/RPP, manual correction, approve | Parsing gagal → isi manual; konflik belum selesai → approval disabled dengan daftar field |
| Assignment editor | Instruksi asli, criteria/descriptor, requirements, mapping CPMK opsional, mode/jadwal | Jangan tampilkan field bobot atau skor; draft belum lengkap menunjukkan field yang kurang |
| Spec editor | Element list, decision question, sumber derivasi berdampingan, grounding mode/roles, applicability, evidence types, relationships, DAG, coverage/numeric policy, boundary, lint report dan resolusi | Blocking lint/hash tidak cocok → approval disabled; review issue belum diakui → daftar aksi; tuntutan baru memerlukan perubahan paket resmi |
| Publish assignment | Ringkasan yang akan dilihat mahasiswa dan versi yang dikunci | Approval/hash tidak cocok → kembali ke langkah yang perlu disahkan |
| Submission list | Owner/group, versi, waktu, late label, status job dan status review/publication; filter status/pencarian | Belum ada pengumpulan; provider unavailable tidak membuat halaman gagal |
| Evidence workspace | Criterion → tuntutan/decision question → sumber/role/coverage → observasi/interpretasi AI → keputusan dosen; selection/highlight dua arah; annotation revisions | Semua elemen tetap ada; dukungan parsial/human_only jelas; unsupported/timeout memberi alasan dan original; tidak ada confidence akademik atau dashboard |
| Feedback editor | Confirmed findings, limitation, draf, edit, approve, publish | Tidak ada finding → template netral; sumber keputusan berubah → stale banner |
| Publication history | Released/withdrawn versions dengan source label | Versi lama tetap jelas; tidak menampilkan draft sebagai hasil resmi |

Evidence workspace desktop memakai dua panel: sumber di kanan, elemen/keputusan di kiri; lebar dapat diubah. Tampilan <768 px memakai tabs "Pemeriksaan", "Bukti", "Keputusan" dan mempertahankan elemen yang dipilih. Seluruh elemen dapat ditelusuri; filter finding tidak menghapus akses ke no_issue/unassessable/not_applicable.

Dua mode review diperbolehkan: per submission atau per criterion lintas submission pada assignment yang sama. Mode criterion menavigasi pekerjaan satu per satu, tidak mengelompokkan jawaban untuk keputusan massal dan tidak memberi ringkasan pola findings kelas. Satu aksi tidak mengonfirmasi kandidat mahasiswa lain.

Accept dan reject sama mudah diakses. Tidak ada default accept, auto-accept setelah timeout, atau tombol bulk approve seluruh kandidat. Publikasi memakai konfirmasi konkret yang memperlihatkan owner, versi sumber, findings yang disertakan, limitation dan teks akhir; ini aksi akademik dosen dalam aplikasi.

State selection/highlight/review correction wajib mengikuti Bagian 14.1.1. Aksi “Perbaiki lokasi”, “Koreksi transkripsi” dan “Ubah tautan bukti” menunjukkan preview sumber/hash/versi sebelum disimpan. Menghapus tautan menampilkan finding yang perlu ditinjau ulang, tidak menghapus source. History internal dapat dibuka dosen per anotasi; tidak membuat halaman analitik. Fokus keyboard tetap pada elemen/aksi yang logis setelah load sumber atau pindah tab; hover tidak menjadi satu-satunya cara mengakses relasi bukti.

### 19.3 Layar mahasiswa

1. Daftar tugas terbit dan detail instruksi/criteria/requirements/bentuk bukti.
2. Upload bundle: progress teknis, alasan format/ukuran ditolak, daftar file, preview original dan tombol finalize.
3. Receipt final: versi, file, timestamp server, label late, serta status feedback belum diterbitkan.
4. Hasil resmi: feedback, confirmed findings, bukti tertaut dan limitation; download JSON/Markdown.
5. Riwayat versi hasil yang boleh diakses. Untuk kelompok, label pekerjaan kelompok dan roster snapshot; tidak ada kartu kemampuan individual.

Mahasiswa tidak melihat proses AI sebagai percakapan dan tidak dapat meminta model memberi feedback sebelum publication. Tidak ada skor atau dashboard pengganti dengan jumlah masalah sebagai ukuran kualitas.

### 19.4 Aksesibilitas

Target WCAG 2.2 AA sebagai standar desain/pengujian: heading semantik, label form/error yang terhubung, focus terlihat, keyboard navigation, focus management modal, tabel dengan header, teks alternatif untuk ikon, dan pesan status `aria-live` yang tidak berisik. Sediakan label locator tekstual seperti "Laporan.pdf halaman 4" selain highlight warna. Viewer harus tetap menyediakan teks hasil ekstraksi dan download original; jangan membuat semua bukti hanya dapat dipahami melalui gambar.

Tidak ada information loss pada 360 px, 768 px dan 1440 px untuk aksi inti. Table/grid besar boleh horizontal scroll dengan konteks label. Test otomatis aksesibilitas dilengkapi pemeriksaan keyboard alur login-upload-review-publish.

## 20. Privasi, keamanan dan pemeliharaan

### 20.1 File dan parser

Parser menerima hanya file dari manifest/source resmi, dijalankan non-root, dengan working directory khusus, read-only dependencies, batas CPU/memori/waktu dan tanpa akses secret. Tidak mengikuti external links/embedded scripts atau menjalankan macro. Batas dekompresi dan image pixel diperiksa sebelum pemrosesan berat. File keluaran parser tidak dianggap HTML aman.

Egress parser default deny setelah dependency/model artifact terpasang. Worker provider boleh mengakses endpoint yang di-allowlist melalui adapter, bukan URL dalam dokumen. Dokumen yang mengandung perintah "abaikan rubrik", URL atau instruksi mengubah role tetap diperlakukan sebagai isi tugas.

### 20.2 Data minimization dan akses

Pisahkan identitas pengguna dari representasi prompt sejauh mungkin. Gunakan pseudonymous submission ID; hilangkan nama/NIM/email dari metadata sebelum embedding/provider bila tidak diperlukan. Isi dokumen dapat mengungkap identitas sehingga jangan mengklaim anonymization sempurna. Simpan original untuk dosen dan mahasiswa berizin; redaksi turunan memiliki lineage dan tidak menimpa original.

Gunakan TLS dan encryption-at-rest yang tersedia pada deployment. Object storage privat; URL viewer melalui endpoint terotorisasi atau URL sementara yang scope/time-nya dibatasi. Jika memakai URL sementara di internal renderer, TTL maksimum 5 menit, tidak dimasukkan ke export/publication permanen. Pemeriksaan izin ulang tetap dilakukan pada penerbitan URL.

### 20.3 Pencegahan kebocoran dan abuse

- Cookie auth same-origin dan CSRF Bagian 4; CORS tidak memakai wildcard dengan credential.
- Normalisasi filename, block path traversal, escape extracted HTML/Markdown, batasi link aktif pada feedback; external links tidak diambil otomatis.
- Renderer feedback mengizinkan paragraf, list, emphasis, code dan tabel sederhana; raw HTML, script, iframe dan remote image dinonaktifkan. Link aktif hanya relatif ke route ALIGN yang diotorisasi; URL eksternal dari isi tugas/feedback ditampilkan sebagai teks, bukan di-fetch atau di-embed. PDF viewer menonaktifkan JavaScript/launch action dan tidak membuka external annotation otomatis.
- Rate limit upload default 20 file/user/jam dan 5 finalize/thread/jam; dosen retry job 10/course/jam; pelanggaran memberi 429 dan tidak memulai pengeluaran model. Limit konfigurabel untuk staging/pilot.
- Jangan melakukan global dedup yang mengungkap apakah file mahasiswa lain ada. Content cache selalu scoped.
- Frontend/private API tidak boleh di-cache publik. Error/report tidak berisi secret, database URL, provider raw payload atau isi submission lain.
- Access test wajib mencakup dosen course A, mahasiswa course A/B, kelompok berbeda, anggota yang dicabut dan role admin yang tidak punya penugasan akademik.

### 20.4 Integritas versi

Hash diperiksa saat sumber dibaca untuk parsing dan sebelum viewer mengaitkan anchor. Hash mismatch mengarantina sumber/artefak dan memblokir auto-check/publish baru atas data yang tidak cocok; publication historis tidak ditulis ulang. Tampilkan batas kepada dosen dan arahkan recovery dari original yang benar/backup terverifikasi.

Metode/model alias yang berubah menghasilkan manifest release baru jika konfigurasi yang diamati berubah. Simpan provider model identifier aktual, request time dan version jika tersedia; jangan menjanjikan hasil inference identik hanya dari alias yang sama.

### 20.5 Audit dan operasi

Audit minimum: login/logout/reset/revoke, perubahan enrollment/kelompok, konteks/paket/spec approval, publish assignment, finalize/reopen, analysis/retry/config release, semua aksi review, feedback approval, publish/withdraw, budget top-up/reconciliation, export sensitif dan purge. Simpan request ID untuk penelusuran. Tidak ada email atau Slack yang dikirim sebagai efek samping audit.

### 20.6 Backup dan pemulihan

Default deployment pilot: backup PostgreSQL harian dan object storage snapshot/versioning sesuai dukungan, encryption serta akses operator terbatas. Target awal RPO 24 jam dan RTO 8 jam; uji restore sintetis sebelum real-data mode. Redis dapat dipulihkan dari job/outbox database; tidak perlu mengandalkan backup Redis untuk mempertahankan keputusan.

Sesudah restore, jalankan reconciliation pointer/hash/job/ledger serta parser child-process inventory/admission leases sebelum memulai task parser baru; terapkan tombstone penghapusan sebelum layanan dibuka. Jangan otomatis mengulang paid call berstatus tidak diketahui. Dokumentasikan hasil restore test dan keterbatasan kapasitas staging.

### 20.7 Retensi dan penghapusan

Nilai 90/365/30 hari pada Bagian 18 adalah default demo sintetis. Untuk data nyata, institusi menetapkan tujuan, pihak akses, pemrosesan eksternal, source/review/backup retention, penghapusan dan izin yang berlaku. Operator merekam policy reference dan masa berlaku konfigurasi; aplikasi tidak membuat kesimpulan kepatuhan hukum.

Purge terotorisasi menghapus original, turunan, embedding, isi evidence/review/feedback/publication yang masuk cakupan dan membuat tombstone minim data serta audit perintah. Histori immutable tidak boleh dipakai sebagai alasan menyimpan isi pribadi selamanya. Backup terhapus melalui siklus retensi; tombstone diterapkan pada restore. Mahasiswa meminta koreksi melalui dosen; tidak ada chatbot wajib. Penggunaan untuk melatih model baru tidak menjadi bagian scope.

Sumber yang telah dipurge ditampilkan sebagai tidak tersedia sesuai retensi, bukan sebagai parser failure atau bukti bahwa persyaratan tidak ada. Otorisasi diperiksa sebelum status sumber expired/purged diberikan; pengguna berizin mendapat 410 SOURCE_PURGED dan label yang jelas, pengguna di luar scope tetap 404. Bila isi publication juga dipurge, jangan menyajikan snapshot kosong sebagai hasil resmi yang masih utuh.

## 21. Protokol evaluasi terpisah dari produk

Bagian ini mengatur pengujian dan penelitian offline; tidak menambahkan dashboard atau analytics endpoint. Data penelitian/gold label tidak boleh tersedia pada retrieval produksi. Keberhasilan software dibedakan dari keberhasilan penelitian.

### 21.1 Dataset dan provenance

- Survei kebutuhan awal: aktual menurut konfirmasi pemilik produk, 21 responden (1 dosen, 20 asisten praktikum). Jangan mengarang respons mentah yang tidak tersedia pada repository.
- Pilot teknis: 60 bundle, terpisah dari evaluasi utama; untuk parser, codebook, UI dan perkiraan dukungan kategori.
- Evaluasi utama: development 180 bundle dari 3 course SI + 3 Manajemen; validation 60 dari 1 SI + 1 Manajemen; frozen test 60 dari 1 SI + 1 Manajemen. Total utama 300; termasuk pilot jumlah 360 bundle, bukan 300.
- Course-disjoint berlaku pada tiga partisi utama. Manifest pilot juga harus dicatat dan diaudit terhadap kebocoran; jangan menyebut pilot course-disjoint dari seluruh partisi bila belum ditetapkan/dibuktikan oleh data yang diperoleh.
- Derived/mutated files mengikuti parent partition. Audit exact/near duplicates, shared template, repeated student dan group membership sebelum freeze. Anggota kelompok, file, cell dan repeated model runs bukan mahasiswa/observasi independen baru.
- Gold dibangun dua rater kompeten per disiplin tanpa melihat output ALIGN atau keputusan dosen di aplikasi. Kalibrasi 10–15 development examples sebelum codebook freeze, laporkan agreement/prevalence/support/nominal Krippendorff alpha sebelum adjudication. Kasus ambigu tetap unassessable.

### 21.2 Label dan denominator

Unit gold: `(bundle_version, obligation/check target, issue_category)`; numeric unit menyertakan comparable quantity/context. Jangan menggunakan label present/absent tanpa objek. Gunakan:

- `applicability=applicable|not_applicable|unresolved`.
- `gold_assessability=assessable|unassessable`.
- `issue_label=issue_present|issue_absent|null`; null wajib ketika gold unassessable/not_applicable.
- Evidence relation dan source coverage adalah field terpisah dari issue_label.

Matching prediction–gold satu-ke-satu berdasarkan target/obligation key, kategori dan context numerik, bukan fuzzy text saja. Prediksi duplikat atas satu gold menambah duplicate burden dan tidak memperoleh TP tambahan. Adjudicator menetapkan mapping untuk contoh yang sulit, dengan riwayat.

| Metrik | Denominator/aturan wajib |
|---|---|
| Precision per kategori | TP/(TP+FP) pada gold applicable dan assessable; denominator nol → NA |
| End-to-end recall | TP/(TP+FN) pada gold issue_present; parse/retrieval/abstention sistem pada gold positif tetap FN |
| F1 | 2PR/(P+R); NA jika P/R tidak terdefinisi, jangan mengisi 0/1 sembarangan |
| Selective decision coverage | Gold applicable+assessable unit yang memperoleh keputusan sistem no_issue/finding dibagi seluruh gold applicable+assessable unit |
| Selective risk | Keputusan sistem yang salah dibagi keputusan sistem yang dibuat pada himpunan tersebut; coverage nol → risk NA |
| Gold-unassessable decision violation | Jumlah gold unassessable yang dipaksa sistem menjadi finding/no_issue, dibagi seluruh gold unassessable; laporkan terpisah agar tidak disembunyikan oleh precision |
| Operational document coverage | File final yang seluruh source unit inventarisnya selesai diparse pada dukungan yang dinyatakan, dibagi seluruh file final yang diterima; partially/failed bukan full covered; laporkan format/support count |
| Source inspection coverage | Unit kompatibel yang benar-benar diinspeksi per check / unit kompatibel yang wajib; tidak sama dengan document coverage atau decision coverage |
| Anchor Validity | Locator yang dapat ditelusuri ke sumber+versi tepat / semua locator yang disitasi; tidak ada locator → NA |
| Evidence Support Rate | Finding yang didukung sumber/coverage menurut rater independen / semua finding yang dievaluasi; absence diuji melalui requirement dan inspeksi, bukan kutipan fiktif |
| False findings per bundle | FP count / bundle yang dievaluasi; sertakan duplicate count dan gold-unassessable violations |
| Biaya | Semua call/retry/repair/vision/embedding; laporkan per bundle, per correctly supported finding dan per approved feedback dengan denominator eksplisit |

Contoh: 3 gold assessable unit, dua issue_present dan satu issue_absent; sistem menemukan satu positif, abstain pada positif lain, dan no_issue pada negatif → TP=1, FN=1, TN=1, FP=0; precision=1, recall=0.5, coverage=2/3, selective risk=0. Tidak boleh menghapus unit abstain dari denominator recall.

### 21.3 Baseline, freeze dan batas klaim

B0 deterministic parser/rules; B1 direct strong model dengan approved context; B2 filtered bundle-only RAG dengan strong model yang sama; B3 full ALIGN. Ablasi: A1 tanpa deterministic-first routing, A2 tanpa gate, A3 tanpa structure-preserving representation/anchors, A4 satu fixed strong model menggantikan adaptive roles. Ablasi hanya pada evaluator offline, bukan opsi mematikan guardrail pada aplikasi mahasiswa.

Konfigurasi dipilih pada development/validation, kemudian frozen: dataset manifest, codebook, model/prompt/parser/retrieval/rules, operating point, matching rules, denominator dan rencana klaim. Regression suite diambil dari development dengan fixture/split yang terpisah dari final test. Frozen test tidak digunakan sebagai bahan memperbaiki prompt lalu mengklaim versi baru masih unseen pada test yang sama.

Target prospektif: precision >=0.85 per kategori, end-to-end recall >=0.70, operational document coverage >=0.80. Sertakan support count dan 95% interval; support tidak memadai → exploratory. Point estimate melewati target bukan bukti safety atau generalisasi. Laporkan parse loss, acquisition failure, interpretation error, unsupported claim dan abstention terpisah.

Default evaluator ketidakpastian: 10.000 assignment-cluster bootstrap resamples, seed 20261018, seluruh unit/bundle assignment ikut bersama, stratifikasi pada dua course frozen test bila jumlah assignment memungkinkan. Laporkan percentile 2.5/97.5 sebagai interval bersyarat pada course yang diamati. Jika assignment independen kurang dari 5, interval cluster diberi label descriptive/unstable; bundle-cluster bootstrap hanya analisis sensitivitas, bukan cara membuat klaim konfirmatori. Replicate dengan denominator kategori nol tidak diubah menjadi nilai sempurna; laporkan jumlah replicate valid dan tandai interval tidak stabil bila dukungan tidak memadai. Tidak mengklaim generalisasi lintas institusi dari dua course.

Studi dosen membandingkan manual versus ALIGN-assisted review dengan kasus sebanding tidak identik, urutan counterbalanced, active review time, issue recall, false acceptance, edit/reject/defer, dan kualitas feedback final blind bila feasible. Tidak mengukur kesepakatan skor akademik karena skor tidak ada dalam produk. Waktu setup konteks/spec dicatat terpisah dan diamortisasi.

Studi mahasiswa menargetkan 64 peserta unik untuk pemahaman kriteria, penjelasan berbukti dan tindakan berikutnya; repeated item response bersarang pada peserta. Feedback kedua kondisi disahkan dosen dan isi/panjang dibuat sebanding. Hasil belum boleh diklaim sebelum penelitian aktual. Budget penuh multi-arm tidak dianggap tersedia hanya karena total kredit awal US$25; keputusan menjalankan eksperimen mengikuti dana terkonfirmasi dan usage yang diukur.

### 21.4 Validasi dan promosi policy routing/batching

Policy baseline adalah konfigurasi software yang eksplisit, bukan klaim target penelitian telah tercapai. Baseline routing Bagian 13.5 dan batch 1 dapat dipakai setelah capability/guardrail smoke lulus. Optimasi efficient routing atau batch 3/5 berstatus candidate sampai mempunyai laporan nyata yang eligible. Tidak perlu menjalankan eksperimen berbayar tanpa dana/kredensial untuk menyelesaikan build: evaluator, fixture, schema laporan dan enforcement harus tersedia; kemampuan yang belum teruji tetap baseline/nonaktif.

CLI wajib: `align evaluate policies --candidate MANIFEST_ID --baseline BASELINE_ID --dataset-manifest PATH --output PATH`. CLI memakai adapter/ledger/budget guardrail aplikasi yang sama, tidak jalur API key tanpa batas. Ia menghasilkan JSON dan Markdown offline, bukan endpoint dashboard. Dataset/protocol/manifest IDs dan mode mock/live ditulis jelas. Hasil mock hanya membuktikan evaluator/gate, tidak mengubah policy live menjadi validated.

Sebelum eksperimen, tetapkan affected task families/categories/modalities, gold/adjudication, unit matching, split, repeats, margins, support thresholds dan batas biaya. Gunakan development untuk merancang policy; gunakan validation untuk memilih/promosi. Semua variasi mengikuti parent partition. Jangan memilih kriteria/bundle yang mudah setelah melihat hasil. Sumber/konteks/approved spec, model/prompt/schema/limits dan daftar pekerjaan harus sama pada paired baseline/candidate kecuali faktor yang memang diuji. Bandingkan actual end-to-end cost termasuk parse/retrieval/verifier/repair/embedding/vision yang relevan.

Default gate promosi operasional v2 berikut wajib tersedia di evaluator. Ini penetapan engineering untuk mengunci keputusan, bukan angka dari paper atau bukti statistik generalisasi. Perubahan gate harus dipraregistrasikan sebagai versi protokol baru sebelum data validation berikutnya dilihat; tidak mengendurkannya setelah gagal pada data yang sama.

| Syarat | Ambang/promosi |
|---|---|
| Invariant/critical fixtures | Seluruh AC yang relevan lulus; nol tambahan pelanggaran anchor, absence, numeric, source/owner scope, publication atau budget pada critical fixtures |
| Dukungan evaluation | Pada tiap kategori terdampak: minimal 20 gold issue_present applicable+assessable dan 20 predicted findings pada masing-masing baseline/candidate untuk precision/ESR; minimal 5 assignment independen pada validation; bila kurang hasil exploratory/incomplete dan policy tidak dipromosikan |
| Precision per kategori | candidate >= max(0,85, precision baseline) |
| Evidence Support Rate per kategori | candidate >= ESR baseline; seluruh finding pada himpunan paired dievaluasi rater berkompeten sesuai codebook, bukan menerima confidence model |
| End-to-end recall per kategori | candidate >= max(0,70, recall baseline - 0,02) |
| Selective decision coverage | candidate >= coverage baseline - 0,02; laporkan perubahan per modality/task family agar penurunan kelompok tidak disembunyikan aggregate |
| Gold-unassessable decision violation | Tidak lebih tinggi dari baseline; seluruh critical unassessable fixtures tetap abstain sebagaimana seharusnya |
| Penghematan | Mean actual cost per paired bundle candidate <= 0,90 × baseline; tidak ada usage/reservation pending yang diperlakukan nol; failure/partial coverage tetap masuk denominator |
| Variasi output | Jalankan 3 repeat pada subset validation yang dipilih sebelum eksperimen, mencakup setiap affected task family; critical invariant tetap lulus pada seluruh repeat; laporkan variasi keputusan/coverage dan biaya, tidak menganggap repeat sebagai peserta baru |
| Release compatibility | Core hash policy, model/prompt/schema/grounding/resource refs dan scope modalitas tepat cocok dengan laporan |

Selisih 0,02 adalah dua percentage points absolut. Tidak ada denominator nol yang diubah menjadi perfect score. Target document coverage Bagian 21.3 tetap dilaporkan; penghematan yang timbul dari parse failure/abstention harus terlihat, bukan disebut otomatis peningkatan efisiensi. Jika invariant/gate gagal atau support/repeats/dana tidak cukup, result ineligible/incomplete; baseline tetap berfungsi. Gate point estimate di atas bukan uji noninferiority statistik. Interval/support/cluster dependence wajib dilaporkan sesuai Bagian 21; klaim noninferiority memerlukan rencana analisis dan power tersendiri yang belum diklaim oleh gate ini.

PolicyValidationReport immutable menyimpan protocol/dataset/codebook/split hashes, provider_mode, data_origin dan allowed_data_modes, candidate dan baseline core hashes, exact model/prompt/parser/resource/schema refs, scope families/categories/modalities, unit/bundle/assignment support, per-category metric numerators/denominators/intervals, violation/repeat results, latency/cost ledger refs dan gate result. Laporan synthetic-only hanya mengizinkan optimasi pada DATA_MODE=synthetic; pemakaian optimasi pada data nyata memerlukan validation data representatif/berizin sesuai protokol dan institutional authorization yang dicatat. Scope ini tidak menjanjikan generalisasi di luar task/modalitas/disiplin yang diuji. Core hash policy dihitung dari schema/rules/grouping/role/limits/scope/compatibility, tanpa state/version label/report ref/registration metadata. Promosi membuat manifest baru state=validated dengan core yang identik dan validation_report_ref; mengubah rule/core sekecil apa pun memerlukan laporan baru. Laporan tidak sekadar menunjuk filename yang isinya dapat berubah.

Operator register/activate memverifikasi hash report dan core serta exact compatibility. Policy state=validated dengan laporan tidak cocok ditolak. Model/prompt/schema/resource berubah → optimasi yang tidak lagi cocok kembali candidate/baseline pada release baru; run lama tidak diganti. Tidak ada auto-training dari accept/edit/reject/defer runtime.

Learned FrugalGPT scorer/optimizer dan ensemble tetap ditunda. Eksperimen routing dapat membandingkan efficient-only pada task yang sah, direct-complex, dan adaptive policy. Eksperimen batching membandingkan 1/3/5 pada semantic families yang sama. Semua percobaan mengonsumsi dana aktual; initial credit US$25 tidak dianggap cukup untuk seluruh protokol. Ketiadaan dana adalah alasan menunda eksperimen/promosi, bukan menghapus enforcement atau memalsukan hasil.

### 21.5 Compiler fidelity dan executor robustness

CLI wajib: `align evaluate robustness --protocol PATH --dataset-manifest PATH --release RELEASE_ID --output PATH`. Output JSON/Markdown memisahkan dua eksperimen dan tetap menggunakan per-call budget/ledger bila live. Offline code tidak dipanggil dari UI mahasiswa.

1. Compiler fidelity: bentuk redaksi/paraphrase/urutan instruksi yang telah diperiksa manusia setara maknanya, compile menjadi draf, lalu bandingkan obligation mapping, applicability, accepted evidence alternatives, derivation fidelity, unsupported additions dan omissions. Kesamaan kata/hash bukan target. Dosen/rater menetapkan semantic equivalence dan source mapping sebelum penilaian output.
2. Executor stability: pada spec yang sama, permutasikan hanya elemen independen dengan DAG tetap sah, urutan evidence presentation yang tidak mengubah kelengkapan, serta label internal setara jika protokol memang mengujinya. Bandingkan hasil per elemen, evidence support, false findings, abstention/coverage dan actual cost; jangan menuntut status sama ketika akses sumber memang berbeda.

Variant formats mencakup bukti lengkap dalam tabel versus narasi yang sama-sama diizinkan, heading berbeda, format angka lokal setara, dan native versus scan yang keterbacaannya terverifikasi. Input blur/glyph rusak sengaja bukan ekuivalensi akses: ekspektasinya bisa abstain/limitation, tidak dipaksa meniru keputusan atas sumber bersih. Menambah syarat atau mengganti boleh menjadi wajib adalah negative fidelity fixture, bukan paraphrase setara.

Protocol menyimpan transformation_id, parent/source hashes, partition, human equivalence status/actor, expected invariant atau obligation mapping, presentation seed, exact manifests dan repeat_count. Default repeat_count=3 pada subset development yang dipilih sebelumnya, sepanjang dana tersedia; jika hanya mock/no live, labeli hasil sebagai software test dan jangan mengklaim stochastic reliability. Repeated calls bukan unit gold/student independen baru. Budget tidak cukup menghasilkan report incomplete dengan alasan, bukan implicit pass.

Regression suite tetap development-only, validation untuk pemilihan konfigurasi, frozen test tetap tidak dipakai tuning. Semantic variants dari satu parent selalu berada pada partition yang sama dan ikut audit near-duplicate. Hash berubah bila bytes/config berbeda; element_code/obligation lineage dapat dipertahankan hanya setelah kesetaraan maknanya sah. Tidak ada penulisan ulang rubrik published per mahasiswa atau penggantian prompt runtime tanpa manifest.

### 21.6 Traceability penelitian dengan DSRM

Sediakan `research/research_traceability.md` dan `research/iterations/` sebagai artefak offline. DSRM mengatur bagaimana masalah, tujuan, desain, demonstrasi, evaluasi dan komunikasi dihubungkan; bukan service/agent/operator pada pipeline tugas dan bukan proses waterfall wajib.

| Aktivitas | Record minimum |
|---|---|
| Problem identification/motivation | problem_id, sumber kebutuhan yang benar-benar tersedia, tanggal, stakeholder scope dan batas klaim; survei 21 responden bukan hasil efektivitas |
| Objectives | objective_id, problem_ids, definisi tujuan, FR/INV terkait, metrik/denominator, target prospektif atau qualitative evaluation criterion yang ditetapkan sebelum evaluasi |
| Design/development | decision_id, alasan metode, alternatif yang relevan, FR/INV, component/manifest/commit versions dan implementation refs |
| Demonstration | scenario_id untuk SI dan Manajemen, input sintetis/berizin/hash, langkah penggunaan, output source/evidence/review/version refs, actual outcome dan keterbatasan |
| Evaluation | evaluation_id, objective_ids, dataset/codebook/split/protocol/release hashes, baseline, metrik/support/interval/cost aktual, failures dan kesimpulan terbatas |
| Communication | release/report/document IDs, audience, kontribusi artifact/metode, hasil yang tersedia dan batas generalisasi; tidak mewajibkan publikasi jurnal sebelum build selesai |

Rantai wajib: problem → objective → design decision → FR/INV → artifact version → demonstration → evaluation metric/result → redesign decision → communication. Satu record dapat menaut beberapa item; jangan menempelkan hasil evaluasi yang belum terjadi. Setiap iteration record menyimpan iteration_id, hipotesis/masalah, perubahan dan versi sebelum/sesudah, data partition/skenario yang digunakan, hasil aktual, keputusan lanjut/ubah/hentikan dan konsekuensi terhadap freeze.

Template boleh mempunyai field `not_yet_available` dengan alasan ketika studi belum dilakukan. Demonstrasi manual/AI flow yang berjalan tidak disamakan dengan precision/recall terpenuhi atau feedback meningkatkan pemahaman. Semua angka empiris pada laporan harus berasal dari data/usage aktual yang dapat dirujuk. Software handover memerlukan template, trace links implementasi dan demo sintetis; penelitian empiris dan klaimnya mempunyai gate AC-RES terpisah.

## 22. Urutan pembangunan dan gate penyelesaian

Tahap B0–B8 adalah MVP yang diminta. B9 adalah kontrak sandbox setelah MVP; tidak menghalangi core dipakai mandiri. Agent tidak boleh menunda alur manual sampai model/adapter eksternal tersedia.

| Tahap | Pekerjaan konkret | Gate selesai yang dapat diperiksa |
|---|---|---|
| B0 Fondasi | Repository, baseline dependencies/lockfiles, Compose, migration awal, config validation, mock provider, synthetic seed, CI | Fresh checkout dapat install/build, migrate, seed, login; tidak ada secret/default password produksi |
| B1 Identitas dan konteks | Sessions/CSRF, roles/enrollment/groups, context extraction/manual/conflict approval | Cross-course access test lolos; konflik KK/RPP dapat diselesaikan dan approved snapshot terbentuk |
| B2 Tugas dan spesifikasi | Instruksi utuh, criteria/requirements, CPMK opsional, schema v2/derivation/decision question/grounding/DSL/DAG/NumericPolicy, lint/resolutions/approval/publish | Tidak ada nilai; unknown ref/operator/contract/cycle ditolak; lint stale memblokir approval; tuntutan publik utuh |
| B3 Submission dan viewer | Upload/version/roster, storage, native/Docling resource profiles, admission/checkpoint/assembly/benchmark, registry dan empat viewer | Mixed-format final; original/anchors tepat; OOM/timeout tidak menghilangkan unit; profile enforcement/benchmark nyata dilaporkan; AI belum wajib |
| B4 Review manual dan release | Semua elemen, linked selection, manual evidence/annotation revisions/findings, terminal decisions, feedback/approve/publish/export | Provider-off end-to-end; koreksi tidak menimpa original/histori; annotation change membuat feedback stale; IDOR/double-publish lolos |
| B5 AI executor | Structural/hybrid retrieval, applicability/DAG, typed observations/support bindings, grounding/semantic verification/negative gate/dedup, baseline routing dan batch 1 | Fixtures positive/absence/partial/unreadable/human_only benar; quote valid tidak otomatis berarti klaim didukung; omission tidak menambah coverage |
| B6 Numeric dan biaya | Normalisasi/comparability/Decimal, provider/routing/batching manifests, decision logs, biaya/waktu reservations, retry/reconciliation | Angka/cadangan/limit benar; policy fingerprint stabil; kandidat batch tidak aktif tanpa validasi; no-key tetap manual |
| B7 UX dan robustness | Responsive/keyboard/touch linked highlights, annotation history, concurrency/recovery/fencing, limits/restore/sanitization; compiler/executor regression runner | AC-MVP lolos; tiga viewport; manifest/partition enforcement; stability fixtures tidak menambah tuntutan atau menghapus sumber rusak |
| B8 Handover dan pilot readiness | README/commands/env/OpenAPI/migrations, test report, parser benchmark/capacity, offline policy/robustness evaluator dan research_traceability template | One-command dev, mock smoke, live synthetic bila tersedia; baseline usable; optimasi empiris/penelitian yang belum ada dilaporkan belum tersedia; tidak mengarang validation report |
| B9 Moodle sandbox | LTI 1.3 launch, identity/course/assignment mapping, read-only import submission, student feedback access | AC-SBX pada Bagian 23; feature flag off default; tanpa grade passback |

### 22.1 Kontrak sandbox Moodle B9

Moodle sasaran awal sandbox menggunakan API versi 4.5 sesuai paper; perubahan versi harus diverifikasi terhadap dokumentasi resminya. Core tidak memuat library Moodle pada domain checks. Pisahkan:

- **LTI 1.3:** secure launch dan konteks/role.
- **Moodle Web Services:** membaca metadata assignment/submission dan file yang diberi izin.
- **ALIGN Core:** seluruh versi konteks, spesifikasi, checks, review dan publication.

Tambahkan tabel sandbox `lti_platforms` (issuer, client_id, deployment_id, auth/token/JWKS URLs allowlisted), `lti_identity_bindings` (issuer, client, deployment, subject, local_user_id unique composite), `lms_course_assignment_mappings`, dan `lms_import_events` (external source identifiers/version/hash, local submission version, status, idempotency key). Secret WS disimpan sebagai secret backend, bukan metadata yang dapat dilihat mahasiswa.

Endpoint sandbox, hanya bila flag aktif: `/api/v1/integrations/lti/login`, `/lti/launch`, `/integrations/moodle/mappings` (admin/dosen terotorisasi), `/integrations/moodle/imports` (dosen), `/integrations/moodle/imports/{id}`. Bootstrap platform config oleh operator; tidak menerima JWKS/auth URL arbitrer dari launch payload.

LTI login menyimpan state+nonce acak single-use dengan TTL 5 menit; validasi issuer/client/deployment yang terdaftar, signature JWT melalui JWKS yang dipin/allowlisted, alg yang diizinkan, audience/azp, exp/iat, nonce, message type/version, resource link/context dan roles. Tolak replay/state tidak cocok. Jangan mencocokkan akun otomatis hanya dari email. Binding subject ke akun/enrollment dilakukan eksplisit sebelum pilot; role claim tidak memberi global admin atau akses course yang belum dipetakan.

Gunakan launch top-level/new tab sebagai default untuk menghindari asumsi third-party cookie. Jika platform mengirim form POST, validasi state/nonce one-time sebagai jalur launch tersendiri, tidak melemahkan CSRF endpoint core. Setelah launch valid, buat session lokal sesuai policy. Mahasiswa hanya menuju published result yang dimilikinya; dosen ke workspace berizin.

Web Services memakai akun/token dengan fungsi minimum yang diaktifkan admin sandbox, misalnya pembacaan assignment dan submission serta akses file yang sah. Verifikasi nama fungsi serta ketersediaannya pada Moodle sandbox sebelum memakai; jangan mengasumsikan semua installation menyalakannya. Adapter mempunyai fixtures response dan integration contract test tanpa server nyata. Token tidak pernah masuk browser/log/export; download dibatasi origin Moodle allowlisted dan metadata file yang terotorisasi, tidak mengikuti redirect ke host sembarang.

Import hanya final/submitted attempt yang dipilih, memetakan course-assignment-owner/group dan roster, memverifikasi source version/hash, kemudian membuat immutable manifest lokal. External attempt berubah → SubmissionVersion baru; retry sumber identik mengembalikan import yang sama. Original submission Moodle tidak dimodifikasi. Assignment/rubric lokal harus sudah disahkan dan dipetakan; impor tidak mengganti instruksi resmi diam-diam. Feedback tetap pada publikasi ALIGN yang dibuka melalui aktivitas/link Moodle. Tidak ada AGS/grade passback, write grade atau posting feedback otomatis ke gradebook Moodle.

Jika mapping, izin, revocation atau versi tidak dapat dipastikan, blokir import/launch objek itu dengan alasan yang aman; core standalone tetap berfungsi. Sandbox diuji dengan dua course, dua kelompok, role dosen/mahasiswa, akun revoked, replay launch, source update, retry dan publication visibility. Jangan mengklaim integrasi produksi dari keberhasilan mock test saja.

## 23. Acceptance criteria dan pengujian wajib

Gunakan pytest untuk domain/API/worker, PostgreSQL+pgvector sungguhan untuk integration tests, serta Playwright untuk UI. Jangan mengganti tes isolasi SQL/vector dengan mock yang selalu mengembalikan scope benar. Tes provider nyata hanya memakai sumber sintetis dan budget yang terkonfirmasi; hasil deterministik utama memakai mock provider/fixtures.

### 23.1 Matriks penerimaan MVP

| ID | Skenario | Hasil yang diwajibkan |
|---|---|---|
| AC-MVP-01 | Fresh install, migrate, seed sintetis, login dosen/mahasiswa | Semua service inti sehat dan alur manual tersedia |
| AC-MVP-02 | Mahasiswa/dosen mencoba course, submission, evidence atau download course lain | Ditolak server, termasuk vector query dan render URL; isi tidak bocor |
| AC-MVP-03 | Admin tanpa enrollment dosen meminta isi submission | Tidak mendapat akses akademik karena global role saja |
| AC-MVP-04 | Enrollment atau session dicabut | Akses berikutnya ditolak tanpa menunggu restart |
| AC-MVP-05 | KK dan RPP berbeda pada field konteks | Dua kandidat tersimpan; approval ditolak sampai dosen menyelesaikan konflik |
| AC-MVP-06 | Konteks dibuat manual tanpa CPMK | Dapat disahkan dengan status not_provided; tidak mengarang outcome |
| AC-MVP-07 | Instruksi menyebut missing value/outlier/scaling lalu diringkas AI | Instruksi asli dan hash tetap utuh; summary tidak mengganti tuntutan |
| AC-MVP-08 | Dosen mengisi criteria/spec | Tidak ada bobot, skor maksimum, grade, dashboard atau model CriterionScore |
| AC-MVP-09 | Unknown operator, cyclic dependency, predicate bebas, policy version salah | Approval/publish ditolak dengan field errors spesifik |
| AC-MVP-10 | Upload lima format yang didukung serta JPG extension JPEG | Diterima jika valid; viewer/provenance cocok |
| AC-MVP-11 | MIME palsu, zip traversal/bomb, oversized image/file, encrypted PDF | Ditolak aman; tidak memulai paid analysis |
| AC-MVP-12 | Dua anggota kelompok finalize bersamaan atau retry key sama | Tepat satu versi final dan satu job domain, receipt konsisten |
| AC-MVP-13 | Finalize ketika Redis mati | Manifest/job/outbox commit; analisis berjalan setelah broker pulih |
| AC-MVP-14 | Group membership berubah setelah final | Roster historis tidak berubah; anggota baru tidak memperoleh histori otomatis |
| AC-MVP-15 | Pengumpulan terlambat | accept_with_label menerima tanpa penalti; reject_after_due menolak sesuai snapshot |
| AC-MVP-16 | Teks/tabel/gambar DOCX, PDF bbox, XLSX merged/hidden dan JPG orientation | Anchor membuka region asli yang tepat; unit yang tidak didukung tetap tercatat |
| AC-MVP-17 | Hasil top-k kosong tetapi bukti ada di bagian lain | Tidak langsung missing; fallback inspeksi menemukan bukti atau mencatat unassessable |
| AC-MVP-18 | Diagram relevan tidak terbaca | Elemen unassessable; elemen independen lain tetap dapat selesai |
| AC-MVP-19 | Bukti positif cukup pada satu elemen | no_issue_detected dalam batas check, tanpa skor atau klaim seluruh tugas sempurna |
| AC-MVP-20 | Conditional applicability fact belum terverifikasi | unresolved/unassessable, bukan not_applicable otomatis |
| AC-MVP-21 | Dua query menghasilkan masalah obligation yang sama | Satu finding utama dengan gabungan tautan bukti/tuntutan |
| AC-MVP-22 | Dua masalah berbeda pada criterion sama | Tidak digabung hanya karena kalimatnya mirip |
| AC-MVP-23 | Evidence ID/version atau quote/model schema salah | Kandidat ditahan; dosen tetap melihat batas, tidak ada publikasi AI |
| AC-MVP-24 | Dokumen memerintahkan model mengubah rubrik/publish/buka course lain | Instruksi mahasiswa tidak mengubah operasi backend atau menghasilkan akses baru |
| AC-MVP-25 | Normalisasi 0,25↔25%, juta↔satuan, tanda negatif dan locale ambigu | Kesetaraan ditangani; ambiguity tidak ditebak |
| AC-MVP-26 | Gross/net, accuracy/precision, periode/skenario berbeda | Tidak menghasilkan numeric inconsistency karena angka berbeda saja |
| AC-MVP-27 | Selisih tepat pada batas, di atas batas, x/y nol | Rumus max benar; tepat batas konsisten; tidak ada divide-by-zero tak tertangani |
| AC-MVP-28 | Formula XLSX cache kosong/usang atau digit OCR belum verified | Unassessable pada check terkait; tidak memakai nilai sebagai operand pasti |
| AC-MVP-29 | Provider dimatikan atau budget habis | Dosen dapat membuat review manual, findings, feedback, approval dan publication |
| AC-MVP-30 | Dua dosen menyimpan keputusan terhadap revision yang sama | Satu sukses, yang stale menerima 409 tanpa kehilangan perubahan diam-diam |
| AC-MVP-31 | Kandidat rejected/deferred | Tidak menjadi kritik feedback; defer muncul sebagai limitation yang tepat bila relevan |
| AC-MVP-32 | Semua elemen direview tetapi sebagian unable_to_conclude | Review dapat complete dan terbit dengan batas yang jelas |
| AC-MVP-33 | Keputusan berubah setelah draft approved | Draft stale dan publish ditolak sampai teks/version disahkan lagi |
| AC-MVP-34 | Mahasiswa meminta endpoint AI/review internal | Ditolak; published serializer tidak menyertakan data internal |
| AC-MVP-35 | Publish ganda/replay request | Satu Publication per idempotency action; snapshot/actor/hash benar |
| AC-MVP-36 | Submission baru final setelah publication lama | Hasil lama berlabel sumber lama; tidak disajikan sebagai hasil versi baru |
| AC-MVP-37 | AI rerun atau model/prompt/parser berubah | Run/manifest baru; human review/publication lama tidak ditimpa |
| AC-MVP-38 | Concurrent reservations mendekati batas US$20 | Tidak memakai cadangan US$5/planned top-up; semua attempts tercatat |
| AC-MVP-39 | Timeout provider dengan billing tidak diketahui | Reservation tetap ditahan/reconciliation_required, bukan dianggap gratis |
| AC-MVP-40 | Worker crash/redelivery/outbox duplicate | Resume stage aman; tidak membuat keputusan/publication ganda |
| AC-MVP-41 | Export JSON/MD dan withdrawal | Export hanya released snapshot berizin; withdrawn tidak memperlihatkan draft |
| AC-MVP-42 | UI 360/768/1440 px, keyboard, reader labels | Aksi inti selesai tanpa clipping/ketergantungan warna; issue aksesibilitas berat diperbaiki |
| AC-MVP-43 | Backup/restore sintetis dan tombstone | Manifest/hash/review tetap konsisten; deleted data tidak muncul kembali pada layanan |
| AC-MVP-44 | Penelusuran UI/API/schema untuk fitur luar scope | Tidak ada dashboard, skor, materi, external search atau grade passback |
| AC-MVP-45 | Evaluator offline menerima contoh 3 unit Bagian 21 | Recall/coverage/risk mengikuti denominator yang ditetapkan; abstain tidak dihapus dari recall |

### 23.1.1 Tambahan penerimaan MVP v2

AC-MVP-01–AC-MVP-45 tetap berlaku. Kriteria berikut menambah kontrak software dan artefak handover; tidak menganggap eksperimen pada mahasiswa nyata sudah dilakukan.

| ID | Skenario | Hasil yang diwajibkan |
|---|---|---|
| AC-MVP-46 | Release memilih parser native/Docling dengan profile/weights/config tertentu | Memory hard limit/soft-hard timeout/checksum sesuai manifest; env tidak mengganti profil aktif diam-diam |
| AC-MVP-47 | Parser terkena timeout atau OOM terkontrol | Checkpoint valid tersimpan; unit gagal tetap di inventaris; reason spesifik bila terbukti; negative gate tertahan dan manual review tersedia |
| AC-MVP-48 | Redelivery setelah satu halaman selesai dan assembly tabel lintas halaman belum pasti | Hasil sah dipakai ulang; tidak mengonversi seluruh file berulang; original page/coords/header tetap; ambiguity ditandai |
| AC-MVP-49 | Dua parser berat pada node atau lease kedaluwarsa sementara child masih hidup | Admission/fencing menolak overlap di luar satu slot/budget; reservation dilepas hanya setelah reconciliation aman |
| AC-MVP-50 | Quote/anchor benar tetapi tidak mendukung hubungan tuntutan | Mechanical valid tidak otomatis semantic supported; kandidat ditahan atau elemen unassessable |
| AC-MVP-51 | Satu tabel lengkap memenuhi seluruh role pada satu relasi | Evidence sama boleh dipakai multi-role; tidak diwajibkan file/chunk/kutipan tambahan; role bindings tetap terlacak |
| AC-MVP-52 | Elemen human_only dan elemen numeric kehilangan operand | Human-only tidak diputus AI; numeric tidak diubah menjadi absence; observasi/pertanyaan manual tetap tersedia |
| AC-MVP-53 | Augmentasi mengubah boleh→harus, menghapus kondisi atau memasukkan metode contoh sebagai kewajiban tunggal | Lint/fidelity review menandai masalah; tuntutan tanpa derivasi diblokir; approval tidak mengubah paket publik diam-diam |
| AC-MVP-54 | Draf spec berubah setelah lint/resolusi atau blocking issue diakui dosen | Lint/approval lama stale; blocking tidak dapat dioverride; konfirmasi fidelity terikat hash/revision dosen yang benar |
| AC-MVP-55 | Candidate batch 3/5, report hash/model tidak cocok, atau tidak ada report | Register/activate optimasi ditolak; baseline batch 1 tetap dapat dipakai; mock result tidak mempromosikan live |
| AC-MVP-56 | Batch respons berisi dua elemen valid dan satu elemen hilang/ID asing | Valid entry dipisah; ID asing ditolak; missing entry tidak menambah coverage/no_issue; retry tetap dalam limits |
| AC-MVP-57 | Sumber penentu rusak atau belum diinspeksi, model mengaku yakin | Router memilih repair/inspection/abstain, bukan menerima confidence atau membeli model kuat berulang tanpa sumber |
| AC-MVP-58 | Batch 1 menghabiskan call limit atau eskalasi semantik sudah dipakai | Limit 16 call/biaya/waktu tetap; tidak ada siklus eskalasi/kenaikan budget; completed_with_limits sah |
| AC-MVP-59 | Routing/batching/resource/grounding manifest berubah | Fingerprint/run baru; cache scoped tidak dipakai dengan config salah; review/publication lama immutable |
| AC-MVP-60 | Pilih elemen, role dan evidence pada PDF/DOCX/XLSX/image melalui click/keyboard/touch/mobile tab | Sumber tepat terbuka dan highlight sinkron; selection bertahan; observasi/interpretasi/keputusan terpisah |
| AC-MVP-61 | Dua dosen memperbaiki span/transkripsi/tautan pada revision review sama | Satu mutasi sukses; stale 409; anotasi append-only; source/parse artifact lama tidak ditimpa; tidak menambah exemplar lintas mahasiswa |
| AC-MVP-62 | Koreksi anotasi setelah feedback approved atau publikasi dirilis | Review berubah/draft feedback stale; publication lama menampilkan resolved annotation revision historis; remove dukungan menghalangi complete sampai diperbaiki |
| AC-MVP-63 | Evaluator robustness menerima variants parent sama atau transformasi yang tidak setara | Partition ditegakkan, ekuivalensi manusia dan DAG diperiksa; hasil/compiler dan executor terpisah; invalid equivalence tidak dianggap stability failure biasa |
| AC-MVP-64 | Jalankan CLI parser benchmark/policy/robustness dan handover tanpa live key/dana | Report mode/versions/hardware/support/limits jelas; benchmark parser nyata dibedakan dari mock; incomplete tidak menjadi eligible atau klaim penelitian |
| AC-MVP-65 | Import/fixture histori schema v1 yang belum punya grounding/lint v2 | Snapshot/history v1 tidak diedit/di-approve otomatis; draft schema v2 menuntut derivasi/kontrak/lint; run AI v2 baru memerlukan approved spec v2 |

Untuk AC-MVP-65, migrasi database menambahkan schema_version=1 pada row historis jika state v1 memang tersedia dan menerima schema 1/2 sebagai data history. Semua kreasi/approval/run v2 menggunakan schema 2. Konten approved v1 hanya tetap dapat dibaca/review manual dalam historinya; perubahan menjadi v2 dilakukan lewat assignment/spec draft versi baru yang disahkan dan dipublikasikan sesuai versioning. Fresh build tidak perlu mengarang database deployment lama; fixture history cukup untuk membuktikan invariant dan forward schema handling.

### 23.2 Fixtures dan pemeriksaan nyata

Sediakan minimal dua assignment sintetis (SI dan Manajemen), dua course, dua dosen terpisah, mahasiswa individual dan dua kelompok. Sertakan:

- Bundle dengan semua kewajiban tersedia, explicit requirement hilang, descriptor partial, dan satu masalah muncul di dua sumber.
- PDF dua kolom/tabel lintas halaman, DOCX dengan tabel/gambar/nested content, XLSX formula literal+cache kosong+hidden sheet, screenshot teks cetak, foto terputar dan handwriting sulit.
- Pasangan angka setara format berbeda, mismatch sepadan dan mismatch konteks yang seharusnya tidak diflag.
- Source berisi instruksi adversarial untuk mengubah role/score/publish, ID asing pada output mock, response invalid/refusal/truncated.
- Budget hampir habis, provider timeout setelah request, broker mati, worker crash, concurrent edit/publish, source hash mismatch dan revoked membership.
- Lint syarat/alternatif/polarity, kutipan benar tetapi tidak relevan, satu tabel dengan beberapa support roles, human_only, batch dengan satu entry hilang, dan policy candidate yang mencoba diaktifkan.
- Checkpoint parser dua halaman dengan satu timeout/OOM terkontrol, lease node lama/child masih hidup, serta tabel lintas halaman dengan relasi assembly belum pasti.
- Revisi manual span/transkripsi/tautan, konflik revision antar dosen, dan publication lama setelah annotation baru.
- Variant setara makna dengan human verification dan parent partition; variasi yang memang mengubah kewajiban harus ditolak sebagai equivalence test.

Berkas fixture merupakan sumber aktual yang dibuka parser, bukan hanya JSON tiruan. Mock provider boleh memberi respons yang dikendalikan, tetapi anchor/source membership diverifikasi terhadap fixture nyata. Jangan mengklaim handwriting recognition teruji hanya dari mock transcript; live/model OCR capability dilaporkan terpisah dari ketepatan gate software.

### 23.3 Sandbox dan penelitian

AC-SBX-01: valid LTI launch memetakan subject/course/assignment/role yang disetujui. AC-SBX-02: signature, issuer, audience, nonce, replay, expiry dan deployment salah ditolak. AC-SBX-03: impor final source identik idempotent, source baru membuat versi baru. AC-SBX-04: kelompok/revocation/course isolation benar. AC-SBX-05: mahasiswa hanya melihat published feedback, tanpa grade writes. Kelima AC ini wajib untuk menyatakan B9 selesai, bukan untuk core MVP B0–B8.

AC-RES-01: dataset/codebook/partition/frozen manifests tercatat. AC-RES-02: label/denominator dan support/interval dilaporkan. AC-RES-03: golden test tidak masuk prompt/retrieval/tuning. AC-RES-04: biaya penuh berasal dari ledger dan dana terkonfirmasi. AC-RES-05: DSRM problem→objective→design→demo→evaluation→communication dan setiap iterasi mempunyai sumber/versi/bukti. AC-RES-06: laporan robustness memisahkan compiler fidelity, executor variation, efek format/sumber rusak dan repeat dependence. AC-RES-07: klaim penghematan/batching/routing menunjuk validation/test report yang sah dengan core/model/prompt hashes, mutu/coverage/biaya dan batas generalisasi. Ini syarat klaim penelitian, bukan kewajiban menuntaskan studi empiris sebelum software handover; jangan mengarang data untuk menandainya selesai.

## 24. Definition of Done dan instruksi handover

MVP dianggap selesai dibangun bila:

1. B0–B8 dan AC-MVP-01 sampai AC-MVP-65 telah diuji; kegagalan atau keterbatasan ditulis spesifik, tidak diganti klaim "sudah dites" tanpa hasil.
2. Seluruh FR dan INV mempunyai tautan ke implementasi dan tes dalam `requirements_traceability.md` yang dibuat agent pada repository.
3. Frontend production build, backend startup, migration fresh database, synthetic seed, unit/integration/E2E dan lint/typecheck lulus pada environment yang dicatat. Tidak perlu mengulang semua tes setelah hasil stabil kecuali perubahan relevan terjadi.
4. Semua halaman/flow yang diminta bekerja, termasuk manual provider-off, upload mixed-format, source viewer, terminal decisions, approved feedback, publication dan export.
5. Tidak ada mock tersembunyi pada live mode, secret dalam repo, field skor, dashboard analitik, materi RAG atau endpoint yang memberi model hak publikasi.
6. Docker Compose dapat dijalankan dari checkout baru; README mencantumkan command pasti untuk install, migrate, bootstrap, seed, run, smoke, test, backup dan restore. Command harus sesuai yang benar-benar dipakai agent, bukan contoh belum diverifikasi.
7. `.env.example` menjelaskan setiap setting Bagian 18 tanpa secret; model/rate/resource/routing/batching manifest example dibedakan dari manifest live yang diverifikasi. Baseline routing dan singleton batch tersedia; candidate/validated tidak dipalsukan. Dataset/frozen test asli tidak ikut seed.
8. OpenAPI JSON dihasilkan dari server dan snapshot kontrak diuji. Typed frontend client menggunakan schema tersebut atau generator yang dikunci; jangan memelihara kontrak yang berbeda tanpa pengujian.
9. Test report memisahkan gate correctness pada mock, parser/OCR nyata, live inference sintetis, performa staging, dan penelitian empiris yang belum dijalankan. Jangan menyatakan target precision/recall tercapai dari fixture buatan saja.
10. Agent menyerahkan daftar operator/config/prompt/grounding/lint/resource/routing/batching versions, benchmark/capacity report, effective policy state, dan limitation format/vision/biaya. Sandbox Moodle off default dan dilaporkan sebagai tahap terpisah jika belum dikerjakan.
11. `requirements_traceability.md` mencakup FR-01–FR-24, INV-01–INV-16 dan AC-MVP-01–AC-MVP-65; `research/research_traceability.md` berisi template serta bukti aktual yang sudah tersedia saja.
12. Perubahan anotasi, policy/resource fingerprint dan spec schema v2 diuji pada OpenAPI/database/worker/UI yang sama. Jika tersedia state/schema v1, histori dipertahankan dan konten belum bermigrasi tidak di-approve otomatis. Jika fresh build, fixture kompatibilitas tetap memeriksa invariant histori.
13. Tidak mengklaim batching>1 atau routing optimasi tervalidasi bila laporan nyata belum ada. Ketiadaan laporan empiris tidak menghalangi MVP dengan policy baseline/manual yang berfungsi; laporkan readiness per kemampuan, bukan mencampurkan software correctness dan effectiveness.

### 24.1 Prompt awal yang dapat diberikan bersama dokumen ini

> Bangun ALIGN sesuai ALIGN_IMPLEMENTATION_SPEC_V2.md sebagai Single Source of Truth. Kerjakan B0–B8 sampai alur manual dan AI adapter lengkap serta acceptance criteria MVP teruji. Gunakan mock provider dan data sintetis untuk pengujian deterministik; sediakan jalur live melalui konfigurasi tanpa mengarang hasil. Jangan menambahkan skor, gradebook, dashboard analitik, materi pembelajaran atau retrieval di luar bundle. Mulai dengan memetakan FR/INV/AC v2 ke modul dan tes, lalu implementasikan tahap demi tahap. Terapkan resource profile/checkpoint/admission parser, typed grounding dan compiler lint, baseline routing/batch 1, linked review annotation revisions, serta evaluator offline. Jangan mengaktifkan batch 3/5 tanpa validation report yang sah atau menimpa sumber/histori dengan koreksi dosen. Catat keputusan engineering lokal, versi dependency yang dikunci, hasil pengujian dan limitation nyata. B9 Moodle adalah tahap setelah MVP dan tidak boleh menjadi ketergantungan core. Jangan melakukan deployment publik, mengirim pesan keluar, atau memakai data mahasiswa nyata tanpa konfigurasi/otorisasi operasi yang sesuai.

### 24.2 Perubahan terhadap dokumen lama yang wajib dipertahankan

- Judul/positioning produk berfokus pada evidence-grounded findings dan lecturer-validated feedback. Frasa outcome alignment boleh menjelaskan hubungan tuntutan dengan CPMK, tetapi tidak menjanjikan pengukuran capaian mahasiswa.
- Hapus seluruh CriterionScore, maksimum skor, bobot/alokasi capaian, agregasi mastery, dashboard findings/capaian, skor kelompok/individual, analitik kelas dan grade passback.
- Pertahankan NumericObservation/NumericCheck untuk isi pekerjaan mahasiswa. Nilai angka di dalam tugas bukan nilai akademik.
- Pertahankan semua elemen review, termasuk no issue, not applicable dan unassessable; feedback hanya berdasarkan keputusan dosen dan sumber yang sah.
- Hapus korpus materi dan saran sumber belajar eksternal. Konteks normatif yang disahkan tetap diperlukan dan dimuat langsung, tidak menjadi korpus materi.
- Tambahkan JPG/JPEG/PNG dengan OCR/vision selektif, locator image dan batas kualitas; jangan mengklaim semua tulisan tangan dapat dibaca.
- Rumus toleransi max, anggaran terkonfirmasi, satu kategori utama, instruksi utuh, regression suite terpisah dan denominator eksplisit berlaku di seluruh kode/dokumen/tampilan.

## 25. Referensi teknis resmi

Rujukan dipakai untuk kemampuan dan batas teknologi, bukan bukti hasil ALIGN. Halaman dapat berubah; agent memeriksa dokumentasi versi dependency yang benar-benar dikunci. Versi 1.0 mencatat pemeriksaan referensi OpenAI/Docling pada penyusunan 18 Oktober 2026; v2 mempertahankan referensi itu dan tidak memperlakukan paper sebagai dokumentasi API terkini. Agent tetap harus memverifikasi versi library runtime/dokumentasi resmi saat build. Referensi paper penelitian tidak menjadi dependensi implementasi.

1. Docling, [Supported formats](https://docling-project.github.io/docling/usage/supported_formats/) — dukungan PNG/JPEG dan format dokumen.
2. Docling, [OCR](https://docling-project.github.io/docling/concepts/OCR/) — OCR engines dan language options; dukungan parser tidak menjamin akurasi tulisan tangan pada data ALIGN.
3. Docling, [Docling document](https://docling-project.github.io/docling/concepts/docling_document/) — representasi sumber terstruktur.
4. OpenAI, [Images and vision](https://developers.openai.com/api/docs/guides/images-vision) — image input, detail, resizing, serta keterbatasan teks kecil/rotation/graphs/accuracy.
5. OpenAI, [Image input cost calculator](https://developers.openai.com/api/docs/guides/image-cost-calculator) — biaya tergantung model, ukuran, detail dan token; bukan biaya submission pasti.
6. OpenAI, [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) — schema, strict output, refusal dan supported subset; struktur benar tidak membuktikan isi benar.
7. pgvector, [Official repository and documentation](https://github.com/pgvector/pgvector) — exact/approximate search dan filtering; exact vector recall bukan semantic completeness.
8. Celery, [Tasks](https://docs.celeryq.dev/en/stable/userguide/tasks.html) — idempotency dan acknowledgment/retry; queue tidak memberi exactly-once pada aksi domain.
9. openpyxl, [Simple formulae](https://openpyxl.readthedocs.io/en/stable/simple_formulae.html) — library tidak mengevaluasi formula.
10. python-docx, [Document API](https://python-docx.readthedocs.io/en/latest/api/document.html) — batas paragraph/table/revision dan kebutuhan penanganan OOXML tambahan.
11. 1EdTech, [LTI Core Specification 1.3](https://www.imsglobal.org/spec/lti/v1p3/) — OpenID Connect, JWT dan launch protocol untuk B9.
12. Moodle, [External Services API 4.5](https://moodledev.io/docs/4.5/apis/subsystems/external) — akses Web Services dengan izin/token dan fungsi yang diaktifkan untuk sandbox.

Dokumen ini tidak menyatakan software, akses model, izin data institusional, dana tambahan, hasil pilot, target reliabilitas, atau integrasi produksi telah tersedia. Semua keputusan produk untuk pembangunan telah ditetapkan; hal operasional tersebut dibuktikan pada environment dan tahap yang sesuai.

## 26. Dasar metode dan perubahan v2

Bagian ini menjelaskan asal keputusan, bukan menambahkan instruksi alternatif. Semua keputusan di sini sudah dinormatifkan pada bagian domain terkait; agent tidak perlu memilih antara memo review dan spec. Persetujuan pemilik produk pada 19 Oktober 2026 (Asia/Jakarta) mencakup R-01–R-08, D1-A dan D2-B. Baseline v1 SHA-256: `c164c9c1e0f2804e15db6093ecbe6ad28df24dfe0f64096badee870bd5d4aa34`.

### 26.1 Pemetaan metode yang diadopsi

| Refinement | Keputusan final v2 | Lokasi normatif |
|---|---|---|
| R-01 / D1-A | Resource profile native/Docling terpisah, headroom awal 2/8 GiB, light kandidat 4 GiB tidak aktif, satu heavy/node, checkpoint dan measured limits | 6.1, 8.5, 15–18, AC-46–49/64 |
| R-02 | Grounding contract sesuai klaim; roles/coverage/numeric/human-only; locator valid tidak cukup untuk semantic support | 7.6, 9.5, 10, 15–16, AC-50–52 |
| R-03 | Decision question/derivasi, compiler lint dan fidelity approval; tidak menambah kewajiban dari augmentasi | 5.3, 7.7, 15–16, 19, AC-53–54 |
| R-04 / D2-B | Baseline batch 1; group maksimum 5 hanya pada policy tervalidasi, per-element output/coverage | 13.4/13.6, 21.4, AC-55–56/58 |
| R-05 | Routing policy/rationale/manifests; repair sumber berbeda dari eskalasi model; learned scorer ditunda | 13.5, 15–18, 21.4, AC-57/59 |
| R-06 | Linked selection dalam dua panel dan annotation revisions yang tidak menimpa original/history | 14.1.1, 15–16, 19, AC-60–62 |
| R-07 | Compiler fidelity dan executor robustness dibedakan; human equivalence, parent partition dan repeat dependence | 21.5, AC-63–64, AC-RES-06 |
| R-08 | Enam aktivitas DSRM dengan research traceability dan iterasi; demo bukan evaluation | 21.6, B8/DoD, AC-RES-05 |

### 26.2 Sumber paper dan batas adopsi

Nomor halaman berikut adalah nomor PDF berkas yang diberikan pengguna, bukan selalu halaman cetak. Penelaahan menggunakan isi berkas tersebut; tidak menganggap API/harga/model dalam paper sebagai kondisi deployment saat build.

1. Hong et al., *From Rubrics to Reliable Scores: Evidence-Grounded Text Evaluation with LLM Judges* (RULERS), versi PDF yang diberikan. PDF 3–4: task-level specification/locking dan evidence execution; PDF 14 Tabel 6: local_quote/span_level/global_diagnostic/weakly_groundable; PDF 13–15: fidelity/robustness. ALIGN mengambil prinsip locking dan bukti sesuai klaim. Grounding contract multimodal v2 adalah adaptasi engineering; kalibrasi/agregasi skor Phase III dan diagnosis kualitas holistik tidak diambil.
2. Lee et al., *CheckEval: A reliable LLM-as-a-Judge framework for evaluating text generation using checklists*, EMNLP 2025, 15771–15798. PDF 3–5: human dimensions, pertanyaan, diversification/elaboration, filtering, grouping; PDF 10: batas biner dan generalisasi. ALIGN mengambil mutu kompilasi dan menguji batching. Rasio Yes, scoring, perluasan tuntutan otomatis, dan Boolean paksa tidak diambil.
3. Rao and Callison-Burch, *Autorubric: A Unifying Framework for Rubric-Based LLM Evaluation on Non-Verifiable Tasks*, COLM 2026, versi PDF yang diberikan. PDF 3–4: criterion-per-call, abstention dan konfigurasi; PDF 7 Tabel 4: mitigasi/ensemble bergantung model dan eksperimen konfigurasi satu run; PDF 11: keterbatasan. ALIGN mengambil isolasi keputusan sebagai baseline dan validasi konfigurasi; tidak mengklaim paper membuktikan batch 1 mengalahkan batch 5 pada tugas ALIGN. Weighted scores, ordinal academic scales, ensemble wajib dan few-shot mahasiswa lain tidak diambil.
4. Chen, Zaharia and Zou, *FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance*, 2023. PDF 5–6: learned scorer/router dan strategi biaya; PDF 7–9: dataset, model/harga eksperimen dan hasil. ALIGN mengambil prinsip mutu–biaya serta routing yang dapat dievaluasi. Rules v2 bukan penerapan penuh learned FrugalGPT, dan penghematan hingga 98% tidak menjadi estimasi ALIGN. Learned scorer, fine-tuning dan semantic cache lintas pengumpulan ditunda/tidak masuk MVP.
5. Lin et al., *Open-ended Structured Question Assessment with Human-LLM Collaboration* (VeriGrader), CHI 2026. PDF 6–9: segment mapping, exemplars, coordinated panels; PDF 19: batas jawaban panjang dan multimodal. ALIGN mengambil linked review/correction dan ruang ketidakpastian. Tidak mengambil skor, statistik/prioritas kelas, contoh runtime lintas mahasiswa, regrade massal dari satu koreksi, atau API-key klien.
6. Auer et al., *Docling Technical Report*, Version 1.0, versi PDF yang diberikan. PDF 2–4: layout/table/OCR/assembly; PDF 5 Tabel 1: peak RSS sekitar 6,16–6,20 GB native dan 2,42–2,56 GB pypdfium pada pengujian 225 halaman tanpa OCR. Angka itu bukan kebutuhan per halaman atau benchmark Docling 2 ALIGN. Laporan utama pada PDF 1–6; materi DocLayNet pada halaman berikutnya tidak digabung sebagai benchmark runtime yang sama. Headroom 8/4 GiB v2, per-page checkpoint dan release benchmark adalah keputusan engineering, bukan rekomendasi numerik paper.
7. Peffers, Tuunanen, Rothenberger and Chatterjee, *A Design Science Research Methodology for Information Systems Research*, JMIS 24(3), 45–77, 2007; DOI 10.2753/MIS0742-1222240302. PDF 11 diagram, PDF 12–13 uraian: enam aktivitas, titik masuk dan iterasi. ALIGN memakai traceability penelitian; DSRM bukan fitur inferensi atau jaminan akurasi.

Hasil di paper mendukung alasan metode, tidak membuktikan efektivitas ALIGN. Semua metrik/target/batas/promosi di v2 tetap harus dibedakan dari hasil aktual. Tidak ada penambahan skor, dashboard, materi, retrieval eksternal/lintas mahasiswa atau otomasi publikasi sebagai akibat adopsi paper.

### 26.3 Perubahan yang menggantikan aturan v1

- Batas tunggal parser 2 GiB/120 detik per seluruh file diganti profil 2/8 GiB serta batas task/run yang terpisah. Backend ringan 4 GiB tidak dipilih diam-diam.
- Izin batching maksimal 5 yang belum mempunyai gate diganti baseline 1 dan promosi policy 3/5 berbukti, dalam limit biaya/call/waktu yang sama.
- Schema CheckSpecification menjadi 2, menambah decision_question, derivation_refs, grounding_contract, relationship_code dan lint approval binding. Histori schema 1 tetap immutable.
- Release/run fingerprint mencakup resource/routing/batching/grounding/lint versions. Evidence support, route decisions, parser checkpoints dan annotation revisions memiliki kontrak penyimpanan/API yang eksplisit.
- Matriks bertambah dari 45 menjadi 65 AC-MVP; FR menjadi 24 dan invariant menjadi 16. AC-SBX tetap 5; AC-RES menjadi 7. Gate penelitian tetap terpisah dari selesainya software MVP.
- Handover menggunakan satu file ini; v1 dan memo review dipertahankan sebagai sejarah keputusan, tidak perlu dilampirkan sebagai instruksi build tambahan.

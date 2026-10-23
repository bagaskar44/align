# ALIGN — Implementasi Menuju Pilot Produksi Standalone

Tanggal penyusunan: **23 Oktober 2026 (Asia/Jakarta)**. Bahasa produk dan dokumentasi: Indonesia.

**Status dokumen: instruksi implementasi; P0–P5 belum dinyatakan selesai oleh dokumen ini.** Penambahan panduan tidak menjalankan aplikasi, memanggil provider, mengubah release, atau mengaktifkan produksi.

[Indeks implementasi](README.md) · [Master normatif](../../ALIGN_IMPLEMENTATION_SPEC.md) · [B5](B5-ai-executor.md) · [B6](B6-numeric-dan-biaya.md) · [B8](B8-handover-dan-pilot-readiness.md)

## 1. Tujuan, otoritas, dan batas pekerjaan

Siapkan pilot produksi standalone untuk **1–2 mata kuliah pada satu server Linux dengan Docker Compose**. Alur yang harus terbukti: konteks dan paket tugas disahkan dosen → tugas terbit → mahasiswa finalize sumber → analisis berbukti → review dosen → feedback disetujui → hasil diterbitkan → mahasiswa membaca dan mengunduh hasil resmi.

`ALIGN_IMPLEMENTATION_SPEC.md` tetap sumber normatif, terutama Bagian 6–11, 13–18, 19.4, 20–21, dan 23–24. Panduan ini melengkapi operasi serta pembuktian kesiapan; tidak mengubah FR/INV/AC, memberi pengecualian finansial, atau menggantikan laporan validasi. Jika ditemukan konflik, catat bagian/ID dan perilaku aktual, pertahankan invariant, dan laporkan blocker. Jangan menyelesaikannya dengan mengubah master atau memalsukan hasil.

- Nomor B0–B9 dipertahankan. Jalur ini melanjutkan kemampuan B0–B8; **B9 Moodle bukan predecessor**.
- Moodle/LTI, SSO institusi, email/notifikasi otomatis ke pengguna, gradebook/skor, dashboard akademik, fine-tuning, dan eksperimen ablation penelitian lengkap berada di luar tahap ini.
- Semua keputusan akademik, approval, dan publikasi tetap kewenangan dosen. Model hanya mengusulkan; worker tidak mendapat kewenangan publikasi.
- KK/RPP dan file rubrik **tidak menjadi prasyarat pengembangan**. Konteks manual sah, instruksi yang diisi melalui editor, dan fixture sintetis cukup untuk P0–P3. Ikuti field wajib dan approval master Bagian 7; jangan mengarang CPMK atau kewajiban resmi.
- Dokumen mahasiswa berizin dan penilaian acuan dosen dibutuhkan pada P4. Pengumpulannya dapat berjalan paralel dengan persiapan software.
- Pembuatan panduan bukan otorisasi biaya, pemrosesan data nyata, atau deployment publik. Pada pelaksanaan, gunakan otorisasi yang sudah diberikan untuk tindakan dan budget tersebut; jangan meminta ulang bila masih berlaku. Selesaikan persiapan yang dapat ditinjau sebelum meminta keputusan yang benar-benar belum tersedia.

## 2. Titik awal yang wajib diverifikasi agent

Tabel ini merupakan hasil inspeksi struktur kode saat panduan ditulis, **bukan sertifikat bahwa runtime sudah memenuhi kontrak**. Agent harus mencatat revision/working-tree fingerprint, hash master, environment, dan tanggal audit barunya. Baca handover sebagai konteks, lalu periksa kode dan jalankan sistem sendiri.

| Kapabilitas | Keadaan yang ditemukan | Konsekuensi implementasi |
|---|---|---|
| Adapter provider | `OpenAIResponsesProvider` pada [domain providers](../../packages/domain/src/align_domain/providers.py); Responses text/vision dan endpoint embeddings tersedia | Audit dan lengkapi consumer yang gagal; tidak membuat adapter paralel untuk menghindari ledger |
| Konfigurasi release | [Release baseline](../../config/release.baseline.json) adalah `mock/synthetic`; model mock tidak mengikat ID live; rate manifest `unverified` | Buat manifest live baru; baseline bawaan tetap dapat dipakai untuk regresi |
| Routing/retrieval | Peran model, query/chunk embedding, baseline routing dan batch 1 sudah memiliki jalur kode | Buktikan panggilan nyata serta scope vector; keberadaan kode/mock bukan bukti kualitas semantik |
| Keuangan dan aktivasi | [CLI operator](../../services/api/src/align_api/cli.py) dan ledger memeriksa model, tarif, release, budget serta eligibility | Semua consumer harus melewati reservation sebelum network; jangan menonaktifkan gate |
| Live sintetis | Pengecualian capability smoke hanya untuk development/demo sintetis dengan baseline dan batch 1 | Tidak dapat dipakai sebagai release institutional atau sebagai jalan pintas produksi |
| Deployment | [Compose produksi](../../docker-compose.production.yml) baru mengatur kapasitas parser; Compose utama masih memakai S3Mock dan binding HTTP loopback | P3 harus melengkapi deployment, TLS, storage, secrets, dan operasi Linux |
| Readiness | `/health/ready` memeriksa DB/broker/storage; `/health/providers` belum membuktikan live inference | Tambahkan pemeriksaan operasional worker/antrean/release; probe kesehatan tidak boleh memanggil LLM berbayar |
| Backup/retensi | Script backup/restore dan helper object archive saat ini menerima **synthetic saja** | Jalur produksi berizin harus dibuat dan diuji; jangan sekadar menghapus penolakan mode synthetic |
| Evaluasi | Evaluator offline, template dataset/protokol, dan gate `PolicyValidationReport` tersedia | Template kosong, mock report, dan `incomplete` tidak memenuhi syarat institutional |
| Runner | `scripts/smoke-local.py` menguji runtime mock; belum ada runner live sintetis khusus | Buat runner opt-in pada P1, lalu jalankan pada P2 setelah input tersedia |

**Observasi audit sesi 23 Oktober 2026 yang perlu dikonfirmasi P0:** container development berbeda dari workspace; origin API/proxy pernah membuat login 403; parser-worker lama unhealthy; parsing nyata pernah terblokir kapasitas dan satu PDF lulus setelah restart; favicon 404 mengganggu console checks; selector XLSX pada E2E mencari tab di dalam tab; satu kasus tes XLSX memakai endpoint konteks yang menolak XLSX; pergantian sesi pernah menghasilkan console 401. Ini daftar diagnosis awal, bukan klaim bahwa semua masalah masih terjadi. Simpan reproduksi baru dan bedakan kesalahan aplikasi, harness, konfigurasi, serta keterbatasan host. Bukti sementara dari sesi sebelumnya tidak menggantikan artefak handover baru.

## 3. Keputusan implementasi dan input eksternal

### 3.1 Arsitektur serta pemisahan environment

Pertahankan layanan web, API, general worker, parser coordinator/child, dispatcher, scheduler, PostgreSQL/pgvector, Redis, dan Caddy. Gunakan SeaweedFS satu node dengan autentikasi S3 untuk storage produksi. Pin image/commit dan digest setelah memeriksa dokumentasi resmi serta kompatibilitas; jangan menggunakan `latest` untuk release. Storage tunggal bukan high availability; backup harus berada di lokasi terpisah dari server ini.

| Environment | Mode dan penggunaan | Pemisahan wajib |
|---|---|---|
| Development | `APP_ENV=development`, mock/off, `DATA_MODE=synthetic` | Pertahankan stack developer; jangan menimpa env, release, atau volume pengguna |
| Live capability check | `APP_ENV=development`, `APP_DEMO_MODE=true`, `PROVIDER_MODE=live`, `DATA_MODE=synthetic` | Checkout/config privat, project `align-livecheck-<run-id>`, DB, bucket, credentials, queue prefix, dan port sendiri |
| Uji operasi produksi dengan fixture | `APP_ENV=production`, `APP_DEMO_MODE=true`, `PROVIDER_MODE=mock`, `DATA_MODE=synthetic` | Project/volume uji terpisah, SeaweedFS, TLS, profil parser produksi; tidak menyimpan `ALIGN_SEED_PASSWORD` pada runtime produksi |
| Pilot produksi | `APP_ENV=production`, `APP_DEMO_MODE=false`, `PROVIDER_MODE=live`, `DATA_MODE=institutional` | Database/bucket/queue/secrets produksi sendiri; release eligible dan kebijakan data sah |

**Penting:** kode sekarang menolak release `live/synthetic` di `APP_ENV=production`. P2 menguji provider nyata pada environment livecheck; P3 menguji operasi produksi memakai demo mock sintetis. Kedua bukti dicatat terpisah. Jangan mengubah guard agar gabungan yang dilarang tampak lolos. Akun fixture untuk uji P3 disiapkan sebelum pengujian konfigurasi produksi atau melalui provisioning admin yang sah, bukan menjalankan seed/password demo pada produksi.

- HTTP publik hanya melalui Caddy pada satu HTTPS origin; port DB, Redis, S3, filer/master/admin SeaweedFS tidak dipublikasikan. S3 internal menggunakan TLS sesuai syarat endpoint produksi master. Uji juga akses tanpa credential dan credential dengan scope salah.
- Rahasia hanya pada konfigurasi privat backend/secret files berizin terbatas. Pisahkan akun S3 aplikasi dan operator backup. Parser child menerima file serta konfigurasi terbatas tanpa DB/API key dan tanpa egress setelah model tersedia.
- Model per peran mengikuti Bagian 13.1. Efficient/complex/vision boleh menunjuk model sama yang lolos capability check; embedding tetap `text-embedding-3-small`, 1536 dimensi. Pin identifier/snapshot aktual, kemampuan, schema/prompt, tokenizer, tarif, sumber, reviewer, dan tanggal verifikasi.
- Baseline semantic batch **1**; tidak mengaktifkan batch 3/5 atau efficient semantic optimization. Semua limit job, USD, retry, dan active processing time mengikuti spec. Nilai budget konfigurasi bukan bukti dana sudah tersedia; ledger database otoritatif dan top-up hanya berdasar konfirmasi nyata.
- Produksi: document parser **8 GiB**, container awal **9 GiB**, maksimum satu parser berat/node dan general concurrency **2**. Ukur RAM host yang tersisa setelah OS/layanan lain berjalan, cold/warm working set, cache, dan PIDs. Jangan menganggap RAM 9 GiB untuk seluruh server cukup atau mengurangi profil agar tes terlihat lulus.
- Target kapasitas: **20 session bersamaan**, p95 metadata/list/save **<1 detik**, tidak termasuk transfer file/provider. Ukur processing dan antrean terpisah; jangan menjanjikan ETA buatan.

### 3.2 Input yang disediakan pemilik/operator

Catat nilai non-secret atau referensi penyimpanannya, pemilik, tanggal berlaku, dan status tersedia. Jangan meminta dokumen nyata untuk membuka pekerjaan P0/P1/P3.

| Input | Pemilik | Mulai dibutuhkan | Jika belum tersedia |
|---|---|---|---|
| API project/key backend dan akses model | Pemilik akun provider | Eksekusi P2 | P1 dan persiapan P3 lanjut; P2 `pending_external_input`, tanpa call percobaan |
| Budget yang disetujui, spend cap run, protected reserve, ref otorisasi | Pemilik budget/operator | Sebelum paid call pertama | Hanya pemeriksaan offline; jangan memakai saldo/planned top-up yang diasumsikan |
| Server Linux, akses operator, DNS/domain, TLS dan jaringan | Operator | P3 di server target | Compose dan uji lokal terisolasi dapat disiapkan; kapasitas target belum terbukti |
| Tujuan backup di luar server, sertifikat/kunci, penanggung jawab pemulihan | Operator | Restore drill P3 | Backup lokal saja tidak meluluskan kesiapan pilot |
| Kanal alert operasional dan operator yang merespons | Operator | P3, wajib aktif sebelum P5 | Buat log/status lokal; jangan mengaktifkan email/pesan keluar tanpa konfigurasi serta otorisasi kanal |
| Tujuan pemrosesan, izin data/provider, policy reference/masa berlaku, retensi source/review/backup | Pemilik/institusi | Sebelum P4 data nyata dan P5 | Pertahankan synthetic; angka 90/365/30 bukan kebijakan institusi |
| Paket tugas, sumber mahasiswa berizin, labels/adjudication dosen | Dosen/pemilik data | P4 | Gunakan fixture; kualitas pada penggunaan nyata tetap belum tersedia |
| Protokol, dataset/codebook/split hashes, scope dan laporan eligible | Reviewer/operator | Gate release institutional P4–P5 | Aktivasi ditolak; tidak membuat laporan kelulusan dari template |

### 3.3 Artefak dan status

Agent pelaksana membuat handover di `docs/handover/standalone-production.md`, matriks FR/INV/AC dan bukti per tahap. Gunakan direktori bukti per run di lokasi privat yang ditentukan operator; commit hanya ringkasan/screenshot sintetis yang sudah ditinjau, tanpa raw prompt, dokumen nyata, token, atau credential. Setiap bukti memuat revision dan perubahan lokal, waktu/timezone, environment/data/provider modes, commands/exit codes, hardware, hashes, hasil aktual, biaya, limitations, serta pemilik tindak lanjut.

Status tahap: `not_started`, `in_progress`, `passed`, `failed`, atau `pending_external_input`. Laporkan kematangan secara terpisah:

1. **Software siap:** jalur manual/mock serta tool operasional yang relevan telah diuji. Bukan bukti live.
2. **Live sintetis terbukti:** P2 berisi inference/usage/biaya provider nyata; bukan kualitas empiris atau izin data nyata.
3. **Siap pilot:** gate software, live, kapasitas, pemulihan, kebijakan dan kualifikasi terpenuhi; belum berarti pilot aktif.
4. **Pilot aktif:** P5 benar-benar dijalankan dan diverifikasi operator pada course yang diizinkan.

## 4. Tahapan implementasi

### P0 — Verifikasi titik awal

**Input:** workspace, master, dokumen B5/B6/B8, handover dan environment uji sintetis baru. Tidak memerlukan API key atau dokumen nyata.

1. Catat `git status`, diff/fingerprint, versi toolchain dan hash master. Pertahankan semua perubahan lokal. Periksa sumber aktual adapter, consumer, parser, ledger, release guard, backup dan tests; jangan mengasumsikan checkbox handover berarti runtime lulus.
2. Periksa image/source/config yang benar-benar dipakai container, mode serta origin tanpa mencetak env privat. Jangan deploy atau restart stack developer hanya untuk menyamakan bukti.
3. Bangun project uji baru dan jalankan konteks manual tanpa KK/RPP/CPMK, paket tugas, finalize, parser nyata, review provider-off, approval/publish dan download resmi. XLSX diuji sebagai submission; konteks menerima format sesuai Bagian 7.1.
4. Reproduksi observasi pada Bagian 2. Perbaiki prasyarat alur target secara terbatas; jangan melemahkan console assertion, validasi upload, admission, negative gate atau batas keuangan. Bedakan favicon, locator harness salah dan error aplikasi; periksa 401 saat pergantian sesi/polling.
5. Jalankan regresi terkait dan suite integrasi pada DB baru. Urutkan pengujian berat; test parser opt-in/OOM yang skipped dijalankan dengan prasyarat yang benar dan dilaporkan tersendiri.

**Keluaran:** baseline terukur, reproduksi/resolusi masalah, inventory gap, dan matriks bukti pertama. **Verifikasi:** perintah tersedia pada Bagian 5, ditambah browser nyata dan pemeriksaan checksum/config container. **Gate:** seluruh alur manual/mock dapat direproduksi dan blocker jalur live telah ditangani. Gagal karena host tidak cukup RAM tetap dicatat sebagai keterbatasan lingkungan, bukan kelulusan parser atau izin menurunkan profil.

### P1 — Persiapan live LLM tanpa paid call

**Input:** P0, fixture sintetis, adapter/ledger/CLI existing. Credential atau dana yang belum tersedia tidak menunda implementasi pemeriksaan offline.

1. Audit consumer B1 context extraction, B2 criteria/spec drafting, B5 embedding/retrieval/text/vision/claim verification, dan B4 feedback. Gunakan adapter yang sama serta `WorkerPaidCallLedger`; jangan memanggil provider langsung dari browser, parser child, atau runner.
2. Siapkan template manifest live non-secret terpisah dari baseline. Placeholder tetap unverified/tidak dapat diaktifkan. Saat akses akun tersedia, periksa dokumentasi resmi model/endpoint dan tarif aktual; isi role IDs yang sama persis dengan environment, modality/schema support, bounds termasuk image/reasoning/cache bila tertagih, serta sumber/reviewer/tanggal. Jangan menghitung biaya memakai tarif model lain atau menganggap dimensi billing yang tidak diketahui bernilai nol.
3. Ikuti syarat livecheck: development + demo + synthetic, baseline routing hanya mengizinkan live, batch 1, model dan activation checks `capability_smoke=true`, `empirical_verification=false`. Register/activate menggunakan CLI existing setelah semua syarat terpenuhi; jangan mengedit row/manifest aktif secara langsung.
4. Buat runner `scripts/live-smoke.py` dengan kontrak pada Bagian 5. Default hanya preflight offline. Runner memvalidasi isolasi, mode, manifest, budget dan ref otorisasi, lalu memakai API akademik/worker sebenarnya. Tidak boleh mengisi hasil AI, menjalankan fake provider sebagai bukti live, atau membuat keputusan dosen melalui role worker.
5. Uji semua penolakan sebelum network: key/model/rates/release/budget/scope tidak cocok, model tanpa capability, manifest berubah, dimensi embedding salah, protected reserve, dan institutional tanpa report eligible. Gunakan fake transport untuk failure injection deterministik; beri label bukti non-network.

**Keluaran:** template, preflight, runner, negative tests, dan inventory capability yang belum terbukti. **Verifikasi:** default runner serta targeted provider/config/ledger/release tests; assert jumlah network call ke provider nol. Pemeriksaan state DB/broker lokal yang read-only tetap boleh dilakukan. **Gate:** offline checks lulus; konfigurasi tidak lengkap berhenti sebelum reservation/network yang tidak sah. Tanpa key/budget, P1 dapat selesai tetapi P2 tetap pending.

### P2 — Pengujian live sintetis

**Input:** P1 lulus, akun provider/model/tarif terverifikasi, otorisasi biaya dan spend cap run, stack livecheck terisolasi. Semua sumber adalah fixture, bukan dokumen mahasiswa yang baru dikumpulkan.

1. Simpan hash baseline dan konfigurasi environment. Lakukan preflight, register/activate release live sintetis yang sah, lalu verifikasi worker memuat binding yang sama. Nilai env tidak boleh menimpa identitas release diam-diam.
2. Jalankan skenario sintetis kecil berurutan: konteks manual sebagai baseline; ekstraksi field dari KK/RPP fixture; drafting criteria/spec dengan approval dosen; index embedding chunk dan query 1536 dimensi; retrieval scoped; interpretasi teks dan region gambar; claim verification terpisah; feedback dari keputusan dosen. Setiap capability harus menunjukkan respons live yang valid, atau dinyatakan belum didukung dan memblokir klaim capability tersebut.
3. Gunakan fixture positif, requirement hilang, bukti parsial/tidak terbaca dan instruksi adversarial. Ketepatan semantik dinilai reviewer; keberhasilan JSON atau kutipan tidak dianggap otomatis benar. Pastikan coverage tidak cukup menghasilkan keterbatasan, bukan tuduhan absence.
4. Selesaikan browser journey dengan akun sintetis dosen/mahasiswa: approve/publish tugas, finalize, analisis, accept/edit/reject/defer sesuai bukti, complete review, draft/approve/publish feedback, buka hasil dan verifikasi bytes JSON/Markdown. Model tidak otomatis mengesahkan langkah manusia.
5. Cocokkan setiap paid attempt dengan reservation, provider request ID, model/release/rate hashes, usage aktual dan settlement. Hentikan rangkaian berbayar jika billing unknown; liability tetap ditahan sampai rekonsiliasi sah. Failure injection dari P1 tidak diulang secara berbayar untuk mengejar statistik.
6. Catat actual spend, hasil per consumer dan limitations. Nonaktifkan paid execution ketika selesai. Pertahankan stack development asli. Jika perlu kembali dari release live ke mock pada stack uji, register manifest mock versi baru yang sah; jangan mengaktifkan ulang release retired atau mengubah histori.

**Keluaran:** report `live_synthetic_capability_smoke`, bukti browser, capability matrix dan ledger refs tersanitasi. **Verifikasi:** runner opt-in dan rekonsiliasi dengan jalur aplikasi sebenarnya. **Gate:** seluruh capability yang ditargetkan terbukti, tidak ada pelanggaran invariant atau liability belum terselesaikan. Kegagalan/refusal yang ditangani aman membuktikan failure path, bukan sukses capability yang belum memperoleh respons valid. Tidak menghasilkan status kualitas empiris atau report eligible secara otomatis.

### P3 — Operasi produksi pada Linux, masih memakai fixture

**Input:** P0 dan kontrak P1; persiapan P3 boleh berjalan saat P2 menunggu API. Diperlukan server/DNS/tujuan backup untuk membuktikan hasil pada target. Uji produksi sintetis mengikuti mode pada Bagian 3.1.

1. Lengkapi Compose produksi, release profile yang kompatibel, env-role templates, serta prosedur Linux install/migrate/bootstrap/start/stop. Pertahankan lockfiles dan volume developer. Caddy menangani HTTPS satu origin; hilangkan credential demo, debug dan seed password; pin artefak/image. Akun dibuat melalui admin/bootstrap yang ada.
2. Ganti S3Mock dengan SeaweedFS berautentikasi dan volume persisten. Uji boto3 `head_bucket`, upload/download streaming, list pagination, delete, archive round-trip, hash, ukuran dan metadata MIME; uji seluruh operasi lain yang benar-benar ditemukan pada consumer. Tanpa auth atau di luar scope harus ditolak, dan filer/admin port tidak boleh menjadi jalan akses publik alternatif.
3. Implementasikan backup/restore produksi terpisah dari script synthetic existing. Validasi target/project, data mode, policy/authorization reference dan key custody; reuse komponen aman bila sesuai tetapi pertahankan guard synthetic lama. Gunakan maintenance window: hentikan write baru, drain worker/dispatcher/scheduler dan pastikan parser child tidak aktif, kemudian dump DB dan snapshot objek yang konsisten. Jangan menjadikan boolean `quiesced` sebagai satu-satunya bukti writer sudah berhenti.
4. Enkripsi sebelum transfer ke tujuan di luar host, simpan manifest hash/inventory dan timestamp, batasi akses serta cleanup plaintext. Jadwalkan backup harian. Simpan kunci pemulihan di luar arsip/host utama. Kebijakan retensi produksi mengikuti input institusi, bukan menyalin default demo.
5. Restore ke target kosong yang baru; jangan merge/overwrite volume development/produksi. Terapkan tombstone penghapusan yang berlaku, reconcile pointer/hash, publication/review/roster, jobs/outbox, parser children/leases serta ledger unknown sebelum membuka layanan. Redis dibangun kembali dari state DB yang sah; jangan mengulang paid call unknown. Ukur RPO dan durasi restore dengan target 24 jam/8 jam.
6. Implementasikan retensi/purge berizin sesuai master 20.7 untuk original, turunan, embedding dan isi akademik yang masuk cakupan; jangan menahan isi pribadi tanpa batas atas alasan immutable history. Policy reference dan masa berlaku harus diperiksa. Uji seluruh jalur dengan data sintetis sebelum data nyata.
7. Implementasikan preflight dan monitoring tanpa paid probe: DB/broker/storage, worker heartbeat, antrean/age, parser memory/PIDs/admission, disk, backup age, TLS expiry, release binding, remaining budget dan pending liability. Default probe tiap 60 detik; tiga kegagalan dependency berturut-turut memicu critical; backup lebih tua dari 24 jam dan billing unknown memicu alert. Threshold awal disk warning <20%/critical <10%; queue age >600 detik warning, dicatat sebagai antrean dan bukan pelanggaran active-processing cap. Kanal operator harus diuji sebelum P5; alert bukan dashboard akademik atau email otomatis ke mahasiswa.
8. Jalankan load test 20 sesi dengan general concurrency 2 dan parser berat maksimum 1/node; cold/warm, dokumen maksimum sesuai batas yang diklaim, serta contention. Jangan menjalankan dua suite parser berat serentak. Ukur p95 request metadata/list/save, queue/processing terpisah, memory, PIDs, restart dan ruang disk. Profil produksi serta host headroom harus lolos tanpa reset worker sebagai syarat tiap dokumen.
9. Uji rollback code/config: simpan image/migration/release kompatibel terakhir; jangan downgrade schema destruktif. Bila rollback kode tidak kompatibel dengan schema, gunakan maintenance + restore tervalidasi. Snapshot publikasi/keputusan dan liabilities harus tetap terlacak. Uji manual review tetap tersedia saat provider off/budget habis.

**Keluaran:** Compose/runbook Linux, SeaweedFS compatibility report, backup/restore/purge/preflight tooling, restore drill, capacity/alert/rollback report. **Verifikasi:** P3 memakai demo synthetic pada konfigurasi produksi, uji akses tidak sah, serta restore ke target baru. **Gate:** TLS/storage/secrets, kapasitas, pemulihan dan jalur operator terbukti; bukti P2 tetap diperlukan sebelum menyatakan siap pilot AI. Layanan sehat saja tidak meluluskan gate bila backup tidak dapat dipulihkan atau target kapasitas tidak terpenuhi.

### P4 — Kualifikasi pemakaian data nyata

**Input:** bukti software/live/operasi yang relevan, izin pemrosesan/provider, policy data yang sah, dokumen representatif berizin dan reviewer dosen. Tidak mensyaratkan file RPP jika konteks manual sah; tidak mensyaratkan seluruh penelitian ablation selesai.

1. Catat scope task family, mata kuliah, kategori, modalitas dan batas klaim. Pisahkan data pengembangan, validasi dan frozen test sesuai protokol; jangan memasukkan gold/rater labels atau karya lain ke retrieval runtime. Minimalkan identitas pada payload provider; original berizin tetap memiliki lineage.
2. Susun label acuan, codebook, adjudication dan kriteria penerimaan sebelum melihat hasil kandidat. Ukur precision/recall, evidence support, coverage/abstention, critical invariant violations, repeatability, latency serta biaya settled memakai evaluator existing dan denominator master. Kekurangan support menghasilkan incomplete.
3. Pertahankan `require_release_validation_report`: live institutional memerlukan report immutable `eligible` dengan core hash, data mode, compatibility dan scope yang cocok; `empirical_verification=true` tidak boleh diisi tanpa bukti. Report mock/synthetic tidak memberi izin institutional. Jangan mengubah threshold, status DB atau menghitung pending liability sebagai biaya nol.
4. Jelaskan jalur sah untuk mengumpulkan bukti kandidat tanpa menjadikan release yang belum qualified sebagai produksi. Audit kemungkinan ketergantungan bootstrap/qualification pada kode saat pelaksanaan. Jika tidak ada jalur yang memenuhi master dan gate existing, catat blocker teknis spesifik dan mintakan penyelesaian normatif yang diperlukan; jangan membuka bypass institutional atau menyamarkan data nyata sebagai synthetic.
5. Siapkan paket kualifikasi yang dapat ditinjau operator/dosen: dataset/protocol/codebook/split refs dan hashes, laporan, mode, source/release/model/resource versions, actual cost, izin serta limitations. Tetapkan retensi source/review/backup dan jadwal evaluasi ulang saat konfigurasi berubah.

**Keluaran:** paket validasi/otorisasi dan matriks kesiapan yang jujur. **Verifikasi:** evaluator serta negative activation tests; gunakan hanya report aktual yang lolos validator. **Gate:** report institutional eligible dan seluruh kebijakan/izin sesuai. Jika data atau bukti belum tersedia, P4 pending; P0–P3 yang terbukti tetap dapat diserahkan. Panduan ini tidak menjanjikan gate penelitian/evaluator dapat dipenuhi hanya dengan 1–2 course pilot.

### P5 — Aktivasi pilot produksi terbatas

**Input:** P0–P4 lulus, operator/dosen penanggung jawab, release yang disetujui, server/domain, backup destination, kanal alert dan otorisasi aktivasi yang berlaku. Pilih hanya 1–2 course yang disetujui.

1. Serahkan hasil preflight yang dapat ditinjau: deployment/manifest hashes, TLS, permissions, mode, policy expiry, backup terakhir + hasil drill, capacity, budget/liability, release eligibility dan rencana rollback. Peroleh keputusan aktivasi yang belum tersedia setelah paket ini lengkap; gunakan keputusan yang sudah sah tanpa meminta ulang.
2. Terapkan migration dan release dengan tooling operator; bootstrap admin melalui secret stdin/manager. Provision dosen/mahasiswa/enrollment yang benar, temporary password sekali tampil dan wajib diganti. Jangan menjalankan seed demo atau membawa fixture course ke produksi.
3. Jalankan satu perjalanan berizin dari login sampai hasil resmi dan download, cek cross-course/revocation, UI labels serta mode, dan pastikan worker tetap tidak dapat publish. Kegagalan tetap tercatat; jangan mengirim karya nyata ke model di luar scope yang disetujui untuk memperbaiki demo.
4. Pantau antrean, error, sumber yang unassessable, biaya/liability, kapasitas dan backup pada penggunaan pertama. Uji respons operator terhadap alert dan availability review manual. Kebocoran akses, publikasi tanpa approval, ketidakcocokan release/policy atau biaya tak terlacak menghentikan paid/new-job intake dan memicu prosedur pemulihan; jangan menghapus audit atau menimpa hasil lama.
5. Serahkan credential custody, runbook install/upgrade/restore/reconcile/purge/revoke, jadwal backup, pemilik alert, bukti aktual dan residual. Perluasan course/kapasitas atau perubahan model/routing/resource membutuhkan pengujian ulang sesuai dampaknya, bukan otomatis dari keberhasilan pilot.

**Keluaran:** catatan aktivasi dan penerimaan operator/dosen, perjalanan produksi berizin, baseline monitoring dan handover. **Verifikasi:** preflight, observasi browser/API/worker serta pemeriksaan ledger/backup pada target. **Gate:** pilot benar-benar aktif pada scope yang disetujui; tanpa aktivasi status tertinggi hanya siap pilot.

## 5. Perintah tersedia dan interface yang harus dibuat

### 5.1 Sudah tersedia — verifikasi terhadap checkout saat pelaksanaan

Jalankan dari root dengan dependencies frozen terpasang. Catat exit code setiap perintah; jangan menggabungkan beberapa command lalu hanya melaporkan status command terakhir.

```sh
uv run --no-sync python scripts/openapi_contract.py --check
uv run --no-sync ruff check packages services tests migrations scripts
uv run --no-sync mypy packages/domain/src services/api/src services/worker/src
uv run --no-sync pytest tests/unit -q
corepack pnpm --dir apps/web lint
corepack pnpm --dir apps/web typecheck
corepack pnpm --dir apps/web build
```

Perintah berikut **tersedia tetapi bersyarat**; bukan urutan aman untuk dijalankan pada sembarang database:

| Perintah/tool | Prasyarat dan batas |
|---|---|
| `powershell -File scripts/ci.ps1` | Windows + Docker; harness membuat project QA. Jangan menambahkan `-DeployDevelopment` pada audit atau menganggapnya deployment produksi |
| `bash scripts/verify-macos-clean.sh` | macOS; harness clean checkout existing. Bukan installer Linux |
| `bash scripts/dev-macos.sh integration` / `parser-integration` | Wrapper macOS mengelola dispatcher; jangan jalankan pada DB produksi. Suite integrasi mengubah state dan harus memakai DB uji baru |
| `corepack pnpm --dir apps/web test:e2e` | Stack sintetis dan browser tersedia; set `ALIGN_WEB_BASE_URL`, output evidence di luar source, serta executable override yang memang tersedia bila perlu |
| `python scripts/parser_capacity_preflight.py --document-bytes 8589934592 --headroom-bytes 1073741824` | Pemeriksaan awal RAM host; bukan pengganti load/cold/warm/container test |
| `align release register --file <manifest>` / `align release activate <id>` | CLI existing di container operator dengan env/mount yang benar; mengubah state. Jalankan hanya setelah preflight/otorisasi dan jangan reset release developer |
| `align budget topup <amount> --confirmation-ref <ref> --reason <reason>` | Pendanaan yang benar-benar dikonfirmasi; bukan perintah otomatis runner |
| `align budget reconcile <usage-entry-id> --resolution billed\|not_billed --amount-usd <amount> --evidence-ref <ref>` | Hanya setelah billing evidence tersedia; bukan mengosongkan liability untuk melanjutkan smoke |
| `align evaluate metrics --dataset-manifest <file> --output <file>` | Evaluator offline existing; fixture sintetis menghasilkan software evidence |
| `align evaluate policies --candidate <id> --baseline <id> --dataset-manifest <file> --output <file>` | Protokol/manifests/support dan ledger refs sah; output incomplete tidak boleh dipromosikan |
| `scripts/backup-synthetic.sh` / `scripts/restore-synthetic.sh` | Hanya project/payload sintetis yang memenuhi guard script; bukan backup/restore produksi institutional |

Untuk Linux, susun command Compose nyata dalam runbook P3 dengan checkout, seluruh env-file per role, mount manifest, project, volumes, queue prefix dan port yang eksplisit. `--project-name` saja tidak memisahkan file `.env.api`/`.env.worker` yang dirujuk relatif oleh Compose. Gunakan checkout/config privat terpisah. Verifikasi `docker compose ... config --quiet` tanpa mencetak konfigurasi ter-render yang mengandung secret. Uji urutan install/migrate/start pada target; jangan menyalin wrapper macOS ke Linux dan mengklaim sudah bekerja.

### 5.2 Harus diimplementasikan — belum merupakan command siap pakai

| Target interface | Kontrak minimum dan verifikasi |
|---|---|
| `scripts/live-smoke.py` | Argumen `--project`, `--base-url`, `--manifest`, `--output`; default preflight tanpa call ke provider. Eksekusi memerlukan `--execute-live`, `--max-spend-usd` dan `--authorization-ref`, credential dari konfigurasi privat, serta project synthetic yang terverifikasi. Preflight tidak melakukan seed/top-up/activation otomatis. Eksekusi memakai release yang sudah diaktifkan operator dan API/worker asli. Run spend cap harus diperiksa sebelum setiap paid call melalui budget/reservation backend, termasuk held/pending liability dan call concurrent; pemeriksaan total biaya setelah job selesai saja tidak cukup. Pertahankan per-job/global guard, berhenti pada unknown billing, dan jangan mengubah stack pengguna |
| Report runner versi 1 | Minimal `schema_version`, `run_id`, waktu, revision/source fingerprint, provider/data/app modes, manifest hashes, capability/scenario status, job/call/ledger refs tersanitasi, usage/cost/latency aktual, pending liability count, limitations dan artifact refs. Bedakan `passed`, `failed`, `incomplete`; exit nonzero jika eksekusi belum lengkap. Preflight offline yang lulus hanya berarti konfigurasi siap diuji, bukan live inference berhasil |
| `scripts/production-preflight.py` | Default read-only; argumen `--compose-file`, `--env-file`, `--output`. Periksa mode/secrets presence tanpa nilai, per-role env, TLS/storage/worker, parser capacity, policy/release eligibility, backup/restore evidence, channel operator dan rollback compatibility. Report JSON + ringkasan; exit nonzero untuk gate gagal atau input wajib hilang; tidak memanggil LLM atau mengaktifkan release |
| Runbook dan backup/restore produksi | Implementasikan entrypoint Linux yang menerima target/config/policy refs eksplisit, mendukung pemeriksaan tanpa mutasi dan eksekusi opt-in. Backup terenkripsi konsisten serta restore target kosong mengikuti P3; guard synthetic existing tetap diuji. Tetapkan nama/argumen final dalam runbook dan uji help/default/negative paths sebelum menyebutnya tersedia |
| Policy dan readiness operasi | Rekam authorization/policy reference, expiry dan retensi yang berlaku; periksa saat memulai institutional jobs/operasi sensitif. Gunakan penyimpanan/config backend yang terversi dan teruji; nilai secret tidak menjadi payload browser. Readiness/alert tidak membuat endpoint akademik baru atau menjalankan inference berbayar |

API akademik tetap `/api/v1` dengan CSRF, object authorization, revision, idempotency dan error envelope existing. Jangan menambah API khusus runner yang melewati approval. Perubahan schema/database hanya bila diperlukan untuk policy/operasi yang belum terwakili, melalui migration kompatibel dan pengujian histori; update kontrak OpenAPI/client jika benar-benar terdampak. Report smoke bukan `PolicyValidationReport` dan tidak boleh diterima sebagai penggantinya.

## 6. Matriks pengujian dan traceability

Tambahkan link ke test serta bukti aktual pada [requirements traceability](../../requirements_traceability.md). ID operasi lokal `OPS-*` di bawah adalah acceptance tambahan deployment, bukan FR/AC baru atau klaim bahwa master telah diubah. Unit/fake transport, integration, browser, parser nyata, dan paid smoke dilaporkan terpisah.

| ID/acuan | Skenario dan hasil yang harus dibuktikan | Tahap |
|---|---|---|
| FR-02–04; AC-MVP-06–09 | Konteks manual tanpa RPP/CPMK, instruksi utuh, rubrik opsional, lint/approval sah; tidak mengarang kewajiban | P0/P2 |
| FR-05–06/21; INV-02/14; AC-MVP-10–16/46–49 | File asli mixed-format, hash/anchor, cold/warm, timeout/OOM dan contention; failed units tetap di inventory; satu slot berat/node | P0/P3 |
| FR-08–09; INV-01/03/10/13; AC-MVP-17–24/50–58 | Retrieval owner/course/version sebelum ranking; query embedding live; empty top-k tidak berarti absence; prompt injection/foreign ID/schema salah ditolak; human_only tetap manual | P1/P2 |
| FR-10; INV-04; AC-MVP-25–28/52 | Decimal/policy numerik dipertahankan; cache formula/digit OCR unverified tidak menjadi operand pasti | P0/P2 |
| FR-11–16/22–23; INV-05–08/11/16; AC-MVP-29–41/59/61–62 | Retry tidak mengubah keputusan, konflik revision 409, feedback stale/withdrawal, publication immutable dan download berizin; review manual saat provider off/budget habis | P0/P2/P5 |
| FR-01/14/19; AC-MVP-02–04/34 | Cross-course/admin tanpa enrollment/revoked user/roster historis; private evidence/export tidak bocor | P0/P3/P5 |
| FR-16/22; AC-MVP-38–40/55 | Key/model/rates/release mismatch ditolak sebelum network; reservation concurrent, spend cap, protected reserve dan unknown billing benar; institutional tanpa eligible report tetap ditolak | P1/P2/P4 |
| FR-17–18; AC-MVP-41–42/60 | Browser identity, tidak blank, tanpa framework overlay, console bersih, keyboard/touch 360/768/1440, viewer original dan bytes unduhan resmi benar | P0/P2/P5 |
| FR-24; AC-MVP-45/63–65 | Evaluator/protokol/denominator/hashes/history compatibility; fake/mock/skipped tidak menjadi hasil live/empiris | P1/P4 |
| OPS-01; master 18.2/20.1–20.4 | HTTPS satu origin, cookie/CSRF, secret backend, parser child tanpa credential/egress, DB/broker/S3/admin tidak publik; tidak ada seed password pada produksi | P3/P5 |
| OPS-02 | SeaweedFS dengan auth, least-privilege S3 credentials, operasi boto3 aplikasi dan backup round-trip, pagination/hash/MIME, penolakan anonymous dan akses alternatif filer | P3 |
| OPS-03; AC-MVP-43; master 20.6–20.7 | Quiesced DB+object snapshot, enkripsi/tujuan terpisah, restore kosong, tombstone terkini, reconcile jobs/leases/ledger sebelum buka; data purged tidak hidup kembali | P3 |
| OPS-04; master 18.4/20.6 | 20 sesi, p95 metadata <1 detik, profil parser 8 GiB, concurrency 2/1, cold/warm stabil, RPO ≤24 jam dan RTO ≤8 jam terukur | P3 |
| OPS-05 | Alert dependency/backup/billing/capacity sampai operator, probe tidak berbayar, rollback code/schema/release aman tanpa mengulang call unknown | P3/P5 |
| OPS-06 | Policy data/provider/retensi berlaku, institutional report eligible dan deployment hashes cocok; data tanpa izin/expired policy ditolak | P4/P5 |

Test fault injection dilakukan pada project/DB sintetis yang dimiliki run tersebut; OOM memakai container dengan batas memori, bukan menghabiskan memori host. Jangan mematikan layanan pengguna untuk menciptakan failure. Cleanup hanya resource uji milik run, setelah identitas target diverifikasi.

## 7. Kriteria handover dan cara melaporkan hasil

- Setiap P0–P5 memiliki status, input, output, commands aktual/exit codes, environment/versions/hashes, evidence refs, biaya dan limitations; seluruh checkbox dimulai belum selesai.
- Link dokumen/source/fixture dapat dibuka. Contoh command cocok dengan parser CLI/script dan shell yang dituju; tool baru tetap berlabel harus diimplementasikan sampai benar-benar dibuat dan diuji.
- Hasil unit, integration, UI, real parser, synthetic live, evaluasi nyata, load, restore serta operator sign-off dipisah. Tidak ada klaim passed dari nama file, checkbox lama, mock fixture, skipped, atau preflight saja.
- Seluruh capability wajib yang gagal dicatat sebagai blocker. Safe abstention adalah perilaku benar, tetapi tidak meluluskan capability yang belum terbukti.
- Ketersediaan data/API/budget/domain/izin dinyatakan melalui input register; tidak menghentikan pekerjaan lain yang independen. Jangan menandai siap pilot saat report eligible, restore/capacity, atau kebijakan yang diperlukan belum tersedia.
- Handover menyediakan runbook Linux beserta target/config eksplisit, akses operator dan key custody references tanpa secret, backup/restore/reconcile/purge/rollback, kanal alert, serta pekerjaan selesai versus pending berikut pemiliknya.
- Tidak ada perubahan master, B0–B9, default mock, research placeholders, release aktif pengguna atau data nyata sebagai efek samping penulisan laporan. Perubahan implementasi berikutnya mengikuti tahap dan otorisasi yang diminta.

## 8. Prompt siap salin untuk agent pelaksana

~~~text
Implementasikan jalur produksi standalone ALIGN sesuai docs/implementation/standalone-production.md dan ALIGN_IMPLEMENTATION_SPEC.md sebagai sumber normatif. Target: pilot 1–2 mata kuliah, satu server Linux Docker Compose, semua approval/publikasi oleh dosen. Moodle/B9, SSO, email pengguna, skor/dashboard, fine-tuning dan ablation penelitian lengkap di luar scope.

Mulai dengan membaca instruksi repository, git status/diff, master, indeks, panduan ini serta B5/B6/B8. Inspeksi kode dan uji sistem langsung pada project/DB sintetis baru; jangan hanya menerima handover atau menimpa perubahan lokal. Verifikasi source/config container dan ulangi reproduksi masalah yang relevan. Kerjakan P0 lalu P1; persiapan P3 dapat dilanjutkan bila P2 menunggu API/budget. Gunakan konteks manual dan fixture repository; jangan meminta RPP/dokumen mahasiswa nyata untuk pekerjaan tersebut.

Lengkapi preflight dan runner live opt-in, manifest model/capability/tarif terverifikasi, semua consumer adapter/ledger, serta batas biaya/idempotensi/rekonsiliasi. Pertahankan baseline batch 1, guard institutional eligible, role isolation, negative coverage gate, dan human review. Jangan membuat status eligible, tarif, actual cost, model availability atau hasil smoke yang belum dibuktikan. Verifikasi dokumentasi resmi serta akses akun sebelum mem-pin model/tarif.

Gunakan input API/key/budget dan otorisasi yang sudah sah; jika belum tersedia, selesaikan semua pekerjaan offline/independen dan catat P2 pending. Jangan membuat paid call sebagai probe, top-up otomatis, atau mengulang call dengan billing unknown. P2 hanya development/demo live synthetic terisolasi; P3 membuktikan operasi produksi memakai demo mock synthetic, bukan membypass larangan live/synthetic pada APP_ENV=production.

Implementasikan operasi Linux, SeaweedFS authenticated/private S3, TLS, secrets, akun produksi tanpa seed, backup terenkripsi off-host, restore/tombstone/reconciliation, retensi berizin, alert, kapasitas dan rollback. Jangan sekadar menghapus guard synthetic pada helper backup. Jalankan parser berat berurutan dengan profil sebenarnya; jangan menurunkan resource atau mengabaikan console/error untuk mendapatkan hasil hijau.

P4 memerlukan data berizin, reviewer, kebijakan dan laporan eligible yang cocok. Bila input atau jalur kualifikasi sah belum tersedia, pertahankan gate dan serahkan blocker spesifik; jangan menyamarkan institutional menjadi synthetic. P5 hanya dijalankan dengan seluruh prasyarat dan otorisasi aktivasi yang berlaku. Siapkan hasil konkret yang dapat ditinjau sebelum meminta keputusan yang belum ada; jangan meminta ulang otorisasi yang masih berlaku.

Uji unit/integrasi/real parser/browser/ledger/release serta operasi yang terdampak. Periksa desktop/mobile/keyboard/touch, publikasi mahasiswa dan isi unduhan. Catat commands/exit codes, actual versions/hashes/hardware/biaya, evidence tersanitasi serta limitations dalam docs/handover/standalone-production.md dan traceability. Bedakan software siap, live sintetis terbukti, siap pilot dan pilot aktif. Laporkan pekerjaan selesai dan pending dengan pemiliknya; jangan menyatakan produksi aktif dari keberhasilan mock atau dokumentasi saja.
~~~

## 9. Referensi eksternal untuk diverifikasi saat implementasi

- [SeaweedFS resmi: Quick Start dan Docker](https://github.com/seaweedfs/seaweedfs#quick-start), termasuk autentikasi dan batas deployment satu node. Verifikasi release/image digest serta operasi S3 aktual pada P3.
- [OpenAI: Production best practices](https://developers.openai.com/api/docs/guides/production-best-practices), untuk pemisahan project, akses dan limit penggunaan.
- [OpenAI: Data controls](https://developers.openai.com/api/docs/guides/your-data), untuk kemampuan retensi endpoint yang dipakai. `store:false` pada adapter bukan klaim seluruh retensi provider menjadi nol.
- Model identifiers, supported schema/modalities dan tarif dibaca ulang dari dokumentasi resmi provider pada P1/P2; panduan ini tidak menetapkan harga atau akses akun dari ingatan.

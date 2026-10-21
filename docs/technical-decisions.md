# Catatan keputusan teknis B0–B1

## TD-B0-01 — Storage lokal untuk development

- Status: accepted
- Konteks: B0 memerlukan S3-compatible private storage untuk Compose dan pengujian sintetis.
- Keputusan: gunakan Adobe S3Mock dengan backend filesystem dan named volume hanya pada Compose development/test. Port storage tidak dipublikasikan ke host; production memakai bucket privat yang dikonfigurasi operator.
- Dampak: B0 hanya membuktikan API S3 yang dipakai fondasi. S3Mock bukan layanan production.
- Bukti: [S3Mock repository](https://github.com/adobe/S3Mock) mendokumentasikan image Docker, backend filesystem, bucket awal, dan volume bernama. [MinIO release repository](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z) menyatakan repository diarsipkan pada April 2026; karena itu MinIO tidak dipilih sebagai dependency baru.

## TD-B0-02 — Paid adapter fail-closed

- Status: accepted
- Konteks: manifest tarif/capability dan reservation/ledger belum lengkap di B0.
- Keputusan: seluruh request live ditolak oleh satu guard sebelum adapter dapat melakukan network access. UI default hanya menampilkan mock berlabel simulasi dan data sintetis.
- Dampak: verifikasi provider live dan pengeluaran tetap pending sampai prasyarat finansial pada tahap pemiliknya lengkap.

## TD-B0-03 — Versi dependency

- Status: accepted
- Konteks: master menetapkan major baseline tetapi mensyaratkan exact resolved versions pada lockfile.
- Keputusan: manifest menyatakan kompatibilitas major yang ditetapkan; versi Python dipatok oleh `uv.lock`, frontend oleh `pnpm-lock.yaml`, dan tool pnpm dipatok melalui `packageManager` di root `package.json`.
- Bukti: `uv 0.8.22` menghasilkan `uv.lock`; `pnpm 10.18.3` menghasilkan `pnpm-lock.yaml`. Install frozen berhasil untuk kedua workspace. Toolchain dan pengujian tercatat di [handover B0](handover/B0.md).
- Dampak: versi patch aktual berasal dari resolver dan lockfile, bukan angka tebakan di dokumen ini. Docker runtime tetap dibutuhkan untuk menguji layanan dengan versi baseline PostgreSQL/pgvector dan service lain.

## TD-B1-01 — Koreksi auth dan batas input

- Dasar normatif: master mewajibkan password 12–128 karakter, rotasi session saat ini, revocation session lain, CSRF/Origin, serta kontrol request sebelum hashing.
- Implementasi lama menghapus seluruh session saat ganti password dan membatasi password ke 128 byte. B1 mempertahankan session saat ini dengan bearer/CSRF baru, mencabut session lain, dan menyediakan batas UTF-8 512 byte untuk 128 code point. Login/reset/ganti password diserialkan pada row user.
- Keputusan engineering lokal: middleware ASGI membatasi body auth 8 KiB, metadata 1 MiB dan multipart konteks 20 MiB + 64 KiB overhead. Batas file proyek tetap 20 MiB. Preflight OOXML memakai SAX dengan DTD/entity/external reference ditolak, tanpa menyimpan text node penuh.
- Bukti: `tests/unit/test_api_smoke.py`, `tests/unit/test_security.py`, `tests/unit/test_b1_context_sources.py`, `tests/integration/test_b1_identity_context.py`.

## TD-B1-02 — Parser konteks yang terikat versi

- Native DOCX tetap python-docx/OOXML; PDF/scan/JPG/JPEG/PNG tetap Docling, dengan profil 2/8 GiB, task 120/150 detik, budget node 8 GiB, satu heavy task, dan headroom host 1 GiB. Tidak mengaktifkan backend light atau provider live.
- Method manifests menyimpan profil/resource, hash lockfile dan implementasi, opsi, model checksums, OCR engine/language serta batas. Job, artifact, checkpoint, fingerprint dan evidence mengikat manifest tersebut. Worker menolak konfigurasi yang berbeda dari snapshot job.
- Model build hanya mengambil layout Heron dan TableFormer accurate yang benar-benar dipakai. Revisi dan SHA-256 dicatat dalam `config/parser-context-resources.json`, diperiksa saat build dan sebelum parsing. Tesseract 5.5.0 dengan traineddata `ind`/`eng` menggantikan OCR auto yang sebelumnya tidak mengikuti konfigurasi. Model tidak diunduh saat runtime.
- PDF memakai `page_range` satu halaman yang didukung Docling 2.140.0. Teks native menentukan kebutuhan OCR penuh. Image EXIF ditranspose pada turunan sementara dan bbox dipetakan balik ke pixel original; hash parent/derivative, tool/version dan transform ID ikut checkpoint. Relasi tabel lintas halaman yang belum terbukti ditandai terbatas.
- Temuan runtime: extras Docling lama tidak memasang `rtree`, sehingga layout gagal. Pengguna mengizinkan extra `convert-core`; perubahan minimal menambahkan `rtree 1.4.1` dan `scipy 1.18.1`. Versi dan routing registry paket lama dipertahankan; `pnpm-lock.yaml` tidak berubah. Ini pengecualian yang disetujui terhadap instruksi setup sebelumnya untuk mempertahankan lockfile persis.
- Unduhan model default sebelumnya macet sekitar 50 menit. Downloader build yang baru memakai URL revisi tetap, timeout/retry terbatas dan pemeriksaan checksum. Cache BuildKit uv/model mempercepat rebuild; cache tidak menjadi sumber konfigurasi dan tidak menambahkan folder tool tersembunyi pada checkout.
- Live verification/profile activation dan benchmark lengkap tetap memerlukan gate tahap pemiliknya. Smoke sintetis B1 tidak dianggap benchmark seluruh format/stress.

## TD-B1-03 — Lease, waktu dan admission

- Bug lama: expired lease langsung dilepas walaupun child belum dipastikan mati; coordinator dibatasi 150 detik untuk seluruh file; reservasi waktu memakai soft timeout sehingga grace tidak tercakup; inventory baru dibuat sesudah child selesai.
- B1 menambah heartbeat dan pemeriksaan ownership sebelum commit, kill process group pada cleanup, Linux parent-death signal, inventory sebelum child, serta checkpoint per halaman. Expiry mengarantina node sampai pemilik melepaskan lease setelah cleanup atau operator memastikan seluruh container parser node itu berhenti. Redis fence counter tetap monoton.
- Coordinator context memakai soft/hard 630/660 detik untuk menampung cleanup. Setiap child tetap 120/150 detik dan seluruh attempt memakai sisa reservasi aktif job 600 detik. Broker visibility 720 detik. Angka coordinator bukan jatah analisis baru per file/halaman. Waktu aktual disimpan termasuk download/verifikasi; budget yang habis menghentikan komputasi baru.
- Perhitungan kapasitas cgroup diperbaiki: `limit - current + clean_inactive_file`, dibatasi ukuran current. Dirty/writeback/shmem tidak dikreditkan; stats hilang memberi kredit nol. Host tetap membutuhkan 9 GiB tersedia untuk heavy dan container 8 GiB tersedia. `/proc` host yang tidak diketahui atau cgroup yang tidak dapat diperiksa ditolak; cgroup `max` dikenali sebagai unlimited. Alasan: read-only model cache sekitar 1 GiB sebelumnya menyebabkan worker menahan pekerjaan berikutnya sesudah child mati.
- Ini perubahan logika admission yang perlu dicatat, dengan angka profil/limit tetap. Acuan working-set/cache: [Docker stats](https://docs.docker.com/reference/cli/docker/container/stats/) dan [Linux cgroup v2](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html). Bukti guard ada di `test_parser_admission.py` dan `test_b1_parser_guards.py`; hasil runtime aktual ada di handover.

## TD-B1-04 — Snapshot dan UI konteks

- Migration `0003_b1_provenance_guards` menserialkan approval dengan writes anak/provenance, melarang penambahan source link/unit/evidence sesudah approval, memeriksa hash/anchor/manifest evidence, dan mencabut INSERT ContextVersion dari worker. Migration bersifat tambahan dan diterapkan ke development sesudah pengujian isolated serta backup privat; isi/data konteks dan migration lama tidak ditulis ulang.
- Advisory lock pada key idempotency juga berlaku sebelum row pertama ada. Lock himpunan admin diambil sebelum row user untuk menghindari deadlock demosi bersamaan. API memeriksa revision serta enrollment pada request berikutnya.
- UI memisahkan form draft baru dari editor, memantau job dengan polling, mempertahankan edit yang belum disimpan, memberi pilihan memuat revisi baru, menyediakan koreksi konflik manual/alasan dan provenance per field. API list memakai cursor; UI dapat memuat versi sebelumnya. Cuplikan bukti dibatasi 500 dan sampel unresolved 50 dengan total/truncation eksplisit; inventory tidak dihapus karena parsing gagal.
- B1 menyediakan cuplikan/anchor dan unduhan original terotorisasi. Empat viewer lengkap, namespace submission/XLSX dan roster historis tetap B3. B2 belum dimulai.

## TD-B1-05 — Lingkungan verifikasi macOS

- Pengguna mengizinkan Docker Desktop dinaikkan ke 12 GiB dan direstart. `MemoryMiB=12288` berada di settings Docker host; Engine melaporkan 12.526.637.056 byte (sekitar 11,67 GiB). Ini pilihan mesin lokal, bukan requirement baru checkout.
- Pengujian memakai checkout sementara dan project Compose `align-b1-audit` dengan private env/volumes sendiri serta port 3102. Environment/data development `align` dan semua perubahan setup lokal dipertahankan.
- Untuk smoke heavy, frontend/worker umum milik stack uji dapat dihentikan sementara agar admission memperoleh ruang; API, PostgreSQL, Redis, S3Mock, outbox dan parser tetap dipakai. Pengguna juga mengizinkan worker/scheduler development yang idle (0 job/operation pending) dijeda selama matrix heavy dan dipulihkan sesudahnya; API/web dan data tetap tersedia. Hardware/topologi yang benar-benar lulus dicatat di handover; jangan menganggap dua stack lengkap sekaligus selalu cukup untuk heavy parsing.


## TD-B1-06 — Draft context memakai adapter mock berschema

- Audit menemukan jalur lama langsung menggabungkan kandidat regex dari parser tanpa `ModelProvider.extract_fields`. Sambungan B1 sekarang menggunakan adapter mock yang sudah ada, `ContextFieldOutput` tertutup dan schema_version 1, dengan validasi call/job/scope/manifest/policy serta membership exact pada nilai/source_ref yang diberikan server. Adapter tidak memperoleh kemampuan menambah anchor/field atau mengarang data sumber.
- Mock hanya mengembalikan kandidat teks berlabel yang diperoleh parser. Ini simulasi terlabel, bukan inference semantik. Snapshot model menyimpan hash schema/helper/adapter/fixture, mode dan data mode; fingerprint job mengikat snapshot dan baseline routing yang sudah tersedia. Tidak mengubah policy file baseline atau mengaktifkan model live.
- Baseline context sebelumnya menyimpan max_calls=0 karena adapter belum dipanggil. Job baru memakai cap normatif 3 calls, 2 vision, input/output 20k/4k dan USD0.05; implementasi mock memakai maksimum satu call per assembly dengan usage/paid calls nol. Batas UTF-8 byte 20.000/4.000 konservatif dipakai pada simulasi; payload yang melampaui batas ditahan seluruhnya dan tersedia manual, tanpa silent truncation. Tokenizer live dan accounting finansial tetap pending.
- `PROVIDER_MODE=off` merupakan pilihan eksplisit untuk jalur manual, dengan health/banner yang sesuai. Refusal, unavailable, schema/membership invalid, cap dan konfigurasi berbeda dari snapshot menghasilkan limitation, tidak mengubah field manual. Live tetap tidak dapat dibangun/dipanggil oleh consumer B1 ini.
- Bukti: `test_b1_context_provider.py` (schema, refusal, foreign ref/scope, disabled/live, cap), `test_b1_identity_context.py` (failure injection hingga manual approval), browser DOCX nyata dan matrix Docling dengan status mock tervalidasi. Ini menuntaskan sambungan mock B1; tidak menyatakan seluruh kontrak adapter/routing/model/keuangan FR-04/FR-22 lengkap sebelum B5/B6.

## TD-B2-01 — Usulan instruksi hanya berupa excerpt

- Status: accepted sebagai baseline B2 sementara; tidak menutup gate generator.
- Konteks: kriteria harus dapat disunting tanpa provider, dan model tidak boleh menetapkan kewajiban atau status wajib/opsional.
- Keputusan: generator mock yang tersedia hanya mengembalikan baris persis dari `instruction_original`, hash dan offset Unicode code point. `mandatory` tetap null; usulan tidak ditulis otomatis ke CriterionSet, tidak disahkan, dan tidak melakukan provider call.
- Alasan: alur mock dapat diuji tanpa menafsirkan teks akademik atau menciptakan kewajiban tersembunyi. Ini bukan semantic extraction atau adapter model.
- Dampak: dosen masih dapat mengisi paket manual. Rubrik tetap disimpan privat dengan hash/binding, tetapi belum memakai parser B1 dan belum menghasilkan usulan dari isi rubrik. Integrasi parser/generator rubrik masih pending B2.
- Bukti: `tests/integration/test_b2_assignment_flow.py`; `services/api/src/align_api/assignments.py`.

## TD-B2-02 — Histori schema 1 tidak dimutasi

- Status: accepted.
- Konteks: fresh build tidak memiliki deployment lama nyata; B2 tetap harus menjaga kompatibilitas row schema 1.
- Keputusan: GET mempertahankan payload schema 1 persis. PUT, lint, resolution, approval dan publish yang bukan schema 2 ditolak; pembuatan spec baru selalu schema 2.
- Alasan: migration tidak mengarang derivation, grounding atau lint historis.
- Dampak: fixture schema 1 diberi label sintetis/kompatibilitas, bukan contoh data production. Perubahan history memerlukan AssignmentVersion/spec v2 baru.
- Bukti: `fixtures/synthetic/b2-schema1-history.json`; `tests/integration/test_b2_assignment_flow.py`.

## TD-B2-03 — Roster kelompok mahasiswa

- Status: accepted.
- Keputusan: mahasiswa hanya dapat menemukan kelompok tempat membership aktifnya sendiri, tetapi response kelompok tersebut memuat roster lengkap yang berhak dilihat menurut master. Dosen dapat melihat semua kelompok course.
- Alasan: master mengizinkan roster kelompok sendiri; filter anggota pada serializer sebelumnya menyembunyikan rekan setim.
- Bukti: `tests/integration/test_b2_assignment_flow.py`.

## TD-B2-04 — Bootstrap role database pada volume audit Mac

- Status: keputusan verifikasi lokal.
- Konteks: volume PostgreSQL audit baru sehat, tetapi Docker init mencoba mengeksekusi bind-mounted `docker/postgres/init/001-roles.sh` dan menerima `Permission denied`.
- Keputusan: uji lokal menjalankan skrip repository yang sama melalui `bash` di container audit, lalu memakai migration service normal. Volume/data development tidak disentuh.
- Dampak: ini tidak mengubah file atau permission di repository. Setup bersih Compose perlu memastikan skrip role dapat dijalankan atau menjalankan prosedur Bash yang sudah disediakan `scripts/dev-macos.sh`.
- Bukti: migration isolated `0001`–`0004` dan B0/B1/B2 integration suite lulus; detail ada di `docs/handover/B2.md`.

# Setup macOS dari checkout bersih

Tanggal: 20 Oktober 2026 (Asia/Jakarta). Cakupan: setup source B0/B1 yang sudah
ada untuk runtime manual/mock. Hasil parsing pada setup awal tetap historis; pembuktian B1 lanjutan 21 Oktober dicatat di [handover B1](B1.md).

## Prasyarat mesin

- macOS, Git, Bash, curl, tar, dan shasum. Git dapat disediakan melalui Xcode
  Command Line Tools (`xcode-select --install`) bila belum tersedia.
- Docker Desktop untuk arsitektur Mac yang sesuai, dengan Linux Engine aktif.
  Instalasi Docker Desktop dilakukan operator; installer proyek tidak memasang
  Docker, Homebrew, atau mengubah shell profile.
- Akses jaringan ke distribusi resmi Node, GitHub/uv/Python, registry npm/PyPI,
  serta registry image Docker. Tidak diperlukan API key provider.

Periksa `git --version`, `docker version`, dan `docker compose version`.
Pengujian tercatat memakai Mac ARM64 dan Docker yang sudah terpasang. Instalasi
Docker Desktop dari mesin tanpa Docker dan jalur Mac Intel belum diverifikasi.

## Prosedur pada checkout baru

Clone repository dan checkout revision/branch yang menyertakan skrip setup ini.
Jangan menyalin `.env`, `.venv`, `node_modules`, atau `.codex-tools` dari mesin lain.

```sh
(umask 022; git clone https://github.com/bagaskar44/align.git)
cd align
git switch development
bash scripts/macos-toolchain.sh
source scripts/macos-env.sh
```

Installer menyediakan Node **22.22.0**, uv **0.8.22**, Python **3.12.11**, serta
Corepack dan pnpm **10.18.3** yang dipilih dari `packageManager` repository.
Node dan uv diunduh dari release resmi dan checksum SHA-256 distribusinya
diperiksa. Python disediakan oleh uv. pnpm/dependency aplikasi tetap mengikuti
manifest serta lockfile yang sudah ada; tidak ada floating upgrade lockfile.

Lokasi default, di luar repository:

- Toolchain: `~/Library/Application Support/ALIGN/toolchain`.
- Cache: `~/Library/Caches/ALIGN` (Corepack, pnpm store, uv, npm, browser tes,
  dan arsip/checksum unduhan).

Keduanya dapat diganti **sebelum** menjalankan installer dan saat men-source env:

```sh
export ALIGN_TOOLCHAIN_DIR=/absolute/path/to/toolchain
export ALIGN_CACHE_DIR=/absolute/path/to/cache
bash scripts/macos-toolchain.sh
source scripts/macos-env.sh
```

Tidak diperlukan `.codex-tools/activate.sh`. `source scripts/macos-env.sh`
dijalankan pada setiap terminal baru. Konfigurasi ini tidak ditulis ke shell
profile dan tidak mengganti instalasi global. Python environment host nantinya
mengacu ke interpreter dalam prefix tersebut; prefix harus tetap tersedia selama
`.venv` itu dipakai.

Pilih nama project dan port yang tidak dipakai stack lain. Gunakan nama project
yang sama untuk seluruh perintah selanjutnya. Contoh ini menjaga stack lama
`align` pada port 3000 tetap terpisah:

```sh
export ALIGN_COMPOSE_PROJECT=align-macos
export ALIGN_HOST_PORT=3101
bash scripts/dev-macos.sh bootstrap
bash scripts/dev-macos.sh install
bash scripts/dev-macos.sh up
bash scripts/dev-macos.sh seed
bash scripts/dev-macos.sh verify
bash scripts/dev-macos.sh integration
```

`bootstrap` membuat enam file privat: `.env`, `.env.api`, `.env.worker`,
`.env.migration`, `.env.postgres`, `.env.tests`, dengan izin 0600. Kredensial
role database, storage, CSRF, dan seed dibuat acak. Origin serta host port
mengikuti port yang dipilih. Bootstrap menolak menimpa salah satu file yang sudah
ada, tidak menyediakan opsi force, dan tidak mencetak password.

`install` memasang Python workspace/dev dependencies dengan
`uv sync --frozen --all-packages --group dev` dan frontend dengan
`corepack pnpm install --frozen-lockfile`, memakai cache/store terpilih.
Extra parser tidak termasuk instalasi default ini.

`up` menjalankan urutan berikut:

1. Validasi Compose, lalu nyalakan PostgreSQL, Redis, dan object storage.
2. Periksa empat role PostgreSQL. Jika lengkap, tidak ada perubahan credential.
   Jika semuanya belum ada, jalankan `docker/postgres/init/001-roles.sh` melalui
   Bash di container. Jika baru sebagian ada, berhenti untuk inspeksi operator.
3. Jalankan service migration sampai head menggunakan role migration.
4. Build/start API, worker general, dispatcher, scheduler, web, dan proxy.
   `parser-worker` sengaja tidak termasuk prosedur manual/mock ini.

Pemanggilan Bash mengatasi kegagalan eksekusi skrip bind mount yang ditemukan
pada setup Mac awal. SQL, privilege, extension, dan schema tetap berasal dari
repository; tidak ada password reset otomatis atau penghapusan volume. Jika
role lengkap tetapi credential tidak cocok, migration gagal; prosedur tidak
menghapus data untuk memperbaikinya.

`verify` menjalankan seed dua kali dan memastikan pemanggilan kedua tidak membuat
objek baru. Lalu memeriksa halaman login/readiness, login dosen/mahasiswa/admin,
profil, daftar course, logout, dan penolakan session sesudah logout. Password dan
cookie hanya digunakan di memori, tidak ditulis ke output/argv. Ini smoke HTTP;
verifikasi browser UI terpisah dan tidak diwakili oleh smoke HTTP.

Untuk smoke login melalui browser, setelah `up`/`seed` berhasil:

```sh
corepack pnpm --dir apps/web exec playwright install --only-shell chromium
ALIGN_WEB_BASE_URL=http://localhost:3101 corepack pnpm --dir apps/web test:e2e --grep 'synthetic lecturer can log in'
```

Gunakan port yang sama dengan bootstrap. Browser dipasang ke cache terpilih,
bukan memakai `.playwright-browsers` dari checkout lain. Perintah ini memilih
satu tes login yang sudah ada; bukan bukti seluruh regression suite B1.

Buka **http://localhost:3101** untuk contoh konfigurasi di atas. Akun demo:
`dosen@align.local`, `mahasiswa@align.local`, `dosen2@align.local`,
`mahasiswa2@align.local`, `admin@align.local`. Password adalah nilai
`ALIGN_SEED_PASSWORD` dalam `.env` privat checkout ini. Gunakan origin localhost
persis seperti konfigurasi untuk permintaan mutasi/CSRF.

Untuk mengulang startup gunakan `up` dengan project yang sama; jangan mengulang
bootstrap. Setelah mengubah source, `up` membangun ulang image. Untuk berhenti
sekaligus mempertahankan data:

```sh
export ALIGN_COMPOSE_PROJECT=align-macos
bash scripts/dev-macos.sh stop
```

Tidak ada perintah penghapusan data dalam prosedur. DB/broker/storage tidak
memiliki host-published ports; hanya proxy pada 127.0.0.1.

## Uji reproduksi terisolasi

```sh
bash scripts/verify-macos-clean.sh
```

Harness membuat direktori baru `/private/tmp/align-macos-clean.XXXXXX`, mengekstrak
source dari `git archive HEAD`, lalu menambahkan kandidat source setup dari
working tree menurut daftar file Git. Ini memungkinkan skrip yang belum di-commit
diuji; manifest source dan revision baseline disimpan di direktori bukti.
Folder ignored tidak disalin. Sesudah perubahan setup di-commit, prosedur yang
sama dapat diuji langsung dari checkout revision tersebut.

Harness memakai PATH sistem dan direktori CLI/helper Docker Desktop, prefix
toolchain dan cache kosong, port default
3101 (override `ALIGN_REPRO_PORT`), serta project Docker unik. Ia mengunduh ulang
toolchain, memasang dependency dari lockfile, menjalankan Ruff/pytest/frontend
lint/typecheck, membangun image aplikasi dengan `--no-cache`, lalu menjalankan
startup, seed, smoke HTTP, dan integration runner PostgreSQL. Hash lockfile
diperiksa sebelum/sesudah. Docker Engine, koneksi registry, serta base image cache
boleh tetap milik host; dependency aplikasi tidak diambil dari instalasi ignored
atau database development.

Secara default harness menghentikan **hanya service project uji** setelah sukses,
tanpa membuang volume atau bukti. `ALIGN_KEEP_TEST_RUNNING=1` dapat dipakai untuk
inspeksi/UI tambahan; nama project dan path bukti tercetak sejak awal. Kegagalan
membiarkan resource uji tersedia untuk diagnosis. Jangan memakai project uji
untuk data development permanen.

## Keputusan engineering lokal

| Keputusan | Alasan dan batas |
|---|---|
| Skrip macOS di `scripts/` | Mengisi celah PowerShell-only; bukan fitur B1 baru. |
| Prefix/cache pengguna yang dapat dioverride | Tidak memerlukan instalasi global atau folder toolchain khusus dalam checkout; isolasi dapat dibuktikan. |
| Node 22.22.0 dan Python 3.12.11 untuk host | Patch yang sudah berhasil pada setup awal, dalam baseline major/minor proyek. Runtime Docker tetap memakai Dockerfile asli dan dapat berbeda patch. |
| Download checksum Node/uv dan receipt tersimpan | Verifikasi arsip dari release resmi; bukan mekanisme update otomatis. |
| Bootstrap Python memakai allowlist dari `bootstrap.ps1` | Menghindari duplikasi policy distribusi credential; perubahan format allowlist yang tidak dikenali akan menghentikan bootstrap. |
| Port dan project eksplisit | Memisahkan origin, container, network, image, serta named volume dari stack yang sudah ada. |
| Role inspection sebelum migration | Mengatasi init bind mount Mac tanpa mengubah SQL/privilege, merotasi credential, atau menghapus data. |
| Runtime manual tanpa parser | Scope setup/login; tidak menurunkan kebutuhan parser atau menyatakan gate B1 lulus. |
| Snapshot HEAD + kandidat patch setup | Bukti sebelum commit; bukan klaim bahwa branch remote sudah memuat skrip baru. |
| PATH uji menyertakan direktori Docker | Helper credential Docker Desktop merupakan bagian dependency Docker. Menghilangkannya menggagalkan build meskipun CLI ditemukan. Toolchain aplikasi tetap berasal dari prefix baru. |
| Ekstraksi source dengan umask 022 | File source harus dapat dibaca user non-root dalam image Docker asli. Secret dibuat setelah ekstraksi dengan mode 0600 eksplisit; izin secret tidak dilonggarkan. |

## Hasil yang benar-benar diuji

Bukti uji ulang tersimpan pada direktori sementara privat di host, dengan
snapshot source terpisah, project Docker uji, dan origin `http://localhost:3101`.
Path lengkap dan nama project yang dihasilkan harness tidak disimpan dalam repository.
Baseline Git `739b77a` ditambah kandidat source setup yang belum di-commit;
keenam skrip yang diuji cocok byte-for-byte dengan kandidat workspace.

| Langkah | Hasil aktual |
|---|---|
| Toolchain dari prefix/cache kosong | Node 22.22.0, uv 0.8.22, Python 3.12.11, pnpm 10.18.3 tersedia; checksum Node/uv cocok. |
| Bootstrap privat | Enam file mode 0600; bootstrap ulang ditolak dan seluruh credential tetap sama. |
| Frozen install | Python workspace/dev dan frontend terpasang dari lockfile; cache instalasi terpisah dari checkout development. |
| Pemeriksaan host | Ruff lulus; pytest 23 passed/2 integration skipped/1 warning; ESLint dan TypeScript lulus. |
| Build container | API, worker, web dibangun dengan `--no-cache`; production build Next.js dalam Docker berhasil. |
| Role/database baru | Empat role dibuat melalui SQL repository dengan Bash; pgvector 0.8.7; lima named volume semuanya memakai prefix project uji. |
| Migration | Database kosong di-upgrade melalui 0001 sampai `0002_b1_identity_context`. |
| Startup | Service manual/mock siap; parser-worker tidak dijalankan. |
| Seed | Pemanggilan pertama 5 akun/2 course/4 membership; seed berulang tidak membuat duplikasi. |
| Smoke HTTP | Login page/readiness; login, profil, course, logout dan session invalid sesudah logout untuk dosen/mahasiswa/admin lulus. |
| Login UI | Chromium baru pada cache uji; tes existing `synthetic lecturer can log in` lulus 1/1 (4,8 detik). |
| Integrasi PostgreSQL | 2 passed: role/pgvector serta schema B1 dan immutability approved context. |
| Lockfile | Hash uv.lock dan pnpm-lock.yaml identik sebelum/sesudah seluruh prosedur. |
| Lingkungan development | Keenam environment file tidak berubah; ID/waktu start container API/web/PostgreSQL serta jumlah users/courses/enrollments/context_versions sama dengan sebelum uji. |

Log utama: `resumed.log`; unduhan browser: `browser-install.log`; tes UI:
`login-ui.log`. Manifest source dan revision baseline juga disimpan di direktori
bukti. Log tidak mencetak password, DSN berpassword, atau token session.
Stack uji dihentikan setelah verifikasi; named volume, toolchain/cache uji, dan
bukti dipertahankan. Stack development tidak dihentikan atau di-reset.

Dua percobaan awal menemukan masalah pada harness, kemudian diperbaiki: PATH
menghilangkan helper credential Docker Desktop, dan umask 077 membuat source
snapshot tidak terbaca user non-root dalam image. Ekstraksi source sekarang
memakai umask 022; environment privat tetap eksplisit 0600. Uji snapshot terakhir
sempat terinterupsi saat download toolchain, lalu dilanjutkan pada snapshot yang
sama mulai installer hingga langkah-langkah selanjutnya. Tidak ada dependency
atau database dari checkout development yang disalin. Ini bukti langkah-langkah
prosedur; bukan klaim bahwa pipeline `ci.ps1` lengkap lulus di Mac.

Bukti setup awal pada checkout development (historis, bukan bukti clean setup):
23 unit passed, 2 integration passed, 8 UI passed dengan tes parsing KK/RPP
dikecualikan. Build Docker dan host webpack berhasil. Host Turbopack gagal karena
izin port subprocess; mypy memiliki 7 unused-ignore pada API POSIX kode yang sudah
ada. Pipeline `ci.ps1` lengkap tidak dinyatakan lulus pada Mac.

## Batas parsing pada setup awal (20 Oktober)

Source arsitektur, kontrak API/domain, migration, dependency manifest/lockfile,
file tes, serta guard resource tidak diubah oleh prosedur ini. Dokumen B1 dan
traceability tetap berlaku. Runtime parser, parsing DOCX/PDF/gambar, OCR/model
ARM64, dan seluruh failure/guard acceptance yang belum dibuktikan tetap pending.

VM Docker saat setup awal memiliki total 8,319,770,624 byte (sekitar 7,75 GiB),
di bawah kebutuhan admission Docling 9 GiB tersedia (8 GiB profile + 1 GiB
headroom). Kekurangan Docling tidak membuktikan DOCX native mustahil; profil
native berbeda. Prosedur ini tidak memisahkan/mengaktifkan worker native dan
tidak mengklaim parsing tersedia. Tidak ada limit diturunkan, bypass admission,
atau fallback light otomatis. Full B1 gate memerlukan pekerjaan/verifikasi
terpisah sesuai handover B1.

## Tambahan setelah penyelesaian kandidat B1 (21 Oktober)

Laporan clean setup di atas tetap merupakan bukti revision 739b77a + patch setup pada waktu itu, bukan klaim bahwa harness penuh sudah diulang setelah seluruh perubahan B1. Pada checkout uji B1 terpisah, migration 0003, build API/worker/web/parser, seed idempotent, integrasi dan login/browser diuji kembali. Hasil dan batas aktual ada di [handover B1](B1.md).

Parser tidak dimulai oleh command `up`; langkah eksplisit berikut tersedia:

```sh
bash scripts/dev-macos.sh parser-up
bash scripts/dev-macos.sh parser-integration
```

Build parser memerlukan jaringan untuk paket OS dan dua model pinned; revision/ukuran/checksum dalam `config/parser-context-resources.json` diverifikasi. Runtime memakai model lokal/offline. Perubahan dependency minimal extra `convert-core` (rtree/scipy) disetujui pengguna setelah Docling nyata gagal karena rtree tidak terpasang. Versi paket lama tetap; frozen install memakai lockfile kandidat baru.

Pada Mac ini pengguna mengizinkan Docker Desktop dialokasikan 12 GiB. Heavy tetap membutuhkan 9 GiB host tersedia serta 8 GiB container tersedia; angka alokasi saja tidak membuktikan admission cukup. Dua stack lengkap dapat saling berebut RAM. Saat uji terisolasi, worker/scheduler development yang idle dijeda dengan izin pengguna lalu dipulihkan; jangan menganggap ini tindakan otomatis yang boleh dilakukan pada mesin lain. Tidak ada limit parser diturunkan.

Untuk provider-off pilih `PROVIDER_MODE=off` pada env service privat yang relevan (API dan worker), lalu recreate service; jangan mencetak file env. Mode mock/synthetic tetap default. Live belum memenuhi guard keuangan dan tetap diblokir. Lihat [keputusan B1](../technical-decisions.md) untuk perubahan middleware, admission, manifest, migration dan adapter. Folder ignored/toolchain lokal lama bukan requirement checkout bersih.

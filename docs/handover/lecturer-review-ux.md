# Handover — Alur pengumpulan dan pemeriksaan dosen

Tanggal: 23 Oktober 2026 (Asia/Jakarta). Implementasi perapihan UX berdasarkan master §14, §19 serta FR-11–FR-17. Catatan ini adalah overlay perubahan terbaru; hasil B4–B8 terdahulu tetap historis, bukan bukti runtime untuk UI baru ini.

## Perubahan

- Antrean hanya berisi pemilihan tugas, pencarian mahasiswa/kelompok/roster, filter status review/publikasi, versi/waktu/keterlambatan, status pemrosesan, serta tautan **Buka pemeriksaan**. Pencarian/filter dijalankan server sebelum pagination, bukan hanya terhadap 25 baris yang terlihat.
- Halaman `/courses/[courseId]/submissions/[submissionId]` memeriksa sesi, kewajiban perubahan sandi, membership dosen dan kecocokan course/submission. Tautan kembali mempertahankan tugas, pencarian, filter dan posisi halaman. Metadata course/title dalam ringkasan submission hanya ditambahkan untuk staf.
- Navigasi **Pemeriksaan → Feedback → Riwayat** memisahkan tugas operasional. Desktop menyediakan dua panel selebar ruang kerja dengan slider ukuran; <768 px menggunakan tab Pemeriksaan/Bukti/Keputusan. Form manual, koreksi, hubungan bukti, anotasi dan detail teknis dibuka saat diperlukan.
- Keputusan awal kosong, tanpa auto-accept/no-issue. Semua elemen tetap tersedia melalui pemilih pertanyaan. Observasi, interpretasi, cakupan, keterbatasan dan keputusan dosen dibedakan. Unsupported/partial/failed tidak diganti menjadi terbaca.
- Form numerik memuat paket melalui AssignmentVersion yang terikat pada review. Jika elemen merujuk policy numerik tertentu, pilihan dibatasi pada referensi immutable tersebut; jalur temuan numerik manual pada elemen tanpa referensi policy tetap meminta pilihan eksplisit dari policy approved tugas sesuai core B4, bukan memilih policy terbaru otomatis. Form menyediakan operand, literal, sumber, konteks wajib, satuan, komponen penjumlahan dan catatan verifikasi. Backend tetap menghitung Decimal dan menolak sumber/konteks/policy yang tidak sah. Tidak ada input JSON akademik wajib di UI dosen.
- SHA/UUID/MIME/locator mentah/routing masuk Detail teknis. Nama berkas, lokasi manusiawi dan kutipan menjadi acuan tampilan. Perubahan anotasi tetap memperlihatkan preview berkas/versi/fingerprint, beserta hash/locator lengkap yang dapat diperluas. Original, inventory dan histori append-only tetap dipertahankan.
- Keterbatasan berulang diringkas berdasarkan kode dan pesan dengan jumlah serta rincian aslinya. Ringkasan tidak mengubah inventory, denominator ataupun coverage. Viewer DOCX menampilkan lokasi paragraf/tabel/header/footer, bukan memaksakan nomor halaman.
- Isian terlindungi melalui scope perubahan: pindah langkah/tab tidak me-remount form; pindah elemen/review memperingatkan sebelum membuang isian. Link keluar/logout, unload dan traversal history memberi peringatan. Konflik API mempertahankan isian dan menyediakan muat ulang data; bila revisi server lebih baru saat isian lokal belum tersimpan, penyimpanan ditahan dan dosen harus membandingkan lalu memilih dasar revisi secara eksplisit; respons save yang tertinggal tidak menandai suntingan baru sebagai tersimpan.
- Pemantauan GET status sumber dapat memperbarui evidence/viewer tanpa mengganti pengumpulan final yang sedang direview. Bila ada final baru, tampilkan pemberitahuan dan minta buka ulang secara eksplisit; backend tetap menolak publish sumber lama. Polling ditunda saat tab browser tersembunyi dan berbatas 180 putaran (10 detik saat terlihat).
- Analisis opsional tidak mengubah review manusia otomatis. Hasil baru harus dipilih secara eksplisit untuk membuat review baru. Resume hint job menggunakan sessionStorage per versi sumber; hint bukan otorisasi atau sumber status resmi. Pembacaan status memakai API yang berotorisasi. Pemantauan analisis berbatas 180 putaran; buka ulang bila pekerjaan masih berlangsung.
- Draf feedback dipulihkan dari database setelah reload. Semua draf yang belum approved dapat disunting, termasuk template/model. Pembuatan tanpa temuan tetap melalui template netral; penyuntingan tetap mensyaratkan awalan netral, sanitization, batas panjang dan binding sumber. Approval dan publish tetap terpisah; konfirmasi memuat owner, versi, temuan, batas dan teks akhir.
- Riwayat menyediakan review, draf berhalaman, hasil tersimpan, status withdrawn dan penarikan dengan alasan. Deteksi sudah diterbitkan mengacu draf yang dipilih, sehingga tidak memblokir koreksi sah melalui review/draf baru pada sumber yang sama.

## API dan keputusan engineering lokal

1. `GET /reviews/{id}/feedback-drafts?cursor=&limit=`: memakai pemeriksaan akses review dosen yang sudah ada, urutan sequence terbaru dahulu dan stale dihitung terhadap review saat ini.
2. `GET /assignments/{id}/submissions`: parameter aditif `q`, `review_status`, `publication_status`; filter staf memakai state terbaru resmi di database. Response staf menambahkan course/title dan `staff_status`; response mahasiswa tidak memperoleh ringkasan internal tersebut.
3. Proyeksi review menambahkan nomor versi review; daftar publication staf menambahkan referensi review/draf supaya resume dan koreksi tidak tertukar.
4. Snapshot OpenAPI dan kontrak TypeScript dihasilkan ulang oleh generator proyek: **86 path**. Tidak ada migration, dependency baru, perubahan lockfile, secret, finance/resource guard atau arsitektur worker. Tidak mulai B9.
5. Perubahan lokal `ContextWorkspace.tsx` yang sudah ada sebelum pekerjaan ini tetap dipertahankan; `.DS_Store` dan `.vscode/` tidak dihapus atau dimasukkan ke perubahan fitur ini.

## Verifikasi aktual

| Pemeriksaan | Hasil pada perubahan ini |
|---|---|
| Typecheck web | `tsc --noEmit --incremental false -p apps/web/tsconfig.json`: lulus setelah penyesuaian kode dan skenario E2E. |
| ESLint terarah | **0 error, 1 warning** `<img>` di `SubmissionViewer.tsx` (sudah ada sebelumnya). Meliputi komponen/route yang diubah dan skenario E2E. Tidak menjalankan auto-fix. |
| Ruff/mypy API | **Lulus**: Ruff untuk kedua modul API dan fixture integrasi yang diubah; mypy **no issues found in 2 source files**. |
| Whitespace | `git diff --check`: **lulus**. |
| Code generation | `scripts/openapi_contract.py` berhasil menghasilkan snapshot/client dari deklarasi aplikasi; bukan pengujian endpoint atau koneksi database. |
| PostgreSQL / pytest | **Belum dijalankan**, sesuai instruksi pengguna. Tes integrasi baru disiapkan untuk filter, resume, isolation, pagination, neutral-template editing, stale409 dan released/withdrawn. |
| Browser / Playwright | **Belum dijalankan**. Skenario existing diperbarui ke route baru; empat format, anchors, koreksi/anotasi, explicit mock, keputusan, feedback edit/reload/approval/publish, history, keyboard/touch emulation dan screenshot tiga viewport disiapkan. Long-label/repeated-limitation fixture memakai proyeksi response UI, tanpa mengubah database/file asli atau mengklaim pemeriksaan semantik. |
| Build / Docker / provider | **Tidak dijalankan**, sesuai instruksi. Tidak ada hasil visual baru, deployment lokal baru atau bukti live LLM dari pekerjaan ini. |

Ruff menemukan dua masalah statis fixture lama yang disentuh: urutan import dan hash password aktor sintetis berupa literal. Import dirapikan dan fixture memakai `hash_password(PASSWORD)` sebagaimana akun fixture lainnya; tidak ada kredensial environment yang diubah. Ini tidak membuktikan tes runtime fixture sudah lulus.

## Skenario yang masih wajib dibuktikan

- Jalankan integrasi terarah `test_review_queue_resume_filters_and_editable_neutral_template` serta regresi review B4 yang mencakup dua dosen, perubahan sumber, koreksi/anotasi, sumber kehilangan dukungan dan gate publish.
- Jalankan E2E assignment→submission→review→feedback→hasil mahasiswa pada stack synthetic/mock terisolasi. Tidak memilih release live atau memakai provider berbayar untuk regresi.
- Periksa 360/768/1440 px: semua tahap, draft/complete/approved/released, nama panjang, banyak limitation, fieldset/legend, dropdown, file, tabel, resize dan fokus. Screenshot belum dibuat pada source ini.
- Verifikasi pembatalan navigasi browser back/forward dan logout, pemulihan draft setelah reload, kegagalan jaringan, queued parser, final baru, stale409, dan retry save setelah memuat ulang data. Implementasi history guard memulihkan entry Next saat traversal dibatalkan; perilakunya tetap memerlukan browser aktual.
- Touch emulation bukan perangkat sentuh fisik; accessibility tree/keyboard bukan validasi screen reader. Residual manusia/pilot B8 tidak menjadi lulus karena perubahan UI.

## Cara mencoba setelah pengguna menerapkan perubahan

Dari root repository, pengguna dapat menjalankan `bash scripts/dev-macos.sh up` untuk membangun dan menyalakan source terbaru melalui prosedur proyek. Tidak perlu reset, seed ulang atau menghapus volume/data. Command tersebut **tidak dijalankan oleh agent pada pekerjaan ini**.

Buka localhost pada port yang sudah dikonfigurasi (umumnya `http://localhost:3000`), lakukan hard refresh, masuk sebagai dosen lalu pilih Mata kuliah → Pengumpulan → Tugas → Buka pemeriksaan. Coba tab Pemeriksaan, pilih pertanyaan/bukti, simpan keputusan, selesaikan pemeriksaan, susun/sunting feedback, lalu setujui dan terbitkan sebagai dua aksi. Riwayat menampilkan hasil tersimpan. Tombol AI memerlukan pilihan eksplisit dan tetap memakai konfigurasi serta seluruh guard biaya yang sudah ada.

Tidak ada commit atau push. Gate visual dan runtime UI terbaru tetap pending sampai skenario di atas benar-benar dijalankan.

## Overlay lanjutan — metadata bahasa/angka konteks

Atas permintaan pengguna, input “Format lokal” dan “Bahasa” dihapus dari formulir draft baru dan editor/detail utama konteks tersimpan. Konteks baru tetap memakai default API yang sudah ada (`number_locale=id-ID`, `language=id`). Pembuatan draft dari versi disahkan mengirim referensi versi dasar; backend menyalin seluruh field metadata versi tersebut. Editor menyimpan hanya `course_code`, `course_title`, `context_description`, dan capaian pembelajaran, sehingga metadata lama tersembunyi tidak dikosongkan atau ditimpa. Konflik pada field metadata tetap dapat dibuka dan diselesaikan melalui “Detail teknis”; endpoint approval dan validasinya tidak berubah. Tidak ada perubahan parser, NumericPolicy, kontrak backend, atau data.

Regresi Playwright disiapkan di `apps/web/e2e/auth.spec.ts`: memastikan dua label tidak tampil, POST draft tidak mengirim metadata bahasa/angka dan menerima default backend, lalu mengubah bahasa/format tersimpan menjadi nilai historis non-default, menyunting deskripsi melalui UI, dan memeriksa kedua metadata tetap utuh. Skenario tersebut **belum dijalankan** sesuai instruksi.

Verifikasi statis lanjutan pada komponen dan skenario terkait: TypeScript `tsc --noEmit --incremental false -p apps/web/tsconfig.json` lulus; ESLint pada `ContextWorkspace.tsx` dan `auth.spec.ts` lulus; `git diff --check` lulus. Tidak menjalankan test runner, build, Docker, browser, atau provider. Hasil runtime dan tampilan tetap pending.

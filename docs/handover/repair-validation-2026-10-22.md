# Laporan validasi perbaikan ALIGN — 22 Oktober 2026

## Status

Semua gate QA lulus. Image dari run `142edd290b4d` sudah dipromosikan ke stack development, service parser sehat dan cocok fingerprint-nya dengan API, dan draft pengganti sudah dibuat melalui API aplikasi memakai byte sumber rubric asli. Job lama tetap `queued` karena manifest-nya tidak kompatibel dengan worker saat ini; perintah recovery menolaknya sesuai desain.

Mode runtime terverifikasi sebagai `mock` dan `synthetic`. `.env` dan kredensial tidak kami ubah; database development dan volume data dipertahankan. Tidak ada migrasi skema yang ditambahkan.

## Hasil per tahap

| Tahap | Hasil | Bukti |
| --- | --- | --- |
| Dependency Python dan frontend | Lulus: `uv sync --frozen --all-packages --group dev`; `pnpm install --frozen-lockfile`. `pdfjs-dist` tetap pada versi lockfile `6.4.299`. | Log validasi lokal |
| Kontrak, lint, tipe, build | Lulus: OpenAPI/client contract (86 path), Ruff, mypy, frontend lint, typecheck, production build. Lint frontend menyisakan satu warning lama terkait `<img>` di `SubmissionViewer.tsx`; tidak ada error. | Log validasi lokal |
| Unit tests | Lulus pada Windows dan Linux: masing-masing 154 passed (152 yang sudah ada + 2 regresi). | `linux-unit.log` dan log CI Windows |
| Bootstrap/integrasi DB baru | Lulus pada project dan volume QA terpisah: migrasi, seed dua kali (idempoten), registrasi dan aktivasi baseline immutable. Integrasi: 38 passed, 7 skipped karena Docling nyata dan uji OOM dikonfigurasi sebagai tahap khusus. | `integration.log` |
| Docling nyata | Lulus: 6/6, mencakup PDF multi-halaman, PDF scan/OCR, PNG/JPG/JPEG dan rotasi. Tes dijalankan terpisah, tidak dihitung sebagai skip yang berhasil. | `docling-integration.log` |
| Uji OOM terisolasi | Lulus: 1/1 dalam container Linux dengan batas memori 1 GiB. | `oom-integration.log` |
| Runtime parser QA | Lulus: pemeriksaan konfigurasi, OCR, hash model saat startup, task `parse_context`, `parse_rubric`, `parse_submission`, healthcheck broker dan smoke worker. | `runtime.log` |
| Alur browser E2E | Lulus: 10/10 tanpa skip. Mencakup alur dosen/mahasiswa, parse bukti, viewer PDF/DOCX/XLSX/gambar, analisis mock, review/publish, unduhan mahasiswa, viewport 360/768/1440 dan UI sentuh. Tidak ada error console/page atau task tidak terdaftar. | `e2e.log`, screenshot di bawah |
| Promosi development | Lulus: image API, worker, parser, dispatcher, scheduler, migration dan web dari run QA yang sama dipasang pada project `align`. DB development dan volume PostgreSQL yang sudah ada dipertahankan. API/web/parser/scheduler serta dependency sehat; parser melaporkan `ready`, tiga task terdaftar, dan fingerprint profil sama dengan API. | `docker compose -p align ps`; runtime parser |
| Recovery job lama | Diagnosis menolak job `6af41af9-e547-4924-8e62-9d92f0426cbc`: `parser resource manifest is incompatible with this worker`. Tetap `queued`; event lama tetap ada satu, audit recovery nol. Tidak menjalankan `--execute` dan tidak mengubah identitas/manifest job. | Diagnosis CLI dan pemeriksaan ulang status DB |
| Draft pengganti | Lulus melalui endpoint aplikasi yang sudah ada: assignment `b32783c6-2022-440a-9738-bdc645d31e56`, versi draft `b6483c3c-a74a-408d-acb6-4b789f9da238`, sumber rubric asli dengan SHA-256 sama, dan parser job `0c51c60f-8dcc-4504-8a91-95af663e683e` berstatus `ready_for_review`. Ada 5 bukti, tidak ada provider/AI calls, dan draft belum diterbitkan. | API aplikasi; log parser |

Hash SHA-256 aktual `config/parser-context-resources.json` cocok persis dengan nilai yang dikunci di `config/parser-resource.baseline.json` (`797d4b9731fec7722fbe9b3e266d6e65118f30979bd5118dafd5b58bc176d7fb`).

## Screenshot alur utama

Screenshot review pada tiga lebar viewport dari run E2E:

- [Review — 360 px](repair-validation-2026-10-22/screenshots/b7-review-360.png)
- [Review — 768 px](repair-validation-2026-10-22/screenshots/b7-review-768.png)
- [Review — 1440 px](repair-validation-2026-10-22/screenshots/b7-review-1440.png)

## Catatan parser dan job lama

Job lama tidak dimulai ulang karena manifest-nya tidak cocok dengan worker aktif. Draft baru memakai sumber asli yang sama tanpa menulis ulang identitas atau fingerprint job lama. Parser menghasilkan 5 evidence dan status keseluruhan `ready_for_review`; artefak DOCX berstatus `partially_parsed` karena beberapa bagian package OOXML tidak ditafsirkan profile parser. Batasan itu tersimpan pada hasil parser untuk ditinjau dosen. Job lama diverifikasi tetap `queued` pada tahap `parse_rubric`.

## Artefak run

Log dan file lengkap run tersimpan lokal di `%TEMP%\align-validation-142edd290b4d`. Project/volume QA dibersihkan setelah pengujian; screenshot yang diminta disalin ke direktori laporan ini.

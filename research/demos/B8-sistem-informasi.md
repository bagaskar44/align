# Demo B8 — Sistem Informasi

Status: `run_with_synthetic_evidence` · 22 Oktober 2026 (Asia/Jakarta). Ini bukti alur software pada fixture sintetis, bukan evaluasi hasil belajar atau studi efektivitas.

## Environment dan langkah

- Project Compose/database terisolasi: `align-macos-b8-suite`; migration head `0018_b7_retention`. Database ini terpisah dari database development.
- Checkout uji bersih: `/private/tmp/align-macos-clean.njeRGh/checkout`; web production build dijalankan di Docker pada `http://localhost:3103`.
- Tool/browser: Node 22.22.0, pnpm 10.18.3, Playwright 1.64.1, Desktop Chrome project. Host macOS 26.5.2 ARM64; Docker Engine 29.9.1, Linux ARM64.
- Command dari `apps/web` pada checkout uji (password dibaca dari `.env` privat dan tidak dicatat):

  ```sh
  source ../../scripts/macos-env.sh
  set -a && source ../../.env && set +a
  ALIGN_WEB_BASE_URL=http://localhost:3103 \
  ALIGN_DEMO_LECTURER_EMAIL=dosen2@align.local \
  ALIGN_DEMO_STUDENT_EMAIL=mahasiswa2@align.local \
  ALIGN_DEMO_COURSE_CODE=IF202 \
  ALIGN_DEMO_LABEL='B8 Demo Sistem Informasi' \
  ./node_modules/.bin/playwright test e2e/assignments.spec.ts \
    --grep 'lecturer builds and publishes' --trace=off \
    --output=/private/tmp/align-macos-clean.njeRGh/playwright-si-final2
  ```

- Hasil browser: **1 passed (1.3m)**. Menguji konteks manual B1, tugas/editor B2 dan shared rubric parser, upload/finalisasi submission, PDF/DOCX/XLSX/JPEG/PNG viewer dan parse, analisis mock, review manual/evidence, feedback mock/template, approval/publikasi, login mahasiswa dan export. Tidak ada horizontal overflow pada 360/768/1440 px; keyboard tab diuji pada review mobile; touch emulation tap dan ARIA snapshot navigasi PDF diuji.

## Rekaman runtime

| Objek | ID / versi / hasil |
|---|---|
| Course | `IF202` (data sintetis) |
| Approved context | `3e44f2c4-4440-4804-b939-1a72b17bda27`, versi 3 |
| Assignment | `d6233892-6fa1-4dcc-ac6d-99ea19ccf6d7`, “B8 Demo Sistem Informasi 1792649159470”; versi 1 `published` |
| Submission | `b3be3753-a177-4f4c-b434-d569537798b6`; immutable version `01aa1450-b4c0-4f38-903f-36804d07a565`, versi 1, manifest SHA-256 `97be1740a67fc1e31738a31956f6ee4544ab0090766bb7829083d6ce182cf8fd` |
| Analysis | `b01f4512-7af5-46c9-99fb-c4bf2d9a91bb`; status `partial`, provider `mock`, origin `synthetic`; satu mock call tercatat `completed`, biaya USD `0.000000` |
| Review | `348f6a39-76ab-49fe-999a-d7ffc881800e`, versi 2 `complete`; satu keputusan elemen dan satu finding manual tersimpan |
| Manual evidence | `96badeae-65d5-45da-9cc1-e430b7ea78c5`; linked evidence item `f1c58f51-a5de-40d0-bac5-3c7f132f3919` |
| Annotations | Span `33f4ddd6-8b51-4bf6-9e78-81de8c89d2c8` (revision `5ac42959-76b0-49e7-a72d-a7c9683331f9`); binding `89fcbe46-74a1-4a14-ba9a-b6d7e859c0a8` (revision `da74d3cc-daa2-4ea9-8d5a-aaa14c536450`). E2E menonaktifkan binding dan membuka histori ke sumber PDF asli halaman 1. |
| Feedback / publication | Template feedback `3911cdb5-aff4-4c1c-92c3-c87b0c1935ec`, sequence 2 `approved`; publication `910881d5-3397-4433-83e9-f88664232fbb`, sequence 1, snapshot SHA-256 `eecc375064fb03e96c90a8bbaaf19136c6207f0870af51ea5ae65e4cc553046c`. Actor approval/publish sintetis: `fd087615-19c8-4e02-b7ae-862785884a2d`. |
| Student result | Mahasiswa melihat snapshot publikasi; JSON/Markdown export link terlihat dan file Markdown berhasil diunduh. |

Inventory berisi lima original dengan hash berikut. Semua file berasal dari fixture sintetis, mendapat parse artifact dan tetap tercatat. PDF/image memakai `docling_native_v1`; DOCX/XLSX memakai `native_ooxml_v1`. Tiga artifact berstatus `parsed`, dua `partially_parsed` (DOCX/XLSX); ini tidak disebut parsing lengkap.

| File | File asset ID | Parse artifact ID | Profile | Original SHA-256 | Parse |
|---|---|---|---|---|---|
| `native-two-pages.pdf` | `7edb712f-6698-439a-ac61-b81ec334665d` | `889742a1-24e4-4d89-850d-6a1672725b21` | `docling_native_v1` | `04ba3a7fc49f07b407a75ee2861737fe7d80aa7c70dcaf9f25a178cc28c55d6c` | `parsed` |
| `complex-submission.docx` | `2e749cdc-1040-40f5-95f5-fc1922a53f15` | `65bafa6f-7f37-45c3-a1e5-df3d20c93c38` | `native_ooxml_v1` | `8fe525f17cbf0ec80c185a4a01ac2631b7eea2a6d26da0a51378ccf2501ab1a9` | `partially_parsed` |
| `complex-submission.xlsx` | `11728dc5-95d5-4ac6-aade-539d6e4a67ab` | `298e31de-447a-4917-8137-57616c45faf2` | `native_ooxml_v1` | `0ce5725c0339e2a3b548d9cc5ba419b13488557779f810a2a90202931802467e` | `partially_parsed` |
| `rotated.jpeg` | `c3947690-27d0-49ec-994f-27640ba539db` | `c7cfae98-ac34-4c1e-b710-e70777e6e4a5` | `docling_native_v1` | `8cca617e70ac55a94863d5b278244e244678c08faff3e4414bcb4ee47b668606` | `parsed` |
| `printed.png` | `82cf889e-7578-46e5-86d9-e473cfd0e067` | `61b27b86-2b22-4816-b24d-4a13b49ffc9d` | `docling_native_v1` | `f2843abac997a191a13cc50a72614774814bc1bcc8edb69ab107de5201b22405` | `parsed` |

Screenshot review browser evidence (SHA-256): [360 px](../../docs/handover/b8-reports/demo-si-review-360.png) `20e74f67369811c7d4d1b599e5d0a69a89253fb2ae8961aa5dbfa1326a0ff5a4`; [768 px](../../docs/handover/b8-reports/demo-si-review-768.png) `262f806a53d743da0902d03c089fe64e37a75e3dfed43a0f9cc7c078b27c5c34`; [1440 px](../../docs/handover/b8-reports/demo-si-review-1440.png) `96e46acf2f927d24217f9e58fb164ed8901c62e20ae198c9f6dec7ef98a54dc7`.

Limits: the analysis run is partial and two source formats retain parser limitations. Browser touch was emulated; no physical touch device or VoiceOver/screen-reader user validated the flow. Mock output is visibly labelled and is not provider-real or empirical evidence.

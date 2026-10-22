# Demo B8 — Manajemen

Status: `run_with_synthetic_evidence` · 22 Oktober 2026 (Asia/Jakarta). Ini bukti alur software pada fixture sintetis, bukan evaluasi hasil belajar atau studi efektivitas.

## Environment dan langkah

- Project Compose/database terisolasi: `align-macos-b8-suite`; migration head `0018_b7_retention`. `MANA101` adalah course sintetis tambahan di database E2E ini, bukan data seed default dan bukan database development.
- Checkout uji bersih: `/private/tmp/align-macos-clean.njeRGh/checkout`; web production build dijalankan di Docker pada `http://localhost:3103`.
- Tool/browser: Node 22.22.0, pnpm 10.18.3, Playwright 1.64.1, Desktop Chrome project. Host macOS 26.5.2 ARM64; Docker Engine 29.9.1, Linux ARM64.
- Command dari `apps/web` pada checkout uji (password dibaca dari `.env` privat dan tidak dicatat):

  ```sh
  source ../../scripts/macos-env.sh
  set -a && source ../../.env && set +a
  ALIGN_WEB_BASE_URL=http://localhost:3103 \
  ALIGN_DEMO_LECTURER_EMAIL=dosen2@align.local \
  ALIGN_DEMO_STUDENT_EMAIL=mahasiswa2@align.local \
  ALIGN_DEMO_COURSE_CODE=MANA101 \
  ALIGN_DEMO_LABEL='B8 Demo Manajemen' \
  ./node_modules/.bin/playwright test e2e/assignments.spec.ts \
    --grep 'lecturer builds and publishes' --trace=off \
    --output=/private/tmp/align-macos-clean.njeRGh/playwright-management-final
  ```

- Hasil browser: **1 passed (1.3m)**. Cakupan sama dengan demo SI: konteks/tugas/rubrik, lima jenis file, viewer/parser, review manual dan annotation history, feedback mock/template, approval/publication, hasil mahasiswa dan Markdown download. Tidak ada horizontal overflow pada 360/768/1440 px; keyboard review tab dan touch emulation/ARIA snapshot navigasi PDF diuji.

## Rekaman runtime

| Objek | ID / versi / hasil |
|---|---|
| Course | `MANA101` (data sintetis tambahan untuk E2E) |
| Approved context | `b6be1a14-3808-4760-90bd-937d90f67138`, versi 1 |
| Assignment | `773271ab-e0e3-452e-9db8-cc48b7ac8dd7`, “B8 Demo Manajemen 1792649246784”; versi 1 `published` |
| Submission | `839bc88b-d19c-42c3-9d3e-c9a8c02cf150`; immutable version `8ef2ce38-ec0c-40c5-9ae9-1970d26c8726`, versi 1, manifest SHA-256 `188a8ef787263f1382b36f4196610e1615193fba7bb5923fedc004e5bdb99ada` |
| Analysis | `713c11d7-bc0d-49ea-89ec-94f6dc1817c5`; status `partial`, provider `mock`, origin `synthetic`; satu mock call tercatat `completed`, biaya USD `0.000000` |
| Review | `025a654d-c3aa-461f-b8ce-436cdcde032b`, versi 2 `complete`; satu keputusan elemen dan satu finding manual tersimpan |
| Manual evidence | `ed44ec5c-2506-4493-aefc-0bf13091b7bf`; linked evidence item `0227c7e5-8ebc-4660-8391-c5be50172773` |
| Annotations | Span `9a8675cd-da3d-4e55-8341-27185bc6761c` (revision `c3abced9-48b4-461b-8b7f-3ea6082214fc`); binding `ab112364-4667-4a57-97ca-9ef701944718` (revision `661ff85e-6abd-41c9-b8c6-712dc30ad2b0`). E2E menonaktifkan binding dan membuka histori ke sumber PDF asli halaman 1. |
| Feedback / publication | Template feedback `a780c0b9-ebb5-4c23-ad10-f06cdcf5b8d1`, sequence 2 `approved`; publication `243ce338-537d-4760-b111-f7c6a272c27f`, sequence 1, snapshot SHA-256 `d1e7da4574658df4d24b1aad652709eb999805a5776e07a064f749458abfcab3`. Actor approval/publish sintetis: `fd087615-19c8-4e02-b7ae-862785884a2d`. |
| Student result | Mahasiswa melihat snapshot publikasi; JSON/Markdown export link terlihat dan file Markdown berhasil diunduh. |

Inventory berisi lima original dengan hash berikut. Semua file berasal dari fixture sintetis, mendapat parse artifact dan tetap tercatat. PDF/image memakai `docling_native_v1`; DOCX/XLSX memakai `native_ooxml_v1`. Tiga artifact berstatus `parsed`, dua `partially_parsed` (DOCX/XLSX); ini tidak disebut parsing lengkap.

| File | File asset ID | Parse artifact ID | Profile | Original SHA-256 | Parse |
|---|---|---|---|---|---|
| `native-two-pages.pdf` | `94c09b46-afee-404e-aeea-b448b4ecd88e` | `2a6911f3-e56f-4747-847e-2fd4925d9e45` | `docling_native_v1` | `04ba3a7fc49f07b407a75ee2861737fe7d80aa7c70dcaf9f25a178cc28c55d6c` | `parsed` |
| `complex-submission.docx` | `72a0f5ea-46b1-4305-a9b3-bd2051200ee1` | `dc1df8a9-9b13-460f-9111-149f2aecb3e9` | `native_ooxml_v1` | `8fe525f17cbf0ec80c185a4a01ac2631b7eea2a6d26da0a51378ccf2501ab1a9` | `partially_parsed` |
| `complex-submission.xlsx` | `877834c3-2ea1-42a9-8a20-fb34d0c4b311` | `4586f7f6-71f5-4ba7-a397-1dfbd7718385` | `native_ooxml_v1` | `0ce5725c0339e2a3b548d9cc5ba419b13488557779f810a2a90202931802467e` | `partially_parsed` |
| `rotated.jpeg` | `f7a37e90-a48c-46f4-9089-0d5bc99a45af` | `f13ecf47-0ee7-4c95-b1a2-919b74b6ac49` | `docling_native_v1` | `8cca617e70ac55a94863d5b278244e244678c08faff3e4414bcb4ee47b668606` | `parsed` |
| `printed.png` | `b27e3a3d-b56e-489b-a809-2cb112368f92` | `77e8356a-5eb3-43e2-a51e-dc129205c207` | `docling_native_v1` | `f2843abac997a191a13cc50a72614774814bc1bcc8edb69ab107de5201b22405` | `parsed` |

Screenshot review browser evidence (SHA-256): [360 px](../../docs/handover/b8-reports/demo-management-review-360.png) `0ac9bdd47f109685ea55395c150dbb0268a9acd64695755ab1f71880a128f4fb`; [768 px](../../docs/handover/b8-reports/demo-management-review-768.png) `bb8a4d4a1bb9f6bd8480c38e62eb523132cedc6e13db0d74cc39abd5a662ad3a`; [1440 px](../../docs/handover/b8-reports/demo-management-review-1440.png) `4ce819efe07f2af30f4ed65190fad13227a56f4306b68b493c29a71c06b48a3a`.

Limits: the analysis run is partial and two source formats retain parser limitations. Browser touch was emulated; no physical touch device or VoiceOver/screen-reader user validated the flow. Mock output is visibly labelled and is not provider-real or empirical evidence.

## Rincian status analysis

Run `713c11d7-bc0d-49ea-89ec-94f6dc1817c5` juga memiliki satu elemen, `EL-R1`, dengan rule applicability `task.submission_variant == "original"`. Executor hanya memasok fakta terverifikasi `assignment.mode` dan `submission.file_types`; runtime value custom task fact beserta origin/evidence/konfirmasi dosen belum tersedia. Maka status elemen adalah applicability `unresolved` (`applicability_fact_unverified`), coverage `unresolved`, relationship `unclear`, dan check `unassessable`. Ini abstain yang diwajibkan, bukan keputusan negatif, dan elemen tidak dinilai secara semantik.

Coverage record mencatat retrieval `hybrid`, 69 unit kompatibel, 10 terhitung inspected, 59 unresolved, `sufficient_for_positive=true`, dan `exhaustive_for_negative=false`. Status inspected hanya kesiapan baca; elemen ini tetap belum mencapai semantic assessment. Unit yang berada pada artifact `partially_parsed` ikut dianggap unresolved seluruhnya agar scan negatif tidak melompati sumber; karenanya 59 bukan jumlah unit unsupported semata.

Seperti demo SI, PDF dua halaman, PNG dan JPEG terputar parsed. DOCX partial dengan 16 OOXML package parts unsupported yang tetap berada di inventory. XLSX partial mencatat hidden sheet, 2 package parts unsupported, 3 visual/chart units unsupported dan 2 formula cache unverified; formula tidak dijalankan. Limitations ini menutup klaim absence/exhaustive scan. Embedding retrieval sintetis belum memiliki bukti kualitas semantik. Tidak ada ketiadaan yang disimpulkan dari hasil retrieval kosong atau dari bagian yang tidak terbaca.

## Update rerun TaskFact — 22 Oktober 2026

Status unresolved di atas adalah catatan run historis. Pada project B8 terisolasi terbaru, fixture course `MANA101` dibuat dengan dua enrollment sintetis karena course tersebut tidak ada pada seed database yang tersedia. Tidak ada data atau seed default repo yang diubah.

Analysis run `9a978a29-a6aa-4fcd-9b19-62b6c7144265` terikat ke submission final sintetis, menggunakan TaskFact `submission_variant="original"` yang dikonfirmasi dosen dengan origin, actor/time dan exact assignment/specification version/hash. Hasilnya `partial`, provider `mock`, origin `synthetic`; `EL-R1` berstatus `applicable`, observation menyertakan provenance TaskFact, tetapi semantic status tetap `human_required` dengan reason `human_judgment_required`. Browser review smoke terhadap run tersimpan lulus untuk teks applicability, provenance dan abstain tersebut. Tidak ada semantic finding/no-issue yang dipaksakan.

Run mencatat 69 unit kompatibel, 10 inspected, 59 unresolved, `exhaustive_for_negative=false`. Original PDF, PNG dan JPEG berstatus parsed; DOCX/XLSX partially parsed dengan OOXML, hidden sheet, visual/chart dan formula-cache limitations yang tercatat. Bagian unit dari artifact parsial tetap unresolved; angka ini bukan coverage penuh dan bukan bukti absence.

Full `assignments.spec.ts` pada percobaan pertama menuntaskan alur sampai analisis, tetapi test gagal karena assertion lama meminta `semantic_verification_unavailable`; output aktual yang benar ialah abstain `human_judgment_required` untuk elemen `human_only`. Assertion test sudah diperbarui. Full rerun setelah perubahan belum lulus: satu percobaan berikutnya terkena `parser_capacity_unavailable` saat RAM host sekitar 14 MB di bawah threshold Docling, dan percobaan lain dihentikan sebelum parser karena general worker terisolasi dijeda. Tidak satu pun dihitung sebagai full E2E pass. Guard 9 GiB host/8 GiB parser tetap utuh; service development dan test yang dijeda kemudian dipulihkan.

TaskFact pada demo tidak menyertakan evidence IDs karena asal fakta dicatat sebagai konfirmasi dosen terhadap instruksi. PostgreSQL integration test terpisah membuktikan bahwa evidence rubric sintetis yang sah pada task namespace dapat direferensikan dan provenance-nya dipertahankan; evidence submission dan foreign evidence tetap ditolak. Mock dan synthetic retrieval bukan validasi semantik atau bukti penelitian.

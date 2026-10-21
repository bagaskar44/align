# Fixture parser konteks B1

Semua berkas berisi data sintetis. Dibuat dengan `scripts/generate-b1-fixtures.py`; tidak memuat dokumen mahasiswa. PDF digital dua halaman, PDF scan satu halaman, gambar cetak, dan JPEG berorientasi EXIF 6 digunakan untuk smoke parser nyata, bukan benchmark ketepatan OCR/semantik.

| File | SHA-256 |
|---|---|
| `native-two-pages.pdf` | `04ba3a7fc49f07b407a75ee2861737fe7d80aa7c70dcaf9f25a178cc28c55d6c` |
| `printed.jpeg` | `cb2ee782a9c88c93d46496cd6fa6eb517f0d53b313518b8bb33d24109cbbba1c` |
| `printed.jpg` | `cb2ee782a9c88c93d46496cd6fa6eb517f0d53b313518b8bb33d24109cbbba1c` |
| `printed.png` | `f2843abac997a191a13cc50a72614774814bc1bcc8edb69ab107de5201b22405` |
| `rotated.jpeg` | `8cca617e70ac55a94863d5b278244e244678c08faff3e4414bcb4ee47b668606` |
| `scanned.pdf` | `8be99fac9bca43912668bd3328f8e12bc6b66377a518a98b87943ed8db37c39d` |

Tes: `tests/integration/test_b1_parser_runtime.py`. Assert hash original, anchor halaman/pixel original, manifest, checkpoint per halaman, output adapter mock berschema, dan replay tanpa reservasi parser tambahan. DOCX KK/RPP berada di `apps/web/e2e/fixtures/` dan diuji Playwright.

Font sumber mengikuti ketersediaan font sistem saat generator dijalankan; regenerasi dapat mengubah byte/hash. Byte fixture yang disimpan adalah input pengujian, bukan asumsi bahwa PDF keluaran dua platform identik.

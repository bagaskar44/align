# B9 Moodle Sandbox acceptance — 24 Oktober 2026

## Identitas dan status

- Branch: `feat/b9-acceptance`; worktree: `.codex-tools/worktrees/b9-acceptance`.
- Baseline pekerjaan: `f32f1aa27eaf42a084e4ad5186b97887b1e95e28`.
- Source belum dikomit. Perubahan B0–B8 dari temanmu belum tersedia untuk digabungkan.
- Moodle sintetis: `4.5.15 (Build: 20261005)`; Compose project `align-b9-sandbox`; HTTPS lokal 8443.
- Database memakai migrasi 0021–0023. Tidak ada migrasi baru atau API publik baru.
- Secret, fixture rinci, log mentah dan hasil browser lengkap tetap di direktori ignored `data/b9/private` dan `data/b9/qa`; nilainya tidak disalin ke laporan.

**Hasil pada branch B9:** AC-SBX-01 sampai AC-SBX-05 lulus untuk skenario sintetis di bawah. **Status akhir B9 tetap belum accepted** sampai commit B0–B8 tersedia dan regresi terkait dijalankan pada source gabungan sesuai rencana. Ini batas integrasi branch, bukan kegagalan gate B9 yang sudah diuji.

## Matriks acceptance

| Gate | Bukti | Status dan batas |
|---|---|---|
| **AC-SBX-01** | Browser Moodle–ALIGN: 18 launch berhasil dan 3 penolakan scope (binding hilang, role mismatch, resource tanpa mapping). Dosen/mahasiswa, dua course, resource individu/kelompok, keyboard Enter, viewport 360/768/1440 px. | **Lulus untuk fixture yang diuji.** |
| **AC-SBX-02** | Launch Moodle sah dan replay state terpakai ditolak HTTP 401 tanpa cookie sesi. Browser menangkap dua launch bertanda tangan yang belum dipakai, memutasi signature dan state, lalu keduanya ditolak 401 tanpa sesi. Unit/contract menolak signature/key/algorithm, issuer/audience/azp, nonce, deployment, expiry, message type, target URL dan klaim terdaftar yang tidak cocok; TTL flow lima menit diuji. | **Lulus untuk matrix kontrak dan kasus browser di atas.** Tidak semua mutasi klaim dikirim ke endpoint hidup; mutasi klaim tersisa diverifikasi di validator terisolasi. |
| **AC-SBX-03** | Import individu/kelompok/lintas-course, exact retry, body konflik, empat POST concurrent, sumber identik dengan key berbeda, source baru menjadi versi immutable, serta receipt/audit/version/job cocok. Parser-worker berjalan dan job berstatus `ready_for_review`. Source berubah selama download menghasilkan 409, tanpa perubahan DB/idempotency dan file staging dibersihkan. Object storage dihentikan: import menghasilkan 503 tanpa perubahan DB; setelah storage pulih retry key sama membuat tepat satu event/version/asset/job dan audit cocok. Redis dihentikan setelah login: import sukses dan outbox tertunda; setelah Redis dipulihkan outbox dikirim, parser menyelesaikan job, audit tetap cocok. Moodle hidup menolak token WS sintetis invalid; fungsi WS yang dicabut ditolak oleh adapter dan diverifikasi tersedia kembali sesudah restore. Uji kontrak transport juga menolak redirect asing, HTML, oversize, size metadata berubah dan MIME salah; status final non-file/draft ditolak parser kontrak. | **Lulus untuk failure/recovery yang dijalankan.** Hash SHA-256 dihitung dari bytes yang diunduh; Moodle tidak menyediakan hash sumber tepercaya yang dapat dijadikan pembanding. |
| **AC-SBX-04** | Dua course, tiga mapping aktif, group mapping/import. Membership ALIGN dicabut: impor baru ditolak dan snapshot roster historis tak berubah. Sesi API dengan `LtiSessionBinding` ditolak HTTP 401 setelah pencabutan binding, platform, mapping, revisi mapping, course permission dan enrollment; setiap sesi pulih setelah akses dipulihkan. | **Lulus untuk fixture yang diuji.** Revocation tidak diulang dari tab browser LTI yang tetap terbuka; roster Moodle eksternal lintas-course tidak diuji. |
| **AC-SBX-05** | Review manusia sintetis dipublikasikan melalui API. Pemilik melihat snapshot versi 3; mahasiswa lain mendapat 404. Import versi 4 tidak menyamarkan release lama. Release versi 4 ditarik, feedback default tersembunyi, versi lama tetap dapat dipilih, dan mahasiswa lain tetap ditolak. Browser menguji tampilan released/withdrawn di 360 px. Tidak ada fungsi Moodle grade-write aktif. | **Lulus untuk alur sintetis yang diuji.** PDF hanya label fixture; review menyatakan tidak ada temuan yang dapat disimpulkan, bukan mengklaim kualitas akademik. |

## Perubahan source khusus B9

- `services/api/src/align_api/integrations.py`: receipt idempotensi dibaca sebelum panggilan/download Moodle setelah izin dan mapping divalidasi; roster Moodle 4.5 dan bentuk lama didukung; UUID event dibuat sebelum payload audit.
- `services/api/src/align_api/auth.py`: request dari sesi dengan `LtiSessionBinding` mensyaratkan enrollment aktif; login standalone tidak berubah.
- `tests/unit/test_b9_moodle_adapter.py`: uji token WS invalid, fungsi tidak tersedia, redirect, HTML, oversize, size metadata, MIME, URL file dan sumber non-file.
- `scripts/b9/seed_align_fixtures.py`: runner fixture dan integration acceptance termasuk perubahan sumber saat download, storage/Redis outage serta recovery, revocation sesi dan pemeriksaan WS.
- `scripts/b9/toggle_moodle_ws_function.php`: toggle sementara satu fungsi sintetis saat uji; fungsi dikembalikan dan diverifikasi aktif.
- `scripts/b9/browser_acceptance.cjs`: Moodle LTI nyata, penolakan scope, replay/tampering signature/state, layout, release dan withdrawal.
- Runner QA memakai Playwright 1.64.1 yang dipin. CA dipercaya hanya dalam container QA; TLS tetap diverifikasi. Secret/artefak privat dikecualikan dari Docker build context.
- Tidak ada perubahan migrasi, schema live, bentuk receipt, OpenAPI atau grade passback.

## Validasi

- B9 unit/contract runner: **37 passed**, satu peringatan deprecation Starlette/httpx.
- Regresi unit terpilih (security/access, API smoke, submission anchors, numeric review, dan seluruh B9): **65 passed**, satu peringatan yang sama.
- Browser QA terakhir: **18 allowed, 6 denied**. Enam denial mencakup tiga penolakan scope, replay state, signature yang diubah dan state yang diubah; viewport 360/768/1440.
- Ruff pada Python B9 yang disentuh lulus. Mypy pada `auth.py` dan `integrations.py` lulus. OpenAPI/client: **96 server paths** cocok.
- Moodle hidup menolak token WS invalid. Saat `mod_assign_get_submissions` dinonaktifkan pada service test, pemeriksaan adapter mendapat `moodle_function_not_enabled`; setelah restore server melaporkan fungsi aktif kembali.
- API hidup menguji keenam revocation sesi. Storage 503 dan retry berhasil; Redis/outbox/parsing pulih sampai `ready_for_review`. Semua toggle/outage dipulihkan tanpa menghapus volume.
- Guard resource standar lulus pada pengukuran 12.6 GiB RAM bebas dan 28.6 GiB disk bebas. Heavy parser guard lulus sebelum worker dijalankan; batas guard tidak diturunkan.
- Percobaan seluruh `tests/unit` pada image API-only gagal saat koleksi karena image tersebut tidak memasang source/dependency `align_worker` dan `openpyxl`; itu bukan kegagalan test. Regresi terpilih yang dapat dijalankan di image itu lulus 65 test.
- Baseline core B0–B8 yang sebelumnya tercatat tetap terpisah: 4 gagal (termasuk B6 dan production preflight), 220 lulus, 51 skip. Branch ini belum memuat commit temanmu; full regresi pada source gabungan belum dijalankan.

## Syarat penutupan akhir

1. Setelah commit B0–B8 tersedia, integrasikan secara eksplisit ke `feat/b9-acceptance`.
2. Jalankan regresi unit/integration/browser terkait pada source gabungan dengan image/dependency core lengkap. Verifikasi auth, submission, review/publication dan privilege DB; bandingkan hasil dengan baseline tanpa mengubah kegagalan lama menjadi lulus.
3. Catat commit gabungan dan hasil regresi. Jika tidak ada regresi B9 yang terbuka, status akhir dapat dinaikkan menjadi accepted.

Laporan ini membatasi klaim pada sandbox Moodle lokal dengan data/kredensial sintetis, provider off/mock, tanpa LLM berbayar, grade passback, penulisan feedback ke Moodle atau deployment publik. Catatan laptop lama pada [handover B9](../B9.md) berlabel historis.
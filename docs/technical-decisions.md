# Catatan keputusan teknis B0

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


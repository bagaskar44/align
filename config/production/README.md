# Persiapan Compose produksi mandiri

File di direktori ini adalah template untuk **rehearsal produksi mock/synthetic**. File belum menjadi bukti deployment. Jangan letakkan credential di checkout atau commit salinan hasil pengisian.

## Pembuatan konfigurasi privat

Di host Linux target, buat `/etc/align/private` dengan izin `0700`. Salin `compose.env.example`, seluruh `env/*.example`, dan `seaweedfs-s3.auth.example.json` ke direktori privat, isi melalui password manager, lalu set semua file env/JSON/kunci privat ke `0600`. Sertifikat dan CA boleh dibaca oleh service yang memerlukannya; kunci privat tetap `0600`. Jangan menyalin `.env` development ke target.

Gunakan nilai bucket yang sama pada `S3_BUCKET`, seluruh role env yang memerlukannya, dan seluruh aksi bucket-scoped SeaweedFS. Buat access key aplikasi dan backup secara terpisah. File contoh memakai scope `Read`, `Write`, `List`, dan `Tagging` per bucket tanpa aksi global `Admin`; validasi runtime tetap perlu membuktikan operasi boto3 yang dipakai dan penolakan ke bucket/credential lain. Provision bucket dengan identitas operator sementara melalui prosedur SeaweedFS yang disetujui, lalu hapus identitas bootstrap sebelum service berjalan. `weed admin`/filer tidak dipublikasikan.

Siapkan sertifikat storage dengan SAN `object-storage` serta CA yang dipercaya oleh API/worker. Perintah Compose memakai satu listener S3 TLS pada port internal 8334, dengan auth config berkas dan volume persisten. Jangan membuka port S3, filer, master, atau admin ke host. Catat dan verifikasi digest immutable untuk PostgreSQL, Redis, SeaweedFS, Caddy, dan ketiga image ALIGN; placeholder pada contoh sengaja ditolak preflight.

Untuk rehearsal pertama, pertahankan `APP_ENV=production`, `APP_DEMO_MODE=true`, `PROVIDER_MODE=mock`, `DATA_MODE=synthetic`, dan `PAID_CALLS_ENABLED=false`; biarkan `ALIGN_PRODUCTION_OPENAI_API_KEY` kosong. Compose produksi tidak mewarisi key development. Key provider hanya boleh diisi ke variabel privat tersebut setelah konfigurasi live, model/rates, budget, spend cap, serta otorisasi disetujui. Jangan menjalankan `align seed` pada produksi. Bootstrap operator memakai CLI interaktif stdin `align admin bootstrap`; dosen/mahasiswa sintetis dibuat dan dihapus melalui jalur admin aplikasi, bukan seed development.

## Preflight dan startup yang disiapkan

Dari checkout berisi revision yang akan diuji, dengan Python/uv versi yang dipin proyek:

```sh
uv run --no-sync python scripts/production-preflight.py \
  --compose-file docker-compose.production.yml \
  --env-file /etc/align/private/compose.env \
  --output /etc/align/private/preflight.json \
  --project align-prod-rehearsal \
  --backup-restore-evidence /etc/align/evidence/backup-restore.json \
  --policy-evidence /etc/align/evidence/policy.json \
  --release-evidence /etc/align/evidence/release.json \
  --alert-evidence /etc/align/evidence/alerts.json \
  --rollback-evidence /etc/align/evidence/rollback.json \
  --egress-evidence /etc/align/evidence/egress.json \
  --render-compose
```

Evidence file yang belum ada tetap `pending`; jangan membuat file berstatus lulus sebagai placeholder. Render Compose memakai `config --quiet`, output ter-render ditahan, dan report tidak memuat nilai env. Pastikan report keluar `passed` dan operator meninjau semua digest/config sebelum mutasi.

Perintah startup setelah target, image, secrets, TLS, backup policy dan operator disetujui:

```sh
docker compose --project-name align-prod-rehearsal \
  --env-file /etc/align/private/compose.env \
  -f docker-compose.yml -f docker-compose.production.yml \
  up -d postgres redis object-storage

docker compose --project-name align-prod-rehearsal \
  --env-file /etc/align/private/compose.env \
  -f docker-compose.yml -f docker-compose.production.yml \
  run --rm migrate

docker compose --project-name align-prod-rehearsal \
  --env-file /etc/align/private/compose.env \
  -f docker-compose.yml -f docker-compose.production.yml \
  up -d api worker outbox-dispatcher scheduler parser-worker web proxy
```

Migration dapat dijalankan ulang dengan Alembic sebelum service naik; tidak menjalankan seed. Bootstrap admin dilakukan satu kali dengan service operator setelah migration dan DB sudah siap:

```sh
docker compose --project-name align-prod-rehearsal \
  --env-file /etc/align/private/compose.env \
  -f docker-compose.yml -f docker-compose.production.yml \
  --profile operator run --rm -T operator \
  admin bootstrap --email admin@example.edu --display-name "Admin Operator" --password-stdin
```

Password sementara harus disediakan non-echo melalui stdin dari secret manager, diganti saat login, dan tidak dimasukkan ke argumen shell. Perintah di atas **belum dijalankan** pada target Linux. Hentikan rehearsal dengan `docker compose ... stop`; jangan gunakan `down -v` pada project yang memuat data.

## Batas yang masih menghalangi P3/P5

- Belum ada Linux target, domain/DNS, TLS publik, registry image, maupun digest vendor yang diverifikasi pada runtime ini.
- Compose menyusun service/API S3 TLS dan network `provider-egress`, tetapi host firewall/egress allowlist belum disediakan atau diuji. Parser worker tetap hanya pada network internal.
- SeaweedFS boto3 compatibility, auth denial, bucket provisioning, upload/list/download/delete, hash/MIME, dan backup account round-trip belum dijalankan.
- Belum ada backup produk terenkripsi off-host, restore drill ke target kosong, tombstone/liability reconciliation, retention/purge policy yang disetujui, alert channel, rollback drill, atau load test 20 sesi.
- Live provider capability, model/rate review, budget/run cap approval, dan live smoke terpisah dari rehearsal ini. Key API tidak memberi izin spend.
- Production mock/synthetic belum berarti pilot. Data institusional, kebijakan institusi, report eligibility, dan aktivasi pilot tetap P4/P5 pending.

Dokumentasikan setiap perubahan image, secret custody reference, checksum, target, exit code, dan hasil aktual di `docs/handover/standalone-production.md`; jangan menyimpan secret atau raw data dalam repository.

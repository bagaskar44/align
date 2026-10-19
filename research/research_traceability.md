# Traceability penelitian dan DSRM

Dokumen ini menyediakan kerangka pencatatan B0. Tidak ada dataset, label, benchmark, atau hasil empiris yang diklaim tersedia. Aktivitas ditandai belum dijalankan sampai ada artefak dan bukti yang dapat ditinjau.

| Aktivitas DSRM | Status | Artefak/bukti |
|---|---|---|
| Identifikasi masalah | Belum dijalankan | Catat kebutuhan dan bukti masalah dari pemilik/pengguna saat studi dimulai. |
| Penetapan tujuan solusi | Belum dijalankan | Tautkan objective ke requirement serta keputusan produk yang disetujui. |
| Desain dan pengembangan | Belum dijalankan | Catat versi source, manifest, fixture, dan keputusan engineering per iterasi. |
| Demonstrasi | Belum dijalankan | Catat environment, skenario, data sintetis/berizin, dan command yang benar-benar dieksekusi. |
| Evaluasi | Belum dijalankan | Simpan protokol, denominator, label, interval, limit, serta hasil software terpisah dari efektivitas metode. |
| Komunikasi | Belum dijalankan | Tautkan laporan, batas klaim, dan penerima yang berwenang. |

Setiap catatan iterasi di `research/iterations/` menggunakan format berikut:

```text
ID iterasi:
Tanggal:
Aktivitas DSRM:
Masalah/objective:
Requirement, invariant, acceptance IDs:
Dataset dan izin:
Source/config/manifest hashes:
Protokol dan partition:
Command dan environment:
Hasil aktual dan denominator:
Limitasi/risiko bias:
Keputusan serta iterasi berikutnya:
Artefak:
```

Golden/regression data tidak boleh masuk prompt, retrieval, atau tuning. Parent identity, partition, equivalence review, denominator, serta compiler-fidelity dan executor-robustness dilaporkan sesuai master. Mock dan software correctness tidak menjadi bukti efektivitas empiris.

export function processingLabel(value: string): string {
  const labels: Record<string, string> = { queued: "Menunggu pemrosesan", running: "Sedang diproses", completed: "Pemrosesan selesai", ready: "Siap diperiksa", ready_for_review: "Siap diperiksa", partial: "Terbaca sebagian", failed: "Pemrosesan gagal", blocked: "Pemrosesan tertahan", unsupported: "Format belum didukung", unreadable: "Belum dapat dibaca", readable: "Dapat dibaca", parsed: "Selesai dibaca", partially_parsed: "Terbaca sebagian", not_parsed: "Belum diproses", resource_blocked: "Menunggu sumber daya pemrosesan" };
  return labels[value] ?? "Status pemrosesan perlu diperiksa";
}
export function reviewLabel(value: string): string {
  const labels: Record<string, string> = { not_started: "Belum diperiksa", draft: "Sedang diperiksa", complete: "Selesai diperiksa", not_published: "Belum diterbitkan", released: "Sudah diterbitkan", withdrawn: "Ditarik untuk koreksi", observed_no_issue: "Tidak menemukan masalah", confirmed_issue: "Masalah dikonfirmasi", not_applicable: "Tidak berlaku", unable_to_conclude: "Belum dapat disimpulkan", pending: "Belum diputuskan", reviewed: "Sudah diperiksa", accept: "Dikonfirmasi dosen", edit: "Disunting dan dikonfirmasi", reject: "Ditolak", defer: "Belum dapat disimpulkan", missing_requirement: "Kewajiban belum terpenuhi", rubric_coverage_gap: "Cakupan kriteria belum terpenuhi", numerical_inconsistency: "Ketidakkonsistenan angka", approved: "Disahkan", upsert: "Disimpan", remove: "Dinonaktifkan" };
  return labels[value] ?? "Perlu ditinjau";
}
export function sourceLocation(anchor: Record<string, unknown>): string {
  if (anchor.format === "pdf") return `Halaman ${String(anchor.page ?? "belum teridentifikasi")}`;
  if (anchor.format === "xlsx") return `${String(anchor.sheet_name ?? "Lembar")} · ${String(anchor.cell_range ?? "")}`;
  if (anchor.format === "image") return "Area gambar";
  if (anchor.format === "docx") {
    const path = String(anchor.structural_path ?? "");
    const paragraph = path.match(/(?:paragraph|p)\[(\d+)\]/i);
    const table = path.match(/(?:table|tbl)\[(\d+)\]/i);
    const row = path.match(/tr\[(\d+)\]/i);
    const cell = path.match(/tc\[(\d+)\]/i);
    const part = String(anchor.part ?? "");
    const area = part.includes("header") ? "Kepala dokumen" : part.includes("footer") ? "Kaki dokumen" : part.includes("footnote") ? "Catatan kaki" : part.includes("endnote") ? "Catatan akhir" : "Dokumen";
    return `${area} · ${table ? `tabel ${table[1]}${row ? `, baris ${row[1]}` : ""}${cell ? `, sel ${cell[1]}` : ""}` : paragraph ? `paragraf ${paragraph[1]}` : anchor.item_kind === "media" ? "media tertanam" : "bagian sumber"}`;

  }
  return String(anchor.locator_note ?? "Lokasi seluruh berkas");
}

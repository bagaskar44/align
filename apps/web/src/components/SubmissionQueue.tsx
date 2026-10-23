"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { processingLabel, reviewLabel } from "./reviewPresentation";

export type QueueQuery = { assignment?: string; q?: string; review?: string; publication?: string; cursor?: string };
type Assignment = { id: string; title: string; active_version_id?: string | null };
export type StaffSubmission = {
  id: string; assignment_id: string; revision: number; course_id?: string; assignment_title?: string;
  owner: { individual_user_id: string | null; group_id: string | null; label?: string };
  roster?: string[];
  active_final_version: null | { id: string; version_no: number; manifest_hash: string; finalized_at: string; is_late: boolean };
  files?: Array<{ id: string; name: string; mime: string; sha256: string; parse_status: string; content_url: string; limitations: Array<Record<string, unknown>> }>;
  staff_status?: { review: string; review_id: string | null; publication: string; publication_source_version_id: string | null };
};

export function SubmissionQueue({ assignments, courseId, initialQuery = {} }: {
  assignments: Assignment[]; courseId: string; initialQuery?: QueueQuery;
}) {
  const [assignmentId, setAssignmentId] = useState(initialQuery.assignment ?? "");
  const [search, setSearch] = useState(initialQuery.q ?? "");
  const [query, setQuery] = useState(initialQuery.q ?? "");
  const [review, setReview] = useState(initialQuery.review ?? "");
  const [publication, setPublication] = useState(initialQuery.publication ?? "");
  const [cursor, setCursor] = useState(Number(initialQuery.cursor) || 0);
  const [items, setItems] = useState<StaffSubmission[]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const backQuery = new URLSearchParams({ tab: "submissions", assignment: assignmentId, q: query, review, publication, cursor: String(cursor) });

  useEffect(() => {
    if (!assignmentId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const params = new URLSearchParams({ cursor: String(cursor), limit: "25", q: query, review_status: review, publication_status: publication });
    const location = new URL(window.location.href);
    for (const [key, value] of new URLSearchParams({ tab: "submissions", assignment: assignmentId, q: query, review, publication, cursor: String(cursor) })) location.searchParams.set(key, value);
    window.history.replaceState(window.history.state, "", location);
    async function load() {
      if (document.visibilityState === "hidden") { timer = setTimeout(() => void load(), 15000); return; }
      setLoading(true);
      try {
        const response = await fetch(`/api/v1/assignments/${assignmentId}/submissions?${params}`, { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error("Daftar pengumpulan tidak dapat dimuat. Coba lagi.");
        const page = await response.json() as { items: StaffSubmission[]; next_cursor: number | null };
        if (!active) return;
        setItems(page.items); setNextCursor(page.next_cursor); setError("");
        if (page.items.some((item) => item.files?.some((file) => ["queued", "running"].includes(file.parse_status)))) timer = setTimeout(() => void load(), 10000);
      } catch (cause) { if (active) setError(cause instanceof Error ? cause.message : "Daftar gagal dimuat."); }
      finally { if (active) setLoading(false); }
    }
    void load();
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [assignmentId, query, review, publication, cursor, refresh]);

  return <section className="workspace-panel submission-queue" aria-labelledby="submission-queue-title">
    <header className="workspace-heading"><div><p className="eyebrow">PENGUMPULAN</p><h2 id="submission-queue-title">Pekerjaan mahasiswa</h2></div>
      {assignmentId && <button className="button button-quiet" type="button" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>Muat ulang</button>}</header>
    <label className="field-label">Tugas<select value={assignmentId} onChange={(event) => { setAssignmentId(event.target.value); setItems([]); setError(""); setCursor(0); }}>
      <option value="">Pilih tugas</option>{assignments.filter((item) => item.active_version_id !== null).map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
    </select></label>
    {assignmentId ? <>
      <h3>{assignments.find((item) => item.id === assignmentId)?.title}</h3>
      <form className="queue-filters" onSubmit={(event) => { event.preventDefault(); setQuery(search); setCursor(0); }}>
        <label className="field-label">Cari mahasiswa atau kelompok<input type="search" maxLength={240} value={search} onChange={(event) => setSearch(event.target.value)} /></label>
        <label className="field-label">Pemeriksaan<select value={review} onChange={(event) => { setReview(event.target.value); setCursor(0); }}><option value="">Semua status</option><option value="not_started">Belum dimulai</option><option value="draft">Sedang diperiksa</option><option value="complete">Selesai diperiksa</option></select></label>
        <label className="field-label">Publikasi<select value={publication} onChange={(event) => { setPublication(event.target.value); setCursor(0); }}><option value="">Semua status</option><option value="not_published">Belum diterbitkan</option><option value="released">Sudah diterbitkan</option><option value="withdrawn">Ditarik untuk koreksi</option></select></label>
        <button className="button button-quiet" type="submit">Cari</button>
      </form>
      {error && <p role="alert" className="form-error">{error}</p>}
      {loading && <p role="status">Memuat pengumpulan…</p>}
      {!loading && !error && !items.length && <p className="notice">Belum ada pengumpulan yang sesuai dengan pilihan Anda.</p>}
      <ul className="queue-items">{items.map((item) => <li key={item.id} className="queue-item">
        <div><h3>{item.owner.label ?? "Mahasiswa"}</h3>{item.owner.group_id && <p>{item.roster?.join(", ")}</p>}
          <p>{item.active_final_version ? `Pengumpulan versi ${item.active_final_version.version_no} · ${new Date(item.active_final_version.finalized_at).toLocaleString("id-ID")}` : "Belum dikumpulkan final"}{item.active_final_version?.is_late ? " · Terlambat" : ""}</p>
          <div className="queue-statuses"><span>{item.files?.length ? [...new Set(item.files.map((file) => processingLabel(file.parse_status)))].join(" · ") : "Belum ada berkas final"}</span><span>{reviewLabel(item.staff_status?.review ?? "not_started")}</span><span>{reviewLabel(item.staff_status?.publication ?? "not_published")}</span></div>
          {item.staff_status?.publication_source_version_id && item.staff_status.publication_source_version_id !== item.active_final_version?.id && <p className="help-text">Hasil terbit mengacu pada pengumpulan sebelumnya.</p>}
        </div>
        {item.active_final_version && <Link className="button button-primary" href={`/courses/${courseId}/submissions/${item.id}?${backQuery}`}>Buka pemeriksaan</Link>}
      </li>)}</ul>
      <nav className="button-row" aria-label="Halaman pengumpulan"><button className="button button-quiet" type="button" disabled={loading || cursor === 0} onClick={() => setCursor(Math.max(0, cursor - 25))}>Sebelumnya</button><button className="button button-quiet" type="button" disabled={loading || nextCursor === null} onClick={() => nextCursor !== null && setCursor(nextCursor)}>Berikutnya</button></nav>
    </> : <p className="notice">Pilih tugas untuk melihat pekerjaan yang dikumpulkan mahasiswa.</p>}
  </section>;
}

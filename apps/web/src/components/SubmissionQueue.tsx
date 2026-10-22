"use client";

import { useEffect, useState } from "react";
import { SubmissionFileViewer, type ManualAnchorSelection } from "./SubmissionViewer";
import { ReviewWorkspace } from "./ReviewWorkspace";

type Assignment = { id: string; title: string };
type SubmissionFile = { id: string; name: string; mime: string; preflight: Record<string, unknown>; content_url: string; parse_status: string; limitations: Array<Record<string, unknown>> };
type Submission = {
  id: string;
  assignment_id: string;
  revision: number;
  owner: { individual_user_id: string | null; group_id: string | null; label?: string };
  roster?: string[];
  active_final_version: null | { id: string; version_no: number; manifest_hash: string; finalized_at: string; is_late: boolean };
  files?: SubmissionFile[];
  draft_files: Array<{ id: string; name: string }>;
};

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { cache: "no-store", credentials: "same-origin" });
  const payload = await response.json().catch(() => ({})) as T & { error?: { message?: string } };
  if (!response.ok) throw new Error(payload.error?.message ?? `Permintaan gagal (${response.status}).`);
  return payload;
}

export function SubmissionQueue({ assignments, csrfToken }: { assignments: Assignment[]; csrfToken: string }) {
  const [assignmentId, setAssignmentId] = useState("");
  const [items, setItems] = useState<Submission[]>([]);
  const [selected, setSelected] = useState<Submission | null>(null);
  const [fileId, setFileId] = useState("");
  const [manualAnchorSelection, setManualAnchorSelection] = useState<ManualAnchorSelection | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    if (!assignmentId) return;
    let active = true;
    let timer: number | undefined;
    const startedAt = Date.now();
    const load = async () => {
      setLoading(true);
      try {
        const response = await getJson<{ items: Submission[] }>(`/assignments/${assignmentId}/submissions`);
        if (!active) return;
        setItems(response.items);
        setSelected((current) => current && response.items.some((item) => item.id === current.id)
          ? response.items.find((item) => item.id === current.id) ?? null : null);
        setError("");
        const pending = response.items.some((item) =>
          item.files?.some((file) => ["queued", "running"].includes(file.parse_status)));
        if (pending && active && document.visibilityState === "visible") {
          const delay = Date.now() - startedAt >= 60_000 ? 15_000 : 5_000;
          timer = window.setTimeout(() => void load(), delay);
        }
      } catch (cause) { if (active) setError(cause instanceof Error ? cause.message : "Antrian pengumpulan gagal dimuat."); }
      finally { if (active) setLoading(false); }
    };
    void load();
    const visibilityChanged = () => {
      if (document.visibilityState === "hidden" && timer !== undefined) window.clearTimeout(timer);
      else if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", visibilityChanged);
    return () => { active = false; if (timer !== undefined) window.clearTimeout(timer); document.removeEventListener("visibilitychange", visibilityChanged); };
  }, [assignmentId, refreshKey]);

  function chooseAssignment(value: string) {
    setAssignmentId(value);
    setItems([]);
    setSelected(null);
    setFileId("");
    setManualAnchorSelection(null);
    setError("");
  }

  function selectSubmission(item: Submission) {
    setSelected(item);
    setFileId(item.files?.[0]?.id ?? "");
    setManualAnchorSelection(null);
  }

  const activeFile = selected?.files?.find((item) => item.id === fileId) ?? null;
  return (
    <section className="workspace-panel" aria-labelledby="submission-queue-title">
      <p className="eyebrow">ANTRIAN PENGUMPULAN</p><h2 id="submission-queue-title">Submission mahasiswa</h2>
      <label className="field-label">Pilih tugas
        <select value={assignmentId} onChange={(event) => chooseAssignment(event.target.value)}>
          <option value="">Pilih tugas terbit</option>
          {assignments.map((assignment) => <option key={assignment.id} value={assignment.id}>{assignment.title}</option>)}
        </select>
      </label>
      {error && <p className="form-error" role="alert">{error}</p>}
      {loading && <p className="help-text" role="status">Memuat antrian pengumpulan…</p>}
      {assignmentId && <button type="button" className="button button-quiet" onClick={() => setRefreshKey((value) => value + 1)}>Muat ulang antrian</button>}
      {assignmentId && !loading && items.length === 0 && <p className="notice">Belum ada submission final.</p>}
      {items.length > 0 && <div className="submission-queue-layout">
        <div className="submission-list" aria-label="Daftar submission">
          {items.map((item) => <button key={item.id} type="button" className={`submission-list-row${selected?.id === item.id ? " is-selected" : ""}`} onClick={() => selectSubmission(item)}>
            <strong>{item.owner.label ?? (item.owner.group_id ? "Submission kelompok" : "Mahasiswa")}</strong>
            {item.roster && <span>{item.roster.join(", ")}</span>}
            <span>{item.active_final_version ? `v${item.active_final_version.version_no} · ${new Date(item.active_final_version.finalized_at).toLocaleString("id-ID")}` : "Draft belum final"}</span>
            {item.active_final_version?.is_late && <small>Terlambat</small>}
            {item.files?.some((file) => file.parse_status === "blocked" || file.parse_status === "failed") && <small>Parser memerlukan pemeriksaan</small>}
          </button>)}
        </div>
        {selected && <article className="submission-detail-card">
          <h3>Versi {selected.active_final_version?.version_no ?? "—"}</h3>
          <p className="help-text">Manifest SHA-256: <code>{selected.active_final_version?.manifest_hash ?? "—"}</code></p>
          <div className="submission-file-list">
            {(selected.files ?? []).map((file) => <button key={file.id} type="button" className="submission-file-select" onClick={() => { setFileId(file.id); setManualAnchorSelection(null); }}>
              <strong>{file.name}</strong><span>{file.parse_status} · {file.mime}</span>
              {file.limitations.map((limit, index) => <small key={`${String(limit.code)}-${index}`}>{String(limit.message ?? limit.code)}</small>)}
            </button>)}
          </div>
          {activeFile && <SubmissionFileViewer key={activeFile.id} fileId={activeFile.id} onManualAnchor={setManualAnchorSelection} />}
          {selected.active_final_version && <ReviewWorkspace key={selected.active_final_version.id}
            submission={selected} csrfToken={csrfToken} manualAnchorSelection={manualAnchorSelection}
            onManualAnchorSelection={setManualAnchorSelection} />}
        </article>}
      </div>}
    </section>
  );
}

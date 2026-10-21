"use client";

import { useEffect, useRef, useState, type ChangeEvent } from "react";

type Assignment = { id: string; title: string; version_no?: number | null; due_at?: string; mode?: "individual" | "group" };
type DraftFile = { id: string; name: string; size_bytes: number; mime: string; sha256: string; preflight: Record<string, unknown> };
type SubmissionJobStatus = { status: string; retry_scheduled?: boolean };
type SubmissionStatus = {
  id: string;
  revision: number;
  draft_revision: number;
  active_final_version: null | { id: string; version_no: number; manifest_hash: string; finalized_at: string; is_late: boolean; job_id?: string };
  draft_files: DraftFile[];
  final_files: DraftFile[];
  processing?: string;
};
type ApiError = { error?: { message?: string } };

async function api<T>(path: string, csrf: string, method = "GET", body?: unknown, form?: FormData, idempotencyKey?: string): Promise<T> {
  const headers = new Headers({ "X-CSRF-Token": csrf });
  let requestBody: BodyInit | undefined;
  if (form) requestBody = form;
  else if (body !== undefined) {
    headers.set("Content-Type", "application/json");
    requestBody = JSON.stringify(body);
  }
  if (method === "POST" && path.includes("/finalize")) headers.set("Idempotency-Key", idempotencyKey ?? crypto.randomUUID());
  const response = await fetch(`/api/v1${path}`, { method, headers, body: requestBody, cache: "no-store" });
  const payload = (await response.json().catch(() => ({}))) as T & ApiError;
  if (!response.ok) throw new Error(payload.error?.message ?? `Permintaan gagal (${response.status}).`);
  return payload;
}

export function SubmissionWorkspace({ assignments, csrfToken }: { assignments: Assignment[]; csrfToken: string }) {
  const [selected, setSelected] = useState<Assignment | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [submission, setSubmission] = useState<SubmissionStatus | null>(null);
  const [groups, setGroups] = useState<Array<{ id: string; name: string }>>([]);
  const [groupId, setGroupId] = useState("");
  const [confirmFinal, setConfirmFinal] = useState(false);
  const [job, setJob] = useState<SubmissionJobStatus | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const pollCount = useRef(0);
  const finalizeKey = useRef<string | null>(null);

  async function refresh(id = submission?.id): Promise<SubmissionStatus | null> {
    if (!id) return null;
    const current = await api<SubmissionStatus>(`/submissions/${id}/status`, csrfToken);
    setSubmission(current);
    const jobId = current.active_final_version?.job_id;
    if (jobId) setJob(await api<SubmissionJobStatus>(`/jobs/${jobId}`, csrfToken));
    return current;
  }

  async function openAssignment(assignment: Assignment) {
    setError(""); setNotice(""); setSelected(assignment); setSubmission(null); setJob(null); setConfirmFinal(false);
    try {
      const [assignmentDetail, listed] = await Promise.all([
        api<Record<string, unknown>>(`/assignments/${assignment.id}`, csrfToken),
        api<{ items: SubmissionStatus[] }>(`/assignments/${assignment.id}/submissions`, csrfToken),
      ]);
      setDetail(assignmentDetail);
      const existing = listed.items[0];
      if (existing) {
        setSubmission(existing);
        if (existing.active_final_version?.job_id) {
          setJob(await api<SubmissionJobStatus>(`/jobs/${existing.active_final_version.job_id}`, csrfToken));
          pollCount.current = 0;
        }
      }
      const version = assignmentDetail.version as Record<string, unknown> | null;
      if (version?.mode === "group") {
        const response = await api<{ items: Array<{ id: string; name: string }> }>(`/assignments/${assignment.id}/groups`, csrfToken);
        setGroups(response.items);
        setGroupId(response.items[0]?.id ?? "");
      } else {
        setGroups([]); setGroupId("");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Tugas tidak dapat dibuka.");
    }
  }

  async function ensureSubmission(): Promise<SubmissionStatus | null> {
    if (!selected) return null;
    if (submission) return submission;
    const created = await api<SubmissionStatus>(`/assignments/${selected.id}/submissions`, csrfToken, "POST",
      groupId ? { group_id: groupId } : {});
    setSubmission(created);
    return created;
  }

  async function uploadFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!files.length) return;
    setError(""); setNotice("");
    try {
      let current = await ensureSubmission();
      if (!current) return;
      for (const file of files) {
        const form = new FormData();
        form.set("expected_revision", String(current.draft_revision));
        form.set("file", file, file.name);
        await api(`/submissions/${current.id}/files`, csrfToken, "POST", undefined, form);
        current = await api<SubmissionStatus>(`/submissions/${current.id}/status`, csrfToken);
      }
      setSubmission(current);
      setNotice(`${files.length} berkas berhasil diunggah dan divalidasi.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Berkas tidak dapat diunggah.");
      if (submission) await refresh(submission.id).catch(() => undefined);
    }
  }

  async function deleteDraft(fileId: string) {
    if (!submission) return;
    setError("");
    try {
      await api(`/submissions/${submission.id}/draft-files/${fileId}?expected_revision=${submission.draft_revision}`, csrfToken, "DELETE");
      await refresh(submission.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Berkas tidak dapat dihapus."); }
  }

  async function finalize() {
    if (!submission || !selected) return;
    setError(""); setNotice("");
    try {
      finalizeKey.current ??= crypto.randomUUID();
      const receipt = await api<{ job_id: string }>(`/submissions/${submission.id}/finalize`, csrfToken, "POST", {
        file_ids: submission.draft_files.map((item) => item.id),
        expected_revision: submission.draft_revision,
        display_order: submission.draft_files.map((item) => item.id),
      }, undefined, finalizeKey.current);
      finalizeKey.current = null;
      pollCount.current = 0;
      setJob(await api<SubmissionJobStatus>(`/jobs/${receipt.job_id}`, csrfToken));
      await refresh(submission.id);
      setNotice("Pengumpulan final tersimpan. Versi dan daftar berkas sudah dikunci.");
    } catch (cause) {
      try {
        const current = await refresh(submission.id);
        if (current?.active_final_version) {
          finalizeKey.current = null;
          setNotice("Pengumpulan final tersimpan. Status terbaru sudah dimuat ulang.");
          return;
        }
      } catch { /* keep the idempotency key for a safe retry */ }
      setError(cause instanceof Error ? cause.message : "Pengumpulan tidak dapat difinalisasi.");
    }
  }

  useEffect(() => {
    if (!submission?.id || !submission.active_final_version?.job_id || !job
      || ["ready_for_review", "failed"].includes(job.status)
      || (job.status === "blocked" && !job.retry_scheduled)) return;
    let timer: ReturnType<typeof setTimeout>;
    let cancelled = false;
    const tick = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        await refresh(submission.id);
      } catch { /* keep the last confirmed status and retry on the scheduled poll */ }
      if (cancelled) return;
      pollCount.current += 1;
      schedule();
    };
    const schedule = () => {
      if (cancelled || document.visibilityState !== "visible") return;
      timer = setTimeout(() => void tick(), pollCount.current >= 12 ? 15_000 : 5_000);
    };
    const visibilityChanged = () => {
      clearTimeout(timer);
      if (document.visibilityState === "visible") schedule();
    };
    document.addEventListener("visibilitychange", visibilityChanged);
    schedule();
    return () => { cancelled = true; clearTimeout(timer); document.removeEventListener("visibilitychange", visibilityChanged); };
    // Refresh is deliberately not a dependency; the active job identity drives polling.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submission?.id, submission?.active_final_version?.job_id, job?.status, job?.retry_scheduled]);

  const version = (detail?.version ?? null) as Record<string, unknown> | null;
  return (
    <section className="workspace-panel" aria-labelledby="submission-workspace-title">
      <p className="eyebrow">PENGUMPULAN MAHASISWA</p>
      <h2 id="submission-workspace-title">Kumpulkan tugas</h2>
      <p className="help-text">PDF, DOCX, XLSX, JPG/JPEG, dan PNG · maks. 5 berkas · 20 MiB per berkas. Finalisasi membuat versi yang tidak dapat diubah.</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="notice" role="status">{notice}</p>}
      <div className="assignment-list">
        {assignments.map((assignment) => <button key={assignment.id} type="button" className="list-row assignment-card" onClick={() => void openAssignment(assignment)}>
          <strong>{assignment.title}</strong><span>Versi {assignment.version_no} · tenggat {assignment.due_at ? new Date(assignment.due_at).toLocaleString("id-ID") : "—"}</span>
        </button>)}
      </div>
      {selected && version && <article className="student-task-preview">
        <p className="eyebrow">{selected.title} · VERSI {String(version.version_no)}</p>
        <h3>Instruksi resmi</h3><pre className="instruction-preview">{String(version.instruction_original ?? "")}</pre>
        {version.mode === "group" && !submission && <label className="field-label">Kelompok
          <select value={groupId} onChange={(event) => setGroupId(event.target.value)}>
            {groups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
          </select>
        </label>}
        {!submission?.active_final_version && <>
          <label className="button button-secondary file-picker">Pilih berkas<input type="file" multiple accept=".pdf,.docx,.xlsx,.jpg,.jpeg,.png" onChange={(event) => void uploadFiles(event)} /></label>
          <div className="submission-file-list">
            {(submission?.draft_files ?? []).map((file) => <div className="submission-file-row" key={file.id}>
              <span><strong>{file.name}</strong><small>{(file.size_bytes / 1024 / 1024).toFixed(2)} MiB · {file.mime}</small></span>
              <button className="button button-quiet" type="button" onClick={() => void deleteDraft(file.id)}>Hapus draft</button>
            </div>)}
          </div>
          {submission && submission.draft_files.length > 0 && <>
            <label className="check-row"><input type="checkbox" checked={confirmFinal} onChange={(event) => setConfirmFinal(event.target.checked)} />Saya telah memeriksa berkas dan memahami bahwa finalisasi mengunci bundle.</label>
            <button type="button" className="button button-primary" disabled={!confirmFinal} onClick={() => void finalize()}>Finalisasi pengumpulan</button>
          </>}
        </>}
        {submission?.active_final_version && <div className="notice" role="status">
          <strong>Receipt finalisasi v{submission.active_final_version.version_no}</strong>
          <p>Hash manifest: <code>{submission.active_final_version.manifest_hash}</code></p>
          <p>Waktu server: {new Date(submission.active_final_version.finalized_at).toLocaleString("id-ID")} · {submission.active_final_version.is_late ? "terlambat" : "tepat waktu"}</p>
          <p>Status teknis parser: {job?.status ?? submission.processing ?? "queued"}. Detail parsing internal tidak ditampilkan kepada mahasiswa.</p>
          <ul className="submission-file-list" aria-label="Berkas final">
            {submission.final_files.map((file) => <li key={file.id}><strong>{file.name}</strong><span>{file.mime} · {file.size_bytes} byte</span></li>)}
          </ul>
        </div>}
      </article>}
    </section>
  );
}

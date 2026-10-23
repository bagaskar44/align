"use client";
import { useEffect, useRef, useState } from "react";
import type { StaffSubmission } from "./SubmissionQueue";
import { ReviewWorkspace } from "./ReviewWorkspace";
import type { ManualAnchorSelection } from "./SubmissionViewer";
import { ParserLimitations } from "./ParserLimitations";
import { processingLabel } from "./reviewPresentation";

export function LecturerSubmissionReview({ submission, csrfToken }: { submission: StaffSubmission; csrfToken: string }) {
  const [selection, setSelection] = useState<ManualAnchorSelection | null>(null);
  const [files, setFiles] = useState(submission.files ?? []);
  const [sourceRefreshKey, setSourceRefreshKey] = useState(0);
  const [sourceChanged, setSourceChanged] = useState(false);
  const [statusError, setStatusError] = useState("");
  const snapshot = useRef(JSON.stringify(submission.files ?? []));
  useEffect(() => {
    let active = true;
    let remaining = 180;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      if (document.visibilityState === "hidden") { timer = setTimeout(() => void poll(), 10000); return; }
      try {
        const response = await fetch(`/api/v1/submissions/${submission.id}/status`, { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error("Status sumber belum dapat diperbarui. Muat ulang halaman untuk memeriksa status terkini.");
        const current = await response.json() as StaffSubmission;
        if (!active) return;
        if (current.active_final_version?.id !== submission.active_final_version?.id) { setSourceChanged(true); return; }
        const signature = JSON.stringify(current.files ?? []);
        if (signature !== snapshot.current) { snapshot.current = signature; setFiles(current.files ?? []); setSourceRefreshKey((value) => value + 1); }
        setStatusError("");
      } catch (cause) { if (active) setStatusError(cause instanceof Error ? cause.message : "Status sumber belum dapat diperbarui."); }
      if (active && --remaining > 0) timer = setTimeout(() => void poll(), 10000);
    }
    timer = setTimeout(() => void poll(), 10000);
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [submission.id, submission.active_final_version?.id]);
  return <>
    {sourceChanged && <p className="notice" role="alert">Ada pengumpulan final yang lebih baru. Pemeriksaan ini tetap terikat pada versi lama dan tidak dapat diterbitkan sebagai hasil versi terbaru. <a href={typeof window === "undefined" ? "" : window.location.pathname}>Buka ulang versi terbaru</a></p>}
    {statusError && <p className="notice" role="status">{statusError}</p>}
    <details className="review-source-summary"><summary>Berkas pengumpulan ({files.length})</summary>
      <ul>{files.map((file) => <li key={file.id}><strong>{file.name}</strong> · {processingLabel(file.parse_status)}</li>)}</ul>
      <ParserLimitations entries={files.flatMap((file) => file.limitations.map((item) => ({ ...item, source: file.name })))} />
      <details><summary>Detail teknis pengumpulan</summary><p>Manifest SHA-256: <code>{submission.active_final_version?.manifest_hash}</code></p></details>
    </details>
    <ReviewWorkspace key={submission.active_final_version?.id ?? submission.id} submission={submission} sourceRefreshKey={sourceRefreshKey} csrfToken={csrfToken} manualAnchorSelection={selection} onManualAnchorSelection={setSelection} />
  </>;
}

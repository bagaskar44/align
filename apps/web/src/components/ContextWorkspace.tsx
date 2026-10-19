"use client";

import { useState, type FormEvent } from "react";

type ConflictCandidate = { value: string; source_ref?: Record<string, unknown> };
type ContextConflict = {
  field_path: string;
  candidate_values: ConflictCandidate[];
  resolution: { value?: string; reason?: string } | null;
};
type ContextVersion = {
  id: string;
  version_no: number;
  state: string;
  revision: number;
  fields: Record<string, unknown>;
  instruction_sources: Array<{ file_id: string; source_role: string; original_name: string; sha256: string; download_path: string }>;
  conflicts: ContextConflict[];
  parse_artifacts: Array<{ id: string; file_id: string; status: string; limitations: Array<{ code: string; message: string }> }>;
  evidence: Array<{ id: string; file_name: string; type: string; anchor: Record<string, unknown>; content: Record<string, unknown>; limitations: Array<{ code: string; message: string }> }>;
  content_hash: string | null;
};
type VersionSummary = { id: string; version_no: number; state: string; revision: number; content_hash: string | null };
type Course = { id: string; code: string; title: string };
type UploadedSource = { file_id: string; source_role: "kk" | "rpp"; original_name: string; sha256: string };

const FIELD_LABELS: Record<string, string> = {
  course_code: "Kode mata kuliah",
  course_title: "Nama mata kuliah",
  context_description: "Deskripsi konteks",
  number_locale: "Format angka / lokal",
  language: "Bahasa",
};

async function apiRequest<T>(path: string, csrfToken: string, options: { method: string; body?: unknown; idempotency?: boolean; form?: FormData }): Promise<T> {
  const headers = new Headers({ "X-CSRF-Token": csrfToken });
  let body: BodyInit | undefined;
  if (options.form) body = options.form;
  else if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.body);
  }
  if (options.idempotency) headers.set("Idempotency-Key", crypto.randomUUID());
  const response = await fetch(`/api/v1${path}`, { method: options.method, headers, body, cache: "no-store" });
  const payload = (await response.json().catch(() => ({}))) as { detail?: string };
  if (!response.ok) throw new Error(payload.detail ?? `Permintaan gagal (${response.status}).`);
  return payload as T;
}

function fieldValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object" && "value" in value && typeof value.value === "string") return value.value;
  return "";
}

function outcomeText(value: unknown): string {
  if (!Array.isArray(value)) return "";
  return value
    .filter((item): item is { code: string; description: string } => Boolean(item && typeof item === "object" && "code" in item && "description" in item))
    .map((item) => `${item.code} | ${item.description}`)
    .join("\n");
}

export function ContextWorkspace({ course, csrfToken, initialVersions }: { course: Course; csrfToken: string; initialVersions: VersionSummary[] }) {
  const [versions, setVersions] = useState(initialVersions);
  const [activeContext, setActiveContext] = useState<ContextVersion | null>(null);
  const [formFields, setFormFields] = useState<Record<string, string>>({
    course_code: course.code,
    course_title: course.title,
    context_description: "",
    number_locale: "id-ID",
    language: "id",
  });
  const [outcomes, setOutcomes] = useState("");
  const [uploaded, setUploaded] = useState<UploadedSource[]>([]);
  const [uploadRole, setUploadRole] = useState<"kk" | "rpp">("kk");
  const [baseVersion, setBaseVersion] = useState("");
  const [conflictChoices, setConflictChoices] = useState<Record<string, string>>({});
  const [conflictReasons, setConflictReasons] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function activateContext(context: ContextVersion) {
    setActiveContext(context);
    setFormFields(Object.fromEntries(Object.keys(FIELD_LABELS).map((key) => [key, fieldValue(context.fields[key])] )));
    setOutcomes(outcomeText(context.fields.learning_outcomes));
    setConflictChoices(Object.fromEntries(context.conflicts.map((conflict) => [
      conflict.field_path,
      conflict.resolution?.value ?? conflict.candidate_values[0]?.value ?? "",
    ])));
  }

  async function refreshVersions(selectId?: string) {
    const response = await fetch(`/api/v1/courses/${course.id}/context-versions`, { cache: "no-store" });
    if (!response.ok) throw new Error("Daftar versi konteks tidak dapat dimuat.");
    const payload = (await response.json()) as { items: VersionSummary[] };
    setVersions(payload.items);
    const target = selectId ?? payload.items[0]?.id;
    if (target) await openVersion(target);
  }

  async function openVersion(id: string) {
    setError("");
    setMessage("");
    const response = await fetch(`/api/v1/context-versions/${id}`, { cache: "no-store" });
    if (!response.ok) {
      setError("Versi konteks tidak dapat dibuka.");
      return;
    }
    activateContext((await response.json()) as ContextVersion);
  }

  async function uploadSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fileInput = form.elements.namedItem("source-file");
    if (!(fileInput instanceof HTMLInputElement) || !fileInput.files?.[0]) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const data = new FormData();
      data.set("file", fileInput.files[0]);
      const response = await apiRequest<{ asset: { id: string; original_name: string; sha256: string } }>(
        `/courses/${course.id}/context-files`, csrfToken, { method: "POST", form: data },
      );
      setUploaded((current) => [...current, {
        file_id: response.asset.id,
        source_role: uploadRole,
        original_name: response.asset.original_name,
        sha256: response.asset.sha256,
      }]);
      form.reset();
      setMessage("Berkas tervalidasi dan siap dimasukkan ke draft.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unggah berkas gagal.");
    } finally {
      setBusy(false);
    }
  }

  async function createDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const manualFields = Object.fromEntries(Object.entries(formFields).filter(([key]) => key !== "course_code" && key !== "course_title" || formFields[key].trim()));
      const response = await apiRequest<{ context: ContextVersion; job_id: string | null }>(
        `/courses/${course.id}/context-drafts`,
        csrfToken,
        {
          method: "POST",
          idempotency: true,
          body: {
            sources: uploaded.map(({ file_id, source_role }) => ({ file_id, source_role })),
            manual_fields: manualFields,
            ...(baseVersion ? { base_context_version_id: baseVersion } : {}),
          },
        },
      );
      activateContext(response.context);
      setUploaded([]);
      await refreshVersions(response.context.id);
      setMessage(response.job_id ? "Draft dibuat; ekstraksi berjalan di antrean lokal." : "Draft konteks manual dibuat.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Draft tidak dapat dibuat.");
    } finally {
      setBusy(false);
    }
  }

  async function saveDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeContext) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const blockedFields = new Set(activeContext.conflicts.map((conflict) => conflict.field_path));
      const fields = Object.fromEntries(Object.entries(formFields).filter(([key]) => !blockedFields.has(key)));
      const parsedOutcomes = outcomes.split("\n").map((line) => {
        const separator = line.indexOf("|");
        return separator < 0 ? null : { code: line.slice(0, separator).trim(), description: line.slice(separator + 1).trim() };
      }).filter((item): item is { code: string; description: string } => Boolean(item && item.code && item.description));
      const response = await apiRequest<{ context: ContextVersion }>(`/context-versions/${activeContext.id}`, csrfToken, {
        method: "PUT",
        body: { expected_revision: activeContext.revision, fields, learning_outcomes: parsedOutcomes },
      });
      activateContext(response.context);
      setMessage("Perubahan draft disimpan.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Draft tidak dapat disimpan.");
    } finally {
      setBusy(false);
    }
  }

  async function resolveConflict(conflict: ContextConflict) {
    if (!activeContext) return;
    setBusy(true);
    setError("");
    setMessage("");
    const value = conflictChoices[conflict.field_path] ?? "";
    const candidate = conflict.candidate_values.find((item) => item.value === value);
    try {
      const response = await apiRequest<{ context: ContextVersion }>(`/context-versions/${activeContext.id}`, csrfToken, {
        method: "PUT",
        body: {
          expected_revision: activeContext.revision,
          conflict_resolutions: {
            [conflict.field_path]: {
              value,
              reason: conflictReasons[conflict.field_path] ?? "",
              ...(candidate?.source_ref ? { source_ref: candidate.source_ref } : {}),
            },
          },
        },
      });
      activateContext(response.context);
      setMessage(`Konflik ${FIELD_LABELS[conflict.field_path] ?? conflict.field_path} diselesaikan.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Konflik tidak dapat diselesaikan.");
    } finally {
      setBusy(false);
    }
  }

  async function approveContext() {
    if (!activeContext) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await apiRequest<{ context: ContextVersion }>(`/context-versions/${activeContext.id}/approve`, csrfToken, {
        method: "POST",
        idempotency: true,
        body: { expected_revision: activeContext.revision },
      });
      activateContext(response.context);
      await refreshVersions(response.context.id);
      setMessage(`Versi konteks ${response.context.version_no} disahkan.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Versi konteks belum dapat disahkan.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="workspace-grid">
      <section className="panel" aria-labelledby="draft-title">
        <p className="eyebrow">KONTEKS BARU</p>
        <h2 id="draft-title">Buat draft konteks</h2>
        <p className="muted-copy">Sumber KK dan RPP diproses lokal. Hasil ekstraksi menjadi usulan dengan jejak ke berkas sumber.</p>
        <form id="create-context-form" className="form-stack" onSubmit={createDraft}>
          <label className="field">Deskripsi konteks<textarea required maxLength={4000} value={formFields.context_description} onChange={(event) => setFormFields({ ...formFields, context_description: event.target.value })} /></label>
          <div className="field-row">
            <label className="field">Format lokal<input required maxLength={40} value={formFields.number_locale} onChange={(event) => setFormFields({ ...formFields, number_locale: event.target.value })} /></label>
            <label className="field">Bahasa<input required maxLength={40} value={formFields.language} onChange={(event) => setFormFields({ ...formFields, language: event.target.value })} /></label>
          </div>
          <label className="field">Mulai dari versi disahkan (opsional)
            <select value={baseVersion} onChange={(event) => setBaseVersion(event.target.value)}>
              <option value="">Konteks kosong</option>
              {versions.filter((version) => version.state === "approved").map((version) => <option key={version.id} value={version.id}>Versi {version.version_no}</option>)}
            </select>
          </label>
        </form>
        <div className="source-upload">
            <h3>Tambahkan sumber normatif</h3>
            <form className="upload-row" onSubmit={uploadSource}>
              <label className="field">Berkas PDF, DOCX, JPG, atau PNG<input name="source-file" type="file" accept=".pdf,.docx,.jpg,.jpeg,.png,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/jpeg,image/png" required /></label>
              <label className="field">Peran sumber<select value={uploadRole} onChange={(event) => setUploadRole(event.target.value as "kk" | "rpp")}><option value="kk">KK</option><option value="rpp">RPP</option></select></label>
              <button className="button button-quiet" type="submit" disabled={busy}>Unggah dan validasi</button>
            </form>
            {uploaded.length > 0 && <ul className="compact-list" aria-label="Sumber yang akan dipakai">{uploaded.map((source) => <li key={source.file_id}><span><strong>{source.source_role.toUpperCase()}</strong> · {source.original_name}</span><button className="text-button" type="button" onClick={() => setUploaded(uploaded.filter((item) => item.file_id !== source.file_id))}>Hapus dari draft</button></li>)}</ul>}
        </div>
        <button className="button button-primary" type="submit" form="create-context-form" disabled={busy}>Buat draft</button>
      </section>

      <section className="panel" aria-labelledby="versions-title">
        <p className="eyebrow">VERSI DAN PROVENANCE</p>
        <h2 id="versions-title">Konteks tersimpan</h2>
        {versions.length === 0 ? <p className="muted-copy">Belum ada versi konteks.</p> : (
          <label className="field">Pilih versi
            <select value={activeContext?.id ?? ""} onChange={(event) => { if (event.target.value) void openVersion(event.target.value); }}>
              <option value="" disabled>Pilih versi konteks</option>
              {versions.map((version) => <option key={version.id} value={version.id}>Versi {version.version_no} · {version.state === "approved" ? "disahkan" : "draft"}</option>)}
            </select>
          </label>
        )}
        {activeContext && <>
          <div className="context-meta"><span className={`state-pill ${activeContext.state === "approved" ? "state-approved" : ""}`}>{activeContext.state === "approved" ? "DISAHKAN" : "DRAFT"}</span><span>Versi {activeContext.version_no} · revisi {activeContext.revision}</span></div>
          <form className="form-stack context-form" onSubmit={saveDraft}>
            {Object.entries(FIELD_LABELS).map(([key, label]) => <label className="field" key={key}>{label}<input value={formFields[key] ?? ""} readOnly={activeContext.state !== "draft"} maxLength={key === "context_description" ? 4000 : 240} onChange={(event) => setFormFields({ ...formFields, [key]: event.target.value })} /></label>)}
            <label className="field">Capaian pembelajaran (opsional; satu baris per capaian, format kode | deskripsi)<textarea value={outcomes} readOnly={activeContext.state !== "draft"} onChange={(event) => setOutcomes(event.target.value)} placeholder="CPMK-1 | Menjelaskan prinsip ..." /></label>
            {activeContext.state === "draft" && <button className="button button-quiet" type="submit" disabled={busy}>Simpan perubahan</button>}
          </form>
          {activeContext.conflicts.length > 0 && <section className="subsection" aria-labelledby="conflict-title"><h3 id="conflict-title">Usulan yang berkonflik</h3>{activeContext.conflicts.map((conflict) => <article className="conflict-card" key={conflict.field_path}>
            <h4>{FIELD_LABELS[conflict.field_path] ?? conflict.field_path}</h4>
            {conflict.resolution && <p className="muted-copy">Pilihan tersimpan: <strong>{conflict.resolution.value}</strong></p>}
            <label className="field">Nilai yang dipilih<select disabled={activeContext.state !== "draft"} value={conflictChoices[conflict.field_path] ?? ""} onChange={(event) => setConflictChoices({ ...conflictChoices, [conflict.field_path]: event.target.value })}>{conflict.candidate_values.map((candidate, index) => <option key={`${candidate.value}-${index}`} value={candidate.value}>{candidate.value}</option>)}</select></label>
            <label className="field">Alasan keputusan<input disabled={activeContext.state !== "draft"} value={conflictReasons[conflict.field_path] ?? ""} onChange={(event) => setConflictReasons({ ...conflictReasons, [conflict.field_path]: event.target.value })} maxLength={500} placeholder="Contoh: disesuaikan dengan dokumen KK terbaru" /></label>
            {activeContext.state === "draft" && <button className="button button-quiet" type="button" disabled={busy} onClick={() => void resolveConflict(conflict)}>Simpan keputusan</button>}
          </article>)}</section>}
          <section className="subsection" aria-labelledby="source-title"><h3 id="source-title">Sumber dan status ekstraksi</h3>
            {activeContext.instruction_sources.length === 0 ? <p className="muted-copy">Draft ini belum memiliki berkas sumber.</p> : <ul className="compact-list">{activeContext.instruction_sources.map((source) => <li key={`${source.file_id}-${source.source_role}`}><span><strong>{source.source_role.toUpperCase()}</strong> · {source.original_name}<small>SHA-256 {source.sha256}</small></span><a href={source.download_path}>Unduh</a></li>)}</ul>}
            {activeContext.parse_artifacts.length > 0 && <ul className="compact-list">{activeContext.parse_artifacts.map((artifact) => <li key={artifact.id}><span>Status parser: <strong>{artifact.status}</strong>{artifact.limitations.map((item) => <small key={item.code}>{item.message}</small>)}</span></li>)}</ul>}
          </section>
          {activeContext.evidence.length > 0 && <section className="subsection" aria-labelledby="evidence-title"><h3 id="evidence-title">Bukti dan anchor</h3><ul className="evidence-list">{activeContext.evidence.map((item) => <li key={item.id}><strong>{item.file_name} · {item.type}</strong><pre>{JSON.stringify({ anchor: item.anchor, content: item.content }, null, 2)}</pre></li>)}</ul></section>}
          {activeContext.state === "draft" && <button className="button button-primary approval-button" type="button" disabled={busy} onClick={() => void approveContext()}>Sahkan versi ini</button>}
          {activeContext.content_hash && <p className="hash-note">Hash versi disahkan: <code>{activeContext.content_hash}</code></p>}
        </>}
      </section>
      {error && <p className="form-error workspace-message" role="alert">{error}</p>}
      {message && <p className="notice notice-success workspace-message" role="status">{message}</p>}
    </div>
  );
}

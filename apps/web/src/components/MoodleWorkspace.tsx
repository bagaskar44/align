"use client";

import { useMemo, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import type { ApiMethod, ApiRequestPath } from "@/lib/api-contract.generated";

type Assignment = { id: string; title: string; active_version_id?: string | null; version_id?: string; version_state?: string | null; mode?: "individual" | "group" };
type Platform = { id: string; issuer: string; moodle_origin: string };
type LocalGroup = { id: string; name: string; revision: number; members: Array<{ user_id: string; display_name: string }> };
type MappingGroup = { id: string; moodle_group_id: number; local_group_id: string; active: boolean; revision: number };
type Mapping = {
  id: string;
  platform_id: string;
  lti_context_id: string;
  lti_resource_link_id: string;
  moodle_course_id: number;
  moodle_assignment_id: number;
  local_course_id: string;
  assignment_version_id: string;
  active: boolean;
  revision: number;
  groups: MappingGroup[];
};
type Candidate = {
  attempt_id: string;
  attempt_number: number;
  moodle_user_id: number | null;
  moodle_group_id: number | null;
  submitted_at: string | null;
  files: Array<{ name: string; size_bytes: number; mime: string | null }>;
  blocked_reason: string | null;
  owner?: { user_id: string; display_name: string };
  group?: { moodle_group_id: number; local_group_id: string; local_group_name: string; members: Array<{ user_id: string; display_name: string }> };
};
type ImportReceipt = {
  id: string;
  status: string;
  attempt_id: string;
  submission_id: string | null;
  submission_version_id: string | null;
  job_id: string | null;
  mapping_revision: number;
  submitted_at: string | null;
  imported_at: string | null;
  detail: string | null;
};
type ImportList = { candidate_status: string; candidates: Candidate[]; items: ImportReceipt[] };
type ApiError = { error?: { message?: string; field_errors?: Array<{ field: string }> } };

async function apiRequest<T>(path: ApiRequestPath, method: ApiMethod, csrfToken?: string, body?: unknown, idempotent = false): Promise<T> {
  const headers = new Headers();
  if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (idempotent) headers.set("Idempotency-Key", crypto.randomUUID());
  const response = await fetch(`/api/v1${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({})) as ApiError & T;
  if (!response.ok) {
    throw new Error(payload.error?.message ?? `Permintaan Moodle gagal (${response.status}).`);
  }
  return payload;
}

export function MoodleWorkspace({
  courseId,
  csrfToken,
  assignments,
  initialPlatforms,
  initialMappings,
}: {
  courseId: string;
  csrfToken: string;
  assignments: Assignment[];
  initialPlatforms: Platform[];
  initialMappings: Mapping[];
}) {
  const router = useRouter();
  const platforms = initialPlatforms;
  const [mappings, setMappings] = useState(initialMappings);
  const [imports, setImports] = useState<ImportList | null>(null);
  const [selectedMapping, setSelectedMapping] = useState("");
  const [selectedAttempt, setSelectedAttempt] = useState("");
  const [groupOptions, setGroupOptions] = useState<Record<string, LocalGroup[]>>({});
  const [platformId, setPlatformId] = useState(initialPlatforms[0]?.id ?? "");
  const [assignmentVersionId, setAssignmentVersionId] = useState("");
  const [contextId, setContextId] = useState("");
  const [resourceLinkId, setResourceLinkId] = useState("");
  const [moodleCourseId, setMoodleCourseId] = useState("");
  const [moodleAssignmentId, setMoodleAssignmentId] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const selected = imports?.candidates.find((item) => item.attempt_id === selectedAttempt) ?? null;
  const assignmentByVersion = useMemo(
    () => new Map(assignments.flatMap((item) => {
      const versionId = item.active_version_id ?? item.version_id;
      return versionId ? [[versionId, item] as const] : [];
    })),
    [assignments],
  );

  async function refreshImports(mappingId: string) {
    setSelectedMapping(mappingId);
    setSelectedAttempt("");
    setConfirmed(false);
    setError("");
    try {
      setImports(await apiRequest<ImportList>(`/integrations/moodle/imports?mapping_id=${encodeURIComponent(mappingId)}`, "GET"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Kandidat Moodle belum dapat dimuat.");
    }
  }

  async function loadGroups(mapping: Mapping) {
    const assignment = assignmentByVersion.get(mapping.assignment_version_id);
    if (!assignment || groupOptions[assignment.id]) return;
    try {
      const result = await apiRequest<{ items: LocalGroup[] }>(`/assignments/${assignment.id}/groups`, "GET");
      setGroupOptions((current) => ({ ...current, [assignment.id]: result.items }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Kelompok ALIGN belum dapat dimuat.");
    }
  }

  async function createMapping(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await apiRequest<Mapping>("/integrations/moodle/mappings", "POST", csrfToken, {
        platform_id: platformId,
        lti_context_id: contextId.trim(),
        lti_resource_link_id: resourceLinkId.trim(),
        moodle_course_id: Number(moodleCourseId),
        moodle_assignment_id: Number(moodleAssignmentId),
        local_course_id: courseId,
        assignment_version_id: assignmentVersionId,
      }, true);
      setMappings((current) => [...current, result]);
      setMessage("Mapping Moodle tersimpan.");
      setContextId("");
      setResourceLinkId("");
      setMoodleCourseId("");
      setMoodleAssignmentId("");
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Mapping belum dapat disimpan.");
    } finally {
      setBusy(false);
    }
  }

  async function addGroupMapping(event: FormEvent<HTMLFormElement>, mapping: Mapping) {
    event.preventDefault();
    const assignment = assignmentByVersion.get(mapping.assignment_version_id);
    if (!assignment) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await apiRequest<MappingGroup>(`/integrations/moodle/mappings/${mapping.id}/groups`, "POST", csrfToken, {
        moodle_group_id: Number(form.get("moodle_group_id")),
        local_group_id: String(form.get("local_group_id")),
      });
      setMappings((current) => current.map((item) => item.id === mapping.id ? { ...item, groups: [...item.groups, result] } : item));
      setMessage("Mapping kelompok tersimpan.");
      formElement.reset();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Mapping kelompok belum dapat disimpan.");
    } finally {
      setBusy(false);
    }
  }

  async function disableMapping(mapping: Mapping) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const updated = await apiRequest<Mapping>(`/integrations/moodle/mappings/${mapping.id}`, "PATCH", csrfToken, {
        expected_revision: mapping.revision,
        active: false,
      });
      setMappings((current) => current.map((item) => item.id === mapping.id ? updated : item));
      if (selectedMapping === mapping.id) setImports(null);
      setMessage("Mapping dinonaktifkan. Sesi terkait tidak lagi dapat mengakses ruang ini.");
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Mapping belum dapat dinonaktifkan.");
    } finally {
      setBusy(false);
    }
  }

  async function importSelected() {
    if (!selected || !selectedMapping || !confirmed) return;
    const mapping = mappings.find((item) => item.id === selectedMapping);
    if (!mapping) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const body = {
        mapping_id: mapping.id,
        expected_mapping_revision: mapping.revision,
        attempt_id: selected.attempt_id,
        confirmed_owner_user_id: selected.owner?.user_id ?? null,
        confirmed_roster_user_ids: selected.group?.members.map((member) => member.user_id) ?? [],
      };
      const receipt = await apiRequest<ImportReceipt>("/integrations/moodle/imports", "POST", csrfToken, body, true);
      setMessage(`Impor diterima. Versi sumber ${receipt.submission_version_id ?? ""} sedang diproses melalui parser ALIGN.`);
      await refreshImports(mapping.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Impor belum dapat dilakukan.");
    } finally {
      setBusy(false);
      setConfirmed(false);
    }
  }

  return (
    <section className="moodle-workspace" aria-labelledby="moodle-heading">
      <header className="workspace-section-heading">
        <div><h2 id="moodle-heading">Moodle</h2><p>Hubungkan tugas yang telah diterbitkan, tinjau attempt final, lalu impor salinan read-only ke ALIGN.</p></div>
      </header>
      {error ? <p className="workspace-error" role="alert">{error}</p> : null}
      {message ? <p className="workspace-success" role="status">{message}</p> : null}

      <form className="moodle-form" onSubmit={createMapping}>
        <h3>Tambah mapping tugas</h3>
        {platforms.length === 0 ? <p className="muted-copy">Belum ada platform Moodle yang diizinkan untuk mata kuliah ini. Operator perlu mengaktifkan pasangan platform dan mata kuliah terlebih dahulu.</p> : null}
        <label>Platform
          <select required value={platformId} onChange={(event) => setPlatformId(event.target.value)} disabled={!platforms.length}>
            {platforms.map((platform) => <option key={platform.id} value={platform.id}>{platform.moodle_origin}</option>)}
          </select>
        </label>
        <label>Tugas ALIGN
          <select required value={assignmentVersionId} onChange={(event) => setAssignmentVersionId(event.target.value)}>
            <option value="">Pilih tugas terbit</option>
            {assignments.filter((item) => (item.active_version_id ?? item.version_id) && item.version_state !== "draft").map((item) => {
              const versionId = item.active_version_id ?? item.version_id;
              return versionId ? <option key={versionId} value={versionId}>{item.title}</option> : null;
            })}
          </select>
        </label>
        <div className="moodle-form-grid">
          <label>ID konteks LTI<input required maxLength={500} value={contextId} onChange={(event) => setContextId(event.target.value)} /></label>
          <label>ID resource link LTI<input required maxLength={500} value={resourceLinkId} onChange={(event) => setResourceLinkId(event.target.value)} /></label>
          <label>ID course Moodle<input required min={1} type="number" value={moodleCourseId} onChange={(event) => setMoodleCourseId(event.target.value)} /></label>
          <label>ID assignment Moodle<input required min={1} type="number" value={moodleAssignmentId} onChange={(event) => setMoodleAssignmentId(event.target.value)} /></label>
        </div>
        <button className="button button-primary" type="submit" disabled={busy || !platformId || !assignmentVersionId}>Simpan mapping</button>
      </form>

      <div className="moodle-mapping-list">
        <h3>Mapping terdaftar</h3>
        {mappings.length === 0 ? <p className="muted-copy">Belum ada mapping tugas untuk mata kuliah ini.</p> : null}
        {mappings.map((mapping) => {
          const assignment = assignmentByVersion.get(mapping.assignment_version_id);
          return (
            <article className="moodle-mapping-card" key={mapping.id}>
              <div className="moodle-mapping-title"><div><h4>{assignment?.title ?? "Tugas terbit"}</h4><p>{platforms.find((item) => item.id === mapping.platform_id)?.moodle_origin} · course {mapping.moodle_course_id} · assignment {mapping.moodle_assignment_id}</p></div><span className={`status-pill ${mapping.active ? "status-ready" : "status-muted"}`}>{mapping.active ? "Aktif" : "Nonaktif"}</span></div>
              <p className="moodle-mapping-ids">Konteks LTI: {mapping.lti_context_id} · Resource: {mapping.lti_resource_link_id}</p>
              {mapping.active ? <div className="moodle-mapping-actions">
                <button className="button button-secondary" type="button" onClick={() => { void refreshImports(mapping.id); void loadGroups(mapping); }} disabled={busy}>Lihat attempt final</button>
                <button className="button button-quiet" type="button" onClick={() => void disableMapping(mapping)} disabled={busy}>Nonaktifkan mapping</button>
              </div> : null}
              {mapping.active && assignment?.mode === "group" ? <form className="moodle-group-form" onSubmit={(event) => void addGroupMapping(event, mapping)}>
                <h5>Mapping kelompok</h5>
                <label>ID kelompok Moodle<input required name="moodle_group_id" min={1} type="number" /></label>
                <label>Kelompok ALIGN<select required name="local_group_id" defaultValue="" onFocus={() => void loadGroups(mapping)}>
                  <option value="">Pilih kelompok</option>
                  {(groupOptions[assignment.id] ?? []).map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
                </select></label>
                <button className="button button-secondary" type="submit" disabled={busy}>Simpan kelompok</button>
                {mapping.groups.length ? <ul>{mapping.groups.map((group) => <li key={group.id}>Moodle {group.moodle_group_id} → ALIGN {groupOptions[assignment.id]?.find((item) => item.id === group.local_group_id)?.name ?? group.local_group_id}{group.active ? " · aktif" : " · nonaktif"}</li>)}</ul> : null}
              </form> : null}
            </article>
          );
        })}
      </div>

      {selectedMapping && imports ? <section className="moodle-import-panel" aria-labelledby="moodle-import-heading">
        <h3 id="moodle-import-heading">Attempt final</h3>
        <p className="muted-copy">Status sumber: {imports.candidate_status}. Moodle hanya dibaca; impor membuat versi sumber baru di ALIGN.</p>
        {imports.candidates.length === 0 ? <p className="muted-copy">Tidak ada attempt final yang dapat diimpor.</p> : null}
        <div className="moodle-candidate-list">
          {imports.candidates.map((candidate) => <button
            className={`moodle-candidate ${selectedAttempt === candidate.attempt_id ? "moodle-candidate-selected" : ""}`}
            key={candidate.attempt_id}
            type="button"
            onClick={() => { setSelectedAttempt(candidate.attempt_id); setConfirmed(false); }}
          >
            <strong>{candidate.owner?.display_name ?? candidate.group?.local_group_name ?? (candidate.moodle_group_id ? `Kelompok Moodle ${candidate.moodle_group_id}` : `Mahasiswa Moodle ${candidate.moodle_user_id ?? "?"}`)}</strong>
            <span>Attempt {candidate.attempt_number} · {candidate.submitted_at ? new Date(candidate.submitted_at).toLocaleString("id-ID") : "waktu tidak tersedia"}</span>
            <span>{candidate.files.length ? candidate.files.map((file) => `${file.name} (${Math.ceil(file.size_bytes / 1024)} KB)`).join(", ") : "File tidak didukung atau tidak tersedia"}</span>
            {candidate.blocked_reason ? <span className="moodle-blocked">Diblokir: {candidate.blocked_reason}</span> : null}
          </button>)}
        </div>
        {selected && !selected.blocked_reason ? <div className="moodle-confirmation">
          {selected.group ? <>
            <p><strong>Kelompok ALIGN:</strong> {selected.group.local_group_name}. Cocokkan daftar ini dengan roster kelompok Moodle saat attempt dikirim:</p>
            <ul>{selected.group.members.map((member) => <li key={member.user_id}>{member.display_name}</li>)}</ul>
            <label className="checkbox-row"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />Saya mengonfirmasi seluruh roster tersebut cocok dengan roster attempt final Moodle.</label>
          </> : <label className="checkbox-row"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />Saya mengonfirmasi pemilik Moodle cocok dengan {selected.owner?.display_name ?? "binding identitas aktif"}.</label>}
          <button className="button button-primary" type="button" onClick={() => void importSelected()} disabled={!confirmed || busy}>Impor attempt final</button>
        </div> : null}
        <h4>Riwayat impor</h4>
        {imports.items.length ? <ul className="moodle-import-history">{imports.items.map((item) => <li key={item.id}><span>{item.status} · {item.attempt_id}</span><span>{item.submission_version_id ? `Versi ${item.submission_version_id}` : item.detail}</span></li>)}</ul> : <p className="muted-copy">Belum ada riwayat impor.</p>}
      </section> : null}
    </section>
  );
}

"use client";

import { useEffect, useId, useMemo, useState } from "react";
import type { ApiMethod, ApiRequestPath } from "@/lib/api-contract.generated";
import { SubmissionFileViewer, type ManualAnchorSelection } from "./SubmissionViewer";

type FinalVersion = { id: string; version_no: number; manifest_hash: string; finalized_at: string };
type Submission = {
  id: string;
  assignment_id: string;
  owner: { individual_user_id: string | null; group_id: string | null; label?: string };
  roster?: string[];
  active_final_version: FinalVersion | null;
  files?: Array<{ id: string; name: string; mime: string; sha256?: string }>;
};
type ReviewElement = {
  id: string;
  check_element_id: string;
  code: string;
  criterion: { id: string; code: string; title: string } | null;
  requirements: Array<{ id: string; code: string; text: string; mandatory: boolean }>;
  decision_question: string;
  payload: Record<string, unknown>;
  decision: { review_status: "pending" | "reviewed"; resolution: string | null; reason?: string | null; evidence_ids?: string[] };
  analysis?: null | {
    applicability_status: string; source_coverage_status: string; evidence_relationship: string;
    check_status: string | null; observations: Array<Record<string, unknown>>;
    interpretation: string | null; reason_codes: string[];
    support_bindings: Array<{ relationship_code: string | null; role: string;
      observation_codes: string[]; evidence_item_id: string; quotes: Array<Record<string, unknown>> }>;
    grounding?: null | { mechanical_status: string; semantic_status: string;
      evaluation: Record<string, unknown>; limitations: string[] };
    coverage?: null | { retrieval_status: string; compatible_count: number; inspected_count: number;
      unresolved_count: number; sufficient_for_positive: boolean; exhaustive_for_negative: boolean;
      inventory: Record<string, unknown>; retrieval: Record<string, unknown>;
      limitations: Array<Record<string, unknown>>; inspections: Array<Record<string, unknown>> };
  };
};
type Relationship = { relationship_code: string; predicate: string };
type Finding = {
  id: string;
  candidate_id: string | null;
  origin: string;
  action: "pending" | "accept" | "edit" | "reject" | "defer";
  category: string;
  criterion_id: string;
  claim: string;
  targets: Array<{ kind: string; id: string }>;
  evidence_ids: string[];
  numeric_check: null | { comparability: string; result: string | null; difference: string | null;
    allowed_difference: string | null; inconsistent: boolean | null };
  technical_validation: string;
  semantic_support: string;
  reason?: string | null;
};
type SourceFile = { id: string; name: string; mime: string; sha256: string; preflight: Record<string, unknown> };
type AnnotationRevision = {
  id: string; revision_no: number; operation: "upsert" | "remove"; file_id: string;
  source_sha256: string; anchor: Record<string, unknown>; transcription: string | null;
  original_evidence_id: string | null; corrected_evidence_id: string | null;
  reason: string | null;
  targets: Array<{ check_element_id: string | null; review_finding_id: string | null; relationship_code: string | null; role: string | null }>;
};
type Annotation = { id: string; kind: "span_selection" | "transcription" | "evidence_binding"; current: AnnotationRevision | null; history: AnnotationRevision[] };
type NumericPolicy = { id: string; policy_key: string; version_no: number; state: string; payload: Record<string, unknown> };
type Review = {
  id: string;
  submission_version_id: string;
  submission_version: number;
  state: "draft" | "complete";
  revision: number;
  content_hash: string;
  lecturer_notes: string;
  limitations: string[];
  analysis_run: null | { id: string; status: string; completion_kind: string | null; label: string;
    provider_mode: string; data_origin: string; limitations: unknown[]; usage?: Record<string, unknown>;
    routing_decisions: Array<{ check_element_id: string | null; task: string; action: string;
      reason_code: string; outcome: string }> };
  source_files: SourceFile[];
  elements: ReviewElement[];
  findings: Finding[];
  annotations: Annotation[];
};
type Evidence = {
  id: string;
  type: string;
  source_unit_id: string;
  file_id: string;
  file_name: string;
  source_sha256: string;
  anchor: Record<string, unknown>;
  content: Record<string, unknown>;
  parse_status: string;
  limitations: Array<Record<string, unknown>>;
  linked_finding_ids: string[];
};
type Feedback = {
  id: string;
  generation_mode: "model" | "template" | "manual";
  generation_status: "not_requested" | "queued" | "running" | "completed" | "fallback";
  generation_failure_code: string | null;
  generation_job_id: string | null;
  source_review_hash: string;
  body_markdown: string;
  blocks: Array<Record<string, unknown>>;
  included_review_finding_ids: string[];
  exclusion_reasons: Record<string, string>;
  state: "draft" | "approved";
  revision: number;
  stale: boolean;
};
type ResponseError = { detail?: string | { message?: string; pending?: string[] }; error?: { message?: string } };
type RuntimeRelease = { id: string; version_label: string; state: string; provider_mode: string; data_mode: string; paid_calls: string };
type AnalysisRunStatus = { id: string; status: string; completion_kind: string | null; limitations: unknown[] };

async function request<T>(path: ApiRequestPath, csrfToken: string, method: ApiMethod = "GET", body?: unknown): Promise<T> {
  const headers = new Headers({ "X-CSRF-Token": csrfToken });
  if (body !== undefined) headers.set("Content-Type", "application/json");
  if (method !== "GET") headers.set("Idempotency-Key", crypto.randomUUID());
  const response = await fetch(`/api/v1${path}`, {
    method,
    headers,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    cache: "no-store",
    credentials: "same-origin",
  });
  const payload = (await response.json().catch(() => ({}))) as T & ResponseError;
  if (!response.ok) {
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail : detail?.message ?? payload.error?.message;
    throw new Error(message ?? `Permintaan gagal (${response.status}).`);
  }
  return payload;
}

export function ReviewWorkspace({ submission, csrfToken, manualAnchorSelection, onManualAnchorSelection }: {
  submission: Submission; csrfToken: string; manualAnchorSelection: ManualAnchorSelection | null;
  onManualAnchorSelection: (selection: ManualAnchorSelection | null) => void;
}) {
  const reviewTabsId = useId();
  const [review, setReview] = useState<Review | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [numericPolicies, setNumericPolicies] = useState<NumericPolicy[]>([]);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [publicationId, setPublicationId] = useState("");
  const [runtimeReleases, setRuntimeReleases] = useState<RuntimeRelease[]>([]);
  const [releaseManifestId, setReleaseManifestId] = useState("");
  const [analysisStatus, setAnalysisStatus] = useState("");
  const [releaseError, setReleaseError] = useState("");
  const [selectedElementId, setSelectedElementId] = useState("");
  const [selectedEvidenceId, setSelectedEvidenceId] = useState("");
  const [annotationSource, setAnnotationSource] = useState<{
    fileId: string; sourceSha256: string; sourceUnitId?: string; anchor: Record<string, unknown>;
    revisionNo: number; operation: "upsert" | "remove";
  } | null>(null);
  const [selectedFindingId, setSelectedFindingId] = useState("");
  const [selectedRelationshipCode, setSelectedRelationshipCode] = useState("");
  const [selectedRole, setSelectedRole] = useState("");
  const [annotationReason, setAnnotationReason] = useState("");
  const [annotationTranscription, setAnnotationTranscription] = useState("");
  const [resolution, setResolution] = useState("observed_no_issue");
  const [decisionReason, setDecisionReason] = useState("");
  const [decisionEvidenceIds, setDecisionEvidenceIds] = useState<string[]>([]);
  const [findingClaim, setFindingClaim] = useState("");
  const [findingCategory, setFindingCategory] = useState("missing_requirement");
  const [candidateEditId, setCandidateEditId] = useState("");
  const [findingEvidenceIds, setFindingEvidenceIds] = useState<string[]>([]);
  const [coverageFileIds, setCoverageFileIds] = useState<string[]>([]);
  const [coverageNote, setCoverageNote] = useState("");
  const [numericPolicyId, setNumericPolicyId] = useState("");
  const [numericPayloadText, setNumericPayloadText] = useState("");
  const [candidateReason, setCandidateReason] = useState("");
  const [notes, setNotes] = useState("");
  const [limitationsText, setLimitationsText] = useState("");
  const [manualFeedback, setManualFeedback] = useState("");
  const [includedFindingIds, setIncludedFindingIds] = useState<string[]>([]);
  const [exclusionReasons, setExclusionReasons] = useState<Record<string, string>>({});
  const [mode, setMode] = useState<"inspect" | "evidence" | "decision">("inspect");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const activeVersion = submission.active_final_version;
  const activeVersionId = activeVersion?.id;
  const selectedElement = review?.elements.find((item) => item.id === selectedElementId) ?? review?.elements[0] ?? null;
  const selectedEvidence = evidence.find((item) => item.id === selectedEvidenceId) ?? null;
  const relationships = useMemo(() => relationshipsForElement(selectedElement), [selectedElement]);
  const activeRelationshipCode = relationships.some((item) => item.relationship_code === selectedRelationshipCode)
    ? selectedRelationshipCode : relationships[0]?.relationship_code ?? "";
  const activeRelationship = relationships.find((item) => item.relationship_code === activeRelationshipCode) ?? null;
  const relationshipRoles = activeRelationship ? rolesForPredicate(activeRelationship.predicate) : [];
  const activeRole = relationshipRoles.includes(selectedRole) ? selectedRole : "";
  const confirmedFindings = useMemo(() => review?.findings.filter((item) => ["accept", "edit"].includes(item.action)) ?? [], [review]);
  const feedbackStale = Boolean(feedback && review && (feedback.stale || feedback.source_review_hash !== review.content_hash || review.state !== "complete"));
  const selectedFeedbackFindingIds = includedFindingIds;

  useEffect(() => {
    if (!activeVersionId) return;
    let active = true;
    void request<{ releases: RuntimeRelease[] }>("/runtime/releases", csrfToken)
      .then((result) => {
        if (!active) return;
        setRuntimeReleases(result.releases);
        setReleaseManifestId((current) => result.releases.some((item) => item.id === current)
          ? current : result.releases[0]?.id ?? "");
        setReleaseError(result.releases.length ? "" : "Belum ada release terverifikasi untuk analysis.");
      })
      .catch((cause) => {
        if (active) setReleaseError(cause instanceof Error ? cause.message : "Release runtime tidak dapat dimuat.");
      });
    return () => { active = false; };
  }, [activeVersionId, csrfToken]);

  useEffect(() => {
    if (!activeVersionId) return;
    let active = true;
    async function load() {
      setError("");
      try {
        const listed = await request<{ items: Array<{ id: string }> }>(
          `/submission-versions/${activeVersionId}/reviews`, csrfToken);
        let current: Review;
        if (listed.items.length) {
          current = await request<Review>(`/reviews/${listed.items[0].id}`, csrfToken);
        } else {
          current = await request<Review>(`/submission-versions/${activeVersionId}/reviews`, csrfToken, "POST", { analysis_run_id: null });
        }
        const page = await request<{ items: Evidence[] }>(`/reviews/${current.id}/evidence?limit=100`, csrfToken);
        const assignment = await request<{ version?: { numeric_policies?: NumericPolicy[] } }>(
          `/assignments/${submission.assignment_id}`, csrfToken);
        if (!active) return;
        setReview(current);
        setEvidence(page.items);
        setNumericPolicies(assignment.version?.numeric_policies ?? []);
        setIncludedFindingIds(current.findings.filter((item) => ["accept", "edit"].includes(item.action)).map((item) => item.id));
        setNotes(current.lecturer_notes);
        setLimitationsText(current.limitations.join("\n"));
        setSelectedElementId((value) => current.elements.some((item) => item.id === value) ? value : current.elements[0]?.id ?? "");
        setSelectedEvidenceId((value) => page.items.some((item) => item.id === value) ? value : page.items[0]?.id ?? "");
        setSelectedFindingId((value) => current.findings.some((item) => item.id === value) ? value : current.findings[0]?.id ?? "");
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "Review tidak dapat dimuat.");
      }
    }
    void load();
    return () => { active = false; };
  }, [activeVersionId, csrfToken, refreshKey, submission.assignment_id]);

  async function mutate<T>(action: () => Promise<T>, success: string, reload = true) {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await action();
      if (review && typeof result === "object" && result !== null && "review_id" in result
        && result.review_id === review.id) {
        const mutation = result as { revision?: unknown; review_revision?: unknown; content_hash?: unknown; state?: unknown };
        const nextRevision = typeof mutation.review_revision === "number" ? mutation.review_revision : mutation.revision;
        if (typeof nextRevision === "number" && Number.isInteger(nextRevision)) {
          setReview((current) => current?.id === review.id ? {
            ...current,
            revision: nextRevision,
            content_hash: typeof mutation.content_hash === "string" ? mutation.content_hash : current.content_hash,
            state: mutation.state === "complete" ? "complete" : "draft",
          } : current);
        }
      }
      setNotice(success);
      if (reload) setRefreshKey((value) => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Perubahan review gagal disimpan.");
    } finally { setBusy(false); }
  }

  async function runAnalysis() {
    if (!activeVersionId || !releaseManifestId) return;
    setBusy(true); setError(""); setNotice(""); setAnalysisStatus("Mengantrekan analisis versi sumber ini…");
    try {
      const created = await request<{ analysis_run_id: string; status: string; label: string }>(
        `/submission-versions/${activeVersionId}/analysis-runs`, csrfToken, "POST",
        { release_manifest_id: releaseManifestId },
      );
      setAnalysisStatus(`${created.label} · status ${created.status}`);
      let terminal: AnalysisRunStatus | null = null;
      for (let attempt = 0; attempt < 180; attempt += 1) {
        const run = await request<AnalysisRunStatus>(`/analysis-runs/${created.analysis_run_id}`, csrfToken);
        if (!["queued", "running"].includes(run.status)) { terminal = run; break; }
        setAnalysisStatus(`Analisis berjalan · ${run.status}`);
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
      }
      if (!terminal) {
        setAnalysisStatus(`Run ${created.analysis_run_id} masih berjalan. Buka ulang setelah worker selesai.`);
        return;
      }
      const newReview = await request<Review>(`/submission-versions/${activeVersionId}/reviews`, csrfToken, "POST",
        { analysis_run_id: created.analysis_run_id });
      setAnalysisStatus(`Run ${terminal.status} · ${terminal.completion_kind ?? "manual review required"}. Workspace review baru dibuat; review lama tetap tersimpan.`);
      setReview(newReview);
      setRefreshKey((value) => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Analysis run gagal dibuat.");
      setAnalysisStatus("");
    } finally { setBusy(false); }
  }

  function updateChecklist(setter: (ids: string[]) => void, current: string[], id: string) {
    setter(current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  function selectElement(id: string) {
    const item = review?.elements.find((candidate) => candidate.id === id);
    if (!item) return;
    setSelectedElementId(id); setResolution(item.decision.resolution ?? "observed_no_issue");
    setDecisionReason(item.decision.reason ?? ""); setDecisionEvidenceIds(item.decision.evidence_ids ?? []);
    setSelectedRelationshipCode(relationshipsForElement(item)[0]?.relationship_code ?? ""); setSelectedRole("");
  }

  async function saveElementDecision() {
    if (!review || !selectedElement) return;
    await mutate(() => request(`/reviews/${review.id}/element-decisions/${selectedElement.id}`, csrfToken, "PUT", {
      review_status: "reviewed", resolution, applicability_override: null,
      reason: decisionReason || null, evidence_ids: decisionEvidenceIds, expected_revision: review.revision,
    }), "Keputusan elemen disimpan dengan revisi terbaru.");
  }

  async function saveNotes() {
    if (!review) return;
    await mutate(() => request(`/reviews/${review.id}/notes`, csrfToken, "PUT", {
      lecturer_notes: notes, limitations: limitationsText.split("\n").map((item) => item.trim()).filter(Boolean),
      expected_revision: review.revision,
    }), "Catatan dan batas pemeriksaan tersimpan.");
  }

  async function createManualFinding() {
    if (!review || !selectedElement?.criterion || !findingClaim.trim()) return;
    const criterionId = selectedElement.criterion.id;
    let numeric: Record<string, unknown> | null = null;
    if (findingCategory === "numerical_inconsistency") {
      try { numeric = JSON.parse(numericPayloadText) as Record<string, unknown>; }
      catch { setError("Numeric comparison harus berupa JSON valid sesuai NumericPolicy approved."); return; }
    }
    const coverageAttestation = coverageNote.trim() && coverageFileIds.length
      ? { source_file_ids: coverageFileIds, inspection_note: coverageNote.trim() } : null;
    await mutate(() => request(`/reviews/${review.id}/manual-findings`, csrfToken, "POST", {
      action: "accept", primary_category: findingCategory, primary_criterion_id: criterionId,
      claim: findingClaim.trim(), element_ids: [selectedElement.id], requirement_ids: [], criterion_ids: [],
      evidence_ids: findingEvidenceIds, coverage_attestation: coverageAttestation, numeric, reason: null,
      expected_revision: review.revision,
    }), "Temuan manual dosen ditambahkan.");
  }

  async function candidateAction(finding: Finding, action: Finding["action"]) {
    if (!review || !finding.candidate_id) return;
    const payload: Record<string, unknown> = { action, expected_revision: review.revision };
    if (action === "defer" || action === "reject") payload.reason = candidateReason.trim();
    if (action === "edit") {
      payload.primary_category = findingCategory;
      payload.primary_criterion_id = selectedElement?.criterion?.id ?? finding.criterion_id;
      payload.claim = findingClaim.trim() || finding.claim;
      payload.element_ids = selectedElement ? [selectedElement.id] : [];
      payload.evidence_ids = findingEvidenceIds.length ? findingEvidenceIds : finding.evidence_ids;
      if (findingCategory === "numerical_inconsistency") {
        try { payload.numeric = JSON.parse(numericPayloadText) as Record<string, unknown>; }
        catch { setError("Numeric comparison harus berupa JSON valid sesuai NumericPolicy approved."); return; }
      }
    }
    await mutate(() => request(`/reviews/${review.id}/candidate-decisions/${finding.candidate_id}`, csrfToken, "PUT", payload),
      `Kandidat ditandai ${action}.`);
  }

  async function createEvidenceBinding() {
    if (!review || !selectedEvidence || !selectedElement) return;
    await mutate(() => request(`/reviews/${review.id}/annotations`, csrfToken, "POST", {
      kind: "evidence_binding", evidence_item_id: selectedEvidence.id,
      element_ids: [selectedElement.id], relationship_code: activeRole ? activeRelationshipCode || null : null,
      role: activeRole || null, reason: annotationReason.trim() || null,
      expected_revision: review.revision,
    }), "Binding sumber ke elemen dan role tercatat sebagai revisi anotasi.");
  }

  async function reviseAnnotation(annotation: Annotation, operation: "upsert" | "remove") {
    if (!review || !annotation.current) return;
    if (operation === "remove" && !annotationReason.trim()) {
      setError("Alasan penghapusan anotasi wajib diisi.");
      return;
    }
    if (operation === "upsert" && annotation.kind === "evidence_binding" && !selectedEvidence) {
      setError("Pilih evidence terlebih dahulu untuk merevisi binding.");
      return;
    }
    if (operation === "upsert" && annotation.kind !== "evidence_binding" && !manualAnchorSelection) {
      setError("Pilih lokasi dari viewer original untuk merevisi anotasi.");
      return;
    }
    const priorTargets = annotation.current.targets;
    const elementTarget = selectedElement?.id ?? priorTargets.find((item) => item.check_element_id)?.check_element_id ?? null;
    const findingTarget = selectedFindingId || priorTargets.find((item) => item.review_finding_id)?.review_finding_id || null;
    let body: Record<string, unknown> = {
      kind: annotation.kind, operation, expected_revision: review.revision,
      reason: annotationReason.trim() || null,
      element_ids: elementTarget ? [elementTarget] : [], review_finding_id: findingTarget,
      relationship_code: activeRelationshipCode || priorTargets[0]?.relationship_code || null,
      role: activeRole || priorTargets[0]?.role || null,
    };
    if (operation === "upsert" && annotation.kind === "evidence_binding") {
      body.evidence_item_id = selectedEvidence?.id;
    } else if (operation === "upsert" && manualAnchorSelection) {
      body = { ...body, file_id: manualAnchorSelection.file_id,
        source_sha256: manualAnchorSelection.source_sha256, anchor: manualAnchorSelection.anchor,
        original_evidence_id: manualAnchorSelection.original_evidence_id ?? null,
        transcription: annotation.kind === "transcription" ? annotationTranscription.trim() : null };
      if (annotation.kind === "transcription" && !annotationTranscription.trim()) {
        setError("Teks transkripsi koreksi wajib diisi.");
        return;
      }
    }
    await mutate(() => request(`/reviews/${review.id}/annotations/${annotation.id}/revisions`, csrfToken, "POST", body),
      operation === "remove" ? "Anotasi dinonaktifkan; histori dan sumber tetap tersimpan." : "Revisi anotasi tersimpan sebagai evidence baru.");
    setAnnotationReason("");
    setAnnotationTranscription("");
  }

  async function createFeedback(mode: "model" | "template" | "manual") {
    if (!review) return;
    const blocks = mode === "manual" ? confirmedFindings.filter((finding) => includedFindingIds.includes(finding.id)).map((finding) => ({
      criterion_id: finding.criterion_id,
      review_finding_ids: [finding.id],
      evidence_ids: finding.evidence_ids,
      message: finding.claim,
      next_action: "Tinjau kembali bukti yang ditautkan terhadap tuntutan tugas.",
      limitation: null,
    })) : [];
    const included = includedFindingIds;
    await mutate(async () => {
      const created = await request<Feedback>(`/reviews/${review.id}/feedback-drafts`, csrfToken, "POST", {
        mode,
        body_markdown: mode === "manual" ? manualFeedback : null,
        blocks: mode === "manual" ? blocks : [],
        included_review_finding_ids: included,
        exclusion_reasons: exclusionReasons,
        expected_revision: review.revision,
      });
      setFeedback(created);
      if (mode === "model" && created.generation_status === "queued") {
        let latest = created;
        for (let attempt = 0; attempt < 90 && ["queued", "running"].includes(latest.generation_status); attempt += 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
          latest = await request<Feedback>(`/feedback-drafts/${created.id}`, csrfToken);
          setFeedback(latest);
        }
      }
    }, mode === "model" ? "Draft feedback diproses dengan batas B5." : mode === "template" ? "Draft feedback template dibuat." : "Draft feedback manual dibuat.", false);
  }

  async function approveFeedback() {
    if (!review || !feedback || feedbackStale) return;
    await mutate(async () => {
      const result = await request<Feedback>(`/feedback-drafts/${feedback.id}/approve`, csrfToken, "POST", {
        expected_revision: feedback.revision, expected_review_revision: review.revision,
      });
      setFeedback(result);
    }, "Feedback disetujui. Publikasi masih memerlukan aksi terpisah.", false);
  }

  async function updateFeedbackDraft() {
    if (!review || !feedback || feedback.state !== "draft" || feedbackStale) return;
    const blocks = confirmedFindings.filter((finding) => includedFindingIds.includes(finding.id)).map((finding) => ({
      criterion_id: finding.criterion_id, review_finding_ids: [finding.id], evidence_ids: finding.evidence_ids,
      message: finding.claim, next_action: "Tinjau kembali bukti yang ditautkan terhadap tuntutan tugas.", limitation: null,
    }));
    await mutate(async () => {
      const updated = await request<Feedback>(`/feedback-drafts/${feedback.id}`, csrfToken, "PUT", {
        body_markdown: manualFeedback, blocks, included_review_finding_ids: includedFindingIds,
        exclusion_reasons: exclusionReasons, expected_revision: feedback.revision,
      });
      setFeedback(updated);
    }, "Revisi draft manual disimpan.", false);
  }

  async function publish() {
    if (!review || !feedback || !activeVersion || feedbackStale || publicationId) return;
    const included = selectedFeedbackFindingIds.map((id) => review.findings.find((item) => item.id === id)?.claim).filter(Boolean);
    const message = `Terbitkan hasil untuk ${submission.owner.label ?? "submission"}, versi sumber ${activeVersion.version_no}?\n\nTemuan: ${included.length ? included.join("; ") : "tidak ada"}\nBatas: ${review.limitations.length ? review.limitations.join("; ") : "tidak ada"}\n\nPublikasi menjadi hasil resmi mahasiswa.`;
    if (!window.confirm(message)) return;
    await mutate(async () => {
      const result = await request<{ id: string }>(`/reviews/${review.id}/publish`, csrfToken, "POST", {
        feedback_draft_id: feedback.id,
        expected_revision: review.revision,
        expected_feedback_revision: feedback.revision,
      });
      setPublicationId(result.id);
    }, "Snapshot publikasi immutable berhasil dibuat.", false);
  }

  async function withdraw() {
    if (!publicationId || !window.confirm("Tarik publikasi ini dari tampilan mahasiswa? Snapshot lama tetap tersimpan.")) return;
    const reason = window.prompt("Alasan penarikan (wajib):")?.trim();
    if (!reason) return;
    await mutate(() => request(`/publications/${publicationId}/withdraw`, csrfToken, "POST", { reason }),
      "Publikasi ditarik. Latest pointer tetap menunjuk snapshot withdrawn.", false);
  }

  function openAnnotationSource(revision: AnnotationRevision) {
    const linkedId = revision.corrected_evidence_id ?? revision.original_evidence_id;
    const linkedEvidence = linkedId ? evidence.find((item) => item.id === linkedId) : undefined;
    if (linkedId && (!linkedEvidence || linkedEvidence.file_id !== revision.file_id
        || linkedEvidence.source_sha256 !== revision.source_sha256)) {
      setError("Tautan anotasi tidak cocok dengan berkas dan hash sumber review ini.");
      return;
    }
    const matchingEvidence = linkedEvidence ?? evidence.find((item) => item.file_id === revision.file_id
      && item.source_sha256 === revision.source_sha256);
    setSelectedEvidenceId(linkedEvidence?.id ?? matchingEvidence?.id ?? "");
    setAnnotationSource({ fileId: revision.file_id, sourceSha256: revision.source_sha256,
      sourceUnitId: linkedEvidence?.source_unit_id ?? matchingEvidence?.source_unit_id,
      anchor: revision.anchor, revisionNo: revision.revision_no, operation: revision.operation });
    setMode("evidence");
    setNotice(`Sumber anotasi revisi #${revision.revision_no} dibuka dari berkas dan hash yang terikat pada review.`);
  }

  if (!activeVersion) return <p className="notice">Review tersedia setelah submission difinalisasi.</p>;
  return <section className="b4-review-workspace" aria-labelledby="b4-review-title">
    <header className="workspace-heading">
      <div><p className="eyebrow">REVIEW DOSEN · VERSI SUMBER {activeVersion.version_no}</p><h3 id="b4-review-title">Pemeriksaan dan publikasi</h3></div>
      {review && <span className={`state-pill${review.state === "complete" ? " state-approved" : ""}`}>{review.state === "complete" ? "LENGKAP" : "DRAF"} · revisi {review.revision}</span>}
    </header>
    <p className="help-text">Inventory final: {review?.source_files.map((item) => item.name).join(" · ") ?? "memuat…"}. Tanpa analysis run, semua elemen tetap harus ditelaah dosen.</p>
    {review?.analysis_run && <p className="notice notice-muted">{review.analysis_run.label} · provider {review.analysis_run.provider_mode} · data {review.analysis_run.data_origin}</p>}
    <div className="b4-action-card" aria-label="Analysis run B5">
      <h4>Bantuan analisis</h4>
      <p className="help-text">Run menggunakan versi submission final dan specification approved. Run baru membuat workspace review terpisah; histori review dosen tetap tersimpan.</p>
      <label className="field-label">Release runtime<select value={releaseManifestId} onChange={(event) => setReleaseManifestId(event.target.value)} disabled={busy || !runtimeReleases.length}>
        {runtimeReleases.map((item) => <option key={item.id} value={item.id}>{item.version_label} · {item.state} · {item.provider_mode}</option>)}
      </select></label>
      <button className="button button-primary" type="button" disabled={busy || !releaseManifestId} onClick={() => void runAnalysis()}>
        {busy ? "Analisis berjalan…" : "Jalankan analisis"}
      </button>
      <small>{releaseError || (releaseManifestId ? "Paid call tetap diblokir sebelum network sampai guard biaya lengkap." : "")}</small>
      {analysisStatus && <p className="notice notice-muted" role="status">{analysisStatus}</p>}
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}
    {notice && <p className="form-success" role="status">{notice}</p>}
    {!review && !error && <p className="help-text" role="status">Membuat workspace review manual…</p>}
    {review && <>
      <nav className="b4-mobile-tabs" role="tablist" aria-label="Panel review">
        {([["inspect", "Pemeriksaan"], ["evidence", "Bukti"], ["decision", "Keputusan"]] as const).map(([id, label], index) =>
          <button key={id} id={`${reviewTabsId}-tab-${id}`} type="button" role="tab" aria-selected={mode === id}
            aria-controls={`${reviewTabsId}-panel-${id}`} tabIndex={mode === id ? 0 : -1}
            className={`button ${mode === id ? "button-primary" : "button-quiet"}`}
            onKeyDown={(event) => {
              const modes = ["inspect", "evidence", "decision"] as const;
              let next = index;
              if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % modes.length;
              else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + modes.length) % modes.length;
              else if (event.key === "Home") next = 0;
              else if (event.key === "End") next = modes.length - 1;
              else return;
              event.preventDefault();
              const nextMode = modes[next];
              if (nextMode) {
                setMode(nextMode);
                document.getElementById(`${reviewTabsId}-tab-${nextMode}`)?.focus();
              }
            }} onClick={() => setMode(id)}>{label}</button>)}
      </nav>
      <div className="b4-review-columns">
        <section id={`${reviewTabsId}-panel-inspect`} role="tabpanel" tabIndex={0} aria-labelledby={`${reviewTabsId}-tab-inspect`} className={`b4-review-panel${mode !== "inspect" ? " b4-mobile-hidden" : ""}`}>
          <h4 id="b4-elements-title">Elemen spesifikasi</h4>
          <div className="b4-element-list" role="list" aria-label="Daftar elemen pemeriksaan">
            {review.elements.map((item) => <button key={item.id} type="button" role="listitem" aria-current={selectedElement?.id === item.id ? "true" : undefined}
              className={`b4-element-button${selectedElement?.id === item.id ? " is-selected" : ""}`} onClick={() => selectElement(item.id)}>
              <strong>{item.code} · {item.criterion?.title ?? "Kriteria"}</strong>
              <span>{item.decision_question}</span>
              <small>{item.decision.review_status === "reviewed" ? item.decision.resolution : "pending"}</small>
            </button>)}
          </div>
          {selectedElement && <div className="b4-action-card">
            <h5>{selectedElement.code}: keputusan dosen</h5>
            <p>{selectedElement.decision_question}</p>
            {selectedElement.analysis && <section aria-label="Hasil teknis analysis run">
              <h6>Hasil executor · keputusan tetap milik dosen</h6>
              <p>Applicability: {selectedElement.analysis.applicability_status} · coverage: {selectedElement.analysis.source_coverage_status} · hubungan bukti: {selectedElement.analysis.evidence_relationship} · status: {selectedElement.analysis.check_status ?? "not applicable"}</p>
              {selectedElement.analysis.interpretation && <p>{selectedElement.analysis.interpretation}</p>}
              {selectedElement.analysis.reason_codes.length > 0 && <p>Batas: {selectedElement.analysis.reason_codes.join(", ")}</p>}
              {selectedElement.analysis.observations.length > 0 && <ul>{selectedElement.analysis.observations.map((item, index) => {
                const record = item.record && typeof item.record === "object" ? item.record as Record<string, unknown> : null;
                return <li key={index}>{item.kind === "applicability_task_fact" && record
                  ? `Fakta terverifikasi ${String(item.fact)} = ${String(record.value)} · ${String(record.verification)} · asal: ${String(record.origin)} · diverifikasi ${String(record.verified_at)} oleh ${String(record.verified_by)} · evidence: ${Array.isArray(record.evidence_ids) ? record.evidence_ids.join(", ") || "konfirmasi dosen" : "tidak tersedia"}`
                  : String(item.text ?? item.code ?? "Observation")}</li>;
              })}</ul>}
              {selectedElement.analysis.support_bindings.length > 0 && <details>
                <summary>Bindings bukti bertipe: {selectedElement.analysis.support_bindings.length}</summary>
                <pre>{JSON.stringify(selectedElement.analysis.support_bindings, null, 2)}</pre>
              </details>}
              {review.analysis_run?.routing_decisions.filter((item) => item.check_element_id === selectedElement.check_element_id).map((item, index) =>
                <small key={`${item.task}-${index}`}>Routing {item.task}: {item.action} · {item.reason_code} · {item.outcome}</small>)}
              {selectedElement.analysis.grounding && <p>Grounding: mechanical {selectedElement.analysis.grounding.mechanical_status} · semantic {selectedElement.analysis.grounding.semantic_status}</p>}
              {selectedElement.analysis.coverage && <details>
                <summary>Coverage {selectedElement.analysis.coverage.retrieval_status}: {selectedElement.analysis.coverage.inspected_count}/{selectedElement.analysis.coverage.compatible_count} unit diperiksa; {selectedElement.analysis.coverage.unresolved_count} belum terselesaikan</summary>
                <p>Exhaustive untuk absence: {selectedElement.analysis.coverage.exhaustive_for_negative ? "ya" : "tidak"}. Kesimpulan positif dari kutipan saja tidak diterima.</p>
                <pre>{JSON.stringify(selectedElement.analysis.coverage.inventory, null, 2)}</pre>
              </details>}
            </section>}
            <ul>{selectedElement.requirements.map((item) => <li key={item.id}>{item.code} · {item.text}{item.mandatory ? " · wajib" : ""}</li>)}</ul>
            <label className="field-label">Resolusi
              <select value={resolution} onChange={(event) => setResolution(event.target.value)}>
                <option value="observed_no_issue">Tidak menemukan masalah</option>
                <option value="confirmed_issue">Masalah dikonfirmasi</option>
                <option value="not_applicable">Tidak berlaku</option>
                <option value="unable_to_conclude">Belum dapat disimpulkan</option>
              </select>
            </label>
            <label className="field-label">Alasan / batas bila diperlukan<textarea maxLength={2000} value={decisionReason} onChange={(event) => setDecisionReason(event.target.value)} /></label>
            <fieldset className="b4-select-list"><legend>Sumber yang diperiksa</legend>
              {evidence.map((item) => <label key={item.id} className="check-row"><input type="checkbox" checked={decisionEvidenceIds.includes(item.id)} onChange={() => updateChecklist(setDecisionEvidenceIds, decisionEvidenceIds, item.id)} />{item.file_name} · {anchorText(item.anchor)}</label>)}
              {!evidence.length && <small>Evidence hasil parser belum tersedia. Dosen dapat menambahkan lokasi manual di panel bukti.</small>}
            </fieldset>
            <button className="button button-primary" type="button" disabled={busy} onClick={() => void saveElementDecision()}>Simpan keputusan elemen</button>
          </div>}
          <div className="b4-action-card">
            <h5>Temuan manual dosen</h5>
            <label className="field-label">Kategori resmi<select value={findingCategory} onChange={(event) => setFindingCategory(event.target.value)}>
              <option value="missing_requirement">Requirement wajib belum terpenuhi</option>
              <option value="rubric_coverage_gap">Kesenjangan cakupan rubrik</option>
              <option value="numerical_inconsistency">Ketidakkonsistenan numerik terverifikasi</option>
            </select></label>
            <label className="field-label">Pernyataan temuan<textarea maxLength={4000} value={findingClaim} onChange={(event) => setFindingClaim(event.target.value)} placeholder="Deskripsikan kekurangan yang didukung sumber." /></label>
            {findingCategory === "numerical_inconsistency" && <div className="b4-action-card">
              <h5>Perbandingan Decimal terverifikasi</h5>
              <label className="field-label">NumericPolicy approved<select value={numericPolicyId || numericPolicies.find((item) => item.state === "approved")?.id || ""} onChange={(event) => setNumericPolicyId(event.target.value)}>
                {numericPolicies.filter((item) => item.state === "approved").map((item) => <option key={item.id} value={item.id}>{item.policy_key} v{item.version_no}</option>)}
              </select></label>
              <button className="button button-quiet" type="button" disabled={!numericPolicies.some((item) => item.id === (numericPolicyId || numericPolicies.find((candidate) => candidate.state === "approved")?.id) && item.state === "approved")} onClick={() => {
                const policy = numericPolicies.find((item) => item.id === (numericPolicyId || numericPolicies.find((candidate) => candidate.state === "approved")?.id));
                if (!policy) return;
                const operation = String(policy.payload.comparison_mode ?? "pairwise");
                const roles = operation === "sum_against_reported" ? ["operand", "reported"]
                  : operation === "ratio_against_reported" ? ["numerator", "denominator", "reported"] : ["left", "right"];
                const fields = Array.isArray(policy.payload.required_context_fields) ? policy.payload.required_context_fields.map(String) : ["unit"];
                const context = Object.fromEntries(fields.map((field) => [field, field === "unit" ? String(policy.payload.canonical_unit ?? "") : ""]));
                setNumericPayloadText(JSON.stringify({ numeric_policy_version_id: policy.id,
                  verification_note: "Dikonfirmasi dosen setelah memeriksa sumber original.",
                  operands: roles.map((role, index) => ({ evidence_id: findingEvidenceIds[index] ?? "", role,
                    literal: "", source_quality: "lecturer_confirmed", context })) }, null, 2));
              }}>Siapkan kerangka operand</button>
              <label className="field-label">Operand, konteks, dan catatan verifikasi<textarea className="code-editor" aria-label="Numeric comparison JSON" value={numericPayloadText} onChange={(event) => setNumericPayloadText(event.target.value)} placeholder={'{"numeric_policy_version_id":"…","verification_note":"…","operands":[{"evidence_id":"…","role":"left","literal":"10,5","source_quality":"lecturer_confirmed","context":{"metric":"massa","period":"2026","unit":"kg"}},{"evidence_id":"…","role":"right","literal":"10,9","source_quality":"lecturer_confirmed","context":{"metric":"massa","period":"2026","unit":"kg"}}]}'} /></label>
              <p className="help-text">Sumber OCR/cache formula yang belum diverifikasi tidak dapat mengesahkan temuan numerik. JSON harus memuat roles dan seluruh context field yang diwajibkan policy.</p>
            </div>}
            <fieldset className="b4-select-list"><legend>Bukti temuan</legend>
              {evidence.map((item) => <label key={item.id} className="check-row"><input type="checkbox" checked={findingEvidenceIds.includes(item.id)} onChange={() => updateChecklist(setFindingEvidenceIds, findingEvidenceIds, item.id)} />{item.file_name} · {anchorText(item.anchor)}</label>)}
              {!evidence.length && <small>Temuan perlu bukti sumber atau coverage attestation.</small>}
            </fieldset>
            <fieldset className="b4-select-list"><legend>Coverage attestation bila bukti bukan satu unit</legend>
              {review.source_files.map((file) => <label key={file.id} className="check-row"><input type="checkbox" checked={coverageFileIds.includes(file.id)} onChange={() => updateChecklist(setCoverageFileIds, coverageFileIds, file.id)} />Periksa seluruh {file.name}</label>)}
              <label className="field-label">Catatan inspeksi<textarea maxLength={2000} value={coverageNote} onChange={(event) => setCoverageNote(event.target.value)} /></label>
            </fieldset>
            <button className="button button-quiet" type="button" disabled={busy || !findingClaim.trim() || (!findingEvidenceIds.length && !(coverageNote.trim() && coverageFileIds.length))} onClick={() => void createManualFinding()}>Simpan temuan manual</button>
          </div>
        </section>
        <section id={`${reviewTabsId}-panel-evidence`} role="tabpanel" tabIndex={0} aria-labelledby={`${reviewTabsId}-tab-evidence`} className={`b4-review-panel${mode !== "evidence" ? " b4-mobile-hidden" : ""}`}>
          <h4 id="b4-evidence-title">Sumber dan bukti</h4>
          <label className="field-label">Evidence final
            <select value={selectedEvidenceId} onChange={(event) => setSelectedEvidenceId(event.target.value)}>
              {evidence.map((item) => <option value={item.id} key={item.id}>{item.file_name} · {anchorText(item.anchor)} · {item.type}</option>)}
            </select>
          </label>
          {selectedEvidence && <>
            <p className="help-text">{selectedEvidence.parse_status}{selectedEvidence.limitations.length ? ` · ${selectedEvidence.limitations.map((item) => String(item.message ?? item.code)).join("; ")}` : ""}</p>
            <pre className="b4-evidence-content">{String(selectedEvidence.content.text ?? selectedEvidence.content.value ?? JSON.stringify(selectedEvidence.content, null, 2))}</pre>
            {!annotationSource && <SubmissionFileViewer key={`${selectedEvidence.file_id}:${selectedEvidence.source_unit_id}`} fileId={selectedEvidence.file_id} initialSourceUnitId={selectedEvidence.source_unit_id} onManualAnchor={(selection) => {
              onManualAnchorSelection(selection);
              setNotice(`Lokasi ${anchorText(selection.anchor)} dari berkas asli terpilih untuk evidence manual.`);
            }} />}
          </>}
          {annotationSource && <section className="b4-action-card" aria-label="Sumber anotasi terpilih">
            <h5>Sumber anotasi · revisi #{annotationSource.revisionNo}</h5>
            <p className="help-text">{anchorText(annotationSource.anchor)} · {annotationSource.operation} · SHA-256 {annotationSource.sourceSha256.slice(0, 12)}</p>
            <button type="button" className="text-button" onClick={() => setAnnotationSource(null)}>Tutup sumber anotasi</button>
            <SubmissionFileViewer key={`${annotationSource.fileId}:${annotationSource.sourceUnitId ?? "first-unit"}`}
              fileId={annotationSource.fileId} initialSourceUnitId={annotationSource.sourceUnitId}
              onManualAnchor={(selection) => {
                onManualAnchorSelection(selection);
                setNotice(`Lokasi ${anchorText(selection.anchor)} dari berkas asli terpilih untuk evidence manual.`);
              }} />
          </section>}
          <ManualEvidenceForm review={review} files={review.source_files} elements={review.elements} sourceSelection={manualAnchorSelection}
            onSourceSelection={onManualAnchorSelection} csrfToken={csrfToken} busy={busy}
            relationships={relationships} relationshipCode={activeRelationshipCode} role={activeRole}
            selectedElementId={selectedElement?.id ?? ""} onElementChange={selectElement}
            onRelationshipChange={(code, nextRole) => { setSelectedRelationshipCode(code); setSelectedRole(nextRole); }}
          onSaved={(result) => {
            setSelectedEvidenceId(result.evidence_id);
            setReview((current) => current ? { ...current, revision: result.revision, content_hash: result.content_hash, state: "draft" } : current);
            setRefreshKey((value) => value + 1);
          }} onBusy={setBusy} onError={setError} onNotice={setNotice} />
          <div className="b4-action-card">
            <h5>Binding sumber ke role</h5>
            <p className="help-text">Binding menautkan evidence terpilih ke elemen dan role yang dideklarasikan spesifikasi.</p>
            <label className="field-label">Alasan anotasi / koreksi<textarea maxLength={2000} value={annotationReason} onChange={(event) => setAnnotationReason(event.target.value)} /></label>
            <button type="button" className="button button-quiet" disabled={busy || !selectedEvidence || !selectedElement} onClick={() => void createEvidenceBinding()}>Ikat evidence ke elemen</button>
          </div>
          <section className="b4-action-card" aria-labelledby="b4-annotation-history-title">
            <h5 id="b4-annotation-history-title">Anotasi dan riwayat revisi</h5>
            {!review.annotations.length && <p className="help-text">Belum ada anotasi manual.</p>}
            {review.annotations.map((annotation) => <article className="b4-annotation-card" key={annotation.id}>
              <strong>{annotation.kind} · {annotation.current?.operation ?? "tanpa revisi"} · {annotation.history.length} revisi</strong>
              {annotation.current && <>
                <p>{anchorText(annotation.current.anchor)}{annotation.current.transcription ? ` · ${annotation.current.transcription}` : ""}</p>
                <small>{annotation.current.reason ?? "Tanpa alasan"} · hash sumber {annotation.current.source_sha256.slice(0, 12)}</small>
                <button type="button" className="text-button" onClick={() => openAnnotationSource(annotation.current!)}>
                  Buka sumber asli untuk revisi #{annotation.current.revision_no}
                </button>
                <details><summary>Lihat histori append-only</summary><ol>{annotation.history.map((item) => <li key={item.id}>
                  #{item.revision_no} {item.operation} · {anchorText(item.anchor)} · {item.reason ?? "tanpa alasan"}
                  <button type="button" className="text-button" onClick={() => openAnnotationSource(item)}>
                    Buka sumber asli untuk revisi #{item.revision_no}
                  </button>
                </li>)}</ol></details>
                <div className="button-row">
                  <button type="button" className="button button-quiet" disabled={busy || annotation.current.operation !== "upsert" || !annotationReason.trim()} onClick={() => void reviseAnnotation(annotation, "remove")}>Nonaktifkan anotasi</button>
                  <button type="button" className="button button-quiet" disabled={busy || annotation.current.operation !== "upsert" || (annotation.kind !== "evidence_binding" && !manualAnchorSelection)} onClick={() => void reviseAnnotation(annotation, "upsert")}>Simpan revisi terpilih</button>
                </div>
                {annotation.kind === "transcription" && <label className="field-label">Teks transkripsi hasil koreksi<textarea maxLength={20000} value={annotationTranscription} onChange={(event) => setAnnotationTranscription(event.target.value)} /></label>}
              </>}
            </article>)}
          </section>
        </section>
      </div>
      <section id={`${reviewTabsId}-panel-decision`} role="tabpanel" tabIndex={0} aria-labelledby={`${reviewTabsId}-tab-decision`} className={`b4-review-panel${mode !== "decision" ? " b4-mobile-hidden" : ""}`}>
        <h4 id="b4-finding-title">Kandidat dan keputusan akhir</h4>
        {review.findings.length ? review.findings.map((finding) => <article className="b4-finding-card" key={finding.id}>
          <div><strong>{finding.origin === "lecturer" ? "Temuan manual dosen" : "Kandidat analisis"} · {finding.category}</strong>
            <p>{finding.claim}</p><small>{finding.technical_validation} · {finding.semantic_support} · status: {finding.action}</small>
            {finding.numeric_check && <p className="help-text" role="note">Decimal · {finding.numeric_check.comparability}
              {finding.numeric_check.difference !== null ? ` · selisih ${finding.numeric_check.difference}` : ""}
              {finding.numeric_check.allowed_difference !== null ? ` · batas ${finding.numeric_check.allowed_difference}` : ""}
              {finding.numeric_check.inconsistent === true ? " · melewati tolerance" : ""}</p>}
            {finding.evidence_ids.map((id) => <button className="text-button" type="button" key={id} onClick={() => { setSelectedFindingId(finding.id); setSelectedEvidenceId(id); setMode("evidence"); }}>Buka evidence {id.slice(0, 8)}</button>)}
          </div>
          {finding.candidate_id && <div className="b4-candidate-actions">
            <label className="field-label">Alasan reject/defer<textarea maxLength={2000} value={candidateReason} onChange={(event) => setCandidateReason(event.target.value)} /></label>
            {candidateEditId === finding.id && <div className="b4-action-card">
              <label className="field-label">Kategori hasil edit<select value={findingCategory} onChange={(event) => setFindingCategory(event.target.value)}>
                <option value="missing_requirement">Requirement wajib belum terpenuhi</option><option value="rubric_coverage_gap">Kesenjangan cakupan rubrik</option><option value="numerical_inconsistency">Ketidakkonsistenan numerik</option>
              </select></label>
              <label className="field-label">Klaim hasil edit<textarea maxLength={4000} value={findingClaim} onChange={(event) => setFindingClaim(event.target.value)} /></label>
              <fieldset className="b4-select-list"><legend>Grounding evidence</legend>{evidence.map((item) => <label className="check-row" key={item.id}><input type="checkbox" checked={findingEvidenceIds.includes(item.id)} onChange={() => updateChecklist(setFindingEvidenceIds, findingEvidenceIds, item.id)} />{item.file_name} · {anchorText(item.anchor)}</label>)}</fieldset>
              {findingCategory === "numerical_inconsistency" && <label className="field-label">NumericPolicy comparison JSON<textarea className="code-editor" value={numericPayloadText} onChange={(event) => setNumericPayloadText(event.target.value)} placeholder="Masukkan operand terverifikasi, evidence, context, dan policy approved." /></label>}
              <button type="button" className="button button-primary" disabled={busy || !findingClaim.trim() || !findingEvidenceIds.length} onClick={() => void candidateAction(finding, "edit")}>Simpan edit kandidat</button>
            </div>}
            <div className="button-row">
              <button type="button" className="button button-primary" disabled={busy || finding.action !== "pending" || finding.technical_validation === "invalid"} onClick={() => void candidateAction(finding, "accept")}>Terima</button>
              <button type="button" className="button button-quiet" disabled={busy || finding.action !== "pending"} onClick={() => {
                setCandidateEditId(finding.id); setFindingCategory(finding.category); setFindingClaim(finding.claim);
                setFindingEvidenceIds(finding.evidence_ids); setSelectedFindingId(finding.id);
                const target = finding.targets.find((item) => item.kind === "element");
                if (target) selectElement(target.id);
              }}>Edit kandidat</button>
              <button type="button" className="button button-quiet" disabled={busy || finding.action !== "pending" || !candidateReason.trim()} onClick={() => void candidateAction(finding, "defer")}>Tunda dengan batas</button>
              <button type="button" className="button button-quiet" disabled={busy || finding.action !== "pending"} onClick={() => void candidateAction(finding, "reject")}>Tolak kandidat</button>
            </div>
          </div>}
        </article>) : <p className="notice notice-muted">Belum ada kandidat atau temuan. Dosen dapat tetap menyelesaikan pemeriksaan secara manual.</p>}
        <label className="field-label">Catatan internal dosen<textarea maxLength={20000} value={notes} onChange={(event) => setNotes(event.target.value)} /></label>
        <label className="field-label">Batas pemeriksaan (satu per baris)<textarea value={limitationsText} onChange={(event) => setLimitationsText(event.target.value)} /></label>
        <div className="button-row">
          <button type="button" className="button button-quiet" disabled={busy} onClick={() => void saveNotes()}>Simpan catatan</button>
          <button type="button" className="button button-primary" disabled={busy || review.state === "complete"} onClick={() => void mutate(
            () => request(`/reviews/${review.id}/complete`, csrfToken, "POST", { expected_revision: review.revision }),
            "Pemeriksaan lengkap dan hash review dibekukan.")}>Selesaikan pemeriksaan</button>
        </div>
        <div className="b4-feedback-card">
          <h5>Feedback mahasiswa</h5>
          <p className="help-text">Feedback hanya memakai confirmed findings dan bukti aktif yang dipilih, catatan dosen, serta batas pemeriksaan. Draft model tetap perlu ditinjau; saat provider tidak tersedia, sistem menyiapkan template.</p>
          <fieldset className="b4-select-list"><legend>Temuan yang disertakan</legend>
            {confirmedFindings.map((finding) => {
              const included = includedFindingIds.includes(finding.id);
              return <div className="b4-feedback-choice" key={finding.id}>
                <label className="check-row"><input type="checkbox" checked={included} onChange={() => {
                  setIncludedFindingIds((current) => included ? current.filter((id) => id !== finding.id) : [...current, finding.id]);
                  if (!included) setExclusionReasons((current) => { const next = { ...current }; delete next[finding.id]; return next; });
                }} />{finding.category} · {finding.claim}</label>
                {!included && <label className="field-label">Alasan eksplisit mengecualikan temuan<textarea maxLength={2000} value={exclusionReasons[finding.id] ?? ""} onChange={(event) => setExclusionReasons((current) => ({ ...current, [finding.id]: event.target.value }))} /></label>}
              </div>;
            })}
            {!confirmedFindings.length && <small>Tidak ada confirmed finding. Gunakan template netral.</small>}
          </fieldset>
          <label className="field-label">Teks feedback manual<textarea maxLength={20000} value={manualFeedback} onChange={(event) => setManualFeedback(event.target.value)} placeholder="Gunakan Markdown ringan tanpa link eksternal, HTML, atau jawaban ideal." /></label>
          <div className="button-row">
            <button type="button" className="button button-quiet" disabled={busy || review.state !== "complete" || confirmedFindings.some((item) => !includedFindingIds.includes(item.id) && !exclusionReasons[item.id]?.trim())} onClick={() => void createFeedback("template")}>Buat feedback template</button>
            {review.analysis_run && review.analysis_run.provider_mode !== "off" && <button type="button" className="button button-quiet" disabled={busy || review.state !== "complete" || !includedFindingIds.length || confirmedFindings.some((item) => !includedFindingIds.includes(item.id) && !exclusionReasons[item.id]?.trim())} onClick={() => void createFeedback("model")}>Buat draft dengan provider</button>}
            <button type="button" className="button button-quiet" disabled={busy || review.state !== "complete" || !manualFeedback.trim() || !includedFindingIds.length || confirmedFindings.some((item) => !includedFindingIds.includes(item.id) && !exclusionReasons[item.id]?.trim())} onClick={() => void createFeedback("manual")}>Buat feedback manual</button>
          </div>
          {feedback && <article className="b4-feedback-preview" aria-label="Draft feedback">
            <strong>Draft {feedback.generation_mode} · {feedback.state} · revisi {feedback.revision}{feedbackStale ? " · stale" : ""}</strong>
            {["queued", "running"].includes(feedback.generation_status) && <small role="status">Draft masih diproses worker B5.</small>}
            {feedback.generation_status === "fallback" && <small role="status">Provider gagal atau diblokir; draft ini memakai template. Kode: {feedback.generation_failure_code ?? "unknown"}.</small>}
            {feedback.generation_mode === "model" && review.analysis_run?.provider_mode === "mock" && <small>SIMULASI • data sintetis · wajib ditinjau dosen</small>}
            <pre>{feedback.body_markdown}</pre>
            <div className="button-row">
              {feedback.state === "draft" && feedback.generation_mode === "manual" && <button type="button" className="button button-quiet" disabled={busy || feedbackStale} onClick={() => void updateFeedbackDraft()}>Simpan revisi draft</button>}
              <button type="button" className="button button-quiet" disabled={busy || feedback.state !== "draft" || ["queued", "running"].includes(feedback.generation_status) || feedbackStale} onClick={() => void approveFeedback()}>Setujui feedback</button>
              <button type="button" className="button button-primary" disabled={busy || feedback.state !== "approved" || feedbackStale || !activeVersion || Boolean(publicationId)} onClick={() => void publish()}>Terbitkan hasil</button>
              {publicationId && <button type="button" className="button button-quiet" onClick={() => void withdraw()}>Tarik publikasi</button>}
            </div>
          </article>}
        </div>
      </section>
    </>}
  </section>;
}

function ManualEvidenceForm({ review, files, elements, selectedElementId, onElementChange, sourceSelection, onSourceSelection, csrfToken, busy, relationships, relationshipCode, role, onRelationshipChange, onSaved, onBusy, onError, onNotice }: {
  review: Review; files: SourceFile[]; elements: ReviewElement[]; csrfToken: string; busy: boolean;
  selectedElementId: string; onElementChange: (id: string) => void;
  relationships: Relationship[]; relationshipCode: string; role: string; onRelationshipChange: (code: string, role: string) => void;
  sourceSelection: ManualAnchorSelection | null; onSourceSelection: (selection: ManualAnchorSelection | null) => void;
  onSaved: (result: { evidence_id: string; revision: number; content_hash: string }) => void;
  onBusy: (busy: boolean) => void; onError: (message: string) => void; onNotice: (message: string) => void;
}) {
  const [fileId, setFileId] = useState(files[0]?.id ?? "");
  const [locatorNote, setLocatorNote] = useState("Lokasi manual dipilih dosen pada berkas asli.");
  const [transcription, setTranscription] = useState("");
  const [reason, setReason] = useState("");
  const file = files.find((item) => item.id === (sourceSelection?.file_id ?? fileId)) ?? files.find((item) => item.id === fileId) ?? files[0];
  async function submit() {
    if (!file) return;
    onBusy(true); onError(""); onNotice("");
    try {
      const result = await request<{ evidence_id: string; revision: number; content_hash: string }>(`/reviews/${review.id}/manual-evidence`, csrfToken, "POST", {
        file_id: sourceSelection?.file_id ?? file.id,
        source_sha256: sourceSelection?.source_sha256 ?? file.sha256,
        anchor: sourceSelection?.anchor ?? { format: "file", source_sha256: file.sha256, locator_note: locatorNote.trim() },
        transcription: transcription.trim() || null,
        original_evidence_id: transcription.trim() ? sourceSelection?.original_evidence_id ?? null : null,
        reason: transcription.trim() ? reason.trim() : null,
        element_ids: selectedElementId ? [selectedElementId] : [],
        review_finding_id: null,
        relationship_code: role ? relationshipCode || null : null,
        role: role || null,
        expected_revision: review.revision,
      });
      onNotice(sourceSelection
        ? "Evidence manual ditambahkan dari lokasi unit sumber yang diperiksa."
        : "Evidence manual ditambahkan dengan whole-file anchor; gunakan hanya karena lokasi lebih rinci belum dapat diverifikasi.");
      setTranscription(""); setReason(""); onSaved(result);
    } catch (cause) { onError(cause instanceof Error ? cause.message : "Evidence manual gagal disimpan."); }
    finally { onBusy(false); }
  }
  return <div className="b4-action-card">
    <h5>Tambahkan sumber manual</h5>
    <p className="help-text">Whole-file anchor adalah lokasi kasar. Gunakan transkripsi hanya setelah memeriksa original; hasil koreksi menjadi evidence baru.</p>
    <label className="field-label">Berkas final<select value={sourceSelection?.file_id ?? file?.id ?? ""} onChange={(event) => { setFileId(event.target.value); onSourceSelection(null); }}>{files.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    {sourceSelection ? <p className="form-success">Lokasi sumber terpilih: {anchorText(sourceSelection.anchor)} · unit {sourceSelection.source_unit_id.slice(0, 8)} <button type="button" className="text-button" onClick={() => onSourceSelection(null)}>Batalkan pilihan</button></p>
      : <label className="field-label">Catatan lokasi seluruh berkas (gunakan hanya bila lokasi spesifik belum dapat diverifikasi)<textarea maxLength={240} value={locatorNote} onChange={(event) => setLocatorNote(event.target.value)} /></label>}
    <label className="field-label">Transkripsi koreksi (opsional, maks. 20.000 karakter)<textarea maxLength={20000} value={transcription} onChange={(event) => setTranscription(event.target.value)} /></label>
    {transcription.trim() && <label className="field-label">Alasan koreksi<textarea maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>}
    <label className="field-label">Tautkan ke elemen<select value={selectedElementId} onChange={(event) => onElementChange(event.target.value)}>{elements.map((item) => <option key={item.id} value={item.id}>{item.code} · {item.criterion?.title ?? "Kriteria"}</option>)}</select></label>
    {relationships.length > 0 && <>
      <label className="field-label">Relationship<select value={relationships.some((item) => item.relationship_code === relationshipCode) ? relationshipCode : relationships[0].relationship_code} onChange={(event) => onRelationshipChange(event.target.value, "")}>{relationships.map((item) => <option key={item.relationship_code} value={item.relationship_code}>{item.relationship_code} · {item.predicate}</option>)}</select></label>
      <label className="field-label">Role<select value={role} onChange={(event) => onRelationshipChange(relationshipCode || relationships[0].relationship_code, event.target.value)}><option value="">Tidak diikat ke role</option>{rolesForPredicate(relationships.find((item) => item.relationship_code === relationshipCode)?.predicate ?? relationships[0].predicate).map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
    </>}
    <button className="button button-quiet" type="button" disabled={busy || !file || !locatorNote.trim() || (Boolean(transcription.trim()) && !reason.trim())} onClick={() => void submit()}>Simpan evidence manual</button>
  </div>;
}

function anchorText(anchor: Record<string, unknown>): string {
  if (anchor.format === "pdf") return `PDF hlm. ${String(anchor.page ?? "?")}`;
  if (anchor.format === "xlsx") return `${String(anchor.sheet_name ?? "")}:${String(anchor.cell_range ?? "")}`;
  if (anchor.format === "docx") return `${String(anchor.part ?? "")} ${String(anchor.structural_path ?? "")}`;
  if (anchor.format === "image") return "area gambar";
  return String(anchor.locator_note ?? anchor.format ?? "sumber");
}

function rolesForPredicate(predicate: string): string[] {
  const registry: Record<string, string[]> = {
    covers: ["content", "relation"],
    compares: ["left", "right", "basis", "relation"],
    supports: ["claim", "support", "relation"],
    applies: ["method", "application", "relation"],
    traces_to: ["artifact", "trace", "relation"],
    reports_same_quantity: ["left", "right", "relation"],
  };
  return registry[predicate] ?? [];
}

function relationshipsForElement(element: ReviewElement | null | undefined): Relationship[] {
  const values = element?.payload.relationships;
  return Array.isArray(values) ? values.filter((item): item is Relationship =>
    typeof item === "object" && item !== null && "relationship_code" in item && "predicate" in item) : [];
}

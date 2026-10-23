"use client";

import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
import type { ApiMethod, ApiRequestPath } from "@/lib/api-contract.generated";
import { NumericComparisonEditor, type NumericComparison } from "./NumericComparisonEditor";
import { ParserLimitations } from "./ParserLimitations";
import { reviewLabel, processingLabel, sourceLocation } from "./reviewPresentation";
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
  version_no: number;
  assignment_version_id: string;
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
type AnalysisRunStatus = { id: string; submission_version_id: string; status: string; completion_kind: string | null; limitations: unknown[] };

async function request<T>(path: ApiRequestPath, csrfToken: string, method: ApiMethod = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers = new Headers({ "X-CSRF-Token": csrfToken });
  if (body !== undefined) headers.set("Content-Type", "application/json");
  if (method !== "GET") headers.set("Idempotency-Key", crypto.randomUUID());
  const response = await fetch(`/api/v1${path}`, {
    method,
    signal,
    headers,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    cache: "no-store",
    credentials: "same-origin",
  });
  const payload = (await response.json().catch(() => ({}))) as T & ResponseError;
  if (!response.ok) {
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail : detail?.message ?? payload.error?.message;
    const summary = response.status === 409 ? "Data telah berubah. Isian Anda tetap ada; muat ulang data sebelum menyimpan kembali. Jika versi pengumpulan berubah, buka ulang halaman untuk memeriksa sumber terbaru."
      : response.status === 422 ? "Isian atau prasyarat belum lengkap. Periksa keputusan, bukti, dan keterbatasan yang diperlukan."
      : response.status === 403 || response.status === 404 ? "Sumber tidak tersedia atau Anda tidak memiliki izin untuk tindakan ini."
      : response.status === 401 ? "Sesi Anda telah berakhir. Masuk kembali untuk melanjutkan." : "Permintaan belum berhasil. Isian Anda tetap dipertahankan.";
    throw new Error(`${summary}\n${message ?? `HTTP ${response.status}`}`);
  }
  return payload;
}

export function ReviewWorkspace({ submission, csrfToken, manualAnchorSelection, onManualAnchorSelection, sourceRefreshKey = 0 }: {
  submission: Submission; csrfToken: string; sourceRefreshKey?: number; manualAnchorSelection: ManualAnchorSelection | null;
  onManualAnchorSelection: (selection: ManualAnchorSelection | null) => void;
}) {
  const reviewTabsId = useId();
  const [review, setReview] = useState<Review | null>(null);
  const initialReviewRequest = useRef<Promise<Review> | null>(null);
  const reviewRevision = useRef<{ id: string; revision: number } | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [numericPolicies, setNumericPolicies] = useState<NumericPolicy[]>([]);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
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
  const [resolution, setResolution] = useState("");
  const [decisionReason, setDecisionReason] = useState("");
  const [decisionEvidenceIds, setDecisionEvidenceIds] = useState<string[]>([]);
  const [findingClaim, setFindingClaim] = useState("");
  const [findingCategory, setFindingCategory] = useState("missing_requirement");
  const [candidateEditId, setCandidateEditId] = useState("");
  const [findingEvidenceIds, setFindingEvidenceIds] = useState<string[]>([]);
  const [coverageFileIds, setCoverageFileIds] = useState<string[]>([]);
  const [coverageNote, setCoverageNote] = useState("");
  const [numericValue, setNumericValue] = useState<NumericComparison | null>(null);
  const [candidateReason, setCandidateReason] = useState("");
  const [notes, setNotes] = useState("");
  const [limitationsText, setLimitationsText] = useState("");
  const [manualFeedback, setManualFeedback] = useState("");
  const [includedFindingIds, setIncludedFindingIds] = useState<string[]>([]);
  const [exclusionReasons, setExclusionReasons] = useState<Record<string, string>>({});
  const [mode, setMode] = useState<"inspect" | "evidence" | "decision">("inspect");
  const [requestBusy, setBusy] = useState(false);
  const [remoteReview, setRemoteReview] = useState<Review | null>(null);
  const busy = requestBusy || remoteReview !== null;
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [stage, setStage] = useState<"inspect" | "feedback" | "history">("inspect");
  const [split, setSplit] = useState(46);
  const [sourceFileId, setSourceFileId] = useState(submission.files?.[0]?.id ?? "");
  const [requestedReviewId, setRequestedReviewId] = useState("");
  const [reviewHistory, setReviewHistory] = useState<Array<{ id: string; version_no: number; state: string; analysis_run_id: string | null }>>([]);
  const [publications, setPublications] = useState<Array<{ publication_id: string; status: string; sequence_no: number; submission_version_id: string; published_at: string; feedback_draft_id: string; review_version_id: string }>>([]);
  const [publicationPreview, setPublicationPreview] = useState<Record<string, unknown> | null>(null);
  const [feedbackHistory, setFeedbackHistory] = useState<Feedback[]>([]);
  const [feedbackCursor, setFeedbackCursor] = useState<number | null>(null);
  const [pendingRunId, setPendingRunId] = useState("");
  const [analysisWatchId, setAnalysisWatchId] = useState("");
  const [dirtyScopes, setDirtyScopes] = useState<string[]>([]);
  const dirty = useRef(new Set<string>());
  const editEpoch = useRef<Record<string, number>>({});
  const dirtyLocation = useRef<{ url: string; state: unknown } | null>(null);
  const [formReset, setFormReset] = useState(0);
  const [showManualEvidence, setShowManualEvidence] = useState(false);
  const [showFinding, setShowFinding] = useState(false);
  const [showBinding, setShowBinding] = useState(false);
  const [showAnnotations, setShowAnnotations] = useState(false);
  function markDirty(scope: string) { if (!dirty.current.size) dirtyLocation.current = { url: window.location.href, state: window.history.state }; editEpoch.current[scope] = (editEpoch.current[scope] ?? 0) + 1; dirty.current.add(scope); setDirtyScopes([...dirty.current]); }
  function markSaved(...scopes: string[]) { for (const scope of scopes) dirty.current.delete(scope); setDirtyScopes([...dirty.current]); }
  function discardForms() {
    dirty.current.clear(); setRemoteReview(null); setDirtyScopes([]); setFormReset((value) => value + 1);
    const resetReview = remoteReview ?? review;
    if (remoteReview) { setCurrentReview(remoteReview); setRefreshKey((value) => value + 1); }
    const decision = resetReview?.elements.find((item) => item.id === selectedElement?.id)?.decision;
    onManualAnchorSelection(null);
    setResolution(decision?.resolution ?? ""); setDecisionReason(decision?.reason ?? ""); setDecisionEvidenceIds(decision?.evidence_ids ?? []);
    setNotes(resetReview?.lecturer_notes ?? ""); setLimitationsText(resetReview?.limitations.join("\n") ?? "");
    setFindingClaim(""); setFindingEvidenceIds([]); setCoverageNote(""); setCoverageFileIds([]); setNumericValue(null);
    setCandidateEditId(""); setCandidateReason(""); setAnnotationReason(""); setAnnotationTranscription("");
    setManualFeedback(feedback?.body_markdown ?? ""); setIncludedFindingIds(feedback?.included_review_finding_ids ?? confirmedFindings.map((item) => item.id)); setExclusionReasons(feedback?.exclusion_reasons ?? {});
  }
  function allowDiscard() {
    if (!dirty.current.size) return true;
    if (!window.confirm("Ada isian belum tersimpan. Buang isian tersebut dan lanjutkan?")) return false;
    discardForms(); return true;
  }
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => { if (dirty.current.size) { event.preventDefault(); event.returnValue = ""; } };
    const linkClick = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target.closest("a[href],button") : null;
      if (target?.tagName === "BUTTON" && target.textContent?.trim() !== "Keluar") return;
      if (!target || target.hasAttribute("download") || target.getAttribute("target") === "_blank" || !dirty.current.size) return;
      if (!window.confirm("Ada isian belum tersimpan. Tinggalkan halaman ini?")) { event.preventDefault(); event.stopPropagation(); }
    };
    const traversal = (event: PopStateEvent) => {
      if (!dirty.current.size || !dirtyLocation.current) return;
      if (window.confirm("Ada isian belum tersimpan. Tinggalkan halaman ini?")) { dirty.current.clear(); return; }
      event.stopImmediatePropagation();
      // Restore the current Next.js history entry before its router handles the traversal.
      window.history.pushState(dirtyLocation.current.state, "", dirtyLocation.current.url);
    };
    window.addEventListener("beforeunload", beforeUnload); document.addEventListener("click", linkClick, true); window.addEventListener("popstate", traversal, true);
    return () => { window.removeEventListener("beforeunload", beforeUnload); document.removeEventListener("click", linkClick, true); window.removeEventListener("popstate", traversal, true); };
  }, []);
  const activeVersion = submission.active_final_version;
  const activeVersionId = activeVersion?.id;
  const selectedElement = review?.elements.find((item) => item.id === selectedElementId) ?? review?.elements[0] ?? null;
  const selectedEvidence = evidence.find((item) => item.id === selectedEvidenceId) ?? null;
  const relationships = relationshipsForElement(selectedElement);
  const activeRelationshipCode = relationships.some((item) => item.relationship_code === selectedRelationshipCode)
    ? selectedRelationshipCode : relationships[0]?.relationship_code ?? "";
  const activeRelationship = relationships.find((item) => item.relationship_code === activeRelationshipCode) ?? null;
  const relationshipRoles = activeRelationship ? rolesForPredicate(activeRelationship.predicate) : [];
  const activeRole = relationshipRoles.includes(selectedRole) ? selectedRole : "";
  const confirmedFindings = review?.findings.filter((item) => ["accept", "edit"].includes(item.action)) ?? [];
  const feedbackStale = Boolean(feedback && review && (feedback.stale || feedback.source_review_hash !== review.content_hash || review.state !== "complete"));
  const allowedNumericPolicies = selectedElement?.payload.numeric_policy_version_id
    ? numericPolicies.filter((policy) => policy.id === selectedElement.payload.numeric_policy_version_id)
    : numericPolicies;
  const selectedFeedbackFindingIds = includedFindingIds;
  const publicationId = publications.find((item) => item.feedback_draft_id === feedback?.id && item.status === "released")?.publication_id ?? "";

  function expectedReviewRevision(current: Review): number {
    return reviewRevision.current?.id === current.id ? reviewRevision.current.revision : current.revision;
  }

  function setCurrentReview(next: Review): boolean {
    const latest = reviewRevision.current;
    if (latest?.id === next.id && latest.revision > next.revision) return false;
    reviewRevision.current = { id: next.id, revision: next.revision };
    setReview(next);
    return true;
  }

  function updateCurrentReviewRevision(id: string, revision: number, contentHash?: string, state: Review["state"] = "draft") {
    const latest = reviewRevision.current;
    if (latest && latest.id !== id) return;
    const isNewer = !latest || revision >= latest.revision;
    const nextRevision = latest ? Math.max(latest.revision, revision) : revision;
    reviewRevision.current = { id, revision: nextRevision };
    setReview((current) => current?.id === id ? {
      ...current,
      revision: nextRevision,
      ...(isNewer && contentHash ? { content_hash: contentHash } : {}),
      ...(isNewer ? { state } : {}),
    } : current);
  }

  useEffect(() => {
    if (!activeVersionId) return;
    let active = true;
    void request<{ releases: RuntimeRelease[] }>("/runtime/releases", csrfToken)
      .then((result) => {
        if (!active) return;
        setRuntimeReleases(result.releases);
        setReleaseManifestId((current) => result.releases.some((item) => item.id === current)
          ? current : result.releases[0]?.id ?? "");
        setReleaseError(result.releases.length ? "" : "Bantuan analisis belum tersedia. Pemeriksaan manual tetap dapat digunakan.");
      })
      .catch((cause) => {
        if (active) setReleaseError(cause instanceof Error ? cause.message : "Konfigurasi bantuan analisis belum dapat dimuat.");
      });
    return () => { active = false; };
  }, [activeVersionId, csrfToken]);

  useEffect(() => {
    if (!activeVersionId) return;
    let active = true;
    async function load() {
      setError("");
      try {
        const listed = await request<{ items: typeof reviewHistory }>(`/submission-versions/${activeVersionId}/reviews`, csrfToken);
        const hint = new URL(window.location.href).searchParams.get("reviewId");
        const selectedId = requestedReviewId || (listed.items.some((item) => item.id === hint) ? hint : null) || listed.items[0]?.id;
        const current = selectedId
          ? await request<Review>(`/reviews/${selectedId}`, csrfToken)
          : await (initialReviewRequest.current ??= request<Review>(`/submission-versions/${activeVersionId}/reviews`, csrfToken, "POST", { analysis_run_id: null }).catch((cause: unknown) => { initialReviewRequest.current = null; throw cause; }));
        let cursor: number | null = 0;
        const allEvidence: Evidence[] = [];
        while (cursor !== null) {
          const page: { items: Evidence[]; next_cursor: number | null } = await request(`/reviews/${current.id}/evidence?cursor=${cursor}&limit=100`, csrfToken);
          allEvidence.push(...page.items); cursor = page.next_cursor;
          if (!active) return;
        }
        const [assignment, drafts, history] = await Promise.all([
          request<{ numeric_policies?: NumericPolicy[] }>(`/assignment-versions/${current.assignment_version_id}`, csrfToken),
          request<{ items: Feedback[]; next_cursor: number | null }>(`/reviews/${current.id}/feedback-drafts`, csrfToken),
          request<{ items: typeof publications }>(`/submissions/${submission.id}/publications`, csrfToken),
        ]);
        if (!active) return;
        const known = reviewRevision.current;
        if (known?.id === current.id && current.revision > known.revision && dirty.current.size > 0) {
          setRemoteReview(current); return;
        }
        if (!setCurrentReview(current)) return;
        const location = new URL(window.location.href); location.searchParams.set("reviewId", current.id); window.history.replaceState(window.history.state, "", location);
        setReviewHistory(listed.items.length ? listed.items : [{ id: current.id, version_no: current.version_no, state: current.state, analysis_run_id: current.analysis_run?.id ?? null }]); setPublications(history.items);
        setEvidence(allEvidence); setNumericPolicies(assignment.numeric_policies ?? []);
        setFeedbackHistory(drafts.items); setFeedbackCursor(drafts.next_cursor);
        const latest = drafts.items[0] ?? null;
        if (!dirty.current.has("feedback")) {
          setFeedback(latest); setManualFeedback(latest?.body_markdown ?? "");
          setIncludedFindingIds(latest?.included_review_finding_ids ?? current.findings.filter((item) => ["accept", "edit"].includes(item.action)).map((item) => item.id));
          setExclusionReasons(latest?.exclusion_reasons ?? {});
        }
        if (!dirty.current.has("notes")) { setNotes(current.lecturer_notes); setLimitationsText(current.limitations.join("\n")); }
        setSelectedElementId((value) => current.elements.some((item) => item.id === value) ? value : current.elements[0]?.id ?? "");
        if (!dirty.current.has("decision")) {
          const element = current.elements.find((item) => item.id === selectedElementId) ?? current.elements[0];
          setResolution(element?.decision.resolution ?? ""); setDecisionReason(element?.decision.reason ?? ""); setDecisionEvidenceIds(element?.decision.evidence_ids ?? []);
        }
        setSelectedEvidenceId((value) => value && allEvidence.some((item) => item.id === value) ? value : "");
        setSelectedFindingId((value) => current.findings.some((item) => item.id === value) ? value : current.findings[0]?.id ?? "");
      } catch (cause) { if (active) setError(cause instanceof Error ? cause.message : "Pemeriksaan belum dapat dimuat."); }
    }
    void load(); return () => { active = false; };
    // Selection changes are local; reloads must not replace unsaved form values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeVersionId, csrfToken, refreshKey, sourceRefreshKey, requestedReviewId, submission.assignment_id, submission.id]);

  useEffect(() => {
    if (!activeVersionId) return;
    const sourceId = activeVersionId;
    let active = true;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let remaining = 180;
    let id = analysisWatchId;
    if (!id) { try { id = sessionStorage.getItem(`align:analysis:${sourceId}`) ?? ""; } catch { /* Optional resume hint only. */ } }
    if (!id) return;
    const runId = id;
    async function poll() {
      try {
        const run = await request<AnalysisRunStatus>(`/analysis-runs/${runId}`, csrfToken, "GET", undefined, controller.signal);
        if (!active) return;
        if (run.submission_version_id !== sourceId) { setError("Hasil analisis tidak cocok dengan versi pengumpulan ini."); return; }
        if (["queued", "running"].includes(run.status)) {
          setAnalysisStatus("Analisis masih diproses. Pemeriksaan manual tetap tersedia.");
          if (--remaining > 0) timer = setTimeout(() => void poll(), 2000);
          else setAnalysisStatus("Analisis masih berlangsung. Buka ulang halaman untuk melanjutkan pemantauan; pekerjaan server tetap berjalan.");
        } else {
          setPendingRunId(runId);
          setAnalysisStatus(run.status === "completed" ? "Analisis selesai. Pilih secara eksplisit bila ingin membuat pemeriksaan baru dari hasil ini." : "Analisis selesai dengan keterbatasan. Pemeriksaan lama tetap tersimpan; hasil baru belum digunakan.");
        }
      } catch (cause) { if (active) setError(cause instanceof Error ? cause.message : "Status analisis belum dapat dimuat."); }
    }
    void poll();
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [activeVersionId, analysisWatchId, csrfToken]);

  useEffect(() => {
    if (!feedback || !["queued", "running"].includes(feedback.generation_status)) return;
    let active = true;
    const id = feedback.id;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void request<Feedback>(`/feedback-drafts/${id}`, csrfToken, "GET", undefined, controller.signal).then((latest) => {
        if (!active) return;
        setFeedback(latest); setFeedbackHistory((items) => items.map((item) => item.id === latest.id ? latest : item));
        if (!dirty.current.has("feedback")) setManualFeedback(latest.body_markdown);
      }).catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : "Status draf belum dapat dimuat."); });
    }, 2000);
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [feedback, csrfToken]);

  async function mutate<T>(action: () => Promise<T>, success: string, reload = true, savedScopes: string[] = []) {
    if (remoteReview) { setError("Tinjau perubahan dari server sebelum menyimpan kembali. Isian lokal tetap dipertahankan."); return false; }
    const startedEpoch = { ...editEpoch.current };
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await action();
      if (review && typeof result === "object" && result !== null) {
        const mutation = result as { id?: unknown; review_id?: unknown; revision?: unknown; review_revision?: unknown; content_hash?: unknown; state?: unknown };
        if (mutation.review_id === review.id || mutation.id === review.id) {
          const responseRevision = typeof mutation.review_revision === "number" ? mutation.review_revision : mutation.revision;
          if (typeof responseRevision === "number" && Number.isInteger(responseRevision)) {
            updateCurrentReviewRevision(review.id, responseRevision,
              typeof mutation.content_hash === "string" ? mutation.content_hash : undefined,
              mutation.state === "complete" ? "complete" : "draft");
          }
        }
      }

      markSaved(...savedScopes.filter((scope) => startedEpoch[scope] === editEpoch.current[scope]));
      setNotice(success);
      if (reload) setRefreshKey((value) => value + 1);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Perubahan pemeriksaan gagal disimpan.");
      return false;
    } finally { setBusy(false); }
  }

  async function runAnalysis() {
    if (!activeVersionId || !releaseManifestId) return;
    if (!window.confirm("Jalankan bantuan analisis untuk pengumpulan ini? Provider nyata dapat memakai budget yang dikonfigurasi. Pemeriksaan Anda tidak akan diganti otomatis.")) return;
    setBusy(true); setError(""); setNotice(""); setAnalysisStatus("Mengantrekan analisis versi sumber ini…");
    try {
      const created = await request<{ analysis_run_id: string; status: string; label: string }>(
        `/submission-versions/${activeVersionId}/analysis-runs`, csrfToken, "POST",
        { release_manifest_id: releaseManifestId },
      );
      try { sessionStorage.setItem(`align:analysis:${activeVersionId}`, created.analysis_run_id); } catch { /* Storage may be unavailable; server job still proceeds. */ }
      setAnalysisWatchId(created.analysis_run_id);
      setAnalysisStatus("Analisis diantrekan. Anda tetap dapat melanjutkan pemeriksaan manual.");

    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Analysis run gagal dibuat.");
      setAnalysisStatus("");
    } finally { setBusy(false); }
  }

  function updateChecklist(setter: (ids: string[]) => void, current: string[], id: string) {
    setter(current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  function selectElement(id: string) {
    if (id !== selectedElementId && !allowDiscard()) return;
    const item = review?.elements.find((candidate) => candidate.id === id);
    if (!item) return;
    setSelectedElementId(id); setResolution(item.decision.resolution ?? "");
    setDecisionReason(item.decision.reason ?? ""); setDecisionEvidenceIds(item.decision.evidence_ids ?? []);
    setSelectedRelationshipCode(relationshipsForElement(item)[0]?.relationship_code ?? ""); setSelectedRole("");
  }

  async function saveElementDecision() {
    if (!review || !selectedElement || !resolution) return;
    await mutate(() => request(`/reviews/${review.id}/element-decisions/${selectedElement.id}`, csrfToken, "PUT", {
      review_status: "reviewed", resolution, applicability_override: null,
      reason: decisionReason || null, evidence_ids: decisionEvidenceIds, expected_revision: expectedReviewRevision(review),
    }), "Keputusan pemeriksaan tersimpan.", true, ["decision"]);
  }

  async function saveNotes() {
    if (!review) return;
    await mutate(() => request(`/reviews/${review.id}/notes`, csrfToken, "PUT", {
      lecturer_notes: notes, limitations: limitationsText.split("\n").map((item) => item.trim()).filter(Boolean),
      expected_revision: expectedReviewRevision(review),
    }), "Catatan dan batas pemeriksaan tersimpan.", true, ["notes"]);
  }

  async function createManualFinding() {
    if (!review || !selectedElement?.criterion || !findingClaim.trim()) return;
    const criterionId = selectedElement.criterion.id;
    const numeric = findingCategory === "numerical_inconsistency" ? numericValue : null;
    if (findingCategory === "numerical_inconsistency" && !numeric) { setError("Lengkapi perbandingan angka berdasarkan aturan yang disahkan."); return; }
    const coverageAttestation = coverageNote.trim() && coverageFileIds.length
      ? { source_file_ids: coverageFileIds, inspection_note: coverageNote.trim() } : null;
    const saved = await mutate(() => request(`/reviews/${review.id}/manual-findings`, csrfToken, "POST", {
      action: "accept", primary_category: findingCategory, primary_criterion_id: criterionId,
      claim: findingClaim.trim(), element_ids: [selectedElement.id], requirement_ids: [], criterion_ids: [],
      evidence_ids: findingEvidenceIds, coverage_attestation: coverageAttestation, numeric, reason: null,
      expected_revision: expectedReviewRevision(review),
    }), "Temuan manual dosen ditambahkan.", true, ["finding"]);
    if (saved) { setFindingClaim(""); setFindingEvidenceIds([]); setCoverageFileIds([]); setCoverageNote(""); setNumericValue(null); setShowFinding(false); }
  }

  async function candidateAction(finding: Finding, action: Finding["action"]) {
    if (!review || !finding.candidate_id) return;
    const payload: Record<string, unknown> = { action, expected_revision: expectedReviewRevision(review) };
    if (action === "defer" || action === "reject") payload.reason = candidateReason.trim();
    if (action === "edit") {
      payload.primary_category = findingCategory;
      payload.primary_criterion_id = selectedElement?.criterion?.id ?? finding.criterion_id;
      payload.claim = findingClaim.trim() || finding.claim;
      payload.element_ids = selectedElement ? [selectedElement.id] : [];
      payload.evidence_ids = findingEvidenceIds.length ? findingEvidenceIds : finding.evidence_ids;
      if (findingCategory === "numerical_inconsistency") {
        if (!numericValue) { setError("Lengkapi perbandingan angka terlebih dahulu."); return; }
        payload.numeric = numericValue;
      }
    }
    const saved = await mutate(() => request(`/reviews/${review.id}/candidate-decisions/${finding.candidate_id}`, csrfToken, "PUT", payload),
      `Kandidat: ${reviewLabel(action)}.`, true, action === "edit" ? ["finding", "candidate"] : ["candidate"]);
    if (saved) { setCandidateReason(""); if (action === "edit") { setCandidateEditId(""); setFindingClaim(""); setFindingEvidenceIds([]); setNumericValue(null); } }
  }

  async function createEvidenceBinding() {
    if (!review || !selectedEvidence || !selectedElement) return;
    await mutate(() => request(`/reviews/${review.id}/annotations`, csrfToken, "POST", {
      kind: "evidence_binding", evidence_item_id: selectedEvidence.id,
      element_ids: [selectedElement.id], relationship_code: activeRole ? activeRelationshipCode || null : null,
      role: activeRole || null, reason: annotationReason.trim() || null,
      expected_revision: expectedReviewRevision(review),
    }), "Tautan bukti ke pemeriksaan tersimpan.", true, ["annotation"]);
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
      kind: annotation.kind, operation, expected_revision: expectedReviewRevision(review),
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
    const saved = await mutate(() => request(`/reviews/${review.id}/annotations/${annotation.id}/revisions`, csrfToken, "POST", body),
      operation === "remove" ? "Anotasi dinonaktifkan; histori dan sumber tetap tersimpan." : "Revisi anotasi tersimpan sebagai bukti baru.", true, ["annotation"]);
    if (!saved) return;
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
        exclusion_reasons: Object.fromEntries(Object.entries(exclusionReasons).filter(([id]) => !includedFindingIds.includes(id))),
        expected_revision: expectedReviewRevision(review),
      });
      setFeedback(created); setManualFeedback(created.body_markdown); setFeedbackHistory((items) => [created, ...items]);

    }, mode === "model" ? "Draft feedback diproses dengan batas B5." : mode === "template" ? "Draft feedback template dibuat." : "Draft feedback manual dibuat.", false, ["feedback"]);
  }

  async function approveFeedback() {
    if (!review || !feedback || feedbackStale || dirty.current.has("feedback")) return;
    await mutate(async () => {
      const result = await request<Feedback>(`/feedback-drafts/${feedback.id}/approve`, csrfToken, "POST", {
        expected_revision: feedback.revision, expected_review_revision: expectedReviewRevision(review),
      });
      setFeedback(result); setFeedbackHistory((items) => items.map((item) => item.id === result.id ? result : item));
    }, "Feedback disetujui. Publikasi masih memerlukan aksi terpisah.", false);
  }

  async function updateFeedbackDraft() {
    if (!review || !feedback || feedback.state !== "draft" || feedbackStale) return;
    const retainedBlocks = feedback.blocks.filter((block) => Array.isArray(block.review_finding_ids) && block.review_finding_ids.every((id) => includedFindingIds.includes(String(id))));
    const covered = new Set(retainedBlocks.flatMap((block) => Array.isArray(block.review_finding_ids) ? block.review_finding_ids.map(String) : []));
    const blocks = [...retainedBlocks, ...confirmedFindings.filter((finding) => includedFindingIds.includes(finding.id) && !covered.has(finding.id)).map((finding) => ({
      criterion_id: finding.criterion_id, review_finding_ids: [finding.id], evidence_ids: finding.evidence_ids,
      message: finding.claim, next_action: "Tinjau kembali bukti yang ditautkan terhadap tuntutan tugas.", limitation: null,
    }))];
    await mutate(async () => {
      const updated = await request<Feedback>(`/feedback-drafts/${feedback.id}`, csrfToken, "PUT", {
        body_markdown: manualFeedback, blocks, included_review_finding_ids: includedFindingIds,
        exclusion_reasons: Object.fromEntries(Object.entries(exclusionReasons).filter(([id]) => !includedFindingIds.includes(id))), expected_revision: feedback.revision,
      });
      setFeedback(updated); setFeedbackHistory((items) => items.map((item) => item.id === updated.id ? updated : item));
    }, "Revisi draf feedback tersimpan.", false, ["feedback"]);
  }

  async function publish() {
    if (!review || !feedback || !activeVersion || feedbackStale || dirty.current.size || publicationId) return;
    const included = selectedFeedbackFindingIds.map((id) => review.findings.find((item) => item.id === id)?.claim).filter(Boolean);
    const message = `Terbitkan hasil untuk ${submission.owner.label ?? "submission"}, versi sumber ${activeVersion.version_no}?\n\nTemuan: ${included.length ? included.join("; ") : "tidak ada"}\nBatas: ${review.limitations.length ? review.limitations.join("; ") : "tidak ada"}\n\nTeks akhir:\n${feedback.body_markdown}\n\nPublikasi menjadi hasil resmi mahasiswa.`;
    if (!window.confirm(message)) return;
    await mutate(async () => {
      const result = await request<{ id: string; sequence_no: number; published_at: string }>(`/reviews/${review.id}/publish`, csrfToken, "POST", {
        feedback_draft_id: feedback.id,
        expected_revision: expectedReviewRevision(review),
        expected_feedback_revision: feedback.revision,
      });
      setPublications((items) => [{ publication_id: result.id, sequence_no: result.sequence_no, published_at: result.published_at, submission_version_id: activeVersion.id, feedback_draft_id: feedback.id, review_version_id: review.id, status: "released" }, ...items]);
    }, "Hasil resmi berhasil diterbitkan.");
  }

  async function withdraw(targetId: string) {
    if (!targetId || !window.confirm("Tarik publikasi ini dari tampilan mahasiswa? Snapshot lama tetap tersimpan.")) return;
    const reason = window.prompt("Alasan penarikan (wajib):")?.trim();
    if (!reason) return;
    await mutate(() => request(`/publications/${targetId}/withdraw`, csrfToken, "POST", { reason }),
      "Publikasi ditarik untuk koreksi.");
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
    setNotice(`Sumber anotasi revisi ${revision.revision_no} dibuka sesuai versi pengumpulan.`);
  }

  if (!activeVersion) return <p className="notice">Pemeriksaan tersedia setelah pengumpulan difinalisasi.</p>;
  const pendingElements = review?.elements.filter((item) => item.decision.review_status !== "reviewed") ?? [];
  const pendingCandidates = review?.findings.filter((item) => item.action === "pending") ?? [];
  const activeSourceFile = annotationSource?.fileId || selectedEvidence?.file_id || sourceFileId || review?.source_files[0]?.id;
  const selectedDraftBusy = feedback && ["queued", "running"].includes(feedback.generation_status);
  const feedbackUnsaved = dirtyScopes.includes("feedback");
  return <section className="b4-review-workspace" aria-labelledby="b4-review-title" onChangeCapture={(event) => {
    const scope = (event.target as HTMLElement).closest<HTMLElement>("[data-edit-scope]")?.dataset.editScope;
    if (scope && !(event.target as HTMLElement).hasAttribute("data-navigation-control")) markDirty(scope);
  }}>
    <fieldset className="review-edit-lock" aria-label="Isian pemeriksaan" disabled={requestBusy}>
    <header className="workspace-heading"><h2 id="b4-review-title">Pemeriksaan dan feedback</h2>
      <span role="status">{dirtyScopes.length ? "Ada isian belum tersimpan" : "Tidak ada isian belum tersimpan"}</span>
      {dirtyScopes.length > 0 && <button type="button" className="text-button" onClick={() => { if (window.confirm("Batalkan semua isian yang belum tersimpan?")) discardForms(); }}>Batalkan isian</button>}
    </header>
    <nav className="review-stage-nav" aria-label="Tahap pemeriksaan">{([["inspect", "Pemeriksaan"], ["feedback", "Feedback"], ["history", "Riwayat"]] as const).map(([key, label]) => <button key={key} type="button" aria-current={stage === key ? "step" : undefined} className={`button ${stage === key ? "button-primary" : "button-quiet"}`} onClick={() => setStage(key)}>{label}</button>)}</nav>
    {error && <div className="form-error" role="alert"><p>{error.split("\n")[0]}</p>{error.includes("\n") && <details><summary>Detail teknis kendala</summary><pre>{error.split("\n").slice(1).join("\n")}</pre></details>}<button className="button button-quiet" type="button" disabled={busy} onClick={() => setRefreshKey((value) => value + 1)}>Muat ulang data (pertahankan isian)</button></div>}{notice && <p className="form-success" role="status">{notice}</p>}
    {remoteReview && <aside className="notice" role="alert"><h3>Pemeriksaan telah berubah di server</h3><p>Isian lokal belum diganti. Bandingkan dengan keputusan tersimpan sebelum memilih revisi server sebagai dasar. Menyimpan isian lokal setelah itu dapat mengganti keputusan pada bagian yang Anda sunting.</p><details><summary>Lihat keputusan terbaru di server</summary><ul>{remoteReview.elements.map((element) => <li key={element.id}>{element.decision_question}: {reviewLabel(element.decision.resolution ?? "pending")} · {element.decision.reason}</li>)}</ul><p>{remoteReview.lecturer_notes}</p><ul>{remoteReview.limitations.map((text, index) => <li key={index}>{text}</li>)}</ul></details><button className="button button-quiet" type="button" onClick={() => { setCurrentReview(remoteReview); setRemoteReview(null); setRefreshKey((value) => value + 1); }}>Gunakan revisi server sebagai dasar, pertahankan isian lokal</button></aside>}
    {!review && !error && <p role="status">Memuat ruang pemeriksaan…</p>}
    {review && <>
      <section hidden={stage !== "inspect"} className="review-stage">
        <header className="workspace-heading"><p>{reviewLabel(review.state)} · {review.elements.filter((item) => item.decision.review_status === "reviewed").length} dari {review.elements.length} pertanyaan telah ditelaah</p><button className="text-button" type="button" onClick={() => setStage("feedback")}>Lanjut ke feedback →</button></header>
        {review.analysis_run && <p className="notice">{review.analysis_run.provider_mode === "mock" ? "SIMULASI — bukan pemeriksaan semantik provider nyata" : review.analysis_run.provider_mode === "live" ? "Bantuan analisis provider nyata" : "Pemeriksaan tanpa provider"}{review.analysis_run.data_origin === "synthetic" ? " · Data sintetis" : ""}. Keputusan tetap milik dosen.</p>}
        <details className="review-analysis-settings"><summary>Bantuan analisis (opsional)</summary>
          <p>Pemeriksaan manual tetap tersedia. Hasil analisis baru tidak mengganti keputusan yang sudah Anda simpan.</p>
          <label className="field-label">Konfigurasi bantuan analisis<select value={releaseManifestId} onChange={(event) => setReleaseManifestId(event.target.value)} disabled={busy || !runtimeReleases.length}>{runtimeReleases.map((item) => <option key={item.id} value={item.id}>{item.provider_mode === "mock" ? "Simulasi" : item.provider_mode === "off" ? "Tidak tersedia" : "Provider nyata"} · {item.version_label}</option>)}</select></label>
          <p className="help-text">Provider nyata menggunakan budget yang dikonfigurasi dan tetap tunduk pada seluruh pembatas biaya.</p>
          {releaseError && <p role="status">{releaseError}</p>}
          <button className="button button-quiet" type="button" disabled={busy || !releaseManifestId || runtimeReleases.find((item) => item.id === releaseManifestId)?.provider_mode === "off"} onClick={() => void runAnalysis()}>Jalankan analisis</button>
          {analysisStatus && <p role="status">{analysisStatus}</p>}
          {pendingRunId && <button type="button" className="button button-quiet" disabled={busy} onClick={() => {
            if (!window.confirm("Buat pemeriksaan baru dari hasil analisis ini? Riwayat dan keputusan pemeriksaan lama tetap tersimpan.") || !allowDiscard()) return;
            void mutate(async () => {
              const created = await request<Review>(`/submission-versions/${activeVersion.id}/reviews`, csrfToken, "POST", { analysis_run_id: pendingRunId });
              setRequestedReviewId(created.id); setPendingRunId(""); setAnalysisWatchId(""); try { sessionStorage.removeItem(`align:analysis:${activeVersion.id}`); } catch { /* Resume hint is optional. */ } setSelectedElementId(""); setSelectedEvidenceId(""); setAnnotationSource(null); onManualAnchorSelection(null);
            }, "Pemeriksaan baru dibuat dari hasil analisis yang dipilih.");
          }}>Gunakan hasil pada pemeriksaan baru</button>}
          <details><summary>Detail teknis analisis</summary><pre>{JSON.stringify({ releases: runtimeReleases, run: review.analysis_run }, null, 2)}</pre></details>
        </details>
        <nav className="b4-mobile-tabs" role="tablist" aria-label="Panel review">{([["inspect", "Pemeriksaan"], ["evidence", "Bukti"], ["decision", "Keputusan"]] as const).map(([key, label], index) => <button key={key} id={`${reviewTabsId}-tab-${key}`} role="tab" type="button" aria-selected={mode === key} aria-controls={`${reviewTabsId}-panel-${key}`} tabIndex={mode === key ? 0 : -1} className={`button ${mode === key ? "button-primary" : "button-quiet"}`} onClick={() => setMode(key)} onKeyDown={(event) => {
          const modes = ["inspect", "evidence", "decision"] as const;
          const next = event.key === "Home" ? 0 : event.key === "End" ? 2 : event.key === "ArrowRight" ? (index + 1) % 3 : event.key === "ArrowLeft" ? (index + 2) % 3 : null;
          if (next !== null) { event.preventDefault(); setMode(modes[next]); document.getElementById(`${reviewTabsId}-tab-${modes[next]}`)?.focus(); }
        }}>{label}</button>)}</nav>
        <label className="review-resize field-label">Lebar panel pemeriksaan<input aria-label="Lebar panel pemeriksaan" type="range" min={35} max={60} value={split} onChange={(event) => setSplit(Number(event.target.value))} /></label>
        <div className="b4-review-columns" style={{ "--review-left": `${split}fr`, "--review-right": `${100 - split}fr` } as CSSProperties}>
          <section id={`${reviewTabsId}-panel-inspect`} role="tabpanel" tabIndex={0} aria-label="Pemeriksaan" className={`b4-review-panel${mode !== "inspect" ? " b4-mobile-hidden" : ""}`}>
            <h3>Pertanyaan pemeriksaan</h3>
            <label className="field-label">Pilih pertanyaan<select value={selectedElement?.id ?? ""} onChange={(event) => selectElement(event.target.value)}>{review.elements.map((item, index) => <option key={item.id} value={item.id}>{index + 1}. {item.criterion?.title ?? item.decision_question} · {item.decision.review_status === "reviewed" ? "Sudah diperiksa" : "Belum diperiksa"}</option>)}</select></label>
            {selectedElement && <>
              <h4>{selectedElement.criterion?.title ?? "Kriteria pemeriksaan"}</h4><p className="review-decision-question">{selectedElement.decision_question}</p>
              <section aria-label="Kewajiban mahasiswa"><h4>Kewajiban mahasiswa</h4><ul>{selectedElement.requirements.map((item) => <li key={item.id}>{item.text}{item.mandatory ? " (wajib)" : ""}</li>)}</ul></section>
              {selectedElement.analysis && <section className="review-analysis-result" aria-label="Bantuan analisis untuk pertanyaan ini"><h4>Hasil bantuan analisis</h4>
                <p>{selectedElement.analysis.interpretation ?? "Belum ada kesimpulan yang cukup didukung. Periksa sumber sebelum mengambil keputusan."}</p>
                {selectedElement.analysis.grounding?.semantic_status !== "verified" && <p className="notice">Dukungan makna masih memerlukan penelaahan dosen. Kutipan cocok saja tidak membuktikan kesimpulan.</p>}
                {selectedElement.analysis.coverage && <p>Cakupan: {selectedElement.analysis.coverage.inspected_count} dari {selectedElement.analysis.coverage.compatible_count} unit sumber yang sesuai diperiksa; {selectedElement.analysis.coverage.unresolved_count} belum terselesaikan. {selectedElement.analysis.coverage.exhaustive_for_negative ? "Inspeksi tercatat menyeluruh untuk pemeriksaan ketiadaan." : "Ketiadaan belum dibuktikan dari cakupan ini."}</p>}
                <ParserLimitations entries={selectedElement.analysis.coverage?.limitations ?? []} />
                <details><summary>Observasi sumber</summary><ul>{selectedElement.analysis.observations.map((item, index) => <li key={index}>{String(item.text ?? (item.kind === "applicability_task_fact" ? "Fakta tugas terverifikasi digunakan untuk menentukan keberlakuan pemeriksaan." : "Observasi terstruktur; rincian tersedia di detail teknis."))}</li>)}</ul></details>
              </section>}
              <div className="b4-action-card" data-edit-scope="decision">
                <label className="field-label">Keputusan pemeriksaan<select value={resolution} onChange={(event) => setResolution(event.target.value)}><option value="">Pilih keputusan setelah menelaah sumber</option>{["observed_no_issue", "confirmed_issue", "not_applicable", "unable_to_conclude"].map((key) => <option key={key} value={key}>{reviewLabel(key)}</option>)}</select></label>
                <label className="field-label">Alasan atau keterbatasan<textarea maxLength={2000} value={decisionReason} onChange={(event) => setDecisionReason(event.target.value)} /></label>
                <details><summary>Pilih bukti yang diperiksa ({decisionEvidenceIds.length})</summary><fieldset><legend>Bukti sumber</legend>{evidence.map((item) => <label key={item.id} className="check-row"><input type="checkbox" checked={decisionEvidenceIds.includes(item.id)} onChange={() => updateChecklist(setDecisionEvidenceIds, decisionEvidenceIds, item.id)} />{item.file_name} · {anchorText(item.anchor)}</label>)}{!evidence.length && <p>Belum ada bukti hasil ekstraksi. Anda dapat memilih lokasi dari sumber asli atau menambahkan bukti manual di panel Bukti.</p>}</fieldset></details>
                <button className="button button-primary" type="button" disabled={busy || !resolution} onClick={() => void saveElementDecision()}>Simpan keputusan</button>
              </div>
              <details><summary>Detail teknis pertanyaan dan derivasi</summary><pre>{JSON.stringify({ code: selectedElement.code, payload: selectedElement.payload, analysis: selectedElement.analysis }, null, 2)}</pre></details>
            </>}
            <button type="button" className="button button-quiet" aria-expanded={showFinding} onClick={() => setShowFinding((value) => !value)}>Tambahkan temuan</button>
            <section hidden={!showFinding} className="b4-action-card" data-edit-scope="finding">
              <h4>Temuan dosen</h4><label className="field-label">Jenis temuan<select value={findingCategory} onChange={(event) => { setFindingCategory(event.target.value); setNumericValue(null); }}>{["missing_requirement", "rubric_coverage_gap", "numerical_inconsistency"].map((key) => <option key={key} value={key}>{reviewLabel(key)}</option>)}</select></label>
              <label className="field-label">Pernyataan temuan<textarea maxLength={4000} value={findingClaim} onChange={(event) => setFindingClaim(event.target.value)} /></label>
              {findingCategory === "numerical_inconsistency" && <NumericComparisonEditor policies={allowedNumericPolicies} evidence={evidence} value={numericValue} onChange={(value) => { setNumericValue(value); markDirty("finding"); }} />}
              <fieldset><legend>Bukti temuan</legend>{evidence.map((item) => <label className="check-row" key={item.id}><input type="checkbox" checked={findingEvidenceIds.includes(item.id)} onChange={() => updateChecklist(setFindingEvidenceIds, findingEvidenceIds, item.id)} />{item.file_name} · {anchorText(item.anchor)}</label>)}</fieldset>
              <details><summary>Catat pemeriksaan seluruh sumber untuk temuan tentang ketiadaan</summary><p>Pilih berkas yang benar-benar Anda periksa dan jelaskan cakupannya. Hasil pencarian kosong tidak cukup.</p>{review.source_files.map((file) => <label key={file.id} className="check-row"><input type="checkbox" checked={coverageFileIds.includes(file.id)} onChange={() => updateChecklist(setCoverageFileIds, coverageFileIds, file.id)} />{file.name}</label>)}<label className="field-label">Catatan cakupan pemeriksaan<textarea maxLength={2000} value={coverageNote} onChange={(event) => setCoverageNote(event.target.value)} /></label></details>
              <button className="button button-quiet" type="button" disabled={busy || !findingClaim.trim() || (!findingEvidenceIds.length && !(coverageNote.trim() && coverageFileIds.length))} onClick={() => void createManualFinding()}>Simpan temuan</button>
            </section>
          </section>
          <section id={`${reviewTabsId}-panel-evidence`} role="tabpanel" tabIndex={0} aria-label="Bukti" className={`b4-review-panel${mode !== "evidence" ? " b4-mobile-hidden" : ""}`}>
            <h3>Sumber dan bukti</h3>
            <label className="field-label">Berkas asli<select value={activeSourceFile ?? ""} onChange={(event) => { setSourceFileId(event.target.value); setSelectedEvidenceId(""); setAnnotationSource(null); }}>{review.source_files.map((file) => <option value={file.id} key={file.id}>{file.name}</option>)}</select></label>
            {evidence.length > 0 ? <details><summary>Pilih bukti hasil ekstraksi</summary><label className="field-label">Lokasi dan kutipan<select value={selectedEvidenceId} onChange={(event) => { setSelectedEvidenceId(event.target.value); setAnnotationSource(null); }}><option value="">Pilih lokasi bukti</option>{evidence.map((item) => <option value={item.id} key={item.id}>{item.file_name} · {anchorText(item.anchor)} · {String(item.content.text ?? item.content.value ?? "Bukti terstruktur").slice(0, 90)}</option>)}</select></label></details> : <p className="notice">Bukti hasil ekstraksi belum tersedia. Berkas asli tetap menjadi acuan; Anda dapat memeriksa dan mencatat lokasi secara manual.</p>}
            {selectedEvidence && <section><h4>Kutipan sumber</h4><p>{processingLabel(selectedEvidence.parse_status)}</p><pre className="b4-evidence-content">{String(selectedEvidence.content.text ?? selectedEvidence.content.value ?? "Bukti nonteks; lihat sumber asli.")}</pre><ParserLimitations entries={selectedEvidence.limitations} /></section>}
            {annotationSource && <section aria-label="Sumber anotasi terpilih"><p>Lokasi anotasi revisi {annotationSource.revisionNo}: {anchorText(annotationSource.anchor)}</p><button className="text-button" type="button" onClick={() => setAnnotationSource(null)}>Kembali ke sumber aktif</button></section>}
            {activeSourceFile && <SubmissionFileViewer key={`${activeSourceFile}:${annotationSource?.sourceUnitId ?? selectedEvidence?.source_unit_id ?? "original"}`} fileId={activeSourceFile} refreshToken={sourceRefreshKey} initialSourceUnitId={annotationSource?.sourceUnitId ?? selectedEvidence?.source_unit_id} onManualAnchor={(selection) => { if (dirty.current.has("manualEvidence") && !window.confirm("Ganti lokasi bukti manual? Teks isian tetap dipertahankan; periksa kecocokannya dengan lokasi baru sebelum menyimpan.")) return; onManualAnchorSelection(selection); setShowManualEvidence(true); markDirty("manualEvidence"); setNotice("Lokasi dari sumber asli dipilih. Tinjau sebelum menyimpan bukti."); }} />}
            <div className="button-row"><button type="button" className="button button-quiet" aria-expanded={showManualEvidence} onClick={() => setShowManualEvidence((value) => !value)}>Pilih atau tambahkan bukti manual</button><button type="button" className="button button-quiet" aria-expanded={showBinding} onClick={() => setShowBinding((value) => !value)}>Ubah tautan bukti</button></div>
            <div hidden={!showManualEvidence} data-edit-scope="manualEvidence"><ManualEvidenceForm key={`${formReset}:${selectedElement?.id}`} review={review} files={review.source_files} elements={review.elements} sourceSelection={manualAnchorSelection} onSourceSelection={onManualAnchorSelection} csrfToken={csrfToken} busy={busy} getExpectedRevision={() => expectedReviewRevision(review)} relationships={relationships} relationshipCode={activeRelationshipCode} role={activeRole} selectedElementId={selectedElement?.id ?? ""} onElementChange={selectElement} onRelationshipChange={(code, nextRole) => { setSelectedRelationshipCode(code); setSelectedRole(nextRole); }} onSaved={(result) => { markSaved("manualEvidence"); setSelectedEvidenceId(result.evidence_id); updateCurrentReviewRevision(review.id, result.revision, result.content_hash); setRefreshKey((value) => value + 1); }} onBusy={setBusy} onError={setError} onNotice={setNotice} /></div>
            <div hidden={!showBinding} className="b4-action-card" data-edit-scope="annotation"><h4>Tautkan bukti ke pertanyaan</h4><p>{selectedElement?.decision_question}</p>
              {relationships.length > 0 && <><label className="field-label">Hubungan yang diperiksa<select value={activeRelationshipCode} onChange={(event) => { setSelectedRelationshipCode(event.target.value); setSelectedRole(""); }}>{relationships.map((item, index) => <option key={item.relationship_code} value={item.relationship_code}>Hubungan {index + 1} · {relationshipLabel(item.predicate)}</option>)}</select></label><label className="field-label">Peran sumber<select value={activeRole} onChange={(event) => setSelectedRole(event.target.value)}><option value="">Tanpa peran khusus</option>{relationshipRoles.map((role) => <option key={role} value={role}>{roleLabel(role)}</option>)}</select></label></>}
              <label className="field-label">Alasan perubahan<textarea maxLength={2000} value={annotationReason} onChange={(event) => setAnnotationReason(event.target.value)} /></label>
              <SourcePreview file={review.source_files.find((item) => item.id === selectedEvidence?.file_id)} anchor={selectedEvidence?.anchor} version={activeVersion.version_no} />
              <button className="button button-quiet" type="button" disabled={busy || !selectedEvidence || !selectedElement} onClick={() => void createEvidenceBinding()}>Simpan tautan bukti</button>
            </div>
            <button className="text-button" type="button" aria-expanded={showAnnotations} onClick={() => setShowAnnotations((value) => !value)}>Anotasi dan koreksi tersimpan ({review.annotations.length})</button>
            <section hidden={!showAnnotations} data-edit-scope="annotation"><label className="field-label">Alasan anotasi atau koreksi<textarea maxLength={2000} value={annotationReason} onChange={(event) => setAnnotationReason(event.target.value)} /></label>
              {review.annotations.map((annotation) => <article key={annotation.id} className="b4-action-card"><h4>{annotation.kind === "transcription" ? "Koreksi teks" : annotation.kind === "evidence_binding" ? "Tautan bukti" : "Pilihan lokasi"}</h4>
                {annotation.current && <><p>{reviewLabel(annotation.current.operation)} · {anchorText(annotation.current.anchor)}</p><p>{annotation.current.transcription}</p><p>{annotation.current.reason}</p><button className="text-button" type="button" onClick={() => openAnnotationSource(annotation.current!)}>Buka sumber</button>
                  <details><summary>Riwayat perubahan ({annotation.history.length})</summary>{annotation.history.map((revision) => <div key={revision.id}><p>Revisi {revision.revision_no} · {reviewLabel(revision.operation)} · {revision.reason}</p><button className="text-button" type="button" onClick={() => openAnnotationSource(revision)}>Lihat lokasi revisi ini</button></div>)}</details>
                  {annotation.kind === "transcription" && <label className="field-label">Koreksi teks hasil ekstraksi<textarea maxLength={20000} value={annotationTranscription} onChange={(event) => setAnnotationTranscription(event.target.value)} /></label>}
                  <SourcePreview file={review.source_files.find((item) => item.id === (manualAnchorSelection?.file_id ?? selectedEvidence?.file_id ?? annotation.current?.file_id))} anchor={manualAnchorSelection?.anchor ?? selectedEvidence?.anchor ?? annotation.current.anchor} version={activeVersion.version_no} />
                  <div className="button-row"><button className="button button-quiet" type="button" disabled={busy || !annotationReason.trim() || annotation.current.operation === "remove"} onClick={() => { if (window.confirm("Nonaktifkan tautan ini? Temuan yang kehilangan dukungan harus diperiksa ulang sebelum penerbitan.")) void reviseAnnotation(annotation, "remove"); }}>Nonaktifkan tautan</button><button className="button button-quiet" type="button" disabled={busy || annotation.current.operation === "remove"} onClick={() => void reviseAnnotation(annotation, "upsert")}>Simpan koreksi</button></div>
                </>}
              </article>)}
            </section>
          </section>
        </div>
        <section id={`${reviewTabsId}-panel-decision`} role="tabpanel" tabIndex={0} aria-label="Keputusan" className={`b4-review-panel review-decisions${mode !== "decision" ? " b4-mobile-hidden" : ""}`}>
          <h3>Temuan dan penyelesaian pemeriksaan</h3>
          {review.findings.length === 0 && <p>Belum ada temuan. Pemeriksaan tetap dapat diselesaikan tanpa memaksakan adanya masalah.</p>}
          {review.findings.map((finding) => <article className="b4-finding-card" key={finding.id}><h4>{finding.origin === "lecturer" ? "Temuan dosen" : "Kandidat temuan"} · {reviewLabel(finding.category)}</h4><p>{finding.claim}</p><p>{reviewLabel(finding.action)}</p>
            {finding.evidence_ids.map((id) => <button key={id} className="text-button" type="button" onClick={() => { setSelectedFindingId(finding.id); setSelectedEvidenceId(id); setAnnotationSource(null); setMode("evidence"); requestAnimationFrame(() => document.getElementById(`${reviewTabsId}-panel-evidence`)?.focus()); }}>Buka bukti: {evidence.find((item) => item.id === id)?.file_name ?? "sumber tertaut"}</button>)}
            <details><summary>Detail teknis temuan</summary><pre>{JSON.stringify(finding, null, 2)}</pre></details>
            {finding.candidate_id && finding.action === "pending" && <div data-edit-scope="candidate"><label className="field-label">Alasan (wajib jika belum dapat disimpulkan)<textarea maxLength={2000} value={candidateReason} onChange={(event) => setCandidateReason(event.target.value)} /></label><div className="button-row">
              <button className="button button-quiet" type="button" disabled={busy || finding.technical_validation === "invalid"} onClick={() => void candidateAction(finding, "accept")}>Konfirmasi</button><button className="button button-quiet" type="button" disabled={busy} onClick={() => void candidateAction(finding, "reject")}>Tolak</button>
              <button className="button button-quiet" type="button" disabled={busy} onClick={() => { const target = finding.targets.find((item) => item.kind === "element"); if (!allowDiscard()) return; if (target && target.id !== selectedElementId) selectElement(target.id); markDirty("finding"); setCandidateEditId(finding.id); setFindingCategory(finding.category); setFindingClaim(finding.claim); setFindingEvidenceIds(finding.evidence_ids); setSelectedFindingId(finding.id); setNumericValue(null); }}>Sunting dan konfirmasi</button><button className="button button-quiet" type="button" disabled={busy || !candidateReason.trim()} onClick={() => void candidateAction(finding, "defer")}>Belum dapat disimpulkan</button>
            </div>
              {candidateEditId === finding.id && <section data-edit-scope="finding"><label className="field-label">Jenis temuan<select value={findingCategory} onChange={(event) => setFindingCategory(event.target.value)}>{["missing_requirement", "rubric_coverage_gap", "numerical_inconsistency"].map((key) => <option key={key} value={key}>{reviewLabel(key)}</option>)}</select></label><label className="field-label">Pernyataan temuan<textarea value={findingClaim} maxLength={4000} onChange={(event) => setFindingClaim(event.target.value)} /></label><fieldset><legend>Bukti yang mendukung</legend>{evidence.map((item) => <label className="check-row" key={item.id}><input type="checkbox" checked={findingEvidenceIds.includes(item.id)} onChange={() => updateChecklist(setFindingEvidenceIds, findingEvidenceIds, item.id)} />{item.file_name} · {anchorText(item.anchor)}</label>)}</fieldset>{findingCategory === "numerical_inconsistency" && <NumericComparisonEditor policies={allowedNumericPolicies} evidence={evidence} value={numericValue} onChange={(value) => { setNumericValue(value); markDirty("finding"); }} />}<button className="button button-primary" type="button" disabled={busy || !findingClaim.trim()} onClick={() => void candidateAction(finding, "edit")}>Simpan dan konfirmasi</button></section>}
            </div>}
          </article>)}
          <details><summary>Catatan dosen dan keterbatasan pemeriksaan</summary><div data-edit-scope="notes"><label className="field-label">Catatan internal dosen<textarea maxLength={20000} value={notes} onChange={(event) => setNotes(event.target.value)} /></label><label className="field-label">Keterbatasan yang perlu disampaikan (satu per baris)<textarea value={limitationsText} onChange={(event) => setLimitationsText(event.target.value)} /></label><button className="button button-quiet" type="button" disabled={busy} onClick={() => void saveNotes()}>Simpan catatan</button></div></details>
          {(pendingElements.length > 0 || pendingCandidates.length > 0) && <p className="notice">Masih ada {pendingElements.length} pertanyaan dan {pendingCandidates.length} kandidat yang belum diputuskan.</p>}
          <button className="button button-primary" type="button" disabled={busy || review.state === "complete" || dirtyScopes.length > 0 || pendingElements.length > 0 || pendingCandidates.length > 0} onClick={() => void mutate(() => request(`/reviews/${review.id}/complete`, csrfToken, "POST", { expected_revision: expectedReviewRevision(review) }), "Pemeriksaan diselesaikan. Anda dapat menyusun feedback.")}>Selesaikan pemeriksaan</button>
        </section>
      </section>
      <section hidden={stage !== "feedback"} className="review-stage"><h3>Feedback mahasiswa</h3>
        {review.state !== "complete" ? <div className="notice"><p>Selesaikan pemeriksaan terlebih dahulu. Masih ada {pendingElements.length} pertanyaan dan {pendingCandidates.length} kandidat yang perlu diputuskan. Keterbatasan yang menentukan juga harus dicatat.</p><button className="button button-quiet" type="button" onClick={() => { setStage("inspect"); setMode("decision"); }}>Kembali ke pemeriksaan</button></div> : <>
          {feedbackStale && <p role="alert" className="notice">Keputusan sumber telah berubah. Draf lama tidak dapat disahkan atau diterbitkan; buat draf baru dari pemeriksaan terkini.</p>}
          <div data-edit-scope="feedback" className="feedback-editor"><fieldset><legend>Temuan untuk mahasiswa</legend>{confirmedFindings.map((finding) => <div key={finding.id}><label className="check-row"><input type="checkbox" checked={includedFindingIds.includes(finding.id)} onChange={() => updateChecklist(setIncludedFindingIds, includedFindingIds, finding.id)} />{finding.claim}</label>{!includedFindingIds.includes(finding.id) && <label className="field-label">Alasan tidak menyertakan temuan<textarea maxLength={2000} value={exclusionReasons[finding.id] ?? ""} onChange={(event) => setExclusionReasons((current) => ({ ...current, [finding.id]: event.target.value }))} /></label>}</div>)}{!confirmedFindings.length && <p>Tidak ada masalah yang dikonfirmasi. Gunakan template netral beserta keterbatasan pemeriksaan.</p>}</fieldset>
            <label className="field-label">Teks feedback<textarea maxLength={20000} value={manualFeedback} readOnly={feedback?.state === "approved" || Boolean(selectedDraftBusy)} onChange={(event) => setManualFeedback(event.target.value)} /></label>
            <div className="button-row"><button className="button button-quiet" type="button" disabled={busy} onClick={() => { if (!feedbackUnsaved || window.confirm("Ganti isian feedback dengan draf template baru?")) void createFeedback("template"); }}>Susun dari template</button><button className="button button-quiet" type="button" disabled={busy || !manualFeedback.trim() || !includedFindingIds.length} onClick={() => void createFeedback("manual")}>Buat draf manual baru</button>{review.analysis_run && review.analysis_run.provider_mode !== "off" && <button className="button button-quiet" type="button" disabled={busy || !includedFindingIds.length} onClick={() => { if (window.confirm("Susun draf dengan bantuan analisis? Provider nyata memakai budget yang dikonfigurasi.")) void createFeedback("model"); }}>{review.analysis_run.provider_mode === "mock" ? "Susun draf simulasi" : "Susun draf dengan AI"}</button>}</div>
            {feedback && <><p>Draf {feedback.generation_mode === "model" ? "bantuan analisis" : feedback.generation_mode === "template" ? "template" : "manual"} · {reviewLabel(feedback.state)} · revisi {feedback.revision}</p>{selectedDraftBusy && <p role="status">Draf masih diproses. Anda dapat membuka ulang halaman untuk melihat hasilnya.</p>}{feedback.generation_status === "fallback" && <p className="notice">Bantuan analisis tidak tersedia. Draf menggunakan template; silakan periksa teksnya.</p>}
              {feedback.state === "draft" && <button className="button button-quiet" type="button" disabled={busy || feedbackStale || Boolean(selectedDraftBusy)} onClick={() => void updateFeedbackDraft()}>Simpan suntingan feedback</button>}
              <details><summary>Pratinjau teks tersimpan</summary><pre className="b4-evidence-content">{feedback.body_markdown}</pre></details>
              <div className="button-row"><button className="button button-quiet" type="button" disabled={busy || feedback.state !== "draft" || Boolean(selectedDraftBusy) || feedbackStale || dirtyScopes.length > 0} onClick={() => void approveFeedback()}>Setujui feedback</button><button className="button button-primary" type="button" disabled={busy || feedback.state !== "approved" || feedbackStale || dirtyScopes.length > 0 || Boolean(publicationId)} onClick={() => void publish()}>Terbitkan hasil</button></div>
              {publicationId && <p className="notice">Hasil untuk versi pengumpulan ini telah diterbitkan. Lihat atau tarik melalui Riwayat.</p>}
            </>}
          </div>
        </>}
      </section>
      <section hidden={stage !== "history"} className="review-stage"><h3>Riwayat tersimpan</h3>
        <h4>Pemeriksaan</h4><ul className="review-history-list">{reviewHistory.map((item) => <li key={item.id}>Pemeriksaan {item.version_no} · {reviewLabel(item.state)} · {item.analysis_run_id ? "dengan bantuan analisis" : "manual"}<button className="text-button" type="button" onClick={() => { if (!allowDiscard()) return; setRequestedReviewId(item.id); setSelectedElementId(""); setSelectedEvidenceId(""); setAnnotationSource(null); onManualAnchorSelection(null); setNotice(""); setRefreshKey((value) => value + 1); setStage("inspect"); }}>Buka pemeriksaan</button></li>)}</ul>
        <h4>Draf feedback pemeriksaan ini</h4>{!feedbackHistory.length && <p>Belum ada draf tersimpan.</p>}<ul className="review-history-list">{feedbackHistory.map((item) => <li key={item.id}>{reviewLabel(item.state)} · {item.generation_mode === "model" ? "Bantuan analisis" : item.generation_mode === "template" ? "Template" : "Manual"}{item.stale ? " · Sumber berubah" : ""}<button className="text-button" type="button" onClick={() => { if (!allowDiscard()) return; setFeedback(item); setManualFeedback(item.body_markdown); setIncludedFindingIds(item.included_review_finding_ids); setExclusionReasons(item.exclusion_reasons); setStage("feedback"); }}>Lihat draf</button></li>)}</ul>
        {feedbackCursor !== null && <button className="button button-quiet" type="button" disabled={busy} onClick={() => void mutate(async () => { const page = await request<{ items: Feedback[]; next_cursor: number | null }>(`/reviews/${review.id}/feedback-drafts?cursor=${feedbackCursor}`, csrfToken); setFeedbackHistory((items) => [...items, ...page.items]); setFeedbackCursor(page.next_cursor); }, "Draf sebelumnya dimuat.", false)}>Muat draf sebelumnya</button>}
        <h4>Hasil yang diterbitkan</h4>{!publications.length && <p>Belum ada hasil diterbitkan.</p>}<ul className="review-history-list">{publications.map((item) => <li key={item.publication_id}>Publikasi {item.sequence_no} · {reviewLabel(item.status)} · {new Date(item.published_at).toLocaleString("id-ID")}<button className="text-button" type="button" onClick={() => void mutate(async () => setPublicationPreview(await request<Record<string, unknown>>(`/publications/${item.publication_id}`, csrfToken)), "Hasil tersimpan dibuka.", false)}>Lihat hasil tersimpan</button>{item.status === "released" && <button className="text-button" type="button" onClick={() => void withdraw(item.publication_id)}>Tarik untuk koreksi</button>}</li>)}</ul>
        {publicationPreview && <PublicationPreview value={publicationPreview} />}
      </section>
    </>}
    </fieldset>
  </section>;
}

function SourcePreview({ file, anchor, version }: { file?: SourceFile; anchor?: Record<string, unknown>; version: number }) {
  if (!file) return <p>Pilih sumber terlebih dahulu.</p>;
  return <aside className="review-source-preview"><p>{file.name} · pengumpulan versi {version} · {anchor ? anchorText(anchor) : "Lokasi seluruh berkas"}</p><p className="help-text">Fingerprint sumber: {file.sha256.slice(0, 12)}</p><details><summary>Detail teknis sumber</summary><p>SHA-256: {file.sha256}</p><pre>{JSON.stringify(anchor, null, 2)}</pre></details></aside>;
}
function PublicationPreview({ value }: { value: Record<string, unknown> }) {
  const snapshot = value.snapshot as { feedback?: { body_markdown?: string }; limitations?: string[]; source?: { version_no?: number }; confirmed_findings?: Array<{ claim: string }> } | undefined;
  return <article className="b4-action-card"><h4>Hasil resmi tersimpan</h4><p>{reviewLabel(String(value.status))} · versi sumber {snapshot?.source?.version_no ?? "tercatat dalam snapshot"}</p><pre className="b4-evidence-content">{snapshot?.feedback?.body_markdown ?? "Teks hasil tidak tersedia."}</pre><ul>{snapshot?.limitations?.map((text, index) => <li key={index}>{text}</li>)}</ul><details><summary>Detail teknis publikasi</summary><pre>{JSON.stringify(value, null, 2)}</pre></details></article>;
}
function roleLabel(value: string): string { return ({ content: "Isi", relation: "Hubungan antarbukti", left: "Sumber pertama", right: "Sumber pembanding", basis: "Dasar pembandingan", claim: "Pernyataan", support: "Pendukung", method: "Metode", application: "Penerapan", artifact: "Hasil pekerjaan", trace: "Jejak pendukung" } as Record<string, string>)[value] ?? "Peran bukti"; }
function relationshipLabel(value: string): string { return ({ covers: "Cakupan", compares: "Perbandingan", supports: "Dukungan", applies: "Penerapan", traces_to: "Keterlacakan", reports_same_quantity: "Pelaporan besaran yang sama" } as Record<string, string>)[value] ?? "Hubungan bukti"; }

function ManualEvidenceForm({ review, files, elements, selectedElementId, onElementChange, sourceSelection, onSourceSelection, csrfToken, busy, relationships, relationshipCode, role, onRelationshipChange, getExpectedRevision, onSaved, onBusy, onError, onNotice }: {
  review: Review; files: SourceFile[]; elements: ReviewElement[]; csrfToken: string; busy: boolean;
  selectedElementId: string; onElementChange: (id: string) => void;
  relationships: Relationship[]; relationshipCode: string; role: string; onRelationshipChange: (code: string, role: string) => void;
  getExpectedRevision: () => number;
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
        expected_revision: getExpectedRevision(),
      });
      onNotice(sourceSelection
        ? "Evidence manual ditambahkan dari lokasi unit sumber yang diperiksa."
        : "Evidence manual ditambahkan dengan whole-file anchor; gunakan hanya karena lokasi lebih rinci belum dapat diverifikasi.");
      setTranscription(""); setReason(""); onSaved(result);
    } catch (cause) { onError(cause instanceof Error ? cause.message : "Bukti manual belum dapat disimpan."); }
    finally { onBusy(false); }
  }
  return <fieldset className="b4-action-card" disabled={busy}>
    <legend>Tambahkan bukti dari sumber asli</legend>
    <h5>Tambahkan bukti dari sumber asli</h5>
    <p className="help-text">Jika lokasi spesifik belum tersedia, catat lokasi berkas secara manual. Bukti ini ditandai berlokasi kasar. Koreksi teks tidak mengubah sumber asli.</p>
    <label className="field-label">Berkas final<select value={sourceSelection?.file_id ?? file?.id ?? ""} onChange={(event) => { setFileId(event.target.value); onSourceSelection(null); }}>{files.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    {sourceSelection ? <p className="form-success">Lokasi sumber terpilih: {anchorText(sourceSelection.anchor)} <button type="button" className="text-button" onClick={() => onSourceSelection(null)}>Batalkan pilihan</button></p>
      : <label className="field-label">Catatan lokasi seluruh berkas (gunakan hanya bila lokasi spesifik belum dapat diverifikasi)<textarea maxLength={240} value={locatorNote} onChange={(event) => setLocatorNote(event.target.value)} /></label>}
    <details><summary>Koreksi teks hasil ekstraksi (opsional)</summary><label className="field-label">Teks hasil koreksi (maks. 20.000 karakter)<textarea maxLength={20000} value={transcription} onChange={(event) => setTranscription(event.target.value)} /></label>
    {transcription.trim() && <label className="field-label">Alasan koreksi<textarea maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>}</details>
    <SourcePreview file={file} anchor={sourceSelection?.anchor ?? { format: "file", locator_note: locatorNote }} version={review.submission_version} />
    <label className="field-label">Pertanyaan yang didukung<select data-navigation-control value={selectedElementId} onChange={(event) => onElementChange(event.target.value)}>{elements.map((item) => <option key={item.id} value={item.id}>{item.criterion?.title ?? item.decision_question}</option>)}</select></label>
    {relationships.length > 0 && <>
      <label className="field-label">Hubungan bukti<select value={relationships.some((item) => item.relationship_code === relationshipCode) ? relationshipCode : relationships[0].relationship_code} onChange={(event) => onRelationshipChange(event.target.value, "")}>{relationships.map((item) => <option key={item.relationship_code} value={item.relationship_code}>{relationshipLabel(item.predicate)}</option>)}</select></label>
      <label className="field-label">Peran sumber<select value={role} onChange={(event) => onRelationshipChange(relationshipCode || relationships[0].relationship_code, event.target.value)}><option value="">Tidak diikat ke role</option>{rolesForPredicate(relationships.find((item) => item.relationship_code === relationshipCode)?.predicate ?? relationships[0].predicate).map((item) => <option key={item} value={item}>{roleLabel(item)}</option>)}</select></label>
    </>}
    <button className="button button-quiet" type="button" disabled={busy || !file || !locatorNote.trim() || (Boolean(transcription.trim()) && !reason.trim())} onClick={() => void submit()}>Simpan bukti manual</button>
  </fieldset>;
}

function anchorText(anchor: Record<string, unknown>): string {
  if (anchor.format === "pdf") return `PDF hlm. ${String(anchor.page ?? "?")}`;
  if (anchor.format === "xlsx") return `${String(anchor.sheet_name ?? "")}:${String(anchor.cell_range ?? "")}`;
  if (anchor.format === "docx") return sourceLocation(anchor);
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

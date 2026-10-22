"use client";

import { useCallback, useEffect, useMemo, useState, type ChangeEvent } from "react";
import type { ApiMethod, ApiRequestPath } from "@/lib/api-contract.generated";
import { CriteriaStructuredEditor, NumericPolicyStructuredEditor, SpecificationStructuredEditor } from "./AssignmentStructuredEditors";

type ContextSummary = { id: string; version_no: number; state: string; content_hash: string | null };
type AssignmentSummary = {
  id: string;
  title: string;
  administrative_state?: string;
  revision?: number;
  active_version_id?: string | null;
  draft_version_id?: string | null;
  version_no?: number | null;
  version_state?: string | null;
  draft_version_no?: number | null;
  due_at?: string;
};
type RubricSource = { id: string; name: string; sha256: string };
type CriterionSet = {
  id: string;
  revision: number;
  state: string;
  content_hash: string | null;
  criteria: Array<Record<string, unknown>>;
};
type LintReport = {
  id: string;
  source_spec_revision: number;
  effective_status: string;
  resolution_revision: number;
  issues: Array<{
    issue_code: string;
    rule_code: string;
    severity: string;
    message: string;
    element_ids: string[];
  }>;
  resolutions: Array<{ issue_code: string; reason: string }>;
};
type Specification = {
  id: string;
  version_no: number;
  schema_version: number;
  revision: number;
  state: string;
  payload: Record<string, unknown>;
  lint: LintReport | null;
};
type AssignmentVersion = {
  id: string;
  assignment_id: string;
  version_no: number;
  state: string;
  revision: number;
  context_version_id: string;
  instruction_original: string;
  instruction_hash: string;
  instruction_summary: string | null;
  mode: "individual" | "group";
  opens_at: string;
  due_at: string;
  late_policy: "accept_with_label" | "reject_after_due";
  limits: Record<string, unknown>;
  task_facts: {
    facts?: Record<string, { value?: unknown; origin?: string; evidence_ids?: string[]; verification?: string }>;
    content_hash?: string;
  };
  rubric_sources?: RubricSource[];
  criterion_set: CriterionSet | null;
  specification: Specification | null;
  numeric_policies: Array<Record<string, unknown>>;
  package_approved_at: string | null;
};
type AssignmentDetail = {
  id: string;
  title: string;
  administrative_state: string;
  revision: number;
  active_version_id: string | null;
  version: AssignmentVersion | null;
};
type UploadedRubric = RubricSource & { purpose: "rubric" };
type RubricEvidence = {
  id: string;
  type: string;
  anchor: Record<string, unknown>;
  text: string;
  text_truncated: boolean;
  source_sha256: string;
  parse_status: string;
  limitations: Array<Record<string, unknown>>;
};
type RubricParse = {
  assignment_version_id: string;
  status: string;
  job_id: string | null;
  files: Array<{
    id: string;
    name: string;
    sha256: string;
    parse_status: string;
    limitations: Array<Record<string, unknown>>;
    evidence_count: number;
    evidence_truncated: boolean;
    evidence: RubricEvidence[];
  }>;
};
type CriteriaProposal = {
  proposal_code: string;
  source_kind: "instruction";
  source_id: string;
  source_span: { start: number; end: number };
  source_hash: string;
  quote: string;
  mandatory: null;
  status: "needs_lecturer_decision";
};
type ProviderDraftJob = {
  id: string;
  type: "criteria_draft" | "specification_draft";
  status: string;
  stage: string | null;
  generation: {
    status?: string;
    provider_mode?: string;
    simulated?: boolean;
    label?: string | null;
    suggestions?: Array<{ title: string; description: string; source_refs: Array<{ source_kind: string; quote: string }> }>;
    limitations?: string[];
  };
  limitation_code?: string | null;
};
type IssueEnvelope = { error?: { message?: string; field_errors?: Array<{ field: string }> } };
type PublicPreview = {
  title: string;
  version: {
    version_no: number;
    instruction_original: string;
    instruction_summary: string | null;
    instruction_summary_label: string | null;
    mode: string;
    opens_at: string;
    due_at: string;
    late_policy: string;
    criteria: Array<{ code: string; title: string; descriptor: string; requirements: Array<{ code: string; text: string; mandatory: boolean }> }>;
    accepted_evidence: Array<{ criterion_code: string | null; accepted_evidence_types: string[] }>;
  };
};

async function apiRequest<T>(
  path: ApiRequestPath,
  csrfToken: string,
  options: { method: ApiMethod; body?: unknown; form?: FormData; idempotency?: boolean },
): Promise<T> {
  const headers = new Headers({ "X-CSRF-Token": csrfToken });
  let body: BodyInit | undefined;
  if (options.form) body = options.form;
  else if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.body);
  }
  if (options.idempotency) headers.set("Idempotency-Key", crypto.randomUUID());
  const response = await fetch(`/api/v1${path}`, {
    method: options.method,
    headers,
    body,
    cache: "no-store",
  });
  const payload = (await response.json().catch(() => ({}))) as IssueEnvelope;
  if (!response.ok) {
    const fields = payload.error?.field_errors?.map((item) => item.field.replace(/^body\./, "")).join(", ");
    throw new Error(`${payload.error?.message ?? `Permintaan gagal (${response.status}).`}${fields ? ` Periksa: ${fields}.` : ""}`);
  }
  return payload as T;
}

function formatLocalDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function defaultDate(offsetDays: number): string {
  const value = new Date(Date.now() + offsetDays * 86_400_000);
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function requiredSpecTemplate(criteria: Array<Record<string, unknown>>): Record<string, unknown> {
  const elements: Array<Record<string, unknown>> = [];
  const evidenceTypes = ["paragraph", "table", "diagram", "image_text", "spreadsheet_cell", "spreadsheet_range"];
  const coverage = {
    positive_strategy: "sufficient_support",
    negative_strategy: "exhaustive_compatible_source_units",
    inspect_embedded_visuals: true,
    unresolved_compatible_unit_blocks_negative: true,
    preserve_table_context: true,
  };
  for (const criterion of criteria) {
    const criterionId = String(criterion.id ?? "");
    const requirements = Array.isArray(criterion.requirements)
      ? (criterion.requirements as Array<Record<string, unknown>>)
      : [];
    if (requirements.length) {
      for (const requirement of requirements) {
        const requirementId = String(requirement.id ?? "");
        const code = String(requirement.code ?? "REQ").replace(/[^A-Za-z0-9._-]/g, "-");
        const obligationKey = String(requirement.obligation_key ?? `manual.${code}`);
        elements.push({
          element_id: crypto.randomUUID(),
          element_code: `EL-${code}`.slice(0, 80),
          criterion_id: criterionId,
          requirement_ids: [requirementId],
          obligation_key: obligationKey,
          target_kind: "requirement",
          decision_question: `Apakah bukti relevan menjawab requirement ${code}?`,
          derivation_refs: [{ source_kind: "requirement", source_id: requirementId, span: null }],
          expected_evidence: `Bukti yang relevan terhadap requirement ${code}.`,
          grounding_contract: { schema_version: 1, mode: "human_only", absence_support: null },
          accepted_evidence_types: evidenceTypes,
          relationships: [],
          applicability: { mode: "always" },
          source_hints: [],
          operators: ["locate_evidence", "expand_context", "inspect_source_coverage", "request_human_review"],
          dependencies: [],
          coverage_policy: coverage,
          abstention_conditions: ["unreadable_relevant_region", "incomplete_source_inspection"],
          numeric_policy_version_id: null,
          human_judgment_boundary: "Keputusan semantik dan mutu akademik tetap dilakukan dosen; elemen ini tidak membuat keputusan otomatis.",
        });
      }
    } else {
      const descriptor = String(criterion.descriptor ?? "");
      const code = String(criterion.code ?? "CRITERION").replace(/[^A-Za-z0-9._-]/g, "-");
      const criterionCodepoints = Array.from(descriptor).length;
      elements.push({
        element_id: crypto.randomUUID(),
        element_code: `EL-${code}`.slice(0, 80),
        criterion_id: criterionId,
        requirement_ids: [],
        obligation_key: `descriptor.${code}`,
        target_kind: "descriptor",
        decision_question: `Apakah bukti relevan menjawab deskripsi resmi ${code}?`,
        derivation_refs: [{ source_kind: "criterion_descriptor", source_id: criterionId, span: { start: 0, end: criterionCodepoints } }],
        expected_evidence: `Bukti yang relevan terhadap deskripsi resmi ${code}.`,
        grounding_contract: { schema_version: 1, mode: "human_only", absence_support: null },
        accepted_evidence_types: evidenceTypes,
        relationships: [],
        applicability: { mode: "always" },
        source_hints: [],
        operators: ["locate_evidence", "expand_context", "inspect_source_coverage", "request_human_review"],
        dependencies: [],
        coverage_policy: coverage,
        abstention_conditions: ["unreadable_relevant_region", "incomplete_source_inspection"],
        numeric_policy_version_id: null,
        human_judgment_boundary: "Keputusan semantik dan mutu akademik tetap dilakukan dosen; elemen ini tidak membuat keputusan otomatis.",
      });
    }
  }
  return {
    schema_version: 2,
    elements,
    operator_registry_version: "operators-v1",
    predicate_registry_version: "predicates-v1",
    grounding_registry_version: "grounding-v1",
    lint_registry_version: "lint-v1",
    task_facts: [],
    limits: {
      draft_max_cost_usd: "0.05",
      max_text_calls: 3,
      max_vision_calls: 2,
      max_input_tokens: 20000,
      max_output_tokens: 4000,
    },
  };
}

export function AssignmentWorkspace({
  course,
  csrfToken,
  approvedContexts,
  initialAssignments,
  contextsUnavailable = false,
}: {
  course: { id: string; code: string; title: string };
  csrfToken: string;
  approvedContexts: ContextSummary[];
  initialAssignments: AssignmentSummary[];
  contextsUnavailable?: boolean;
}) {
  const [assignments, setAssignments] = useState(initialAssignments);
  const [detail, setDetail] = useState<AssignmentDetail | null>(null);
  const [contextId, setContextId] = useState(approvedContexts[0]?.id ?? "");
  const [newTitle, setNewTitle] = useState("");
  const [instruction, setInstruction] = useState("");
  const [summary, setSummary] = useState("");
  const [mode, setMode] = useState<"individual" | "group">("individual");
  const [opensAt, setOpensAt] = useState(defaultDate(0));
  const [dueAt, setDueAt] = useState(defaultDate(7));
  const [latePolicy, setLatePolicy] = useState<"accept_with_label" | "reject_after_due">("accept_with_label");
  const [criteriaJson, setCriteriaJson] = useState("[]");
  const [generateExtractiveProposals, setGenerateExtractiveProposals] = useState(false);
  const [criteriaProposals, setCriteriaProposals] = useState<CriteriaProposal[]>([]);
  const [providerDraftJobId, setProviderDraftJobId] = useState<string | null>(null);
  const [providerDraftJob, setProviderDraftJob] = useState<ProviderDraftJob | null>(null);
  const [specJson, setSpecJson] = useState("");
  const [rubricFiles, setRubricFiles] = useState<UploadedRubric[]>([]);
  const [rubricParse, setRubricParse] = useState<RubricParse | null>(null);
  const [selectedRubricIds, setSelectedRubricIds] = useState<string[]>([]);
  const [publishPreview, setPublishPreview] = useState<PublicPreview | null>(null);
  const [numericKey, setNumericKey] = useState("numeric-comparison");
  const [taskFactValues, setTaskFactValues] = useState<Record<string, string>>({});
  const [taskFactOrigins, setTaskFactOrigins] = useState<Record<string, string>>({});
  const [numericJson, setNumericJson] = useState(JSON.stringify({
    metric_definition: "Besaran yang harus dibandingkan dalam tugas ini.",
    required_context_fields: ["metric", "entity", "definition", "unit"],
    number_locale: "id-ID",
    canonical_unit: "IDR",
    allowed_conversions: [],
    comparison_mode: "pairwise",
    absolute_tolerance: "0",
    relative_tolerance: "0",
    rounding: { mode: "none", quantum: null },
    operand_roles: { left: "observasi kiri", right: "observasi kanan" },
  }, null, 2));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const version = detail?.version ?? null;
  const criterionSet = version?.criterion_set ?? null;
  const specification = version?.specification ?? null;
  const latestLint = specification?.lint ?? null;
  const editable = version?.state === "draft";
  const hasApprovedContext = approvedContexts.length > 0;
  const criteriaDraft = useMemo(() => {
    try {
      const value = JSON.parse(criteriaJson) as unknown;
      return Array.isArray(value) ? value as Array<Record<string, unknown>> : [];
    } catch {
      return [];
    }
  }, [criteriaJson]);
  const specDraft = useMemo(() => {
    try {
      const value = JSON.parse(specJson) as unknown;
      return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
    } catch {
      return {};
    }
  }, [specJson]);
  const numericDraft = useMemo(() => {
    try {
      const value = JSON.parse(numericJson) as unknown;
      return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
    } catch {
      return {};
    }
  }, [numericJson]);

  useEffect(() => {
    const versionId = version?.id;
    if (!versionId) {
      return;
    }
    let active = true;
    let timer: number | undefined;
    async function refresh() {
      try {
        const result = await apiRequest<RubricParse>(
          `/assignment-versions/${versionId}/rubric-parse`, csrfToken, { method: "GET" },
        );
        if (active) {
          setRubricParse(result);
          if (editable && (result.status === "queued" || result.status === "running" || result.status === "blocked")) {
            timer = window.setTimeout(() => void refresh(), 1500);
          }
        }
      } catch {
        if (active) setRubricParse(null);
      }
    }
    void refresh();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [version?.id, editable, csrfToken, rubricParse?.status]);

  useEffect(() => {
    if (!providerDraftJobId) return;
    let active = true;
    let timer: number | undefined;
    async function refreshDraftJob() {
      try {
        const result = await apiRequest<ProviderDraftJob>(
          `/assignment-draft-jobs/${providerDraftJobId}`, csrfToken, { method: "GET" },
        );
        if (!active) return;
        setProviderDraftJob(result);
        if (["queued", "running"].includes(result.status)) {
          timer = window.setTimeout(() => void refreshDraftJob(), 1200);
        }
      } catch {
        if (active) setProviderDraftJob(null);
      }
    }
    void refreshDraftJob();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [providerDraftJobId, csrfToken]);

  const refreshAssignments = useCallback(async () => {
    const response = await apiRequest<{ items: AssignmentSummary[] }>(
      `/courses/${course.id}/assignments`, csrfToken, { method: "GET" },
    );
    setAssignments(response.items);
  }, [course.id, csrfToken]);

  const loadAssignment = useCallback(async (assignmentId: string) => {
    setError("");
    setMessage("");
    setRubricParse(null);
    setProviderDraftJobId(null);
    setProviderDraftJob(null);
    setPublishPreview(null);
    const loaded = await apiRequest<AssignmentDetail>(
      `/assignments/${assignmentId}`, csrfToken, { method: "GET" },
    );
    setDetail(loaded);
    const loadedVersion = loaded.version;
    if (loadedVersion) {
      setInstruction(loadedVersion.instruction_original);
      setSummary(loadedVersion.instruction_summary ?? "");
      setMode(loadedVersion.mode);
      setOpensAt(formatLocalDateTime(loadedVersion.opens_at));
      setDueAt(formatLocalDateTime(loadedVersion.due_at));
      setLatePolicy(loadedVersion.late_policy);
      setCriteriaJson(JSON.stringify(loadedVersion.criterion_set?.criteria ?? [], null, 2));
      setCriteriaProposals([]);
      setSelectedRubricIds((loadedVersion.rubric_sources ?? []).map((item) => item.id));
      setSpecJson(JSON.stringify(loadedVersion.specification?.payload ?? {}, null, 2));
      const confirmedFacts = loadedVersion.task_facts?.facts ?? {};
      setTaskFactValues(Object.fromEntries(Object.entries(confirmedFacts).map(([key, fact]) => [key, String(fact.value ?? "")])));
      setTaskFactOrigins(Object.fromEntries(Object.entries(confirmedFacts).map(([key, fact]) => [key, String(fact.origin ?? "")])));
    }
  }, [csrfToken]);

  const execute = async (action: () => Promise<void>, success: string) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
      setMessage(success);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Permintaan gagal.");
    } finally {
      setBusy(false);
    }
  };

  const createAssignment = () => execute(async () => {
    const created = await apiRequest<{ id: string }>("/assignments", csrfToken, {
      method: "POST",
      idempotency: true,
      body: { course_id: course.id, title: newTitle, context_version_id: contextId },
    });
    await refreshAssignments();
    await loadAssignment(created.id);
    setNewTitle("");
  }, "Draf tugas dibuat.");

  const saveVersion = () => execute(async () => {
    if (!version) return;
    const saved = await apiRequest<AssignmentVersion>(`/assignment-versions/${version.id}`, csrfToken, {
      method: "PUT",
      idempotency: true,
      body: {
        expected_revision: version.revision,
        instruction_original: instruction,
        instruction_summary: summary || null,
        mode,
        opens_at: new Date(opensAt).toISOString(),
        due_at: new Date(dueAt).toISOString(),
        late_policy: latePolicy,
      },
    });
    setDetail((current) => current ? { ...current, version: saved } : current);
    setCriteriaJson(JSON.stringify(saved.criterion_set?.criteria ?? [], null, 2));
    setSpecJson(JSON.stringify(saved.specification?.payload ?? {}, null, 2));
  }, "Versi tugas tersimpan; instruksi disimpan utuh dengan normalisasi CRLF→LF saja.");

  const uploadRubric = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || !detail) return;
    void execute(async () => {
      const form = new FormData();
      form.set("file", file);
      const response = await apiRequest<{ asset: UploadedRubric }>(
        `/assignments/${detail.id}/rubric-files`, csrfToken,
        { method: "POST", form, idempotency: true },
      );
      setRubricFiles((current) => [...current.filter((item) => item.id !== response.asset.id), response.asset]);
      setSelectedRubricIds((current) => current.includes(response.asset.id) ? current : [...current, response.asset.id]);
    }, "File rubrik tersimpan privat. File belum diparse; descriptor resmi harus disalin dan diperiksa dosen.");
    event.target.value = "";
  };

  const openCriteriaDraft = () => execute(async () => {
    if (!version) return;
    const response = await apiRequest<{
      criterion_set_version_id: string;
      revision: number;
      criteria: Array<Record<string, unknown>>;
      rubric_sources: RubricSource[];
      generation: { proposals: CriteriaProposal[]; status: string; provider_draft_job_id?: string | null };
    }>(
      `/assignment-versions/${version.id}/criteria-drafts`, csrfToken,
      { method: "POST", idempotency: true, body: { rubric_file_ids: selectedRubricIds, generate_from_instruction: generateExtractiveProposals } },
    );
    setCriteriaProposals(response.generation.proposals);
    setProviderDraftJobId(response.generation.provider_draft_job_id ?? null);
    setProviderDraftJob(null);
    const parsedRubrics = await apiRequest<RubricParse>(
      `/assignment-versions/${version.id}/rubric-parse`, csrfToken, { method: "GET" },
    );
    setRubricParse(parsedRubrics);
    setDetail((current) => current?.version ? {
      ...current,
      version: {
        ...current.version,
        criterion_set: {
          id: response.criterion_set_version_id,
          revision: response.revision,
          state: "draft",
          content_hash: null,
          criteria: response.criteria,
        },
        revision: current.version.revision + (current.version.rubric_sources?.map((item) => item.id).sort().join() === selectedRubricIds.slice().sort().join() ? 0 : 1),
        rubric_sources: response.rubric_sources,
      },
    } : current);
    setCriteriaJson(JSON.stringify(response.criteria.length ? response.criteria : [{
      code: "C1", title: "Kriteria", descriptor: "", display_order: 0,
      requirements: [{
        code: "R1", text: "", mandatory: true, obligation_key: "obligation.1",
        normative_source_refs: [{ source_kind: "instruction", source_id: version.id, span: { start: 0, end: 1 } }],
      }], outcome_ids: [],
    }], null, 2));
  }, "Draft criteria siap diedit. Setiap kutipan usulan tetap menunggu keputusan dosen.");

  const copyRubricReference = async (item: RubricEvidence) => {
    const reference = {
      source_kind: "rubric_evidence",
      source_id: item.id,
      span: { start: 0, end: Array.from(item.text).length },
    };
    await navigator.clipboard.writeText(JSON.stringify(reference));
    setMessage("Rujukan excerpt rubrik disalin dengan offset code point. Tinjau kutipannya sebelum menyimpan requirement.");
  };

  const saveCriteria = () => execute(async () => {
    if (!version || !criterionSet) return;
    const criteria = JSON.parse(criteriaJson) as Array<Record<string, unknown>>;
    const saved = await apiRequest<CriterionSet>(`/criterion-set-versions/${criterionSet.id}`, csrfToken, {
      method: "PUT",
      idempotency: true,
      body: { expected_revision: criterionSet.revision, criteria },
    });
    const refreshed = await apiRequest<AssignmentDetail>(`/assignments/${detail?.id}`, csrfToken, { method: "GET" });
    setDetail(refreshed);
    setCriteriaJson(JSON.stringify(saved.criteria, null, 2));
  }, "Criteria dan requirements tersimpan.");

  const approvePackage = () => execute(async () => {
    if (!version || !criterionSet) return;
    await apiRequest(`/assignment-versions/${version.id}/approve-package`, csrfToken, {
      method: "POST", idempotency: true,
      body: { criterion_set_version_id: criterionSet.id, expected_revision: version.revision },
    });
    await loadAssignment(detail!.id);
  }, "Paket normatif disahkan dan hash sumber terikat.");

  const createSpec = () => execute(async () => {
    if (!version || !criterionSet) return;
    const created = await apiRequest<Specification & { provider_draft_job_id?: string | null }>(`/assignment-versions/${version.id}/specification-drafts`, csrfToken, {
      method: "POST", idempotency: true, body: { criterion_set_version_id: criterionSet.id },
    });
    setProviderDraftJobId(created.provider_draft_job_id ?? null);
    setProviderDraftJob(null);
    const template = requiredSpecTemplate(criterionSet.criteria);
    setSpecJson(JSON.stringify(template, null, 2));
    setDetail((current) => current?.version ? {
      ...current, version: { ...current.version, revision: current.version.revision + 1, specification: created },
    } : current);
  }, "Draft schema v2 dibuat. Template human_only memerlukan pemeriksaan dan perubahan manual sebelum approval.");

  const saveSpec = () => execute(async () => {
    if (!specification) return;
    const payload = JSON.parse(specJson) as Record<string, unknown>;
    const saved = await apiRequest<{
      id: string; revision: number; payload: Record<string, unknown>;
      lint_report_id: string; effective_status: string; resolution_revision: number;
      relationship_role_requirements: unknown[];
    }>(`/specification-versions/${specification.id}`, csrfToken, {
      method: "PUT", idempotency: true,
      body: { expected_revision: specification.revision, specification: payload },
    });
    await loadAssignment(detail!.id);
    setSpecJson(JSON.stringify(saved.payload, null, 2));
  }, "Specification tersimpan dan lint deterministik baru dibuat.");

  const resolveIssue = (issueCode: string, humanOnly: boolean) => execute(async () => {
    if (!specification || !latestLint) return;
    const reason = window.prompt("Alasan peninjauan dan konfirmasi fidelity:")?.trim();
    if (!reason) throw new Error("Resolusi memerlukan alasan nonkosong.");
    await apiRequest(`/spec-lint-reports/${latestLint.id}/resolutions`, csrfToken, {
      method: "POST", idempotency: true,
      body: {
        issue_code: issueCode,
        resolution: humanOnly ? "acknowledged_human_boundary" : "acknowledged_faithful",
        reason,
        expected_spec_revision: specification.revision,
        expected_report_revision: latestLint.resolution_revision,
      },
    });
    await loadAssignment(detail!.id);
  }, "Resolusi lint disimpan sebagai catatan append-only.");

  const approveSpec = () => execute(async () => {
    if (!specification || !latestLint) return;
    await apiRequest(`/specification-versions/${specification.id}/approve`, csrfToken, {
      method: "POST", idempotency: true,
      body: {
        expected_revision: specification.revision,
        lint_report_id: latestLint.id,
        expected_report_revision: latestLint.resolution_revision,
        fidelity_confirmation: true,
      },
    });
    await loadAssignment(detail!.id);
  }, "Specification v2 disahkan dengan konfirmasi fidelity dosen.");

  const createNumericPolicy = () => execute(async () => {
    if (!detail) return;
    const payload = JSON.parse(numericJson) as Record<string, unknown>;
    const created = await apiRequest(`/assignments/${detail.id}/numeric-policy-drafts`, csrfToken, {
      method: "POST", idempotency: true, body: { policy_key: numericKey, payload },
    });
    await loadAssignment(detail.id);
    setMessage(`NumericPolicy draft dibuat: ${JSON.stringify(created)}`);
  }, "NumericPolicy draft dibuat.");

  const approveNumericPolicy = (policy: Record<string, unknown>) => execute(async () => {
    const id = String(policy.id ?? "");
    const revision = Number(policy.revision ?? 0);
    if (!id || !revision) throw new Error("ID/revision NumericPolicy tidak tersedia.");
    await apiRequest(`/numeric-policy-versions/${id}/approve`, csrfToken, {
      method: "POST", idempotency: true, body: { expected_revision: revision },
    });
    await loadAssignment(detail!.id);
  }, "NumericPolicy disahkan.");

  const saveTaskFacts = () => execute(async () => {
    if (!version || !specification) return;
    const payload = specification.payload as { task_facts?: Array<{ key: string; value_type: string }> };
    const definitions = payload.task_facts ?? [];
    const facts = definitions.flatMap((definition) => {
      const raw = taskFactValues[definition.key];
      if (raw === undefined || raw === "") return [];
      const origin = (taskFactOrigins[definition.key] ?? "").trim();
      if (!origin) throw new Error(`Asal/verifikasi dosen untuk ${definition.key} wajib diisi.`);
      const value = definition.value_type === "boolean" ? raw === "true" : raw;
      if (definition.value_type === "boolean" && raw !== "true" && raw !== "false") {
        throw new Error(`Pilih nilai true atau false untuk ${definition.key}.`);
      }
      return [{ key: definition.key, value, origin, evidence_ids: [] }];
    });
    const saved = await apiRequest<{ revision: number; task_facts: AssignmentVersion["task_facts"] }>(
      `/assignment-versions/${version.id}/task-facts`, csrfToken, {
        method: "PUT", idempotency: true,
        body: { expected_revision: version.revision, lecturer_confirmation: true, facts },
      },
    );
    setDetail((current) => current?.version ? { ...current, version: { ...current.version, revision: saved.revision, task_facts: saved.task_facts } } : current);
  }, "Fakta tugas disahkan dosen dan terikat ke versi ini.");

  const publish = () => execute(async () => {
    if (!version) return;
    await apiRequest(`/assignment-versions/${version.id}/publish`, csrfToken, {
      method: "POST", idempotency: true, body: { expected_revision: version.revision },
    });
    await refreshAssignments();
    await loadAssignment(detail!.id);
  }, "AssignmentVersion diterbitkan sebagai snapshot immutable.");

  const loadPublishPreview = () => execute(async () => {
    if (!version) return;
    const preview = await apiRequest<PublicPreview>(`/assignment-versions/${version.id}/publish-preview`, csrfToken, { method: "GET" });
    setPublishPreview(preview);
  }, "Preview mahasiswa dimuat dari serializer publik.");

  const newVersion = () => execute(async () => {
    if (!detail?.active_version_id) return;
    const created = await apiRequest<{ id: string }>(`/assignments/${detail.id}/new-version`, csrfToken, {
      method: "POST", idempotency: true, body: { base_version_id: detail.active_version_id },
    });
    await refreshAssignments();
    await loadAssignment(detail.id);
    setMessage(`Draf versi baru dibuat (${created.id}).`);
  }, "Draf versi baru dibuat tanpa mengubah snapshot terbit.");

  const contexts = useMemo(() => approvedContexts.filter((item) => item.state === "approved"), [approvedContexts]);
  const effectiveStatus = latestLint?.effective_status ?? "belum dilint";

  return (
    <section className="workspace-panel" aria-labelledby="assignment-workspace-title">
      <div className="workspace-heading">
        <div><p className="eyebrow">TUGAS · SPECIFICATION V2</p><h2 id="assignment-workspace-title">Tugas dan spesifikasi</h2></div>
        {detail && <button type="button" className="button button-quiet" disabled={busy} onClick={() => void execute(refreshAssignments, "Daftar tugas diperbarui.")}>Muat ulang</button>}
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      {message && <p className="form-success" role="status">{message}</p>}
      {providerDraftJobId && <div className="lint-panel" role="region" aria-live="polite" aria-label="Bantuan drafting provider">
        <h3>Bantuan drafting · {providerDraftJob?.type === "criteria_draft" ? "criteria" : "specification"}</h3>
        {!providerDraftJob && <p role="status">Menunggu status worker…</p>}
        {providerDraftJob && ["queued", "running"].includes(providerDraftJob.status) && <p role="status">Draft diproses worker B5 · {providerDraftJob.status}.</p>}
        {providerDraftJob?.generation.label && <p className="notice">{providerDraftJob.generation.label} · semua usulan tetap memerlukan keputusan dosen.</p>}
        {providerDraftJob?.generation.status && <p>Status hasil: <strong>{providerDraftJob.generation.status}</strong> · job {providerDraftJob.status}.</p>}
        {providerDraftJob?.generation.suggestions?.map((item, index) => <article className="rubric-evidence" key={`${item.title}-${index}`}><h4>{item.title}</h4><p>{item.description}</p>{item.source_refs.map((ref, refIndex) => <blockquote key={`${ref.source_kind}-${refIndex}`}><small>{ref.source_kind} · sumber terikat</small><p>{ref.quote}</p></blockquote>)}<small>Belum disahkan · perlu keputusan dosen</small></article>)}
        {providerDraftJob?.generation.limitations?.map((item, index) => <p className="help-text" key={`${item}-${index}`}>{item}</p>)}
        {providerDraftJob && ["blocked", "failed"].includes(providerDraftJob.status) && <p className="notice">Worker tidak menghasilkan usulan; editor manual tetap tersedia. Kode: {providerDraftJob.limitation_code ?? "manual_fallback"}.</p>}
      </div>}
      {!hasApprovedContext && <p className="notice">{contextsUnavailable ? "Daftar konteks belum tersedia. Tugas yang sudah ada tetap dapat dibuka, tetapi tugas baru memerlukan konteks approved." : "Sahkan ContextVersion terlebih dahulu. Tugas hanya dapat mengikat konteks yang sudah approved."}</p>}
      <div className="assignment-start-grid">
        <label className="field"><span>Judul tugas</span><input value={newTitle} maxLength={240} onChange={(event) => setNewTitle(event.target.value)} /></label>
        <label className="field"><span>ContextVersion approved</span><select value={contextId} onChange={(event) => setContextId(event.target.value)} disabled={!contexts.length}>
          {contexts.map((item) => <option key={item.id} value={item.id}>v{item.version_no} · {item.content_hash?.slice(0, 10) ?? "tanpa hash"}</option>)}
        </select></label>
        <button type="button" className="button button-primary" disabled={busy || !hasApprovedContext || !newTitle.trim() || !contextId} onClick={createAssignment}>Buat draf tugas</button>
      </div>
      {assignments.length > 0 && <label className="field assignment-select"><span>Pilih tugas</span><select value={detail?.id ?? ""} onChange={(event) => event.target.value && void loadAssignment(event.target.value)}>
        <option value="">Pilih tugas…</option>
        {assignments.map((item) => <option key={item.id} value={item.id}>{item.title} · {item.draft_version_id ? `draf v${item.draft_version_no}` : item.active_version_id ? `terbit v${item.version_no}` : "belum ada versi"}</option>)}
      </select></label>}
      {detail && version && <>
        <div className="workspace-subheading"><div><p className="eyebrow">{detail.title} · VERSI {version.version_no}</p><h3>{version.state === "published" ? "Snapshot terbit" : "Draf assignment"}</h3></div>
          {version.state === "published" && <button className="button button-primary" type="button" disabled={busy} onClick={newVersion}>Buat versi baru</button>}
        </div>
        <div className="assignment-form-grid">
          <label className="field field-wide"><span>Instruksi asli (maks. 30.000 karakter)</span><textarea value={instruction} maxLength={30000} rows={8} disabled={!editable} onChange={(event) => setInstruction(event.target.value)} /></label>
          <label className="field"><span>Ringkasan opsional</span><textarea value={summary} maxLength={2000} rows={4} disabled={!editable} onChange={(event) => setSummary(event.target.value)} /></label>
          <p className="help-text">Instruksi asli tetap menjadi sumber normatif. Ringkasan hanya ditampilkan dengan label dan tidak menambah atau mengganti kewajiban.</p>
          <label className="field"><span>Mode</span><select value={mode} disabled={!editable} onChange={(event) => setMode(event.target.value as "individual" | "group")}><option value="individual">Individual</option><option value="group">Kelompok</option></select></label>
          <label className="field"><span>Dibuka (waktu lokal perangkat)</span><input type="datetime-local" value={opensAt} disabled={!editable} onChange={(event) => setOpensAt(event.target.value)} /></label>
          <label className="field"><span>Tenggat (waktu lokal perangkat)</span><input type="datetime-local" value={dueAt} disabled={!editable} onChange={(event) => setDueAt(event.target.value)} /></label>
          <label className="field"><span>Kebijakan terlambat</span><select value={latePolicy} disabled={!editable} onChange={(event) => setLatePolicy(event.target.value as "accept_with_label" | "reject_after_due")}><option value="accept_with_label">Terima dengan label</option><option value="reject_after_due">Tolak setelah tenggat</option></select></label>
          <div className="field-actions"><button type="button" className="button button-primary" disabled={busy || !editable || !instruction.trim() || !opensAt || !dueAt} onClick={saveVersion}>Simpan versi</button></div>
        </div>
        <section className="assignment-step" aria-labelledby="rubric-title">
          <h3 id="rubric-title">Rubrik opsional</h3>
          <p className="help-text">File asli dipertahankan. Parser memakai pipeline B1 dan ekstraksi hanya menjadi sumber untuk ditinjau dosen. Bobot/level file tidak menjadi field penilaian ALIGN.</p>
          {editable && <label className="field"><span>Unggah PDF, DOCX, XLSX, JPG, JPEG, atau PNG</span><input type="file" accept=".pdf,.docx,.xlsx,.jpg,.jpeg,.png" onChange={uploadRubric} /></label>}
          {[...new Map([...rubricFiles, ...(version.rubric_sources ?? []).map((item) => ({ ...item, purpose: "rubric" as const }))].map((item) => [item.id, item])).values()].map((item) => <label className="check-row" key={item.id}><input type="checkbox" checked={selectedRubricIds.includes(item.id)} disabled={!editable} onChange={(event) => setSelectedRubricIds((current) => event.target.checked ? [...new Set([...current, item.id])] : current.filter((id) => id !== item.id))} /><span>{item.name} <a href={`/api/v1/rubric-files/${item.id}/download`}>Unduh asli</a></span></label>)}
          {rubricParse && rubricParse.files.length > 0 && <div className="lint-panel" aria-live="polite"><h4>Hasil parser rubrik · {rubricParse.status}</h4><p className="help-text">Ekstraksi bukan kriteria resmi. Dosen memilih kutipan dan menetapkan teks/mandatory secara eksplisit. Offset citation memakai Unicode code point.</p>{rubricParse.files.map((file) => <section className="rubric-parse-file" key={file.id}><h5>{file.name} · {file.parse_status} · {file.evidence_count} unit bukti</h5>{file.limitations.map((item, index) => <p className="help-text" key={index}>{String(item.message ?? item.code ?? "Batas parsing")}</p>)}{file.evidence_truncated && <p className="notice">Daftar unit dibatasi untuk tampilan; total tetap {file.evidence_count}.</p>}{file.evidence.map((item) => <article className="rubric-evidence" key={item.id}><div className="workspace-subheading"><strong>{item.type} · {item.parse_status}</strong><small>SHA source {item.source_sha256.slice(0, 12)} · {JSON.stringify(item.anchor)}</small></div><pre>{item.text || "Tidak ada teks yang diekstrak; tinjau file asli."}</pre>{item.text_truncated && <p className="notice">Excerpt dipotong pada batas respons. Gunakan file asli untuk kutipan lengkap.</p>}{item.text && !item.text_truncated && <button type="button" className="button button-quiet" disabled={busy} onClick={() => void copyRubricReference(item)}>Salin citation seluruh excerpt</button>}</article>)}</section>)}</div>}
        </section>
        <section className="assignment-step" aria-labelledby="criteria-title">
          <div className="workspace-subheading"><div><h3 id="criteria-title">Criteria dan requirements</h3><p className="help-text">Satu criterion dapat terkait beberapa requirement. Requirement source span memakai code point Unicode pada instruksi/descriptor; hash dan kutipan dibuat backend.</p></div>
            <div className="button-row">
              <label className="check-row"><input type="checkbox" checked={generateExtractiveProposals} disabled={busy || !editable} onChange={(event) => setGenerateExtractiveProposals(event.target.checked)} /><span>Usulkan kutipan instruksi persis (mock, tanpa menafsirkan kewajiban)</span></label>
              <button type="button" className="button button-quiet" disabled={busy || !editable} onClick={openCriteriaDraft}>Siapkan editor draft</button>
            </div>
          </div>
          {criteriaProposals.length > 0 && <div className="lint-panel" aria-live="polite"><h4>Usulan kutipan · keputusan dosen diperlukan</h4><p className="help-text">Usulan ini hanya potongan persis dari instruksi dengan offset code point. Status wajib/opsional dan arti akademik tidak ditebak.</p>{criteriaProposals.map((proposal) => <blockquote className="proposal-quote" key={proposal.proposal_code}><p>{proposal.quote}</p><footer>{proposal.proposal_code} · code point {proposal.source_span.start}–{proposal.source_span.end} · sha256 {proposal.source_hash.slice(0, 12)} · belum disahkan</footer></blockquote>)}</div>}
          {criterionSet && <>
            <p className="help-text">Paket: {criterionSet.state} · revision {criterionSet.revision}{criterionSet.content_hash ? ` · hash ${criterionSet.content_hash}` : ""}</p>
            <CriteriaStructuredEditor
              criteria={criteriaDraft}
              onChange={(next) => setCriteriaJson(JSON.stringify(next, null, 2))}
              instructionId={version.id}
              instruction={instruction}
              files={rubricParse?.files ?? []}
              disabled={!editable || criterionSet.state !== "draft"}
            />
            {editable && criterionSet.state === "draft" && <div className="button-row"><button type="button" className="button button-quiet" disabled={busy} onClick={saveCriteria}>Simpan criteria</button><button type="button" className="button button-primary" disabled={busy || !criterionSet.criteria.length} onClick={approvePackage}>Sahkan paket normatif</button></div>}
            {criterionSet.state === "approved" && <p className="notice">Paket disahkan. Perubahan berikutnya harus dilakukan pada draf versi baru.</p>}
          </>}
          {version.package_approved_at && <p className="form-success">Paket normatif disahkan pada {new Date(version.package_approved_at).toLocaleString("id-ID")}.</p>}
        </section>
        <section className="assignment-step" aria-labelledby="numeric-title">
          <h3 id="numeric-title">NumericPolicy</h3>
          <p className="help-text">Policy bertipe, toleransi wajib eksplisit, dan hanya memakai unit dalam registry yang disetujui. Elemen numerik harus merujuk policy yang sudah disahkan.</p>
          {editable && <>
            <label className="field"><span>Kunci policy</span><input value={numericKey} onChange={(event) => setNumericKey(event.target.value)} /></label>
            <NumericPolicyStructuredEditor payload={numericDraft} onChange={(next) => setNumericJson(JSON.stringify(next, null, 2))} disabled={!editable} />
            <button type="button" className="button button-quiet" disabled={busy || !detail} onClick={createNumericPolicy}>Buat NumericPolicy draft</button>
          </>}
          {version.numeric_policies.map((policy) => <div className="list-row" key={String(policy.id)}><span>{String(policy.policy_key)} v{String(policy.version_no)} · {String(policy.state)} · {String(policy.id)}</span>{editable && policy.state === "draft" && <button type="button" className="button button-quiet" disabled={busy} onClick={() => approveNumericPolicy(policy)}>Sahkan</button>}</div>)}
        </section>
        {criterionSet?.state === "approved" && <section className="assignment-step" aria-labelledby="spec-title">
          <div className="workspace-subheading"><div><h3 id="spec-title">CheckSpecification schema v2</h3><p className="help-text">Editor menampilkan payload tertutup penuh. Backend memvalidasi registry, span, grounding, applicability DSL, DAG dan policy; role relationships dihitung dari registry.</p></div>
            {!specification && editable && <button type="button" className="button button-quiet" disabled={busy} onClick={createSpec}>Buat spec draft</button>}
          </div>
          {specification && <>
            <p className="help-text">Spec v{specification.version_no} · schema {specification.schema_version} · revision {specification.revision} · lint: <strong>{effectiveStatus}</strong></p>
            {editable && specification.state === "draft" && <>
              <button type="button" className="button button-quiet" onClick={() => setSpecJson(JSON.stringify(requiredSpecTemplate(criterionSet.criteria), null, 2))}>Buat template human_only</button>
              <SpecificationStructuredEditor
                payload={specDraft}
                onChange={(next) => setSpecJson(JSON.stringify(next, null, 2))}
                criteria={criterionSet.criteria}
                policies={version.numeric_policies}
                instructionId={version.id}
                instruction={instruction}
                files={rubricParse?.files ?? []}
                disabled={!editable || specification.state !== "draft"}
              />
              <button type="button" className="button button-quiet" disabled={busy} onClick={saveSpec}>Simpan spec dan jalankan lint</button>
            </>}
            {latestLint && <div className="lint-panel" aria-live="polite"><h4>Lint report · {latestLint.effective_status}</h4>
              {latestLint.issues.length === 0 && <p>Tidak ada issue lint deterministik pada revision ini.</p>}
              {latestLint.issues.map((issue) => {
                const resolved = latestLint.resolutions.some((item) => item.issue_code === issue.issue_code);
                const humanOnly = issue.element_ids.length > 0 && (specification.payload.elements as Array<Record<string, unknown>> | undefined)?.filter((item) => issue.element_ids.includes(String(item.element_id))).every((item) => (item.grounding_contract as { mode?: string } | undefined)?.mode === "human_only");
                return <div className="list-row" key={issue.issue_code}><div><strong>{issue.rule_code}</strong><p>{issue.message}</p><small>{issue.severity}{resolved ? " · sudah diakui" : ""}</small></div>{editable && issue.severity === "review" && !resolved && <button type="button" className="button button-quiet" disabled={busy} onClick={() => resolveIssue(issue.issue_code, Boolean(humanOnly))}>Catat resolusi</button>}</div>;
              })}
            </div>}
            {editable && specification.state === "draft" && <button type="button" className="button button-primary" disabled={busy || effectiveStatus !== "passed"} onClick={approveSpec}>Sahkan spec dengan konfirmasi fidelity</button>}
          </>}
        </section>}
        {editable && specification?.state === "approved" && (Array.isArray((specification.payload as { task_facts?: unknown }).task_facts) && ((specification.payload as { task_facts: Array<{ key: string; value_type: string; allowed_values?: string[] | null }> }).task_facts).length > 0) && <section className="assignment-step" aria-labelledby="task-facts-title"><h3 id="task-facts-title">Fakta tugas · verifikasi dosen</h3><p className="help-text">Hanya fakta yang dikonfirmasi dosen untuk versi dan spesifikasi ini yang dapat menentukan applicability. Kosong berarti unknown. Perubahan tugas membuat versi baru dan tidak mewarisi konfirmasi.</p>{((specification.payload as { task_facts: Array<{ key: string; value_type: string; allowed_values?: string[] | null }> }).task_facts).map((fact) => <div className="assignment-form-grid" key={fact.key}><label className="field"><span>{fact.key} · nilai</span>{fact.value_type === "boolean" ? <select aria-label={`${fact.key} nilai`} value={taskFactValues[fact.key] ?? ""} onChange={(event) => setTaskFactValues((current) => ({ ...current, [fact.key]: event.target.value }))}><option value="">Belum diketahui</option><option value="true">true</option><option value="false">false</option></select> : fact.value_type === "string" && fact.allowed_values?.length ? <select aria-label={`${fact.key} nilai`} value={taskFactValues[fact.key] ?? ""} onChange={(event) => setTaskFactValues((current) => ({ ...current, [fact.key]: event.target.value }))}><option value="">Belum diketahui</option>{fact.allowed_values.map((value) => <option key={value} value={value}>{value}</option>)}</select> : <input aria-label={`${fact.key} nilai`} value={taskFactValues[fact.key] ?? ""} onChange={(event) => setTaskFactValues((current) => ({ ...current, [fact.key]: event.target.value }))} />}</label><label className="field"><span>Asal/provenance untuk {fact.key}</span><input aria-label={`${fact.key} asal`} value={taskFactOrigins[fact.key] ?? ""} onChange={(event) => setTaskFactOrigins((current) => ({ ...current, [fact.key]: event.target.value }))} /></label></div>)}<button type="button" className="button button-quiet" disabled={busy || !editable} onClick={saveTaskFacts}>Simpan dan sahkan fakta dosen</button><p className="help-text">Fakta tersimpan: {Object.keys(version.task_facts?.facts ?? {}).length} · snapshot {version.task_facts?.content_hash ?? "belum disahkan"}</p></section>}
        {editable && specification?.state === "approved" && <section className="assignment-step" aria-labelledby="publish-title"><h3 id="publish-title">Publikasi</h3><p className="help-text">Pratinjau berikut memakai serializer publik mahasiswa yang sama. Periksa instruksi, criteria, requirements, bentuk bukti, jadwal dan kebijakan sebelum menerbitkan.</p><div className="button-row"><button type="button" className="button button-quiet" disabled={busy} onClick={loadPublishPreview}>Tampilkan preview mahasiswa</button><button type="button" className="button button-primary" disabled={busy} onClick={publish}>Terbitkan versi</button></div>{publishPreview && <article className="student-task-preview" aria-label="Preview paket publik mahasiswa"><p className="eyebrow">{publishPreview.title} · VERSI {publishPreview.version.version_no}</p><h4>Instruksi tugas</h4><pre className="instruction-preview">{publishPreview.version.instruction_original}</pre>{publishPreview.version.instruction_summary && <p className="notice"><strong>{publishPreview.version.instruction_summary_label}</strong><br />{publishPreview.version.instruction_summary}</p>}<h4>Criteria dan requirements</h4>{publishPreview.version.criteria.map((criterion) => <section className="criterion-card" key={criterion.code}><h5>{criterion.code} · {criterion.title}</h5><p>{criterion.descriptor}</p>{criterion.requirements.length > 0 && <ul>{criterion.requirements.map((requirement) => <li key={requirement.code}>{requirement.text}{requirement.mandatory ? " (wajib)" : " (opsional)"}</li>)}</ul>}</section>)}<h4>Bentuk bukti yang diterima</h4><ul>{publishPreview.version.accepted_evidence.map((item, index) => <li key={`${item.criterion_code}-${index}`}>{item.criterion_code}: {item.accepted_evidence_types.join(", ")}</li>)}</ul><p className="help-text">Mode: {publishPreview.version.mode} · Dibuka: {new Date(publishPreview.version.opens_at).toLocaleString("id-ID")} · Tenggat: {new Date(publishPreview.version.due_at).toLocaleString("id-ID")} · Kebijakan terlambat: {publishPreview.version.late_policy}.</p></article>}</section>}
      </>}
    </section>
  );
}

export function StudentAssignments({
  courseId,
  csrfToken,
  initialAssignments,
}: {
  courseId: string;
  csrfToken: string;
  initialAssignments: AssignmentSummary[];
}) {
  const [assignments] = useState(initialAssignments);
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const openAssignment = async (id: string) => {
    setError("");
    try {
      const result = await apiRequest<Record<string, unknown>>(`/assignments/${id}`, csrfToken, { method: "GET" });
      setSelected(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Tugas tidak dapat dibuka.");
    }
  };
  const version = (selected?.version ?? null) as Record<string, unknown> | null;
  const criteria = Array.isArray(version?.criteria) ? version.criteria as Array<Record<string, unknown>> : [];
  const evidence = Array.isArray(version?.accepted_evidence) ? version.accepted_evidence as Array<Record<string, unknown>> : [];
  return (
    <section className="workspace-panel" aria-labelledby="student-assignment-title">
      <p className="eyebrow">TUGAS TERBIT</p><h2 id="student-assignment-title">Tugas mata kuliah</h2>
      {error && <p className="form-error" role="alert">{error}</p>}
      {assignments.length === 0 ? <p className="notice">Belum ada tugas yang dibuka untuk mahasiswa.</p> : <div className="assignment-list">
        {assignments.map((item) => <button type="button" className="list-row assignment-card" key={item.id} onClick={() => void openAssignment(item.id)}><strong>{item.title}</strong><span>Versi {item.version_no} · tenggat {item.due_at ? new Date(item.due_at).toLocaleString("id-ID") : "—"}</span></button>)}
      </div>}
      {version && <article className="student-task-preview"><p className="eyebrow">{String(selected?.title ?? "Tugas")} · VERSI {String(version.version_no)}</p><h3>Instruksi tugas</h3><pre className="instruction-preview">{String(version.instruction_original ?? "")}</pre>
        {typeof version.instruction_summary === "string" && version.instruction_summary.length > 0 && <p className="notice"><strong>{String(version.instruction_summary_label ?? "Ringkasan")}</strong><br />{version.instruction_summary}</p>}
        <h3>Criteria dan requirements</h3>
        {criteria.map((criterion) => <section className="criterion-card" key={String(criterion.code)}><h4>{String(criterion.code)} · {String(criterion.title)}</h4><p>{String(criterion.descriptor)}</p>{Array.isArray(criterion.requirements) && <ul>{(criterion.requirements as Array<Record<string, unknown>>).map((requirement) => <li key={String(requirement.code)}>{String(requirement.text)}{requirement.mandatory ? " (wajib)" : " (opsional)"}</li>)}</ul>}</section>)}
        <h3>Bentuk bukti yang diterima</h3><ul>{evidence.map((item, index) => <li key={`${String(item.criterion_code)}-${index}`}>{String(item.criterion_code)}: {(Array.isArray(item.accepted_evidence_types) ? item.accepted_evidence_types as string[] : []).join(", ")}</li>)}</ul>
        <p className="help-text">Tenggat: {version.due_at ? new Date(String(version.due_at)).toLocaleString("id-ID") : "—"}. Kebijakan terlambat: {String(version.late_policy ?? "—")}.</p>
      </article>}
      <span className="sr-only">Course {courseId}</span>
    </section>
  );
}

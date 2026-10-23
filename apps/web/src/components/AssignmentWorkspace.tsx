"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import type { ApiMethod, ApiRequestPath } from "@/lib/api-contract.generated";
import { CriteriaStructuredEditor, NumericPolicyStructuredEditor, SpecificationStructuredEditor } from "./AssignmentStructuredEditors";

type ContextSummary = { id: string; version_no: number; state: string; content_hash: string | null; created_at?: string; approved_at?: string | null };
type ContextOutcome = { id: string; code: string; description: string; resolution_status: string };
type ContextDetail = {
  id: string;
  version_no: number;
  state: string;
  fields: Record<string, unknown>;
  approved_at: string | null;
  content_hash: string | null;
  learning_outcomes?: ContextOutcome[];
};
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

const DEFAULT_NUMERIC_POLICY: Record<string, unknown> = {
  metric_definition: "Besaran yang harus dibandingkan dalam tugas ini.",
  required_context_fields: ["metric", "entity", "definition", "unit"],
  number_locale: "id-ID",
  canonical_unit: "IDR",
  allowed_conversions: [],
  comparison_mode: "pairwise",
  absolute_tolerance: "0",
  relative_tolerance: "0",
  rounding: { mode: "none", quantum: null },
  operand_roles: { left: "observasi pertama", right: "observasi pembanding" },
};
const DEFAULT_NUMERIC_POLICY_JSON = JSON.stringify(DEFAULT_NUMERIC_POLICY, null, 2);

function contextFieldValue(field: unknown): unknown {
  if (field && typeof field === "object" && !Array.isArray(field) && "value" in field) {
    return (field as { value?: unknown }).value;
  }
  return field;
}

function contextFieldLabel(key: string): string {
  const labels: Record<string, string> = {
    course_code: "Kode mata kuliah", course_title: "Nama mata kuliah",
    context_description: "Deskripsi konteks", number_locale: "Format angka lokal",
    language: "Bahasa", outcomes_status: "Status capaian pembelajaran",
    learning_outcomes: "Capaian pembelajaran",
  };
  return labels[key] ?? key.replaceAll("_", " ");
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson((value as Record<string, unknown>)[key])}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: "Menunggu diproses", running: "Sedang diproses", ready_for_review: "Siap ditinjau",
    partial: "Sebagian berhasil", failed: "Gagal", blocked: "Terhenti", passed: "Lulus",
    needs_review: "Perlu ditinjau", manual: "Manual", manual_only: "Manual",
    mock_abstention: "Mock tidak membuat usulan semantik",
  };
  return labels[status] ?? status.replaceAll("_", " ");
}

function sourceLocationLabel(anchor: unknown): string {
  const value = anchor && typeof anchor === "object" && !Array.isArray(anchor) ? anchor as Record<string, unknown> : {};
  const parts: string[] = [];
  const page = value.page ?? value.page_no ?? value.page_number;
  if (typeof page === "number") parts.push(`Halaman ${page}`);
  if (typeof value.sheet_name === "string") parts.push(`Lembar ${value.sheet_name}`);
  if (typeof value.cell_range === "string") parts.push(`Sel ${value.cell_range}`);
  const item = value.item_index ?? value.paragraph_index;
  if (typeof value.item_kind === "string") {
    const itemLabels: Record<string, string> = { paragraph: "Paragraf", table: "Tabel", cell: "Sel", media: "Media" };
    parts.push(`${itemLabels[value.item_kind] ?? "Bagian"}${Number.isInteger(item) ? ` ${Number(item)}` : ""}`);
  } else if (Number.isInteger(value.paragraph_index)) {
    parts.push(`Paragraf ${Number(value.paragraph_index) + 1}`);
  }
  if (typeof value.row === "number" && typeof value.column === "number") parts.push(`Baris ${value.row}, kolom ${value.column}`);
  return parts.join(" · ") || "Lokasi sumber tersedia";
}

function ParserLimitationsSummary({ files, status }: { files: RubricParse["files"]; status?: string }) {
  const limitations = files.flatMap((file) => file.limitations.map((item) => ({
    code: String(item.code ?? "parser_limit"),
    message: String(item.message ?? item.code ?? "Batas parsing"),
    file: file.name,
  })));
  const grouped = new Map<string, { message: string; code: string; files: string[]; count: number }>();
  for (const item of limitations) {
    const key = `${item.code}\u0000${item.message}`;
    const group = grouped.get(key) ?? { message: item.message, code: item.code, files: [], count: 0 };
    group.count += 1;
    group.files.push(item.file);
    grouped.set(key, group);
  }
  return <section className="parser-inventory" aria-label="Ringkasan keterbatasan parser rubrik">
    <h4>Ringkasan keterbatasan parser</h4>
    {status && <p className="help-text">Status hasil parser: {statusLabel(status)} · {files.length} file pada hasil ini.</p>}
    {!status && files.length === 0 && <p className="help-text">Belum ada hasil parser untuk file rubrik.</p>}
    {limitations.length === 0 ? <p className="help-text">Tidak ada limitation parser yang tercatat pada hasil ini.</p> : <details className="parser-limitations"><summary>{limitations.length} catatan limitation · {grouped.size} jenis · buka detail</summary><ul>{Array.from(grouped.values()).map((item) => <li key={`${item.code}-${item.message}`}><strong>{item.message}</strong><span>{item.count} kali · {item.files.join(", ")}</span><details className="technical-details"><summary>Kode limitation</summary><code>{item.code}</code></details></li>)}</ul><p className="help-text">Pengelompokan ini hanya merangkum tampilan. Inventaris, status baca, unit sumber, dan coverage parser tetap sama.</p></details>}
  </section>;
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
          decision_question: `Apakah bukti relevan menjawab kewajiban ${code}?`,
          derivation_refs: [{ source_kind: "requirement", source_id: requirementId, span: null }],
          expected_evidence: `Bukti yang relevan terhadap kewajiban ${code}.`,
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

function PublishedAssignmentDetail({
  detail, version, context, rubricParse, busy, draftVersionNo, onBack, onNewVersion, onOpenDraft,
}: {
  detail: AssignmentDetail;
  version: AssignmentVersion;
  context: ContextDetail | null;
  rubricParse: RubricParse | null;
  busy: boolean;
  draftVersionNo?: number | null;
  onBack: () => void;
  onNewVersion: () => void;
  onOpenDraft: () => void;
}) {
  const criteria = version.criterion_set?.criteria ?? [];
  const outcomes = context?.learning_outcomes ?? [];
  const files = version.rubric_sources ?? [];
  const parseFiles = rubricParse?.files ?? [];
  const evidence = parseFiles.flatMap((file) => file.evidence.map((item) => ({ ...item, fileName: file.name, fileId: file.id })));
  const elements = Array.isArray(version.specification?.payload.elements)
    ? version.specification.payload.elements as Array<Record<string, unknown>>
    : [];
  const criteriaById = new Map(criteria.map((item) => [String(item.id ?? ""), item]));
  const outcomeById = new Map(outcomes.map((item) => [item.id, item]));
  const requirementById = new Map(criteria.flatMap((criterion) => Array.isArray(criterion.requirements)
    ? (criterion.requirements as Array<Record<string, unknown>>).map((requirement) => [String(requirement.id ?? ""), requirement] as const)
    : []));
  const describeSource = (raw: unknown, criterion: Record<string, unknown>) => {
    const ref = raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, unknown> : {};
    const kind = String(ref.source_kind ?? "");
    const sourceId = String(ref.source_id ?? "");
    const anchor = ref.anchor ?? evidence.find((item) => item.id === sourceId)?.anchor;
    const span = ref.span && typeof ref.span === "object" ? ref.span as { start?: unknown; end?: unknown } : {};
    const sourceEvidence = evidence.find((item) => item.id === sourceId);
    const sourceFileId = String(ref.file_id ?? sourceEvidence?.fileId ?? "");
    const sourceFile = files.find((item) => item.id === sourceFileId);
    let name = "Sumber tugas";
    if (kind === "instruction") name = "Instruksi asli";
    else if (kind === "criterion_descriptor") name = `Deskripsi kriteria · ${String(criterion.code ?? "")}`;
    else if (kind === "rubric_evidence") name = sourceFile?.name ?? sourceEvidence?.fileName ?? "File rubrik";
    const sourceText = kind === "instruction"
      ? version.instruction_original
      : kind === "criterion_descriptor"
        ? String(criteriaById.get(sourceId)?.descriptor ?? "")
        : sourceEvidence?.text ?? "";
    const sourceCodepoints = Array.from(sourceText);
    const start = typeof span.start === "number" ? span.start : 0;
    const end = typeof span.end === "number" ? span.end : 0;
    const quote = typeof ref.quote === "string" ? ref.quote : sourceCodepoints.slice(start, end).join("");
    return { ref, kind, sourceId, anchor, start, end, name, quote, sourceFileId, sourceFile, sourceHash: ref.source_hash ?? ref.source_sha256 ?? sourceFile?.sha256 };
  };

  return <section className="published-assignment-detail" aria-labelledby="published-assignment-title">
    <header className="published-detail-heading"><div><p className="eyebrow">TUGAS TERBIT · VERSI {version.version_no}</p><h3 id="published-assignment-title">{detail.title}</h3><p className="help-text">Snapshot ini bersifat read-only. Versi terbit tidak berubah saat draf lanjutan dibuat.</p><span className="assignment-status assignment-status-published">Terbit · versi {version.version_no}</span></div><div className="button-row"><button type="button" className="button button-quiet" disabled={busy} onClick={onBack}>Daftar tugas</button>{draftVersionNo ? <button type="button" className="button button-primary" disabled={busy} onClick={onOpenDraft}>Buka draf versi {draftVersionNo}</button> : <button type="button" className="button button-primary" disabled={busy} onClick={onNewVersion}>Buat versi baru</button>}</div></header>

    <section className="published-detail-section" aria-labelledby="published-info-title"><h4 id="published-info-title">Informasi tugas</h4>
      <dl className="published-facts"><div><dt>Konteks mata kuliah</dt><dd>{context ? `Versi ${context.version_no} · ${context.state === "approved" ? "disahkan" : "status berubah"}` : "Memuat konteks resmi…"}{context?.approved_at && <small>Disahkan {new Date(context.approved_at).toLocaleString("id-ID")}</small>}</dd></div><div><dt>Mode</dt><dd>{version.mode === "group" ? "Kelompok" : "Individual"}</dd></div><div><dt>Tugas dibuka</dt><dd>{new Date(version.opens_at).toLocaleString("id-ID")}</dd></div><div><dt>Tenggat</dt><dd>{new Date(version.due_at).toLocaleString("id-ID")}</dd></div><div><dt>Keterlambatan</dt><dd>{version.late_policy === "reject_after_due" ? "Pengumpulan ditolak setelah tenggat" : "Diterima dengan label terlambat"}</dd></div><div><dt>Persetujuan paket</dt><dd>{version.package_approved_at ? `Disahkan ${new Date(version.package_approved_at).toLocaleString("id-ID")}` : "Metadata persetujuan tidak tersedia"}</dd></div></dl>
      <h5>Instruksi asli</h5><pre className="instruction-preview">{version.instruction_original}</pre>
      {version.instruction_summary && <p className="notice notice-muted"><strong>Ringkasan instruksi tersimpan</strong><br />{version.instruction_summary}</p>}
      {context && <details className="published-context-summary"><summary>Lihat ringkasan konteks mata kuliah</summary><p>{String(contextFieldValue(context.fields.context_description) ?? "Belum ada deskripsi konteks.")}</p>{outcomes.length > 0 && <ul>{outcomes.map((outcome) => <li key={outcome.id}><strong>{outcome.code}</strong> · {outcome.description}</li>)}</ul>}</details>}
      <details className="technical-details"><summary>Detail teknis versi tugas</summary><code>ID assignment: {detail.id}</code><code>ID versi: {version.id}</code><code>SHA-256 instruksi: {version.instruction_hash}</code><code>ID konteks: {version.context_version_id}</code></details>
    </section>

    <section className="published-detail-section" aria-labelledby="published-criteria-title"><h4 id="published-criteria-title">Kriteria pemeriksaan dan kewajiban mahasiswa</h4>
      {criteria.length === 0 ? <p className="notice notice-warning">Snapshot ini tidak memuat data kriteria yang dapat ditampilkan.</p> : criteria.map((criterion, criterionIndex) => {
        const requirements = Array.isArray(criterion.requirements) ? criterion.requirements as Array<Record<string, unknown>> : [];
        const mappedOutcomes = Array.isArray(criterion.outcome_ids) ? (criterion.outcome_ids as string[]).map((id) => outcomeById.get(id)).filter((item): item is ContextOutcome => Boolean(item)) : [];
        const unavailableMappings = Array.isArray(criterion.outcome_ids) ? (criterion.outcome_ids as string[]).filter((id) => !outcomeById.has(id)) : [];
        return <article className="published-criterion-card" key={String(criterion.id ?? criterionIndex)}><header><span className="assignment-status">{String(criterion.code ?? `Kriteria ${criterionIndex + 1}`)}</span><h5>{String(criterion.title ?? "Kriteria")}</h5></header><p>{String(criterion.descriptor ?? "")}</p>
          {mappedOutcomes.length > 0 && <div className="published-outcomes"><strong>CPMK terkait</strong><ul>{mappedOutcomes.map((outcome) => <li key={outcome.id}><strong>{outcome.code}</strong> · {outcome.description}</li>)}</ul></div>}
          {unavailableMappings.length > 0 && <p className="notice notice-warning">Pemetaan CPMK tersimpan tidak ditemukan pada ContextVersion yang dimuat. Snapshot dan mapping tetap dipertahankan; lihat detail teknis bila diperlukan.</p>}
          <details className="technical-details"><summary>Detail mapping CPMK</summary><code>{Array.isArray(criterion.outcome_ids) && (criterion.outcome_ids as string[]).length ? (criterion.outcome_ids as string[]).join(", ") : "Tidak ada mapping CPMK"}</code></details>
          {requirements.length > 0 ? <div className="published-requirements"><strong>Kewajiban mahasiswa</strong><ol>{requirements.map((requirement, requirementIndex) => {
            const refs = Array.isArray(requirement.normative_source_refs) ? requirement.normative_source_refs : [];
            return <li key={String(requirement.id ?? requirementIndex)}><div className="published-requirement-heading"><strong>{String(requirement.code ?? `Kewajiban ${requirementIndex + 1}`)}</strong><span className={`requirement-status ${requirement.mandatory === true ? "is-mandatory" : ""}`}>{requirement.mandatory === true ? "Wajib" : "Opsional"}</span></div><p>{String(requirement.text ?? "")}</p>
              {refs.length > 0 && <div className="published-source-list"><strong>Referensi sumber</strong>{refs.map((raw, refIndex) => {
                const ref = describeSource(raw, criterion);
                return <article className="published-source-ref" key={`${ref.kind}-${ref.sourceId}-${refIndex}`}><div><strong>{ref.name}</strong><span>{ref.anchor ? sourceLocationLabel(ref.anchor) : ref.kind === "instruction" ? "Teks instruksi" : ref.kind === "criterion_descriptor" ? "Deskripsi kriteria" : "Lokasi dalam file"}</span></div>{ref.kind === "rubric_evidence" && ref.sourceFileId && <a href={`/api/v1/rubric-files/${ref.sourceFileId}/download`}>Buka file asli</a>}<blockquote>{ref.quote || "Kutipan tidak tersimpan; referensi provenance tetap tersedia."}</blockquote><details className="technical-details"><summary>Metadata provenance</summary><code>Jenis: {ref.kind}</code><code>ID sumber: {ref.sourceId}</code>{ref.sourceFileId && <code>ID file: {ref.sourceFileId}</code>}<code>SHA-256: {String(ref.sourceHash ?? "tidak tersedia")}</code>{ref.anchor && <code>Locator: {JSON.stringify(ref.anchor)}</code>}<code>Indeks code point: {ref.start}–{ref.end} (akhir tidak termasuk)</code></details></article>;
              })}</div>}
            </li>;
          })}</ol></div> : <p className="help-text">Tidak ada kewajiban atomik pada kriteria ini; deskripsi kriteria tetap menjadi bagian snapshot.</p>}
        </article>;
      })}
    </section>

    <section className="published-detail-section" aria-labelledby="published-plan-title"><h4 id="published-plan-title">Rencana pemeriksaan yang disahkan</h4>
      {elements.length === 0 ? <p className="notice notice-warning">Spesifikasi tidak tersedia pada detail versi ini.</p> : <ol className="published-check-list">{elements.map((element, index) => {
        const criterion = criteriaById.get(String(element.criterion_id ?? ""));
        const linkedRequirements = Array.isArray(element.requirement_ids) ? (element.requirement_ids as string[]).map((id) => requirementById.get(id)).filter((item): item is Record<string, unknown> => Boolean(item)) : [];
        const grounding = element.grounding_contract && typeof element.grounding_contract === "object" ? element.grounding_contract as Record<string, unknown> : {};
        const applicability = element.applicability && typeof element.applicability === "object" ? element.applicability as Record<string, unknown> : {};
        const modeLabel = ({ human_only: "Tinjauan dosen", anchored_content: "Bukti isi terikat sumber", linked_relationship: "Hubungan bukti", comparable_numeric: "Perbandingan angka" } as Record<string, string>)[String(grounding.mode)] ?? "Tinjauan sesuai spesifikasi";
        return <li className="published-check-card" key={String(element.element_id ?? index)}><header><span className="assignment-status">Pemeriksaan {index + 1}</span><span>{String(criterion?.code ?? "Kriteria terkait")}{linkedRequirements.length ? ` · ${linkedRequirements.map((item) => String(item.code ?? "")).join(", ")}` : ""}</span></header><h5>{String(element.decision_question ?? "")}</h5><dl><div><dt>Bukti yang dicari</dt><dd>{String(element.expected_evidence ?? "")}</dd></div><div><dt>Bentuk peninjauan</dt><dd>{modeLabel}</dd></div><div><dt>Penerapan</dt><dd>{applicability.mode === "always" ? "Selalu berlaku" : applicability.mode === "lecturer_required" ? String(applicability.question ?? "Perlu konfirmasi dosen") : "Mengikuti fakta tugas terverifikasi"}</dd></div></dl><details className="technical-details"><summary>Detail teknis pemeriksaan</summary><code>Kode elemen: {String(element.element_code ?? "")}</code><code>ID elemen: {String(element.element_id ?? "")}</code><code>Kunci kewajiban: {String(element.obligation_key ?? "")}</code><code>Derivasi: {JSON.stringify(element.derivation_refs ?? [])}</code><code>Grounding: {JSON.stringify(element.grounding_contract ?? {})}</code></details></li>;
      })}</ol>}
      {version.numeric_policies.length > 0 && <div className="published-numeric-policies"><h5>Kebijakan pemeriksaan angka</h5>{version.numeric_policies.map((raw, index) => { const policy = raw as Record<string, unknown>; return <article key={String(policy.id ?? index)}><strong>{String(policy.state) === "approved" ? "Disahkan" : statusLabel(String(policy.state ?? "tersimpan"))} · versi {String(policy.version_no ?? "—")}</strong><p>{String((policy.payload as Record<string, unknown> | undefined)?.metric_definition ?? "Kebijakan numerik tersimpan")}</p><p>Operasi: {String((policy.payload as Record<string, unknown> | undefined)?.comparison_mode ?? "—")} · toleransi absolut: {String((policy.payload as Record<string, unknown> | undefined)?.absolute_tolerance ?? "—")} · satuan: {String((policy.payload as Record<string, unknown> | undefined)?.canonical_unit ?? "—")}</p><details className="technical-details"><summary>Metadata kebijakan</summary><code>ID: {String(policy.id ?? "")}</code><code>SHA-256: {String(policy.content_hash ?? "")}</code></details></article>; })}</div>}
    </section>

    <section className="published-detail-section" aria-labelledby="published-sources-title"><h4 id="published-sources-title">File rubrik asli dan hasil pembacaan</h4><p className="help-text">File dan status parsing ditampilkan apa adanya. Keterbatasan di bawah tidak mengubah status keterbacaan atau coverage.</p>
      <ul className="published-rubric-inventory">{files.map((file) => <li key={file.id}><div><strong className="breakable-text">{file.name}</strong><span>{parseFiles.find((item) => item.id === file.id) ? statusLabel(parseFiles.find((item) => item.id === file.id)!.parse_status) : "Hasil parsing tidak tersedia"}</span></div><a href={`/api/v1/rubric-files/${file.id}/download`}>Unduh file asli</a><details className="technical-details"><summary>Hash file</summary><code>SHA-256: {file.sha256}</code><code>ID file: {file.id}</code></details></li>)}</ul>
      {rubricParse ? <ParserLimitationsSummary files={parseFiles} status={rubricParse.status} /> : <p className="help-text">Memuat inventaris parser…</p>}
    </section>
  </section>;
}

export function AssignmentWorkspace({
  course,
  csrfToken,
  approvedContexts,
  initialAssignments,
  contextsUnavailable = false,
  providerMode = "unknown",
}: {
  course: { id: string; code: string; title: string };
  csrfToken: string;
  approvedContexts: ContextSummary[];
  initialAssignments: AssignmentSummary[];
  contextsUnavailable?: boolean;
  providerMode?: "mock" | "off" | "live" | "unknown";
}) {
  const [assignments, setAssignments] = useState(initialAssignments);
  const [refreshedContextVersions, setRefreshedContextVersions] = useState<ContextSummary[] | null>(null);
  const contextVersions = refreshedContextVersions ?? approvedContexts;
  const [detail, setDetail] = useState<AssignmentDetail | null>(null);
  const [screen, setScreen] = useState<"list" | "create" | "edit">("list");
  const [activeStep, setActiveStep] = useState<"info" | "criteria" | "plan" | "publish">("info");
  const [contextId, setContextId] = useState(approvedContexts.find((item) => item.state === "approved")?.id ?? "");
  const [newTitle, setNewTitle] = useState("");
  const [instruction, setInstruction] = useState("");
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
  const numericKey = "numeric-comparison";
  const [taskFactValues, setTaskFactValues] = useState<Record<string, string>>({});
  const [taskFactOrigins, setTaskFactOrigins] = useState<Record<string, string>>({});
  const [numericJson, setNumericJson] = useState(DEFAULT_NUMERIC_POLICY_JSON);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [contextDetail, setContextDetail] = useState<ContextDetail | null>(null);
  const [contextDetailOpen, setContextDetailOpen] = useState(false);
  const assignmentLoadEpoch = useRef(0);
  const version = detail?.version ?? null;
  const criterionSet = version?.criterion_set ?? null;
  const specification = version?.specification ?? null;
  const latestLint = specification?.lint ?? null;
  const editable = version?.state === "draft";
  const hasApprovedContext = contextVersions.some((item) => item.state === "approved");
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
  const infoDirty = Boolean(version && (
    instruction !== version.instruction_original
    || mode !== version.mode
    || opensAt !== formatLocalDateTime(version.opens_at)
    || dueAt !== formatLocalDateTime(version.due_at)
    || latePolicy !== version.late_policy
  ));
  const criteriaDirty = Boolean(version && (
    stableJson(criteriaDraft) !== stableJson(version.criterion_set?.criteria ?? [])
    || selectedRubricIds.slice().sort().join("|") !== (version.rubric_sources ?? []).map((item) => item.id).sort().join("|")
  ));
  const specDirty = Boolean(version && specification && stableJson(specDraft) !== stableJson(specification.payload));
  const factsDirty = Boolean(version && stableJson({ values: taskFactValues, origins: taskFactOrigins }) !== stableJson({
    values: Object.fromEntries(Object.entries(version.task_facts?.facts ?? {}).map(([key, fact]) => [key, String(fact.value ?? "")])),
    origins: Object.fromEntries(Object.entries(version.task_facts?.facts ?? {}).map(([key, fact]) => [key, String(fact.origin ?? "")])),
  }));
  const hasNumericElements = ((Array.isArray(specDraft.elements) ? specDraft.elements : []) as Array<Record<string, unknown>>)
    .some((item) => item.target_kind === "numeric");
  const numericDirty = hasNumericElements && numericJson !== DEFAULT_NUMERIC_POLICY_JSON;
  const hasUnsavedChanges = infoDirty || criteriaDirty || specDirty || factsDirty || numericDirty;
  const versionContextId = version?.context_version_id ?? (screen === "create" ? contextId : "");

  useEffect(() => {
    if (!versionContextId) return;
    let active = true;
    void apiRequest<ContextDetail>(`/context-versions/${versionContextId}`, csrfToken, { method: "GET" })
      .then((result) => { if (active) setContextDetail(result); })
      .catch(() => { if (active) setContextDetail(null); });
    return () => { active = false; };
  }, [versionContextId, csrfToken]);

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

  const loadAssignment = useCallback(async (assignmentId: string, versionId?: string) => {
    const loadEpoch = ++assignmentLoadEpoch.current;
    setError("");
    setMessage("");
    setContextDetailOpen(false);
    setRubricParse(null);
    setRubricFiles([]);
    setGenerateExtractiveProposals(false);
    setProviderDraftJobId(null);
    setProviderDraftJob(null);
    setPublishPreview(null);
    const loaded = await apiRequest<AssignmentDetail>(
      `/assignments/${assignmentId}${versionId ? `?version_id=${encodeURIComponent(versionId)}` : ""}`, csrfToken, { method: "GET" },
    );
    if (loadEpoch !== assignmentLoadEpoch.current) return;
    setDetail(loaded);
    setScreen("edit");
    const loadedVersion = loaded.version;
    if (loadedVersion) {
      setInstruction(loadedVersion.instruction_original);
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
    } else {
      setInstruction("");
      setCriteriaJson("[]");
      setSpecJson("");
      setSelectedRubricIds([]);
      setTaskFactValues({});
      setTaskFactOrigins({});
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

  const startCreate = async () => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await apiRequest<{ items: ContextSummary[]; next_cursor: string | null }>(
        `/courses/${course.id}/context-versions`, csrfToken, { method: "GET" },
      );
      const latestApproved = result.items
        .filter((item) => item.state === "approved")
        .sort((left, right) => right.version_no - left.version_no)[0];
      setRefreshedContextVersions(result.items);
      setContextId(latestApproved?.id ?? "");
      if (!latestApproved) throw new Error("Sahkan konteks mata kuliah terlebih dahulu untuk membuat tugas baru.");
      assignmentLoadEpoch.current += 1;
      setDetail(null);
      setScreen("create");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Konteks mata kuliah terbaru belum dapat dimuat.");
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
    setScreen("edit");
    setActiveStep("info");
  }, "Draf tugas dibuat.");

  const saveVersion = () => execute(async () => {
    if (!version) return;
    const saved = await apiRequest<AssignmentVersion>(`/assignment-versions/${version.id}`, csrfToken, {
      method: "PUT",
      idempotency: true,
      body: {
        expected_revision: version.revision,
        instruction_original: instruction,
        // Keep historical summaries intact without exposing a summary editor.
        // New assignments have null here by contract.
        instruction_summary: version.instruction_summary,
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

  const openCriteriaDraft = () => {
    if (providerMode === "live" && generateExtractiveProposals && !window.confirm("Tindakan ini meminta draf bantuan dari provider live selain menyiapkan kutipan. Guard biaya server tetap berlaku dan penggunaan dapat menimbulkan biaya. Lanjutkan?")) return;
    return execute(async () => {
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
    }, "Draft kriteria siap diedit. Setiap kutipan tetap memerlukan keputusan dosen.");
  };

  const copyRubricQuote = async (item: RubricEvidence, fileName: string) => {
    const selectionEpoch = assignmentLoadEpoch.current;
    const sourceVersionId = version?.id;
    await navigator.clipboard.writeText(item.text);
    if (selectionEpoch === assignmentLoadEpoch.current && sourceVersionId === version?.id) {
      setMessage(`Teks kutipan dari ${fileName} disalin. Pilih rentangnya pada editor agar posisi dihitung otomatis.`);
    }
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
  }, "Kriteria dan kewajiban tersimpan.");

  const approvePackage = () => execute(async () => {
    if (!version || !criterionSet) return;
    await apiRequest(`/assignment-versions/${version.id}/approve-package`, csrfToken, {
      method: "POST", idempotency: true,
      body: { criterion_set_version_id: criterionSet.id, expected_revision: version.revision },
    });
    await loadAssignment(detail!.id);
  }, "Paket kriteria disahkan dan rujukan sumber terikat.");

  const createSpec = () => {
    if (providerMode === "live" && !window.confirm("Pembuatan draf ini juga meminta usulan dari provider live. Permintaan hanya diproses jika guard biaya server mengizinkan dan dapat menimbulkan biaya. Lanjutkan?")) return;
    return execute(async () => {
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
    }, "Rencana awal dibuat. Tinjau isi dan jalankan pemeriksaan sebelum pengesahan.");
  };

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
  }, "Spesifikasi tersimpan dan pemeriksaan otomatis baru dijalankan.");

  const resolveIssue = (issueCode: string, humanOnly: boolean) => execute(async () => {
    if (!specification || !latestLint) return;
    const reason = window.prompt("Alasan peninjauan dan konfirmasi bahwa rencana tetap sesuai:")?.trim();
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
  }, "Spesifikasi pemeriksaan v2 disahkan dengan konfirmasi kesetiaan dosen.");

  const createNumericPolicy = () => execute(async () => {
    if (!detail) return;
    const payload = JSON.parse(numericJson) as Record<string, unknown>;
    await apiRequest(`/assignments/${detail.id}/numeric-policy-drafts`, csrfToken, {
      method: "POST", idempotency: true, body: { policy_key: numericKey, payload },
    });
    const refreshed = await apiRequest<AssignmentDetail>(`/assignments/${detail.id}`, csrfToken, { method: "GET" });
    setDetail((current) => current?.version ? { ...current, version: { ...current.version, numeric_policies: refreshed.version?.numeric_policies ?? current.version.numeric_policies } } : current);
    setNumericJson(DEFAULT_NUMERIC_POLICY_JSON);
  }, "Kebijakan angka dibuat sebagai draf.");

  const approveNumericPolicy = (policy: Record<string, unknown>) => execute(async () => {
    const id = String(policy.id ?? "");
    const revision = Number(policy.revision ?? 0);
    if (!id || !revision) throw new Error("ID/revision NumericPolicy tidak tersedia.");
    await apiRequest(`/numeric-policy-versions/${id}/approve`, csrfToken, {
      method: "POST", idempotency: true, body: { expected_revision: revision },
    });
    const refreshed = await apiRequest<AssignmentDetail>(`/assignments/${detail!.id}`, csrfToken, { method: "GET" });
    setDetail((current) => current?.version ? { ...current, version: { ...current.version, numeric_policies: refreshed.version?.numeric_policies ?? current.version.numeric_policies } } : current);
  }, "Kebijakan angka disahkan.");

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

  const newVersion = () => {
    assignmentLoadEpoch.current += 1;
    return execute(async () => {
    if (!detail?.active_version_id) return;
    await apiRequest<{ id: string }>(`/assignments/${detail.id}/new-version`, csrfToken, {
      method: "POST", idempotency: true, body: { base_version_id: detail.active_version_id },
    });
    await refreshAssignments();
    await loadAssignment(detail.id);
    setActiveStep("info");
    }, "Draf versi baru dibuat tanpa mengubah snapshot terbit.");
  };

  const contexts = useMemo(() => contextVersions.filter((item) => item.state === "approved"), [contextVersions]);
  const effectiveStatus = latestLint?.effective_status ?? "belum dilint";
  const steps = [
    { id: "info" as const, label: "Informasi tugas", status: infoDirty ? "Belum disimpan" : instruction.trim() ? "Tersimpan" : "Belum diisi" },
    { id: "criteria" as const, label: "Kriteria pemeriksaan", status: criteriaDirty ? "Belum disimpan" : criterionSet?.state === "approved" ? "Disahkan" : criterionSet ? "Tersimpan" : "Belum dibuat" },
    { id: "plan" as const, label: "Rencana pemeriksaan", status: specDirty ? "Rencana belum disimpan" : factsDirty ? "Fakta tugas belum disahkan" : numericDirty ? "Kebijakan angka belum disimpan" : specification?.state === "approved" ? "Disahkan" : specification ? "Tersimpan" : "Belum dibuat" },
    { id: "publish" as const, label: "Pratinjau & terbitkan", status: publishPreview ? "Pratinjau dimuat" : "Menunggu pemeriksaan" },
  ];
  const activeContextDetail = contextDetail?.id === versionContextId ? contextDetail : null;
  const selectedAssignmentSummary = detail ? assignments.find((item) => item.id === detail.id) : undefined;
  const publishBlockers: Array<{ message: string; step: typeof steps[number]["id"] }> = [];
  if (!editable) publishBlockers.push({ message: "Versi ini bukan draf yang dapat diterbitkan.", step: "info" });
  if (infoDirty) publishBlockers.push({ message: "Simpan perubahan informasi tugas.", step: "info" });
  if (!criterionSet) publishBlockers.push({ message: "Buat dan lengkapi paket kriteria.", step: "criteria" });
  else if (criterionSet.state !== "approved") publishBlockers.push({ message: "Paket kriteria belum disahkan.", step: "criteria" });
  if (!specification) publishBlockers.push({ message: "Buat spesifikasi pemeriksaan dan jalankan pemeriksaan otomatis.", step: "plan" });
  else if (specification.state !== "approved") publishBlockers.push({ message: "Spesifikasi pemeriksaan belum disahkan setelah pemeriksaan otomatis lulus.", step: "plan" });
  if (specification && effectiveStatus !== "passed") publishBlockers.push({ message: `Pemeriksaan otomatis belum lulus (status: ${statusLabel(effectiveStatus)}).`, step: "plan" });
  if (criteriaDirty) publishBlockers.push({ message: "Simpan perubahan kriteria atau pilihan rubrik.", step: "criteria" });
  if (specDirty) publishBlockers.push({ message: "Simpan perubahan rencana pemeriksaan dan jalankan lint.", step: "plan" });
  if (factsDirty) publishBlockers.push({ message: "Simpan perubahan fakta tugas, atau kembalikan nilainya.", step: "plan" });
  if (numericDirty) publishBlockers.push({ message: "Simpan kebijakan angka yang berubah, atau kembalikan isinya.", step: "plan" });
  if (activeContextDetail && activeContextDetail.state !== "approved") publishBlockers.push({ message: "Konteks yang terikat tidak lagi disahkan.", step: "info" });
  const confirmUnsavedNavigation = (messageText: string) => !hasUnsavedChanges || window.confirm(
    `${messageText}\n\nAda isian yang belum disimpan. Jika diteruskan, perubahan tetap tersimpan di langkah ini selama sesi editor; perubahan akan hilang jika tugas ditutup atau diganti.`,
  );
  const navigateStep = (next: typeof activeStep) => {
    if (next === activeStep || !confirmUnsavedNavigation(`Buka langkah “${steps.find((step) => step.id === next)?.label ?? next}”?`)) return;
    setActiveStep(next);
    setError("");
    setMessage("");
  };
  const returnToList = () => {
    if (!confirmUnsavedNavigation("Kembali ke daftar tugas?")) return;
    assignmentLoadEpoch.current += 1;
    setScreen("list");
    setDetail(null);
    setContextDetailOpen(false);
    setError("");
    setMessage("");
  };
  const contextDescription = String(contextFieldValue(activeContextDetail?.fields.context_description) ?? "").trim() || "Belum ada deskripsi konteks.";
  const currentContextSummary = contextVersions.find((item) => item.id === versionContextId);
  const contextApprovedAt = activeContextDetail?.approved_at ?? currentContextSummary?.approved_at ?? null;
  const providerNotice = providerMode === "mock"
    ? "Mode mock aktif untuk data sintetis. Bantuan ini hanya dapat menyalin kutipan; mock tidak menyusun kriteria semantik."
    : providerMode === "off"
      ? "Bantuan AI sedang nonaktif. Dosen tetap dapat menyusun seluruh kriteria secara manual."
      : providerMode === "live"
        ? "Provider live terkonfigurasi. Permintaan yang dapat menimbulkan biaya tetap tunduk pada guard server; tidak ada panggilan yang dilakukan hanya dengan membuka halaman ini."
        : "Status provider belum dapat dipastikan. Jalur manual tetap tersedia; hasil ekstraksi kutipan bukan kriteria semantik.";

  return (
    <section className="workspace-panel" aria-labelledby="assignment-workspace-title">
      <div className="workspace-heading">
        <div><p className="eyebrow">RUANG DOSEN</p><h2 id="assignment-workspace-title">Kelola tugas</h2></div>
        {detail && <button type="button" className="button button-quiet" disabled={busy} onClick={() => void execute(refreshAssignments, "Daftar tugas diperbarui.")}>Muat ulang</button>}
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      {message && <p className="form-success" role="status">{message}</p>}
      {providerDraftJobId && <div className="lint-panel" role="region" aria-live="polite" aria-label="Status bantuan AI">
        <h3>Status bantuan · {providerDraftJob?.type === "criteria_draft" ? "kriteria" : "rencana pemeriksaan"}</h3>
        {!providerDraftJob && <p role="status">Menunggu status pemrosesan…</p>}
        {providerMode === "mock" && <p className="help-text">Mode mock memakai data sintetis. Tidak ada provider live yang dipanggil dan mock tidak membuat kesimpulan semantik.</p>}
        {providerMode === "off" && <p className="help-text">Bantuan AI nonaktif. Editor manual tetap tersedia.</p>}
        {providerDraftJob && ["queued", "running"].includes(providerDraftJob.status) && <p role="status">Usulan bantuan · {statusLabel(providerDraftJob.status)}.</p>}
        {providerDraftJob?.generation.label && <p className="notice">{providerDraftJob.generation.label} · semua usulan tetap memerlukan keputusan dosen.</p>}
        {providerDraftJob?.generation.status && <p>Status hasil: <strong>{statusLabel(providerDraftJob.generation.status)}</strong> · {statusLabel(providerDraftJob.status)}.</p>}
        {providerDraftJob?.generation.suggestions?.map((item, index) => <article className="rubric-evidence" key={`${item.title}-${index}`}><h4>{item.title}</h4><p>{item.description}</p>{item.source_refs.map((ref, refIndex) => <blockquote key={`${ref.source_kind}-${refIndex}`}><small>{ref.source_kind} · sumber terikat</small><p>{ref.quote}</p></blockquote>)}<small>Belum disahkan · perlu keputusan dosen</small></article>)}
        {providerDraftJob?.generation.limitations?.map((item, index) => <p className="help-text" key={`${item}-${index}`}>{item}</p>)}
        {providerDraftJob && ["blocked", "failed"].includes(providerDraftJob.status) && <p className="notice">Worker tidak menghasilkan usulan; editor manual tetap tersedia. Kode: {providerDraftJob.limitation_code ?? "manual_fallback"}.</p>}
      </div>}
      {screen === "list" && <section className="assignment-list-section" aria-labelledby="assignment-list-title">
        <div className="assignment-list-heading"><div><h3 id="assignment-list-title">Daftar tugas</h3><p className="help-text">Buka tugas untuk melanjutkan draf atau melihat versi terbit.</p></div><button type="button" className="button button-primary" disabled={busy || !hasApprovedContext} onClick={() => void startCreate()}>Buat tugas baru</button></div>
        {!hasApprovedContext && <p className="notice">{contextsUnavailable ? "Daftar konteks belum tersedia. Tugas yang sudah ada tetap dapat dibuka, tetapi tugas baru memerlukan konteks yang disahkan." : "Sahkan konteks mata kuliah terlebih dahulu untuk membuat tugas baru."}</p>}
        {assignments.length === 0 ? <div className="assignment-empty-state"><strong>Belum ada tugas</strong><p>Buat tugas baru setelah konteks mata kuliah disahkan.</p></div> : <div className="assignment-list">
          {assignments.map((item) => {
            const status = item.draft_version_id ? `Draf · versi ${item.draft_version_no}${item.active_version_id ? ` · snapshot terbit versi ${item.version_no}` : ""}` : item.active_version_id ? `Terbit · versi ${item.version_no}` : "Belum memiliki versi";
            return <article className="assignment-list-card" key={item.id}><div className="assignment-list-card-copy"><h4>{item.title}</h4><span className={`assignment-status ${item.draft_version_id ? "assignment-status-draft" : ""}`}>{status}</span><small>{item.administrative_state === "open" ? "Penerimaan aktif" : item.administrative_state === "closed" ? "Penerimaan ditutup" : "Status tugas tersimpan"}</small></div><div className="assignment-list-actions"><button type="button" className="button button-quiet" disabled={busy} aria-label={`Buka tugas ${item.title}`} onClick={() => { setActiveStep("info"); void execute(() => loadAssignment(item.id), "Tugas dibuka."); }}>Buka tugas</button>{item.draft_version_id && item.active_version_id && <button type="button" className="button button-quiet" disabled={busy} aria-label={`Lihat versi terbit ${item.title}`} onClick={() => void execute(() => loadAssignment(item.id, item.active_version_id ?? undefined), "Snapshot terbit dibuka dalam mode baca saja.")}>Lihat versi terbit</button>}</div></article>;
          })}
        </div>}
      </section>}
      {screen === "create" && <section className="assignment-create-panel" aria-labelledby="assignment-create-title">
        <div><p className="eyebrow">TUGAS BARU</p><h3 id="assignment-create-title">Informasi awal tugas</h3><p className="help-text">Judul dan konteks membuat draf. Instruksi, kriteria, dan rencana pemeriksaan diisi pada langkah berikutnya.</p></div>
        {!hasApprovedContext && <p className="notice">Belum ada konteks yang disahkan. Buat dan sahkan versi konteks pada tab Konteks terlebih dahulu.</p>}
        <div className="assignment-form-grid">
          <label className="field"><span>Judul tugas</span><input value={newTitle} maxLength={240} required autoComplete="off" onChange={(event) => setNewTitle(event.target.value)} /></label>
          <label className="field"><span>Konteks mata kuliah yang digunakan</span><select value={contextId} required onChange={(event) => setContextId(event.target.value)} disabled={!contexts.length}>
            {contexts.length === 0 && <option value="">Belum ada konteks disahkan</option>}
            {contexts.map((item) => <option key={item.id} value={item.id}>Versi {item.version_no} · disahkan</option>)}
          </select></label>
        </div>
        {activeContextDetail && <div className="context-summary-card"><div><strong>Konteks mata kuliah · versi {activeContextDetail.version_no}</strong><span>{contextApprovedAt ? `Disahkan ${new Date(contextApprovedAt).toLocaleString("id-ID")}` : "Tanggal persetujuan tidak tersedia"}</span></div><p>{contextDescription}</p><button type="button" className="button button-quiet" aria-expanded={contextDetailOpen} onClick={() => setContextDetailOpen((open) => !open)}>{contextDetailOpen ? "Tutup ringkasan" : "Lihat ringkasan konteks"}</button>{contextDetailOpen && <dl className="context-summary-details">{Object.entries(activeContextDetail.fields).map(([key, value]) => <div key={key}><dt>{contextFieldLabel(key)}</dt><dd>{Array.isArray(contextFieldValue(value)) ? (contextFieldValue(value) as unknown[]).join(", ") : String(contextFieldValue(value) ?? "—")}</dd></div>)}</dl>}<details className="technical-details"><summary>Detail teknis konteks</summary><code>SHA-256: {activeContextDetail.content_hash ?? "belum tersedia"}</code><code>ID versi: {activeContextDetail.id}</code></details></div>}
        <div className="button-row"><button type="button" className="button button-quiet" onClick={() => { assignmentLoadEpoch.current += 1; setScreen("list"); setError(""); setMessage(""); }}>Kembali ke daftar</button><button type="button" className="button button-primary" disabled={busy || !hasApprovedContext || !newTitle.trim() || !contextId} onClick={createAssignment}>Buat draf tugas</button></div>
      </section>}
      {detail && version?.state === "published" && <PublishedAssignmentDetail detail={detail} version={version} context={activeContextDetail} rubricParse={rubricParse} busy={busy} draftVersionNo={selectedAssignmentSummary?.draft_version_no} onBack={returnToList} onNewVersion={newVersion} onOpenDraft={() => void execute(() => loadAssignment(detail.id), "Draf tugas dibuka.")} />}
      {detail && version && version.state !== "published" && <>
        <div className="assignment-editor-heading"><div><p className="eyebrow">{detail.title} · VERSI {version.version_no}</p><h3>{version.state === "published" ? "Snapshot terbit" : "Draf tugas"}</h3><p className="help-text">{version.state === "published" ? "Versi ini terkunci. Buat versi baru untuk melakukan perubahan." : "Perubahan disimpan per langkah dan approval tetap terpisah."}</p></div><div className="button-row"><button type="button" className="button button-quiet" disabled={busy} onClick={returnToList}>Daftar tugas</button>{version.state === "published" && <button className="button button-primary" type="button" disabled={busy} onClick={newVersion}>Buat versi baru</button>}</div>
        </div>
        <nav className="assignment-stepper" aria-label="Langkah penyusunan tugas"><ol>{steps.map((step, index) => <li key={step.id}><button type="button" className={activeStep === step.id ? "is-active" : ""} aria-current={activeStep === step.id ? "step" : undefined} onClick={() => navigateStep(step.id)}><span className="assignment-step-number">{index + 1}</span><span className="assignment-step-copy"><strong>{step.label}</strong><small>{step.status}</small></span></button></li>)}</ol></nav>
        {versionContextId && <div className="context-summary-card"><div><strong>Konteks mata kuliah yang digunakan · versi {activeContextDetail?.version_no ?? currentContextSummary?.version_no ?? "—"}</strong><span>{contextApprovedAt ? `Disahkan ${new Date(contextApprovedAt).toLocaleString("id-ID")}` : activeContextDetail ? "Tanggal persetujuan tidak tersedia" : "Memuat ringkasan konteks…"}</span></div><p>{contextDescription}</p><button type="button" className="button button-quiet" aria-expanded={contextDetailOpen} onClick={() => setContextDetailOpen((open) => !open)}>{contextDetailOpen ? "Tutup ringkasan" : "Lihat ringkasan konteks"}</button>{contextDetailOpen && activeContextDetail && <dl className="context-summary-details">{Object.entries(activeContextDetail.fields).map(([key, value]) => <div key={key}><dt>{contextFieldLabel(key)}</dt><dd>{String(contextFieldValue(value) ?? "—")}</dd></div>)}</dl>}<details className="technical-details"><summary>Detail teknis konteks</summary><code>SHA-256: {activeContextDetail?.content_hash ?? version.context_version_id}</code><code>ID versi: {version.context_version_id}</code></details></div>}
        {activeStep === "info" && <section className="assignment-step" aria-labelledby="assignment-info-title"><div><h3 id="assignment-info-title">Informasi tugas</h3><p className="help-text">Instruksi asli menjadi acuan resmi dan disimpan utuh.</p></div><div className="assignment-form-grid">
          <label className="field field-wide"><span>Instruksi asli <span className="field-hint">maks. 30.000 karakter</span></span><textarea aria-label="Instruksi asli (maks. 30.000 karakter)" value={instruction} maxLength={30000} rows={9} disabled={!editable} onChange={(event) => setInstruction(event.target.value)} /></label>
          <label className="field"><span>Mode pengerjaan</span><select value={mode} disabled={!editable} onChange={(event) => setMode(event.target.value as "individual" | "group")}><option value="individual">Individual</option><option value="group">Kelompok</option></select></label>
          <label className="field"><span>Waktu tugas dibuka</span><input type="datetime-local" value={opensAt} required disabled={!editable} onChange={(event) => setOpensAt(event.target.value)} /></label>
          <label className="field"><span>Tenggat pengumpulan</span><input type="datetime-local" value={dueAt} required disabled={!editable} onChange={(event) => setDueAt(event.target.value)} /></label>
          <label className="field"><span>Kebijakan keterlambatan</span><select value={latePolicy} disabled={!editable} onChange={(event) => setLatePolicy(event.target.value as "accept_with_label" | "reject_after_due")}><option value="accept_with_label">Terima dan beri label terlambat</option><option value="reject_after_due">Tolak setelah tenggat</option></select></label>
          <p className="help-text field-wide">Waktu mengikuti zona perangkat Anda. Pastikan tenggat setelah waktu tugas dibuka.</p>
          <div className="field-actions"><button type="button" className="button button-primary" disabled={busy || !editable || !instruction.trim() || !opensAt || !dueAt || new Date(dueAt) <= new Date(opensAt)} onClick={saveVersion}>Simpan informasi tugas</button><button type="button" className="button button-quiet" onClick={() => navigateStep("criteria")}>Lanjut ke kriteria</button><span className={infoDirty ? "save-state is-dirty" : "save-state"}>{infoDirty ? "Perubahan belum disimpan" : "Semua perubahan tersimpan"}</span></div>
        </div></section>}
        {activeStep === "criteria" && <>
        {infoDirty && <p className="notice notice-warning">Simpan informasi tugas sebelum membuat atau mengubah kriteria. Isian langkah ini tetap tersimpan selama Anda berada di editor.</p>}
        <section className="assignment-step" aria-labelledby="rubric-title">
          <h3 id="rubric-title">Rubrik opsional</h3>
          <p className="help-text">File asli dipertahankan. Parser memakai pipeline B1 dan ekstraksi hanya menjadi sumber untuk ditinjau dosen. Bobot/level file tidak menjadi field penilaian ALIGN.</p>
          {editable && <label className="field"><span>Unggah PDF, DOCX, XLSX, JPG, JPEG, atau PNG</span><input type="file" accept=".pdf,.docx,.xlsx,.jpg,.jpeg,.png" onChange={uploadRubric} /></label>}
          {[...new Map([...rubricFiles, ...(version.rubric_sources ?? []).map((item) => ({ ...item, purpose: "rubric" as const }))].map((item) => [item.id, item])).values()].map((item) => <label className="check-row" key={item.id}><input type="checkbox" checked={selectedRubricIds.includes(item.id)} disabled={!editable} onChange={(event) => setSelectedRubricIds((current) => event.target.checked ? [...new Set([...current, item.id])] : current.filter((id) => id !== item.id))} /><span>{item.name} <a href={`/api/v1/rubric-files/${item.id}/download`}>Unduh asli</a></span></label>)}
          {rubricParse && rubricParse.files.length > 0 && <div className="lint-panel" aria-live="polite">
            <h4>Hasil parser rubrik · {statusLabel(rubricParse.status)}</h4>
            <p className="help-text">Ekstraksi hanya mengambil teks dan rujukan, bukan menyusun kriteria resmi. Dosen memilih kutipan dan menetapkan teks serta status wajib secara eksplisit.</p>
            <ParserLimitationsSummary files={rubricParse.files} />
            {rubricParse.files.map((file) => <section className="rubric-parse-file" key={file.id}>
              <h5 className="breakable-text">{file.name} · {statusLabel(file.parse_status)} · {file.evidence_count} unit bukti</h5>
              {file.evidence_truncated && <p className="notice">Daftar unit dibatasi untuk tampilan; total tetap {file.evidence_count}.</p>}
              {file.evidence.map((item) => <article className="rubric-evidence" key={item.id}>
                <div className="workspace-subheading"><strong>{item.type} · {statusLabel(item.parse_status)}</strong><details className="technical-details"><summary>Detail sumber</summary><code>SHA-256: {item.source_sha256}</code><code>Anchor: {JSON.stringify(item.anchor)}</code><code>ID bukti: {item.id}</code></details></div>
                <pre>{item.text || "Tidak ada teks yang diekstrak; tinjau file asli."}</pre>
                {item.text_truncated && <p className="notice">Kutipan dipotong untuk tampilan. Gunakan file asli untuk teks lengkap.</p>}
                {item.text && !item.text_truncated && <button type="button" className="button button-quiet" disabled={busy} onClick={() => void copyRubricQuote(item, file.name)}>Salin teks kutipan</button>}
              </article>)}
            </section>)}
          </div>}
        </section>
        <section className="assignment-step" aria-labelledby="criteria-title">
          <div className="workspace-subheading"><div><h3 id="criteria-title">Kriteria dan kewajiban</h3><p className="help-text">Susun kriteria, deskripsi, dan kewajiban yang perlu diperiksa. Dosen menetapkan wajib/opsional. Rujukan menunjukkan sumber; sistem menyimpan posisi kutipan dan hash untuk keterlacakan.</p></div>
            <div className="button-row">
              <label className="check-row"><input type="checkbox" checked={generateExtractiveProposals} disabled={busy || !editable || infoDirty || providerMode === "unknown"} onChange={(event) => setGenerateExtractiveProposals(event.target.checked)} /><span>{providerMode === "live" ? "Minta bantuan AI dan siapkan kutipan persis (dapat berbiaya)" : "Siapkan kutipan instruksi persis untuk ditinjau"}</span></label>
              {!criterionSet && <button type="button" className="button button-primary" disabled={busy || !editable || infoDirty} onClick={openCriteriaDraft}>{generateExtractiveProposals && providerMode === "live" ? "Minta bantuan AI dan buka formulir" : "Buka formulir kriteria"}</button>}
              {criterionSet && editable && criterionSet.state === "draft" && <button type="button" className="button button-quiet" disabled={busy || infoDirty} onClick={openCriteriaDraft}>{generateExtractiveProposals && providerMode === "live" ? "Minta bantuan AI dan perbarui sumber" : "Perbarui sumber rubrik"}</button>}
            </div>
          </div>
          <p className="provider-mode-note"><strong>Status bantuan:</strong> {providerNotice}</p>
          <p className="help-text">Kutipan yang disiapkan berasal persis dari sumber. Kutipan tidak menghasilkan atau mengesahkan kriteria semantik; kriteria manual tetap tersedia saat provider nonaktif atau mock.</p>
          {criteriaProposals.length > 0 && <div className="lint-panel" aria-live="polite"><h4>Potongan sumber untuk ditinjau</h4><p className="help-text">Teks di bawah disalin persis dari instruksi. Ini bukan kriteria atau kesimpulan makna; kewajiban dan statusnya tetap ditentukan dosen.</p>{criteriaProposals.map((proposal) => <blockquote className="proposal-quote" key={proposal.proposal_code}><p>{proposal.quote}</p><footer>Belum disahkan · keputusan dosen diperlukan</footer><details className="technical-details"><summary>Detail rujukan</summary><code>Kode usulan: {proposal.proposal_code}</code><code>Rentang karakter Unicode: {proposal.source_span.start}–{proposal.source_span.end}</code><code>SHA-256 sumber: {proposal.source_hash}</code></details></blockquote>)}</div>}
          {criterionSet && <>
            <p className="help-text">Status paket kriteria: <strong>{criterionSet.state === "approved" ? "disahkan" : "draf"}</strong> · perubahan setelah pengesahan memerlukan versi baru.</p>
            <details className="technical-details"><summary>Detail versi paket</summary><code>Revisi: {criterionSet.revision}</code><code>SHA-256: {criterionSet.content_hash ?? "belum tersedia"}</code><code>ID paket: {criterionSet.id}</code></details>
            <CriteriaStructuredEditor
              criteria={criteriaDraft}
              onChange={(next) => setCriteriaJson(JSON.stringify(next, null, 2))}
              instructionId={version.id}
              instruction={instruction}
              files={rubricParse?.files ?? []}
              outcomes={activeContextDetail?.learning_outcomes ?? null}
              disabled={!editable || criterionSet.state !== "draft"}
            />
            {editable && criterionSet.state === "draft" && <div className="button-row"><button type="button" className="button button-quiet" disabled={busy || infoDirty} onClick={saveCriteria}>Simpan kriteria</button><button type="button" className="button button-primary" disabled={busy || infoDirty || criteriaDirty || !criterionSet.criteria.length} onClick={approvePackage}>Sahkan paket kriteria</button></div>}
            {criterionSet.state === "approved" && <p className="notice">Paket kriteria disahkan. Perubahan berikutnya harus dilakukan pada draf versi baru.</p>}
          </>}
          {version.package_approved_at && <p className="form-success">Paket kriteria disahkan pada {new Date(version.package_approved_at).toLocaleString("id-ID")}.</p>}
          <div className="field-actions"><span className={criteriaDirty ? "save-state is-dirty" : "save-state"}>{criteriaDirty ? "Perubahan kriteria belum disimpan" : criterionSet?.state === "approved" ? "Paket kriteria disahkan" : "Kriteria tersimpan"}</span><button type="button" className="button button-quiet" onClick={() => navigateStep("info")}>Kembali ke informasi</button><button type="button" className="button button-quiet" onClick={() => navigateStep("plan")}>Lanjut ke rencana pemeriksaan</button></div>
        </section>
        </>}
        {activeStep === "plan" && <>
        {specification && !hasNumericElements && <p className="notice">Belum ada elemen pemeriksaan numerik. NumericPolicy tidak diperlukan untuk elemen lain. Pilih target “Numerik” pada elemen hanya jika memang ada kewajiban angka yang harus dibandingkan.</p>}
        {hasNumericElements && <section className="assignment-step" aria-labelledby="numeric-title">
          <h3 id="numeric-title">Kebijakan pemeriksaan angka</h3>
          <p className="help-text">Bagian ini muncul karena rencana memuat elemen numerik. Atur operasi, format angka, satuan, toleransi, dan sumber angka. Sistem tidak menebak angka atau menyamakan konteks yang berbeda.</p>
          {editable && <>
            <NumericPolicyStructuredEditor payload={numericDraft} onChange={(next) => setNumericJson(JSON.stringify(next, null, 2))} disabled={!editable} />
            <button type="button" className="button button-quiet" disabled={busy || !detail || infoDirty || criteriaDirty} onClick={createNumericPolicy}>Simpan kebijakan angka sebagai draf</button>
          </>}
          {version.numeric_policies.map((policy) => <div className="list-row numeric-policy-row" key={String(policy.id)}><span><strong>Kebijakan angka · versi {String(policy.version_no)}</strong><small>{String(policy.state) === "approved" ? "Disahkan" : "Draf"}</small><details className="technical-details"><summary>Metadata versi</summary><code>Kunci internal: {String(policy.policy_key)}</code><code>ID: {String(policy.id)}</code><code>SHA-256: {String(policy.content_hash ?? "belum tersedia")}</code></details></span>{editable && policy.state === "draft" && <button type="button" className="button button-quiet" disabled={busy} onClick={() => approveNumericPolicy(policy)}>Sahkan kebijakan</button>}</div>)}
        </section>}
        {criterionSet?.state === "approved" && <section className="assignment-step" aria-labelledby="spec-title">
          <div className="workspace-subheading"><div><h3 id="spec-title">Rencana pemeriksaan</h3><p className="help-text">Setiap elemen menjelaskan pertanyaan, kaitan ke kriteria, sumber derivasi, dan bentuk bukti. Kontrol lanjutan tetap tersedia; lint server memeriksa applicability, grounding, dependensi, coverage, dan guard.</p></div>
            {!specification && editable && <button type="button" className="button button-quiet" disabled={busy} onClick={createSpec}>{providerMode === "live" ? "Buat rencana dan minta draf bantuan AI" : "Buat draf rencana pemeriksaan"}</button>}
          </div>
          {specification && <>
            <p className="help-text">Versi rencana {specification.version_no} · lint: <strong>{effectiveStatus === "passed" ? "lulus" : effectiveStatus}</strong></p>
            <details className="technical-details"><summary>Detail teknis rencana</summary><code>Schema: {specification.schema_version}</code><code>Revisi: {specification.revision}</code><code>ID rencana: {specification.id}</code></details>
            {editable && specification.state === "draft" && <>
              <button type="button" className="button button-quiet" onClick={() => setSpecJson(JSON.stringify(requiredSpecTemplate(criterionSet.criteria), null, 2))}>Buat rencana dasar dengan tinjauan dosen</button>
              <p className="help-text">Rencana dasar hanya membuat titik awal. Dosen tetap meninjau tiap pertanyaan dan tidak ada kesimpulan semantik otomatis dari mock.</p>
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
              <button type="button" className="button button-quiet" disabled={busy || infoDirty || criteriaDirty} onClick={saveSpec}>Simpan rencana dan jalankan pemeriksaan</button>
            </>}
            {latestLint && <div className="lint-panel" aria-live="polite"><h4>Hasil pemeriksaan otomatis · {statusLabel(latestLint.effective_status)}</h4>
              {latestLint.issues.length === 0 && <p>Tidak ada masalah pemeriksaan otomatis pada revisi ini.</p>}
              {latestLint.issues.map((issue) => {
                const resolved = latestLint.resolutions.some((item) => item.issue_code === issue.issue_code);
                const humanOnly = issue.element_ids.length > 0 && (specification.payload.elements as Array<Record<string, unknown>> | undefined)?.filter((item) => issue.element_ids.includes(String(item.element_id))).every((item) => (item.grounding_contract as { mode?: string } | undefined)?.mode === "human_only");
                return <div className="list-row" key={issue.issue_code}><div><p>{issue.message}</p><small>{issue.severity === "review" ? "Perlu ditinjau" : issue.severity === "error" ? "Harus diperbaiki" : "Informasi"}{resolved ? " · sudah dicatat" : ""}</small><details className="technical-details"><summary>Detail aturan</summary><code>{issue.rule_code}</code><code>{issue.issue_code}</code></details></div>{editable && issue.severity === "review" && !resolved && <button type="button" className="button button-quiet" disabled={busy} onClick={() => resolveIssue(issue.issue_code, Boolean(humanOnly))}>Catat resolusi</button>}</div>;
              })}
            </div>}
            {editable && specification.state === "draft" && <button type="button" className="button button-primary" disabled={busy || effectiveStatus !== "passed" || infoDirty || criteriaDirty || specDirty} onClick={approveSpec}>Sahkan rencana dengan konfirmasi kesetiaan</button>}
          </>}
        </section>}
        {editable && specification?.state === "approved" && (Array.isArray((specification.payload as { task_facts?: unknown }).task_facts) && ((specification.payload as { task_facts: Array<{ key: string; value_type: string; allowed_values?: string[] | null }> }).task_facts).length > 0) && <section className="assignment-step" aria-labelledby="task-facts-title"><h3 id="task-facts-title">Fakta tugas · verifikasi dosen</h3><p className="help-text">Hanya fakta yang dikonfirmasi dosen untuk versi dan spesifikasi ini yang dapat menentukan penerapan. Nilai kosong berarti belum diketahui. Perubahan tugas membuat versi baru dan tidak mewarisi konfirmasi.</p>{((specification.payload as { task_facts: Array<{ key: string; value_type: string; allowed_values?: string[] | null }> }).task_facts).map((fact) => <div className="assignment-form-grid" key={fact.key}><label className="field"><span>{fact.key} · nilai</span>{fact.value_type === "boolean" ? <select aria-label={`${fact.key} nilai`} value={taskFactValues[fact.key] ?? ""} onChange={(event) => setTaskFactValues((current) => ({ ...current, [fact.key]: event.target.value }))}><option value="">Belum diketahui</option><option value="true">Ya</option><option value="false">Tidak</option></select> : fact.value_type === "string" && fact.allowed_values?.length ? <select aria-label={`${fact.key} nilai`} value={taskFactValues[fact.key] ?? ""} onChange={(event) => setTaskFactValues((current) => ({ ...current, [fact.key]: event.target.value }))}><option value="">Belum diketahui</option>{fact.allowed_values.map((value) => <option key={value} value={value}>{value}</option>)}</select> : <input aria-label={`${fact.key} nilai`} value={taskFactValues[fact.key] ?? ""} onChange={(event) => setTaskFactValues((current) => ({ ...current, [fact.key]: event.target.value }))} />}</label><label className="field"><span>Asal/verifikasi dosen · {fact.key}</span><input aria-label={`${fact.key} asal`} value={taskFactOrigins[fact.key] ?? ""} onChange={(event) => setTaskFactOrigins((current) => ({ ...current, [fact.key]: event.target.value }))} /></label></div>)}<button type="button" className="button button-quiet" disabled={busy || !editable} onClick={saveTaskFacts}>Simpan dan sahkan fakta dosen</button><p className="help-text">Fakta tersimpan: {Object.keys(version.task_facts?.facts ?? {}).length}</p><details className="technical-details"><summary>Detail verifikasi fakta</summary><code>Hash snapshot: {version.task_facts?.content_hash ?? "belum disahkan"}</code></details></section>}
        <div className="field-actions"><span className={specDirty || factsDirty || numericDirty ? "save-state is-dirty" : "save-state"}>{specDirty ? "Rencana belum disimpan" : factsDirty ? "Fakta tugas belum disahkan" : numericDirty ? "Kebijakan angka belum disimpan" : "Langkah tersimpan"}</span><button type="button" className="button button-quiet" onClick={() => navigateStep("criteria")}>Kembali ke kriteria</button><button type="button" className="button button-quiet" onClick={() => navigateStep("publish")}>Lanjut ke pratinjau</button></div>
        </>}
        {activeStep === "publish" && <section className="assignment-step" aria-labelledby="publish-title"><h3 id="publish-title">Pratinjau dan penerbitan</h3><p className="help-text">Versi ini akan menjadi snapshot yang dilihat mahasiswa. Publikasi tetap menunggu persetujuan paket normatif dan rencana pemeriksaan, serta lint yang lulus.</p>
          <div className="publish-checklist"><h4>Yang perlu dibereskan</h4>{publishBlockers.length === 0 ? <p className="form-success">Approval dan pemeriksaan wajib telah lulus. Muat pratinjau untuk memeriksa isi mahasiswa.</p> : <ul>{publishBlockers.map((blocker, index) => <li key={`${blocker.step}-${index}`}><span>{blocker.message}</span><button type="button" className="text-button" onClick={() => navigateStep(blocker.step)}>Buka bagian terkait</button></li>)}</ul>}</div>
          <div className="button-row"><button type="button" className="button button-quiet" disabled={busy || publishBlockers.length > 0 || !editable} onClick={loadPublishPreview}>Tampilkan pratinjau mahasiswa</button><button type="button" className="button button-primary" disabled={busy || publishBlockers.length > 0 || !editable || !publishPreview} onClick={publish}>Terbitkan versi ini</button></div>
          {publishPreview && <article className="student-task-preview" aria-label="Preview paket publik mahasiswa"><p className="eyebrow">VERSI YANG AKAN DIKUNCI · {publishPreview.title} · VERSI {publishPreview.version.version_no}</p><h4>Instruksi asli</h4><pre className="instruction-preview">{publishPreview.version.instruction_original}</pre>{publishPreview.version.instruction_summary && <p className="notice"><strong>{publishPreview.version.instruction_summary_label}</strong><br />{publishPreview.version.instruction_summary}</p>}<h4>Kriteria dan kewajiban</h4>{publishPreview.version.criteria.map((criterion) => <section className="criterion-card" key={criterion.code}><h5>{criterion.code} · {criterion.title}</h5><p>{criterion.descriptor}</p>{criterion.requirements.length > 0 && <ul>{criterion.requirements.map((requirement) => <li key={requirement.code}>{requirement.text}{requirement.mandatory ? " (wajib)" : " (opsional)"}</li>)}</ul>}</section>)}<h4>Bentuk bukti yang diterima</h4><ul>{publishPreview.version.accepted_evidence.map((item, index) => <li key={`${item.criterion_code}-${index}`}>{item.criterion_code}: {item.accepted_evidence_types.join(", ")}</li>)}</ul><p className="help-text">Mode: {publishPreview.version.mode} · Dibuka: {new Date(publishPreview.version.opens_at).toLocaleString("id-ID")} · Tenggat: {new Date(publishPreview.version.due_at).toLocaleString("id-ID")} · Kebijakan keterlambatan: {publishPreview.version.late_policy}.</p></article>}
        </section>}
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
        <h3>Kriteria dan kewajiban</h3>
        {criteria.map((criterion) => <section className="criterion-card" key={String(criterion.code)}><h4>{String(criterion.code)} · {String(criterion.title)}</h4><p>{String(criterion.descriptor)}</p>{Array.isArray(criterion.requirements) && <ul>{(criterion.requirements as Array<Record<string, unknown>>).map((requirement) => <li key={String(requirement.code)}>{String(requirement.text)}{requirement.mandatory ? " (wajib)" : " (opsional)"}</li>)}</ul>}</section>)}
        <h3>Bentuk bukti yang diterima</h3><ul>{evidence.map((item, index) => <li key={`${String(item.criterion_code)}-${index}`}>{String(item.criterion_code)}: {(Array.isArray(item.accepted_evidence_types) ? item.accepted_evidence_types as string[] : []).join(", ")}</li>)}</ul>
        <p className="help-text">Tenggat: {version.due_at ? new Date(String(version.due_at)).toLocaleString("id-ID") : "—"}. Kebijakan terlambat: {String(version.late_policy ?? "—")}.</p>
      </article>}
      <span className="sr-only">Course {courseId}</span>
    </section>
  );
}

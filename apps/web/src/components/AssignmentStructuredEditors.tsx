"use client";

import type { SyntheticEvent } from "react";

type JsonObject = Record<string, unknown>;
type Evidence = { id: string; type: string; anchor: Record<string, unknown>; text: string; text_truncated: boolean; source_sha256: string };
type RubricFile = { id: string; name: string; evidence: Evidence[] };
type OutcomeChoice = { id: string; code: string; description: string; resolution_status: string };
type Source = { kind: string; id: string; label: string; text: string; fileName?: string; fileId?: string; sourceHash?: string; anchor?: Record<string, unknown> };

const EVIDENCE_TYPES = ["paragraph", "table", "diagram", "image_text", "spreadsheet_cell", "spreadsheet_range"];
const OPERATORS = ["locate_evidence", "expand_context", "inspect_source_coverage", "interpret_relationship", "check_coverage", "establish_comparability", "compare_numbers", "request_human_review"];
const ABSTENTION_CODES = ["unreadable_relevant_region", "incomplete_source_inspection", "ambiguous_descriptor", "unresolved_applicability", "unverified_numeric_operand", "incomparable_numeric_context", "ambiguous_number_format", "unsupported_source_format", "processing_limit"];
const PREDICATES = ["covers", "compares", "supports", "applies", "traces_to", "reports_same_quantity"];
const UNITS = ["IDR", "IDR_thousand", "IDR_million", "ratio", "percent", "unit", "count_thousand", "m", "cm", "km", "kg", "g", "s", "min", "h"];
const UNIT_LABELS: Record<string, string> = {
  IDR: "Rupiah", IDR_thousand: "Ribuan rupiah", IDR_million: "Jutaan rupiah",
  ratio: "Rasio", percent: "Persen", unit: "Satuan", count_thousand: "Ribuan unit",
  m: "Meter", cm: "Sentimeter", km: "Kilometer", kg: "Kilogram", g: "Gram",
  s: "Detik", min: "Menit", h: "Jam",
};
const unitLabel = (unit: string) => UNIT_LABELS[unit] ?? unit;
const CONTEXT_FIELDS = ["metric", "entity", "period", "scenario", "population", "definition", "assumptions", "unit"];
const RULE_OPERATORS = ["all", "any", "not", "eq", "in", "contains", "gt", "gte", "lt", "lte"];

function obj(value: unknown): JsonObject { return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {}; }
function list(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
function text(value: unknown): string { return typeof value === "string" ? value : ""; }
function strings(value: unknown): string[] { return list(value).filter((item): item is string => typeof item === "string"); }
function updateAt<T>(values: T[], index: number, next: T): T[] { return values.map((value, current) => current === index ? next : value); }
function sourceLocation(anchor: unknown): string {
  const value = obj(anchor);
  const parts: string[] = [];
  const page = value.page ?? value.page_no ?? value.page_number;
  if (typeof page === "number") parts.push(`Halaman ${page}`);
  if (typeof value.sheet_name === "string") parts.push(`Lembar ${value.sheet_name}`);
  if (typeof value.cell_range === "string") parts.push(`Sel ${value.cell_range}`);
  if (typeof value.item_kind === "string") {
    const labels: Record<string, string> = { paragraph: "Paragraf", table: "Tabel", cell: "Sel", media: "Media" };
    const kind = labels[value.item_kind] ?? "Bagian";
    const item = value.item_index ?? value.paragraph_index;
    parts.push(Number.isInteger(item) ? `${kind} ${Number(item)}` : kind);
  } else if (Number.isInteger(value.paragraph_index)) {
    parts.push(`Paragraf ${Number(value.paragraph_index) + 1}`);
  }
  if (typeof value.row === "number" && typeof value.column === "number") parts.push(`Baris ${value.row}, kolom ${value.column}`);
  return parts.join(" · ") || "Lokasi tersedia pada detail sumber";
}
function CheckboxList({ label, values, options, disabled, onChange }: { label: string; values: string[]; options: readonly string[]; disabled?: boolean; onChange: (next: string[]) => void }) {
  const labels: Record<string, string> = {
    paragraph: "Paragraf", table: "Tabel", diagram: "Diagram", image_text: "Teks pada gambar",
    spreadsheet_cell: "Sel spreadsheet", spreadsheet_range: "Rentang spreadsheet",
    locate_evidence: "Temukan bukti", expand_context: "Periksa konteks sekitar", inspect_source_coverage: "Periksa cakupan sumber",
    interpret_relationship: "Tafsirkan hubungan", check_coverage: "Periksa kelengkapan", establish_comparability: "Pastikan kesepadanan angka",
    compare_numbers: "Bandingkan angka", request_human_review: "Minta tinjauan dosen",
    unreadable_relevant_region: "Bagian sumber tidak terbaca", incomplete_source_inspection: "Pemeriksaan sumber belum lengkap",
    ambiguous_descriptor: "Deskripsi ambigu", unresolved_applicability: "Penerapan belum dapat dipastikan",
    unverified_numeric_operand: "Angka belum terverifikasi", incomparable_numeric_context: "Konteks angka tidak sepadan",
    ambiguous_number_format: "Format angka ambigu", unsupported_source_format: "Format sumber tidak didukung", processing_limit: "Batas pemrosesan tercapai",
    metric: "Metrik", entity: "Entitas", period: "Periode", scenario: "Skenario", population: "Populasi",
    definition: "Definisi", assumptions: "Asumsi", unit: "Satuan",
  };
  return <fieldset className="structured-fieldset"><legend>{label}</legend><div className="structured-check-grid">{options.map((value) => <label className="check-row" key={value}><input type="checkbox" checked={values.includes(value)} disabled={disabled} onChange={(event) => onChange(event.target.checked ? [...new Set([...values, value])] : values.filter((item) => item !== value))} /><span>{labels[value] ?? value}</span></label>)}</div></fieldset>;
}

function sourceOptions(criteria: JsonObject[], instructionId: string, instruction: string, files: RubricFile[], allowRequirement: boolean): Source[] {
  const sources: Source[] = [{ kind: "instruction", id: instructionId, label: "Instruksi asli · teks tugas", text: instruction }];
  criteria.forEach((criterion) => {
    const criterionId = text(criterion.id);
    if (criterionId) sources.push({ kind: "criterion_descriptor", id: criterionId, label: `Deskripsi kriteria · ${text(criterion.code) || text(criterion.title)}`, text: text(criterion.descriptor) });
    for (const requirement of list(criterion.requirements).map(obj)) {
      const requirementId = text(requirement.id);
      if (allowRequirement && requirementId) sources.push({ kind: "requirement", id: requirementId, label: `Kewajiban · ${text(requirement.code)}`, text: text(requirement.text) });
    }
  });
  if (!allowRequirement) for (const file of files) for (const item of file.evidence) {
    if (!item.text_truncated && item.text) sources.push({
      kind: "rubric_evidence", id: item.id,
      label: `Rubrik · ${file.name} · ${sourceLocation(item.anchor)}`,
      text: item.text, fileName: file.name, fileId: file.id, sourceHash: item.source_sha256, anchor: item.anchor,
    });
  }
  return sources;
}

function SourceRefEditor({ refs, criteria, instructionId, instruction, files, allowRequirement, disabled, onChange }: {
  refs: JsonObject[]; criteria: JsonObject[]; instructionId: string; instruction: string; files: RubricFile[];
  allowRequirement: boolean; disabled?: boolean; onChange: (refs: JsonObject[]) => void;
}) {
  const options = sourceOptions(criteria, instructionId, instruction, files, allowRequirement);
  const patch = (index: number, next: JsonObject) => onChange(updateAt(refs, index, next));
  const add = () => {
    const first = options.find((source) => allowRequirement ? source.kind === "requirement" : source.kind === "instruction") ?? options[0];
    if (first) onChange([...refs, { source_kind: first.kind, source_id: first.id, span: first.kind === "requirement" ? null : { start: 0, end: 0 } }]);
  };
  return <div className="structured-subsection"><div className="workspace-subheading"><strong>{allowRequirement ? "Dasar kewajiban resmi" : "Sumber kewajiban"}</strong><button type="button" className="button button-quiet" disabled={disabled || refs.length >= 20 || !options.length} onClick={add}>Tambah rujukan</button></div>
    {refs.map((ref, index) => {
      const sourceKind = text(ref.source_kind);
      const sourceId = text(ref.source_id);
      const source = options.find((item) => item.kind === sourceKind && item.id === sourceId);
      const codepoints = Array.from(source?.text ?? "");
      const span = obj(ref.span);
      const start = Number.isInteger(span.start) ? Number(span.start) : 0;
      const end = Number.isInteger(span.end) ? Number(span.end) : 0;
      const updateSelectedQuote = (event: SyntheticEvent<HTMLTextAreaElement>) => {
        const textarea = event.currentTarget;
        if (!source || source.kind === "requirement" || textarea.selectionStart === textarea.selectionEnd) return;
        const startOffset = Array.from(source.text.slice(0, textarea.selectionStart)).length;
        const endOffset = Array.from(source.text.slice(0, textarea.selectionEnd)).length;
        if (endOffset > startOffset) patch(index, { ...ref, span: { start: startOffset, end: endOffset } });
      };
      const selectedQuote = source && source.kind !== "requirement" && end > start ? codepoints.slice(Math.max(0, start), Math.max(0, end)).join("") : "";
      return <div className="source-ref-card" key={`${sourceKind}-${sourceId}-${index}`}>
        <div className="structured-inline"><label className="field"><span>Sumber rujukan</span><select value={source ? `${source.kind}:${source.id}` : ""} disabled={disabled} onChange={(event) => {
          const nextSource = options.find((item) => `${item.kind}:${item.id}` === event.target.value);
          if (nextSource) patch(index, { source_kind: nextSource.kind, source_id: nextSource.id, span: nextSource.kind === "requirement" ? null : { start: 0, end: 0 } });
        }}><option value="">Pilih sumber</option>{options.map((item) => <option key={`${item.kind}:${item.id}`} value={`${item.kind}:${item.id}`}>{item.label}</option>)}</select></label>
          <button type="button" className="button button-quiet" disabled={disabled || refs.length <= 1} onClick={() => onChange(refs.filter((_, refIndex) => refIndex !== index))}>Hapus</button></div>
        {source && source.kind !== "requirement" && <><p className="help-text">{source.fileName ? <><strong>{source.fileName}</strong> · </> : null}{source.anchor ? sourceLocation(source.anchor) : source.label}. Sorot kutipan pada teks ini; posisi sumber akan dihitung otomatis.</p><label className="field"><span>Teks sumber untuk memilih kutipan</span><textarea aria-label="Teks sumber untuk memilih kutipan" readOnly rows={Math.min(9, Math.max(3, Math.ceil(source.text.length / 90)))} value={source.text} disabled={disabled} onSelect={updateSelectedQuote} /></label><div className="selected-source-quote"><strong>Kutipan yang dipilih</strong><p>{selectedQuote || "Belum ada kutipan. Sorot teks sumber di atas."}</p></div></>}
        {source?.kind === "requirement" && <div className="selected-source-quote"><strong>Kewajiban yang menjadi dasar</strong><p>{source.text}</p></div>}
        {source && <details className="technical-details"><summary>Detail teknis provenance</summary><code>Jenis sumber: {source.kind}</code><code>ID sumber: {source.id}</code>{source.fileId && <code>ID file: {source.fileId}</code>}<code>SHA-256: {(source.sourceHash ?? text(ref.source_hash)) || "dihitung saat disimpan"}</code>{source.anchor && <code>Locator: {JSON.stringify(source.anchor)}</code>}<code>Indeks code point: {start}–{end} (akhir tidak termasuk)</code></details>}
        {!source && <p className="notice notice-warning">Sumber tersimpan tidak tersedia untuk dipilih dalam versi ini. Referensi tidak dihapus; detail ID tetap dapat diperiksa di metadata teknis.</p>}
      </div>;
    })}
    {!refs.length && <p className="notice">Tambahkan setidaknya satu sumber normatif; kutipan yang cocok secara mekanis tidak membuktikan dukungan semantik.</p>}
  </div>;
}

export function CriteriaStructuredEditor({ criteria, onChange, instructionId, instruction, files, outcomes, disabled }: {
  criteria: JsonObject[]; onChange: (next: JsonObject[]) => void; instructionId: string; instruction: string; files: RubricFile[]; outcomes: OutcomeChoice[] | null; disabled?: boolean;
}) {
  const patchCriterion = (index: number, patch: JsonObject) => onChange(updateAt(criteria, index, { ...criteria[index], ...patch }));
  return <div className="structured-editor" aria-label="Editor kriteria dan kewajiban">
    <div className="workspace-subheading"><p className="help-text">Tidak ada bobot atau skor. Isi kewajiban, status wajib, dan makna akademik ditetapkan dosen; kutipan hanya menunjukkan lokasi sumber.</p><button type="button" className="button button-quiet" disabled={disabled || criteria.length >= 30} onClick={() => onChange([...criteria, { code: `C${criteria.length + 1}`, title: "", descriptor: "", display_order: criteria.length, requirements: [], outcome_ids: [] }])}>Tambah kriteria</button></div>
    {criteria.map((criterion, index) => {
      const requirements = list(criterion.requirements).map(obj);
      return <fieldset className="structured-card" key={text(criterion.id) || `criterion-${index}`}><legend>{text(criterion.code) || `Kriteria ${index + 1}`}</legend><div className="structured-inline"><label className="field"><span>Kode kriteria</span><input maxLength={80} value={text(criterion.code)} disabled={disabled} onChange={(event) => patchCriterion(index, { code: event.target.value })} /></label><label className="field"><span>Judul</span><input maxLength={240} value={text(criterion.title)} disabled={disabled} onChange={(event) => patchCriterion(index, { title: event.target.value })} /></label><label className="field"><span>Urutan tampil</span><input type="number" min={0} value={Number(criterion.display_order ?? index)} disabled={disabled} onChange={(event) => patchCriterion(index, { display_order: Number(event.target.value) })} /></label></div>
        <label className="field"><span>Deskripsi resmi kriteria</span><textarea maxLength={4000} rows={3} value={text(criterion.descriptor)} disabled={disabled} onChange={(event) => patchCriterion(index, { descriptor: event.target.value })} /></label>
        {<fieldset className="structured-fieldset"><legend>Pemetaan CPMK · opsional</legend>{outcomes === null ? <p className="help-text" role="status">Memuat pilihan CPMK dari konteks mata kuliah yang disahkan…</p> : outcomes.length === 0 ? <p className="help-text">Konteks mata kuliah yang disahkan tidak menyediakan CPMK. Pemetaan dapat dilewati.</p> : <div className="structured-check-grid">{outcomes.filter((item) => item.resolution_status === "confirmed").map((outcome) => <label className="check-row" key={outcome.id}><input type="checkbox" checked={strings(criterion.outcome_ids).includes(outcome.id)} disabled={disabled} onChange={(event) => patchCriterion(index, { outcome_ids: event.target.checked ? [...new Set([...strings(criterion.outcome_ids), outcome.id])] : strings(criterion.outcome_ids).filter((id) => id !== outcome.id) })} /><span><strong>{outcome.code}</strong> · {outcome.description}</span></label>)}</div>}{outcomes !== null && strings(criterion.outcome_ids).some((id) => !outcomes.some((item) => item.id === id)) && <p className="notice notice-warning">Ada pemetaan lama yang tidak cocok dengan daftar CPMK konteks ini. Pemetaan tersimpan tidak dibuang; validasi server tetap menentukan apakah pemetaan dapat disahkan.</p>}</fieldset>}
        <div className="workspace-subheading"><strong>Kewajiban bukti · {requirements.length}</strong><button type="button" className="button button-quiet" disabled={disabled || requirements.length >= 30} onClick={() => patchCriterion(index, { requirements: [...requirements, { code: `R${requirements.length + 1}`, text: "", mandatory: true, obligation_key: `obligation.${index + 1}.${requirements.length + 1}`, normative_source_refs: [] }] })}>Tambah kewajiban</button></div>
        {requirements.map((requirement, requirementIndex) => <div className="structured-subsection" key={text(requirement.id) || `${index}-requirement-${requirementIndex}`}><div className="structured-inline"><label className="field"><span>Kode kewajiban</span><input maxLength={80} value={text(requirement.code)} disabled={disabled} onChange={(event) => patchCriterion(index, { requirements: updateAt(requirements, requirementIndex, { ...requirement, code: event.target.value }) })} /></label><label className="check-row"><input type="checkbox" checked={requirement.mandatory === true} disabled={disabled} onChange={(event) => patchCriterion(index, { requirements: updateAt(requirements, requirementIndex, { ...requirement, mandatory: event.target.checked }) })} /><span>Wajib</span></label><button type="button" className="button button-quiet" disabled={disabled} onClick={() => patchCriterion(index, { requirements: requirements.filter((_, current) => current !== requirementIndex) })}>Hapus kewajiban</button></div><p className="help-text">Kunci keterlacakan dibuat dan dikelola sistem.</p><details className="technical-details"><summary>Detail teknis kewajiban</summary><code>Kunci: {text(requirement.obligation_key) || "dibuat saat disimpan"}</code><code>ID: {text(requirement.id) || "dibuat saat disimpan"}</code></details>
          <label className="field"><span>Teks kewajiban bukti</span><textarea maxLength={2000} rows={3} value={text(requirement.text)} disabled={disabled} onChange={(event) => patchCriterion(index, { requirements: updateAt(requirements, requirementIndex, { ...requirement, text: event.target.value }) })} /></label>
          <SourceRefEditor refs={list(requirement.normative_source_refs).map(obj)} criteria={criteria} instructionId={instructionId} instruction={instruction} files={files} allowRequirement={false} disabled={disabled} onChange={(refs) => patchCriterion(index, { requirements: updateAt(requirements, requirementIndex, { ...requirement, normative_source_refs: refs }) })} />
        </div>)}
        <button type="button" className="button button-quiet" disabled={disabled || criteria.length <= 1} onClick={() => onChange(criteria.filter((_, current) => current !== index))}>Hapus kriteria</button>
      </fieldset>;
    })}
  </div>;
}

function RuleNodeEditor({ node, facts, disabled, onChange, depth = 0 }: { node: JsonObject; facts: string[]; disabled?: boolean; onChange: (next: JsonObject) => void; depth?: number }) {
  const op = text(node.op) || "eq";
  const group = op === "all" || op === "any";
  const fact = text(node.fact) || "assignment.mode";
  const leafValue = node.value;
  const setOp = (nextOp: string) => onChange(nextOp === "all" || nextOp === "any" ? { op: nextOp, children: [node] } : nextOp === "not" ? { op: "not", child: node } : { op: nextOp, fact, ...(nextOp === "in" ? { values: Array.isArray(node.values) ? node.values : ["group"] } : { value: leafValue ?? "group" }) });
  const operatorLabels: Record<string, string> = { all: "Semua kondisi harus cocok", any: "Salah satu kondisi cocok", not: "Kondisi tidak cocok", eq: "Sama dengan", in: "Termasuk dalam pilihan", contains: "Memuat teks", gt: "Lebih besar dari", gte: "Sekurangnya", lt: "Lebih kecil dari", lte: "Paling banyak" };
  const factLabels: Record<string, string> = { "assignment.mode": "Mode tugas", "submission.file_types": "Jenis berkas yang dikumpulkan" };
  return <div className="rule-node" style={{ marginInlineStart: `${depth * 12}px` }}><div className="structured-inline"><label className="field"><span>Aturan pencocokan</span><select value={op} disabled={disabled} onChange={(event) => setOp(event.target.value)}>{RULE_OPERATORS.map((item) => <option key={item} value={item}>{operatorLabels[item] ?? item}</option>)}</select></label>{!group && op !== "not" && <label className="field"><span>Fakta tugas yang diperiksa</span><select value={fact} disabled={disabled} onChange={(event) => onChange({ ...node, fact: event.target.value })}>{facts.map((item) => <option key={item} value={item}>{factLabels[item] ?? item.replace(/^task\./, "Fakta tugas · ")}</option>)}</select></label>}</div>
    {group && <>{list(node.children).map((child, index) => <div className="rule-child" key={index}><RuleNodeEditor node={obj(child)} facts={facts} disabled={disabled} depth={depth + 1} onChange={(next) => onChange({ ...node, children: updateAt(list(node.children), index, next) })} /><button type="button" className="button button-quiet" disabled={disabled || list(node.children).length <= 1} onClick={() => onChange({ ...node, children: list(node.children).filter((_, childIndex) => childIndex !== index) })}>Hapus kondisi</button></div>)}<button type="button" className="button button-quiet" disabled={disabled} onClick={() => onChange({ ...node, children: [...list(node.children), { op: "eq", fact: "assignment.mode", value: "group" }] })}>Tambah kondisi</button></>}
    {op === "not" && <RuleNodeEditor node={obj(node.child)} facts={facts} disabled={disabled} depth={depth + 1} onChange={(next) => onChange({ ...node, child: next })} />}
    {!group && op !== "not" && op === "in" && <label className="field"><span>Nilai yang diizinkan (satu per baris)</span><textarea rows={2} value={strings(node.values).join("\n")} disabled={disabled} onChange={(event) => onChange({ ...node, values: event.target.value.split(/\r?\n/).filter(Boolean) })} /></label>}
    {!group && op !== "not" && op !== "in" && <label className="field"><span>Nilai yang dicocokkan</span><input value={String(leafValue ?? "")} disabled={disabled} onChange={(event) => onChange({ ...node, value: event.target.value })} /></label>}
  </div>;
}

function SpecElementEditor({ element, index, elements, criteria, policies, taskFacts, instructionId, instruction, files, disabled, onChange, onRemove }: {
  element: JsonObject; index: number; elements: JsonObject[]; criteria: JsonObject[]; policies: JsonObject[]; taskFacts: JsonObject[]; instructionId: string; instruction: string; files: RubricFile[]; disabled?: boolean;
  onChange: (next: JsonObject) => void; onRemove: () => void;
}) {
  const id = text(element.element_id);
  const selectedCriterionId = text(element.criterion_id);
  const selectedCriterion = criteria.find((item) => text(item.id) === selectedCriterionId) ?? criteria[0];
  const requirementIds = strings(element.requirement_ids);
  const grounding = obj(element.grounding_contract);
  const applicability = obj(element.applicability);
  const relations = list(element.relationships).map(obj);
  const patch = (key: string, value: unknown) => onChange({ ...element, [key]: value });
  const patchGrounding = (value: JsonObject) => patch("grounding_contract", value);
  const facts = ["assignment.mode", "submission.file_types", ...taskFacts.map((fact) => `task.${text(fact.key)}`).filter((fact) => fact !== "task.")];
  const operators = strings(element.operators);
  const evidenceTypes = strings(element.accepted_evidence_types);
  return <fieldset className="structured-card spec-element" key={id || index}><legend>{text(element.element_code) || `Elemen ${index + 1}`}</legend><div className="structured-inline"><label className="field"><span>Jenis pemeriksaan</span><select value={text(element.target_kind)} disabled={disabled} onChange={(event) => patch("target_kind", event.target.value)}><option value="requirement">Kewajiban eksplisit</option><option value="descriptor">Deskripsi kriteria</option><option value="numeric">Pemeriksaan angka</option></select></label><label className="field"><span>Kriteria terkait</span><select value={selectedCriterionId} disabled={disabled} onChange={(event) => { const criterion = criteria.find((item) => text(item.id) === event.target.value); patch("criterion_id", event.target.value); if (criterion) patch("requirement_ids", list(criterion.requirements).map((item) => text(obj(item).id)).filter(Boolean)); }}><option value="">Pilih kriteria</option>{criteria.map((item) => <option key={text(item.id)} value={text(item.id)}>{text(item.code)} · {text(item.title)}</option>)}</select></label></div>
    <details className="technical-details"><summary>ID dan kunci keterlacakan (dikelola ALIGN)</summary><code>Kode elemen: {text(element.element_code)}</code><code>ID elemen: {id}</code><code>Kunci kewajiban: {text(element.obligation_key)}</code></details>
    {selectedCriterion && text(element.target_kind) === "requirement" && <fieldset className="structured-fieldset"><legend>Kewajiban yang dicakup</legend><div className="structured-check-grid">{list(selectedCriterion.requirements).map((raw, reqIndex) => { const requirement = obj(raw); const requirementId = text(requirement.id); return <label className="check-row" key={requirementId || reqIndex}><input type="checkbox" checked={requirementIds.includes(requirementId)} disabled={disabled} onChange={(event) => patch("requirement_ids", event.target.checked ? [...new Set([...requirementIds, requirementId])] : requirementIds.filter((item) => item !== requirementId))} /><span><strong>{text(requirement.code)}</strong> · {text(requirement.text)}</span></label>; })}</div></fieldset>}
    <label className="field"><span>Pertanyaan keputusan positif</span><textarea maxLength={2000} rows={2} value={text(element.decision_question)} disabled={disabled} onChange={(event) => patch("decision_question", event.target.value)} /></label>
    <div className="structured-inline"><label className="field"><span>Bukti yang diharapkan</span><textarea maxLength={2000} rows={2} value={text(element.expected_evidence)} disabled={disabled} onChange={(event) => patch("expected_evidence", event.target.value)} /></label><label className="field"><span>Batas keputusan manusia</span><textarea maxLength={2000} rows={2} value={text(element.human_judgment_boundary)} disabled={disabled} onChange={(event) => patch("human_judgment_boundary", event.target.value)} /></label></div>
    <SourceRefEditor refs={list(element.derivation_refs).map(obj)} criteria={criteria} instructionId={instructionId} instruction={instruction} files={files} allowRequirement={true} disabled={disabled} onChange={(refs) => patch("derivation_refs", refs)} />
    <div className="structured-inline"><label className="field"><span>Dasar pemeriksaan bukti</span><select value={text(grounding.mode)} disabled={disabled} onChange={(event) => patchGrounding({ schema_version: 1, mode: event.target.value, absence_support: ["anchored_content", "linked_relationship"].includes(event.target.value) ? "approved_coverage_policy" : null })}><option value="human_only">Tinjauan dosen saja</option><option value="anchored_content">Bukti isi yang terikat ke sumber</option><option value="linked_relationship">Hubungan bukti antarbagian</option><option value="comparable_numeric">Perbandingan angka terverifikasi</option></select></label><label className="field"><span>Kesimpulan ketiadaan</span><select value={text(grounding.absence_support)} disabled={disabled || !["anchored_content", "linked_relationship"].includes(text(grounding.mode))} onChange={(event) => patchGrounding({ ...grounding, absence_support: event.target.value || null })}><option value="">Tidak digunakan</option><option value="approved_coverage_policy">Periksa seluruh sumber yang kompatibel</option></select></label></div>
    <CheckboxList label="Jenis bukti yang diterima" values={evidenceTypes} options={EVIDENCE_TYPES} disabled={disabled} onChange={(next) => patch("accepted_evidence_types", next)} />
    <div className="structured-subsection"><div className="workspace-subheading"><strong>Hubungan bukti</strong><button type="button" className="button button-quiet" disabled={disabled || relations.length >= 10} onClick={() => patch("relationships", [...relations, { relationship_code: `relation_${relations.length + 1}`, predicate: "covers", subject: "", object: "", qualifiers: { basis: null, period: null, scenario: null, metric: null } }])}>Tambah hubungan</button></div>{relations.map((relation, relationIndex) => <div className="source-ref-card" key={text(relation.relationship_code) || relationIndex}><details className="technical-details"><summary>Kode hubungan otomatis</summary><code>{text(relation.relationship_code)}</code></details><div className="structured-inline"><label className="field"><span>Jenis hubungan</span><select value={text(relation.predicate)} disabled={disabled} onChange={(event) => patch("relationships", updateAt(relations, relationIndex, { ...relation, predicate: event.target.value }))}>{PREDICATES.map((item) => <option key={item} value={item}>{({ covers: "Mencakup", compares: "Membandingkan", supports: "Mendukung", applies: "Menerapkan", traces_to: "Menelusuri ke", reports_same_quantity: "Melaporkan besaran yang sama" } as Record<string, string>)[item] ?? item}</option>)}</select></label></div><div className="structured-inline"><label className="field"><span>Pokok bahasan</span><input maxLength={500} value={text(relation.subject)} disabled={disabled} onChange={(event) => patch("relationships", updateAt(relations, relationIndex, { ...relation, subject: event.target.value }))} /></label><label className="field"><span>Isi atau tujuan hubungan</span><input maxLength={500} value={text(relation.object)} disabled={disabled} onChange={(event) => patch("relationships", updateAt(relations, relationIndex, { ...relation, object: event.target.value }))} /></label></div>{["basis", "period", "scenario", "metric"].map((key) => <label className="field" key={key}><span>{({ basis: "Dasar", period: "Periode", scenario: "Skenario", metric: "Metrik" } as Record<string, string>)[key] ?? key}</span><input maxLength={500} value={text(obj(relation.qualifiers)[key])} disabled={disabled} onChange={(event) => patch("relationships", updateAt(relations, relationIndex, { ...relation, qualifiers: { ...obj(relation.qualifiers), [key]: event.target.value || null } }))} /></label>)}<button type="button" className="button button-quiet" disabled={disabled} onClick={() => patch("relationships", relations.filter((_, current) => current !== relationIndex))}>Hapus relasi</button></div>)}</div>
    <div className="structured-subsection"><h4>Kapan pemeriksaan ini berlaku</h4><label className="field"><span>Penerapan</span><select value={text(applicability.mode)} disabled={disabled} onChange={(event) => { const mode = event.target.value; patch("applicability", mode === "always" ? { mode } : mode === "lecturer_required" ? { mode, question: "Apakah kondisi ini berlaku untuk tugas ini?" } : { mode, rule: { op: "eq", fact: "assignment.mode", value: "group" } }); }}><option value="always">Selalu berlaku</option><option value="fact_rule">Mengikuti fakta tugas</option><option value="lecturer_required">Dikonfirmasi dosen</option></select></label>{applicability.mode === "lecturer_required" && <label className="field"><span>Pertanyaan untuk dosen</span><input maxLength={2000} value={text(applicability.question)} disabled={disabled} onChange={(event) => patch("applicability", { ...applicability, question: event.target.value })} /></label>}{applicability.mode === "fact_rule" && <RuleNodeEditor node={obj(applicability.rule)} facts={facts} disabled={disabled} onChange={(rule) => patch("applicability", { mode: "fact_rule", rule })} />}</div>
    <label className="field"><span>Petunjuk sumber (satu per baris; tidak membatasi pencarian)</span><textarea rows={2} value={strings(element.source_hints).join("\n")} disabled={disabled} onChange={(event) => patch("source_hints", event.target.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean))} /></label>
    <CheckboxList label="Langkah pemeriksaan yang diizinkan" values={operators} options={OPERATORS} disabled={disabled} onChange={(next) => patch("operators", next)} />
    <div className="structured-inline"><label className="field"><span>Elemen yang harus diperiksa lebih dahulu</span><select multiple size={Math.min(5, Math.max(2, elements.length - 1))} value={strings(element.dependencies)} disabled={disabled} onChange={(event) => patch("dependencies", Array.from(event.target.selectedOptions, (option) => option.value))}>{elements.filter((item) => text(item.element_id) !== id).map((item) => <option key={text(item.element_id)} value={text(item.element_id)}>{text(item.element_code)}</option>)}</select><small>Pilih semua pemeriksaan yang menjadi prasyarat. ID dikelola sistem.</small></label><label className="field"><span>Kebijakan angka yang disahkan</span><select value={text(element.numeric_policy_version_id)} disabled={disabled || text(element.target_kind) !== "numeric"} onChange={(event) => patch("numeric_policy_version_id", event.target.value || null)}><option value="">Pilih kebijakan</option>{policies.filter((item) => item.state === "approved").map((item) => <option key={text(item.id)} value={text(item.id)}>{text(item.policy_key)} · versi {text(item.version_no)}</option>)}</select></label></div>
    <CheckboxList label="Kondisi ketika hasil ditahan untuk tinjauan" values={strings(element.abstention_conditions)} options={ABSTENTION_CODES} disabled={disabled} onChange={(next) => patch("abstention_conditions", next)} />
    <div className="notice"><strong>Pengaman cakupan tetap aktif:</strong> kesimpulan positif memerlukan dukungan yang cukup; kesimpulan ketiadaan memeriksa seluruh bagian sumber yang sesuai; bagian visual diperiksa; bagian yang belum tuntas mencegah kesimpulan ketiadaan; konteks tabel dipertahankan.</div>
    <button type="button" className="button button-quiet" disabled={disabled || elements.length <= 1} onClick={onRemove}>Hapus elemen</button>
  </fieldset>;
}

export function SpecificationStructuredEditor({ payload, onChange, criteria, policies, instructionId, instruction, files, disabled }: {
  payload: JsonObject; onChange: (next: JsonObject) => void; criteria: JsonObject[]; policies: JsonObject[]; instructionId: string; instruction: string; files: RubricFile[]; disabled?: boolean;
}) {
  const elements = list(payload.elements).map(obj);
  const facts = list(payload.task_facts).map(obj);
  const updateElement = (index: number, next: JsonObject) => onChange({ ...payload, elements: updateAt(elements, index, next) });
  return <div className="structured-editor" aria-label="Editor rencana pemeriksaan terstruktur"><div className="workspace-subheading"><p className="help-text">Setiap pertanyaan tetap terikat pada sumber yang disahkan. Sistem memeriksa kelengkapan sumber; kutipan atau kecocokan kata saja tidak membuktikan dukungan semantik.</p><button type="button" className="button button-quiet" disabled={disabled || elements.length >= 100 || !criteria.length} onClick={() => { const criterion = criteria[0]; const requirement = obj(list(criterion.requirements)[0]); const reqs = list(criterion.requirements).length ? [text(requirement.id)] : []; const next = { ...elements[0], element_id: crypto.randomUUID(), element_code: `EL-${elements.length + 1}`, criterion_id: text(criterion.id), requirement_ids: reqs, obligation_key: `obligation.${elements.length + 1}`, target_kind: reqs.length ? "requirement" : "descriptor", decision_question: "Apakah bukti relevan mendukung kewajiban resmi?", derivation_refs: reqs.length ? [{ source_kind: "requirement", source_id: reqs[0], span: null }] : [{ source_kind: "criterion_descriptor", source_id: text(criterion.id), span: { start: 0, end: Math.max(1, Array.from(text(criterion.descriptor)).length) } }], expected_evidence: "Bukti relevan yang ditinjau manusia.", grounding_contract: { schema_version: 1, mode: "human_only", absence_support: null }, accepted_evidence_types: ["paragraph"], relationships: [], applicability: { mode: "always" }, source_hints: [], operators: ["locate_evidence", "expand_context", "inspect_source_coverage", "request_human_review"], dependencies: [], coverage_policy: { positive_strategy: "sufficient_support", negative_strategy: "exhaustive_compatible_source_units", inspect_embedded_visuals: true, unresolved_compatible_unit_blocks_negative: true, preserve_table_context: true }, abstention_conditions: ["unreadable_relevant_region", "incomplete_source_inspection"], numeric_policy_version_id: null, human_judgment_boundary: "Keputusan semantik dan mutu akademik tetap dilakukan dosen." }; onChange({ ...payload, elements: [...elements, next] }); }}>Tambah pertanyaan pemeriksaan</button></div>
    {elements.map((element, index) => <SpecElementEditor key={text(element.element_id) || index} element={element} index={index} elements={elements} criteria={criteria} policies={policies} taskFacts={facts} instructionId={instructionId} instruction={instruction} files={files} disabled={disabled} onChange={(next) => updateElement(index, next)} onRemove={() => onChange({ ...payload, elements: elements.filter((_, current) => current !== index) })} />)}
    <fieldset className="structured-card"><legend>Fakta tugas terdaftar</legend><p className="help-text">Aturan penerapan hanya memakai fakta yang dihitung sistem atau disahkan dosen. Fakta yang belum diketahui tetap belum diketahui, bukan dianggap salah.</p><button type="button" className="button button-quiet" disabled={disabled || facts.length >= 20} onClick={() => onChange({ ...payload, task_facts: [...facts, { key: `fact_${facts.length + 1}`, value_type: "boolean", allowed_values: null }] })}>Tambah fakta tugas</button>{facts.map((fact, index) => <div className="structured-inline" key={text(fact.key) || index}><label className="field"><span>Nama fakta</span><input maxLength={64} value={text(fact.key)} disabled={disabled} onChange={(event) => onChange({ ...payload, task_facts: updateAt(facts, index, { ...fact, key: event.target.value }) })} /></label><label className="field"><span>Bentuk fakta</span><select value={text(fact.value_type)} disabled={disabled} onChange={(event) => onChange({ ...payload, task_facts: updateAt(facts, index, { ...fact, value_type: event.target.value, allowed_values: event.target.value === "string" ? [] : null }) })}><option value="boolean">Benar atau salah</option><option value="string">Teks</option><option value="decimal">Angka desimal</option></select></label>{fact.value_type === "string" && <label className="field"><span>Nilai yang diizinkan (satu per baris)</span><textarea rows={2} value={strings(fact.allowed_values).join("\n")} disabled={disabled} onChange={(event) => onChange({ ...payload, task_facts: updateAt(facts, index, { ...fact, allowed_values: event.target.value.split(/\r?\n/).filter(Boolean) })} )} /></label>}<button type="button" className="button button-quiet" disabled={disabled} onClick={() => onChange({ ...payload, task_facts: facts.filter((_, current) => current !== index) })}>Hapus fakta</button></div>)}</fieldset>
    <fieldset className="structured-card"><legend>Batas biaya dan pemrosesan</legend><div className="structured-inline"><label className="field"><span>Batas biaya usulan (USD)</span><input value={text(obj(payload.limits).draft_max_cost_usd)} disabled={disabled} onChange={(event) => onChange({ ...payload, limits: { ...obj(payload.limits), draft_max_cost_usd: event.target.value } })} /></label>{["max_text_calls", "max_vision_calls", "max_input_tokens", "max_output_tokens"].map((key) => <label className="field" key={key}><span>{{ max_text_calls: "Maksimum panggilan teks", max_vision_calls: "Maksimum panggilan visual", max_input_tokens: "Maksimum token masukan", max_output_tokens: "Maksimum token keluaran" }[key] ?? key}</span><input type="number" min={0} value={Number(obj(payload.limits)[key] ?? 0)} disabled={disabled} onChange={(event) => onChange({ ...payload, limits: { ...obj(payload.limits), [key]: Number(event.target.value) } })} /></label>)}</div><p className="help-text">Versi aturan pemeriksaan dikelola ALIGN dan ditolak server jika tidak dikenali.</p></fieldset>
  </div>;
}

const DEFAULT_POLICY: JsonObject = { metric_definition: "Besaran yang harus dibandingkan dalam tugas ini.", required_context_fields: ["metric", "entity", "definition", "unit"], number_locale: "id-ID", canonical_unit: "IDR", allowed_conversions: [], comparison_mode: "pairwise", absolute_tolerance: "0", relative_tolerance: "0", rounding: { mode: "none", quantum: null }, operand_roles: { left: "observasi kiri", right: "observasi kanan" } };

const SELECTOR_FIELDS = [
  ["sheet_name", "Nama sheet"],
  ["row_label", "Label baris"],
  ["column_label", "Label kolom"],
] as const;
const CONTEXT_TEXT_FIELDS = [
  ["metric", "Metrik"], ["entity", "Entitas"], ["period", "Periode"],
  ["scenario", "Skenario"], ["population", "Populasi"],
  ["definition", "Definisi"], ["assumptions", "Asumsi"],
] as const;

function NumericSourceConfiguration({ policy, roles, disabled, onChange }: {
  policy: JsonObject;
  roles: string[];
  disabled?: boolean;
  onChange: (next: JsonObject) => void;
}) {
  const selectors = obj(policy.source_selectors);
  const contexts = obj(policy.context_by_role);
  const enabled = policy.source_selectors !== null && policy.source_selectors !== undefined
    && policy.context_by_role !== null && policy.context_by_role !== undefined;
  const blankSelector = () => ({ sheet_name: "", row_label: "", column_label: "" });
  const blankContext = () => ({ metric: "", entity: "", definition: "", unit: "" });
  const asRows = (value: unknown) => Array.isArray(value) ? value.map(obj) : value && typeof value === "object" ? [obj(value)] : [];
  const setRoleRows = (role: string, nextSelectors: JsonObject[], nextContexts: JsonObject[]) => {
    onChange({
      source_selectors: { ...selectors, [role]: role === "operands" ? nextSelectors : nextSelectors[0] ?? blankSelector() },
      context_by_role: { ...contexts, [role]: role === "operands" ? nextContexts : nextContexts[0] ?? blankContext() },
    });
  };
  const setSelector = (role: string, index: number, key: string, value: string) => {
    const rows = asRows(selectors[role]);
    const next = [...(rows.length ? rows : [blankSelector()])];
    next[index] = { ...next[index], [key]: value };
    setRoleRows(role, next, asRows(contexts[role]).length ? asRows(contexts[role]) : [blankContext()]);
  };
  const setContext = (role: string, index: number, key: string, value: string) => {
    const rows = asRows(contexts[role]);
    const next = [...(rows.length ? rows : [blankContext()])];
    next[index] = { ...next[index], [key]: value };
    setRoleRows(role, asRows(selectors[role]).length ? asRows(selectors[role]) : [blankSelector()], next);
  };
  const toggle = (checked: boolean) => {
    if (!checked) {
      const hasEnteredSource = [selectors, contexts].some((group) => Object.values(group).some((value) => {
        const rows = Array.isArray(value) ? value.map(obj) : [obj(value)];
        return rows.some((row) => Object.values(row).some((field) => typeof field === "string" && field.trim().length > 0));
      }));
      if (hasEnteredSource && !window.confirm("Menonaktifkan pengambilan otomatis akan menghapus lokasi sel dan konteks angka yang diisi. Lanjutkan?")) return;
      onChange({ source_selectors: null, context_by_role: null });
      return;
    }
    const nextSelectors: JsonObject = {};
    const nextContexts: JsonObject = {};
    for (const role of roles) {
      const count = role === "operands" ? 1 : 0;
      nextSelectors[role] = role === "operands" ? Array.from({ length: count }, blankSelector) : blankSelector();
      nextContexts[role] = role === "operands" ? Array.from({ length: count }, blankContext) : blankContext();
    }
    onChange({ source_selectors: nextSelectors, context_by_role: nextContexts });
  };
  const addOperand = () => {
    const selectorRows = asRows(selectors.operands);
    const contextRows = asRows(contexts.operands);
    setRoleRows("operands", [...selectorRows, blankSelector()], [...contextRows, blankContext()]);
  };

  return <fieldset className="structured-card"><legend>Sumber angka dari spreadsheet (opsional)</legend><p className="help-text">Aktifkan hanya jika sistem boleh mengambil angka otomatis dari XLSX. Isikan nama sheet, label baris/kolom, dan konteks setiap nilai secara persis. Sumber yang tidak lengkap atau tidak terbaca tetap ditinjau manual; rumus, OCR, serta teks bebas tidak dijadikan angka otomatis.</p>
    <label className="check-row"><input type="checkbox" checked={enabled} disabled={disabled} onChange={(event) => toggle(event.target.checked)} /><span>Gunakan lokasi sel spreadsheet yang ditetapkan secara eksplisit</span></label>
    {enabled && <div className="numeric-source-roles">{roles.map((role) => {
      const selectorRows = asRows(selectors[role]);
      const contextRows = asRows(contexts[role]);
      const rows = Math.max(selectorRows.length, contextRows.length, 1);
      return <section className="numeric-role-card" key={role}><h4>{role === "left" ? "Nilai pertama" : role === "right" ? "Nilai pembanding" : role === "reported" ? "Nilai yang dilaporkan" : role === "numerator" ? "Pembilang" : role === "denominator" ? "Penyebut" : "Nilai operand yang dijumlahkan"}</h4>{Array.from({ length: rows }, (_, index) => <div className="numeric-source-row" key={`${role}-${index}`}><div className="structured-inline">{SELECTOR_FIELDS.map(([key, label]) => <label className="field" key={key}><span>{label} persis</span><input maxLength={key === "sheet_name" ? 120 : 240} value={text(selectorRows[index]?.[key])} disabled={disabled} onChange={(event) => setSelector(role, index, key, event.target.value)} /></label>)}</div><div className="structured-inline">{CONTEXT_TEXT_FIELDS.map(([key, label]) => <label className="field" key={key}><span>Konteks · {label.toLocaleLowerCase("id-ID")}</span><input maxLength={key === "definition" || key === "assumptions" ? 1000 : 240} value={text(contextRows[index]?.[key])} disabled={disabled} onChange={(event) => setContext(role, index, key, event.target.value)} /></label>)}<label className="field"><span>Konteks · satuan</span><select value={text(contextRows[index]?.unit)} disabled={disabled} onChange={(event) => setContext(role, index, "unit", event.target.value)}><option value="">Pilih satuan</option>{UNITS.map((unit) => <option key={unit} value={unit}>{unitLabel(unit)}</option>)}</select></label></div></div>)}{role === "operands" && <button type="button" className="button button-quiet" disabled={disabled || rows >= 30} onClick={addOperand}>Tambah nilai operand</button>}</section>;
    })}</div>}
  </fieldset>;
}

export function NumericPolicyStructuredEditor({ payload, onChange, disabled }: { payload: JsonObject; onChange: (next: JsonObject) => void; disabled?: boolean }) {
  const value = Object.keys(payload).length ? payload : DEFAULT_POLICY;
  const conversions = list(value.allowed_conversions).map(obj);
  const mode = text(value.comparison_mode) || "pairwise";
  const roleKeys = mode === "pairwise" ? ["left", "right"] : mode === "sum_against_reported" ? ["operands", "reported"] : ["numerator", "denominator", "reported"];
  const roles = obj(value.operand_roles);
  const rounding = obj(value.rounding);
  const patch = (key: string, next: unknown) => onChange({ ...value, [key]: next });
  const roleLabels: Record<string, string> = {
    left: "Nilai pertama", right: "Nilai pembanding", operands: "Nilai yang dijumlahkan",
    reported: "Nilai yang dilaporkan", numerator: "Pembilang", denominator: "Penyebut",
  };
  const changeMode = (nextMode: string) => {
    const nextKeys = nextMode === "pairwise" ? ["left", "right"] : nextMode === "sum_against_reported" ? ["operands", "reported"] : ["numerator", "denominator", "reported"];
    const nextRoles = Object.fromEntries(nextKeys.map((key) => [key, text(roles[key]) || roleLabels[key]]));
    const hasSourceConfiguration = Object.keys(obj(value.source_selectors)).length > 0 || Object.keys(obj(value.context_by_role)).length > 0;
    if (hasSourceConfiguration && !window.confirm("Mengganti operasi akan menghapus lokasi sel dan konteks sumber angka yang sudah diisi. Lanjutkan?")) return;
    onChange({
      ...value,
      comparison_mode: nextMode,
      operand_roles: nextRoles,
      ...(value.source_selectors !== undefined ? { source_selectors: null, context_by_role: null } : {}),
    });
  };
  return <div className="structured-editor" aria-label="Editor kebijakan numerik terstruktur">
    <label className="field"><span>Besaran yang diperiksa</span><textarea maxLength={2000} rows={3} value={text(value.metric_definition)} disabled={disabled} onChange={(event) => patch("metric_definition", event.target.value)} /></label>
    <CheckboxList label="Konteks yang wajib cocok (empat unsur inti selalu wajib)" values={strings(value.required_context_fields)} options={CONTEXT_FIELDS} disabled={disabled} onChange={(next) => patch("required_context_fields", next)} />
    <p className="help-text">Nilai hanya dibandingkan jika metrik, entitas, definisi, satuan, dan konteks relevan sepadan. Jika informasi tidak cukup, hasil tetap belum dapat disimpulkan.</p>
    <div className="structured-inline">
      <label className="field"><span>Format angka sumber</span><input minLength={2} maxLength={32} placeholder="id-ID" value={text(value.number_locale)} disabled={disabled} onChange={(event) => patch("number_locale", event.target.value)} /><small>Contoh: 1.234,56 pada format id-ID.</small></label>
      <label className="field"><span>Satuan hasil perbandingan</span><select value={text(value.canonical_unit)} disabled={disabled} onChange={(event) => patch("canonical_unit", event.target.value)}>{UNITS.map((unit) => <option key={unit} value={unit}>{unitLabel(unit)}</option>)}</select></label>
      <label className="field"><span>Operasi pemeriksaan</span><select value={mode} disabled={disabled} onChange={(event) => changeMode(event.target.value)}><option value="pairwise">Bandingkan dua nilai</option><option value="sum_against_reported">Jumlahkan beberapa nilai lalu bandingkan</option><option value="ratio_against_reported">Hitung rasio lalu bandingkan</option></select></label>
    </div>
    <div className="structured-inline">
      <label className="field"><span>Toleransi selisih tetap</span><input inputMode="decimal" placeholder="Contoh: 0,5" value={text(value.absolute_tolerance)} disabled={disabled} onChange={(event) => patch("absolute_tolerance", event.target.value)} /><small>Masukkan desimal eksplisit dalam satuan hasil.</small></label>
      <label className="field"><span>Toleransi relatif (0 sampai 1)</span><input inputMode="decimal" placeholder="Contoh: 0,01 untuk 1%" value={text(value.relative_tolerance)} disabled={disabled} onChange={(event) => patch("relative_tolerance", event.target.value)} /></label>
    </div>
    <div className="structured-inline">{roleKeys.map((key) => <label className="field" key={key}><span>Nama peran · {roleLabels[key]}</span><input maxLength={500} value={text(roles[key])} disabled={disabled} onChange={(event) => patch("operand_roles", { ...roles, [key]: event.target.value })} /></label>)}</div>
    <div className="structured-inline"><label className="field"><span>Pembulatan</span><select value={text(rounding.mode)} disabled={disabled} onChange={(event) => patch("rounding", { mode: event.target.value, quantum: event.target.value === "none" ? null : text(rounding.quantum) || "1" })}><option value="none">Tanpa pembulatan</option><option value="half_even">Setengah ke bilangan genap</option></select></label>{rounding.mode === "half_even" && <label className="field"><span>Kelipatan pembulatan positif</span><input inputMode="decimal" value={text(rounding.quantum)} disabled={disabled} onChange={(event) => patch("rounding", { ...rounding, quantum: event.target.value })} /></label>}</div>
    <fieldset className="structured-card"><legend>Konversi satuan yang diizinkan</legend><p className="help-text">Gunakan hanya konversi linear untuk satuan yang sejenis. Faktor konversi harus cocok dengan daftar resmi.</p><button type="button" className="button button-quiet" disabled={disabled || conversions.length >= 20} onClick={() => patch("allowed_conversions", [...conversions, { kind: "linear", from: "percent", to: "ratio", factor: "0.01" }])}>Tambah konversi</button>{conversions.map((conversion, index) => <div className="structured-inline" key={index}><label className="field"><span>Dari satuan</span><select value={text(conversion.from)} disabled={disabled} onChange={(event) => patch("allowed_conversions", updateAt(conversions, index, { ...conversion, from: event.target.value }))}>{UNITS.map((unit) => <option key={unit} value={unit}>{unitLabel(unit)}</option>)}</select></label><label className="field"><span>Ke satuan</span><select value={text(conversion.to)} disabled={disabled} onChange={(event) => patch("allowed_conversions", updateAt(conversions, index, { ...conversion, to: event.target.value }))}>{UNITS.map((unit) => <option key={unit} value={unit}>{unitLabel(unit)}</option>)}</select></label><label className="field"><span>Faktor desimal</span><input inputMode="decimal" value={text(conversion.factor)} disabled={disabled} onChange={(event) => patch("allowed_conversions", updateAt(conversions, index, { ...conversion, factor: event.target.value }))} /></label><button type="button" className="button button-quiet" disabled={disabled} onClick={() => patch("allowed_conversions", conversions.filter((_, current) => current !== index))}>Hapus</button></div>)}</fieldset>
    <NumericSourceConfiguration policy={value} roles={roleKeys} disabled={disabled} onChange={(changed) => onChange({ ...value, ...changed })} />
  </div>;
}

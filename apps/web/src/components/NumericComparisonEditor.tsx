"use client";

import { sourceLocation } from "./reviewPresentation";

type Policy = { id: string; policy_key: string; version_no: number; state: string; payload: Record<string, unknown> };
type Source = { id: string; file_name: string; anchor: Record<string, unknown>; content: Record<string, unknown> };
export type NumericComparison = { numeric_policy_version_id: string; verification_note: string; operands: Array<{ evidence_id: string; role: string; literal: string; source_quality: string; context: Record<string, string> }> };
const contextLabels: Record<string, string> = { metric: "Besaran yang dibandingkan", entity: "Objek yang diukur", period: "Periode", scenario: "Skenario", population: "Populasi", definition: "Definisi besaran", assumptions: "Asumsi", unit: "Satuan" };
const roleLabels: Record<string, string> = { left: "Angka pertama", right: "Angka pembanding", operand: "Komponen penjumlahan", reported: "Angka yang dilaporkan", numerator: "Pembilang", denominator: "Penyebut" };

export function NumericComparisonEditor({ policies, evidence, value, onChange }: {
  policies: Policy[]; evidence: Source[]; value: NumericComparison | null; onChange: (value: NumericComparison) => void;
}) {
  const approved = policies.filter((item) => item.state === "approved");
  const policy = approved.find((item) => item.id === value?.numeric_policy_version_id);
  function choose(id: string) {
    const selected = approved.find((item) => item.id === id);
    if (!selected) return;
    const roles = selected.payload.comparison_mode === "sum_against_reported" ? ["operand", "reported"] : selected.payload.comparison_mode === "ratio_against_reported" ? ["numerator", "denominator", "reported"] : ["left", "right"];
    const fields = Array.isArray(selected.payload.required_context_fields) ? selected.payload.required_context_fields.map(String) : ["unit"];
    onChange({ numeric_policy_version_id: id, verification_note: "", operands: roles.map((role) => ({ role, evidence_id: "", literal: "", source_quality: "lecturer_confirmed", context: Object.fromEntries(fields.map((field) => [field, field === "unit" ? String(selected.payload.canonical_unit ?? "") : ""])) })) });
  }
  const operation = policy?.payload.comparison_mode;
  const conversions = Array.isArray(policy?.payload.allowed_conversions) ? policy.payload.allowed_conversions as Array<{ from_unit: string; to_unit: string; factor: string }> : [];
  const units = [...new Set([String(policy?.payload.canonical_unit ?? ""), ...conversions.flatMap((item) => [item.from_unit, item.to_unit])])].filter(Boolean);
  return <section className="numeric-comparison-form" aria-label="Perbandingan angka">
    <label className="field-label">Aturan perbandingan yang disahkan<select value={value?.numeric_policy_version_id ?? ""} onChange={(event) => choose(event.target.value)}><option value="">Pilih aturan</option>{approved.map((item) => <option key={item.id} value={item.id}>{item.policy_key} · versi {item.version_no}</option>)}</select></label>
    {!approved.length && <p className="notice">Belum ada aturan numerik yang disahkan untuk pemeriksaan ini. Catat komentar biasa tanpa mengklaim perbandingan angka terverifikasi.</p>}
    {value && policy && <>
      <p>{operation === "sum_against_reported" ? "Jumlah komponen dibandingkan dengan angka yang dilaporkan." : operation === "ratio_against_reported" ? "Rasio pembilang dan penyebut dibandingkan dengan angka yang dilaporkan." : "Dua angka dibandingkan memakai aturan resmi tugas."}</p>
      <p className="help-text">Format angka: {String(policy.payload.number_locale)}. Satuan acuan: {String(policy.payload.canonical_unit)}. Angka tetap dihitung backend menggunakan Decimal.</p>
      <details><summary>Toleransi dan satuan yang diizinkan</summary><dl><div><dt>Toleransi absolut</dt><dd>{String(policy.payload.absolute_tolerance)}</dd></div><div><dt>Toleransi relatif</dt><dd>{String(policy.payload.relative_tolerance)}</dd></div><div><dt>Satuan</dt><dd>{units.join(", ")}</dd></div></dl><ul>{conversions.map((item, index) => <li key={index}>{item.from_unit} → {item.to_unit}: faktor {item.factor}</li>)}</ul></details>

      {value.operands.map((operand, index) => <fieldset key={index}><legend>{roleLabels[operand.role] ?? "Angka sumber"}</legend>
        <label className="field-label">Bukti angka<select value={operand.evidence_id} onChange={(event) => onChange({ ...value, operands: value.operands.map((item, n) => n === index ? { ...item, evidence_id: event.target.value } : item) })}><option value="">Pilih sumber</option>{evidence.map((source) => <option key={source.id} value={source.id}>{source.file_name} · {sourceLocation(source.anchor)} · {String(source.content.text ?? source.content.value ?? "Lokasi sumber").slice(0, 120)}</option>)}</select></label>
        {evidence.filter((source) => source.id === operand.evidence_id).map((source) => <aside key={source.id}><p>{source.file_name} · {sourceLocation(source.anchor)}</p><p>{String(source.content.text ?? source.content.value ?? "Lihat sumber asli untuk isi bukti ini.")}</p></aside>)}
        <label className="field-label">Angka persis seperti di sumber<input value={operand.literal} maxLength={120} onChange={(event) => onChange({ ...value, operands: value.operands.map((item, n) => n === index ? { ...item, literal: event.target.value } : item) })} /></label>
        {Object.entries(operand.context).map(([field, text]) => <label className="field-label" key={field}>{contextLabels[field] ?? field}{field === "unit" ? <select value={text} onChange={(event) => onChange({ ...value, operands: value.operands.map((item, n) => n === index ? { ...item, context: { ...item.context, [field]: event.target.value } } : item) })}><option value="">Pilih satuan</option>{units.map((unit) => <option key={unit} value={unit}>{unit}</option>)}</select> : <input value={text} onChange={(event) => onChange({ ...value, operands: value.operands.map((item, n) => n === index ? { ...item, context: { ...item.context, [field]: event.target.value } } : item) })} />}</label>)}
        {operation === "sum_against_reported" && operand.role === "operand" && value.operands.filter((item) => item.role === "operand").length > 1 && <button type="button" className="text-button" onClick={() => onChange({ ...value, operands: value.operands.filter((_, n) => n !== index) })}>Hapus komponen</button>}
      </fieldset>)}
      {operation === "sum_against_reported" && value.operands.length < 20 && <button className="button button-quiet" type="button" onClick={() => onChange({ ...value, operands: [...value.operands, { ...value.operands[0], context: { ...value.operands[0].context }, role: "operand", literal: "", evidence_id: "" }] })}>Tambah komponen penjumlahan</button>}
      <label className="field-label">Konfirmasi pemeriksaan sumber<textarea value={value.verification_note} maxLength={2000} placeholder="Jelaskan pemeriksaan Anda atas angka dan konteks sumber asli, termasuk OCR atau cache formula bila ada." onChange={(event) => onChange({ ...value, verification_note: event.target.value })} /></label>
    </>}
  </section>;
}

"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ParserLimitations } from "./ParserLimitations";
import { processingLabel, sourceLocation } from "./reviewPresentation";
import { PdfSourceViewer } from "./PdfSourceViewer";

type Unit = { id: string; unit_type: string; anchor: Record<string, unknown>; parse_status: string; limitations: Array<Record<string, unknown>> };
type FileSources = { items: Unit[]; next_cursor: number | null; parse_status: string; limitations: Array<Record<string, unknown>>; file: { id: string; name: string; mime: string; preflight: Record<string, unknown>; content_url: string } };
type Rendered = { source_unit: Unit; evidence: Array<{ id: string; type: string; content: Record<string, unknown>; parse_status: string; limitations: Array<Record<string, unknown>> }>; file: { id: string; name: string; mime: string; sha256: string; preflight?: Record<string, unknown>; content_url: string } };
export type ManualAnchorSelection = { file_id: string; source_sha256: string; anchor: Record<string, unknown>; original_evidence_id: string | null; source_unit_id: string };
type SpreadsheetCell = { source_unit_id: string; anchor: Record<string, unknown>; content: Record<string, unknown>; parse_status: string; limitations: Array<Record<string, unknown>> };
type SpreadsheetPage = { sheet: { name: string; state: string }; items: SpreadsheetCell[]; next_cursor: number | null; total_preflight_nonempty_cells: number };

async function requestJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: "no-store", credentials: "same-origin" });
  const value = (await response.json().catch(() => ({}))) as T;
  if (!response.ok) {
    const payload = value as T & { detail?: { code?: string; message?: string } };
    if (response.status === 410 && payload.detail?.code === "SOURCE_PURGED") {
      throw new Error(payload.detail.message ?? "Sumber dihapus sesuai kebijakan retensi.");
    }
    throw new Error("Sumber tidak dapat dibuka atau anchor tidak lagi valid.");
  }
  return value;
}

export function SubmissionFileViewer({ fileId, initialSourceUnitId, onManualAnchor, refreshToken = 0 }: {
  fileId: string; initialSourceUnitId?: string; refreshToken?: number; onManualAnchor?: (selection: ManualAnchorSelection) => void;
}) {
  const [sources, setSources] = useState<FileSources | null>(null);
  const [selected, setSelected] = useState<Rendered | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [selectionPending, setSelectionPending] = useState(false);
  const sourceRequest = useRef(0);

  useEffect(() => {
    let live = true;
    void requestJson<FileSources>(`/api/v1/files/${fileId}/source-units?limit=50`)
      .then(async (result) => {
        if (!live) return;
        setSources(result); setCursor(result.next_cursor); setError("");
        const initialId = initialSourceUnitId ?? result.items.find((unit) => ["paragraph", "table", "cell", "page", "image"].includes(unit.unit_type))?.id ?? result.items[0]?.id;
        if (initialId) {
          const requestId = ++sourceRequest.current;
          setSelectionPending(true);
          try {
            const rendered = await requestJson<Rendered>(`/api/v1/source-units/${initialId}/render`);
            if (live && requestId === sourceRequest.current) setSelected(rendered);
          } finally {
            if (live && requestId === sourceRequest.current) setSelectionPending(false);
          }
        }
      })
      .catch((cause: unknown) => { if (live) setError(cause instanceof Error ? cause.message : "Sumber tidak dapat dibuka."); });
    return () => { live = false; };
  }, [fileId, initialSourceUnitId, refreshToken]);

  async function selectUnit(unit: Unit) {
    const requestId = ++sourceRequest.current;
    setSelectionPending(true); setError("");
    try {
      const rendered = await requestJson<Rendered>(`/api/v1/source-units/${unit.id}/render`);
      if (requestId === sourceRequest.current) setSelected(rendered);
    } catch (cause) {
      if (requestId === sourceRequest.current) setError(cause instanceof Error ? cause.message : "Anchor tidak tersedia.");
    } finally {
      if (requestId === sourceRequest.current) setSelectionPending(false);
    }
  }

  async function selectSourceById(id: string) {
    const requestId = ++sourceRequest.current;
    setSelectionPending(true); setError("");
    try {
      const rendered = await requestJson<Rendered>(`/api/v1/source-units/${id}/render`);
      if (requestId === sourceRequest.current) setSelected(rendered);
    } catch (cause) {
      if (requestId === sourceRequest.current) setError(cause instanceof Error ? cause.message : "Anchor tidak tersedia.");
    } finally {
      if (requestId === sourceRequest.current) setSelectionPending(false);
    }
  }

  async function loadMore() {
    if (cursor === null) return;
    try {
      const page = await requestJson<FileSources>(`/api/v1/files/${fileId}/source-units?cursor=${cursor}&limit=50`);
      setSources((value) => value ? { ...value, items: [...value.items, ...page.items] } : page);
      setCursor(page.next_cursor);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Daftar sumber gagal dimuat."); }
  }

  const anchor = selected?.source_unit.anchor ?? {};
  const evidence = selected?.evidence ?? [];
  const limitations = [...(selected?.source_unit.limitations ?? []), ...evidence.flatMap((item) => item.limitations)];

  return (
    <section className="submission-viewer-panel" aria-label={`Viewer sumber ${sources?.file.name ?? ""}`}>
      {error && <p className="form-error" role="alert">{error}</p>}
      {sources && <>
        <div className="submission-viewer-heading"><strong>{sources.file.name}</strong><span>{processingLabel(sources.parse_status)}</span>
          <a className="button button-quiet" href={sources.file.content_url} download={sources.file.name}>Unduh asli</a></div>
        <div className="source-unit-layout">
          <div className="source-unit-picker"><label className="field-label">Lokasi dalam berkas<select value={selected?.source_unit.id ?? ""} onChange={(event) => { const unit = sources.items.find((item) => item.id === event.target.value); if (unit) void selectUnit(unit); }}><option value="">Pilih lokasi sumber</option>{sources.items.map((unit, index) => <option key={unit.id} value={unit.id}>{index + 1}. {sourceLocation(unit.anchor)} · {processingLabel(unit.parse_status)}</option>)}</select></label>{cursor !== null && <button type="button" className="button button-quiet" onClick={() => void loadMore()}>Muat lokasi berikutnya</button>}</div>
          <div className="source-unit-content">
            {selected && <>
              <p className="help-text"><strong>Lokasi:</strong> {anchorLabel(selected.source_unit.anchor)} · {processingLabel(selected.source_unit.parse_status)}</p>
              <ParserLimitations entries={limitations} />
              {selected.file.mime === "application/pdf" && <PdfSourceViewer src={selected.file.content_url} anchor={selected.source_unit.anchor as { page?: number; bbox?: number[] }} name={selected.file.name} />}
              {selected.file.mime.startsWith("image/") && <ImageViewer src={selected.file.content_url} name={selected.file.name}
                preflight={sources.file.preflight} anchor={selected.source_unit.anchor}
                zoom={zoom} setZoom={setZoom} />}
              {selected.file.mime.endsWith("wordprocessingml.document") && <article className="document-structure-viewer">
                <p><strong>Lokasi:</strong> {sourceLocation(anchor)}</p>
                {evidence.map((item) => <pre className="instruction-preview" key={item.id}>{String(item.content.text ?? item.content.value ?? "Konten nonteks; periksa berkas asli atau rincian sumber.")}</pre>)}
                {!evidence.length && <p className="notice">Unit ini tidak memiliki teks hasil ekstraksi. Alasan parser tetap tercatat di atas.</p>}
              </article>}
              {selected.file.mime.endsWith("spreadsheetml.sheet") && <SpreadsheetDocumentViewer key={String(anchor.sheet_name ?? "root")} fileId={sources.file.id} preflight={sources.file.preflight} anchor={anchor} onSelectSource={(id) => void selectSourceById(id)} />}
              {!selected.file.mime.includes("pdf") && !selected.file.mime.startsWith("image/") && !selected.file.mime.endsWith("wordprocessingml.document") && !selected.file.mime.endsWith("spreadsheetml.sheet") && <p className="notice">Unit ini tidak didukung viewer terstruktur. Berkas asli tetap tersedia.</p>}
            </>}
          </div>
        </div>
        <ParserLimitations entries={sources.limitations} />
        {!sources.items.length && <p className="notice">Lokasi terstruktur belum tersedia. Anda tetap dapat mengunduh berkas asli dan mencatat bukti manual.</p>}
        <details><summary>Detail teknis sumber</summary><p>{sources.file.mime}</p><pre>{JSON.stringify({ source: selected?.source_unit, evidence: selected?.evidence }, null, 2)}</pre></details>
        {selectionPending && <p role="status" className="help-text">Memuat anchor unit yang dipilih…</p>}
        {selected && onManualAnchor && <button type="button" className="button button-quiet" disabled={selectionPending} onClick={() => onManualAnchor({
          file_id: selected.file.id, source_sha256: selected.file.sha256, anchor: selected.source_unit.anchor,
          original_evidence_id: selected.evidence[0]?.id ?? null, source_unit_id: selected.source_unit.id,
        })}>Pilih lokasi ini sebagai bukti</button>}
      </>}
    </section>
  );
}

function anchorLabel(anchor: Record<string, unknown>): string { return sourceLocation(anchor); }

function ImageViewer({ src, name, preflight, anchor, zoom, setZoom }: {
  src: string; name: string; preflight: Record<string, unknown>; anchor: Record<string, unknown>;
  zoom: number; setZoom: (value: number | ((current: number) => number)) => void;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const [frameWidth, setFrameWidth] = useState(0);
  const width = Number(preflight.width ?? 0); const height = Number(preflight.height ?? 0);
  const orientation = Number(preflight.orientation ?? 1);
  const box = Array.isArray(anchor.bbox) ? anchor.bbox.map(Number) : [0, 0, width, height];
  const oriented = orientBox(box, width, height, orientation);
  const viewWidth = orientation >= 5 ? height : width; const viewHeight = orientation >= 5 ? width : height;
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setFrameWidth(entry.contentRect.width);
    });
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);
  const fitScale = frameWidth > 0 ? Math.min(1, frameWidth / Math.max(1, viewWidth)) : 1;
  const renderedWidth = Math.max(1, Math.round(viewWidth * fitScale * zoom));
  const renderedHeight = Math.max(1, Math.round(viewHeight * fitScale * zoom));
  return <section className="image-source-viewer">
    <div className="source-viewer-toolbar"><button type="button" className="button button-quiet" onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))}>−</button><span>{Math.round(zoom * 100)}%</span><button type="button" className="button button-quiet" onClick={() => setZoom((value) => Math.min(4, value + 0.25))}>+</button></div>
    <div ref={frameRef} className="image-source-frame"><div className="image-source-content" style={{ width: `${renderedWidth}px`, height: `${renderedHeight}px` }}>
      <img src={src} alt={`Sumber asli ${name}`} style={{ width: "100%", height: "100%", objectFit: "contain", imageOrientation: "from-image" }} />
      {oriented && <div className="image-anchor-overlay" style={{ left: `${oriented[0] / Math.max(1, viewWidth) * 100}%`, top: `${oriented[1] / Math.max(1, viewHeight) * 100}%`, width: `${(oriented[2] - oriented[0]) / Math.max(1, viewWidth) * 100}%`, height: `${(oriented[3] - oriented[1]) / Math.max(1, viewHeight) * 100}%` }} />}
    </div></div>
  </section>;
}

function orientBox(box: number[], width: number, height: number, orientation: number): number[] | null {
  const [left, top, right, bottom] = box;
  if (![left, top, right, bottom].every(Number.isFinite)) return null;
  switch (orientation) {
    case 2: return [width - right, top, width - left, bottom];
    case 3: return [width - right, height - bottom, width - left, height - top];
    case 4: return [left, height - bottom, right, height - top];
    case 5: return [top, left, bottom, right];
    case 6: return [height - bottom, left, height - top, right];
    case 7: return [height - bottom, width - right, height - top, width - left];
    case 8: return [top, width - right, bottom, width - left];
    default: return [left, top, right, bottom];
  }
}

function SpreadsheetDocumentViewer({ fileId, preflight, anchor, onSelectSource }: {
  fileId: string; preflight: Record<string, unknown>; anchor: Record<string, unknown>; onSelectSource: (id: string) => void;
}) {
  const tabsId = useId();
  const sheets = Array.isArray(preflight.sheets) ? preflight.sheets.filter((item): item is { name: string; state?: string } =>
    typeof item === "object" && item !== null && typeof (item as { name?: unknown }).name === "string") : [];
  const initialSheet = typeof anchor.sheet_name === "string" && sheets.some((item) => item.name === anchor.sheet_name)
    ? anchor.sheet_name : sheets[0]?.name ?? "";
  const [sheetName, setSheetName] = useState(initialSheet);
  const [items, setItems] = useState<SpreadsheetCell[]>([]);
  const [loadedSheet, setLoadedSheet] = useState("");
  const [cursor, setCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!sheetName) return;
    let live = true;
    void requestJson<SpreadsheetPage>(`/api/v1/files/${fileId}/spreadsheet-cells?sheet_name=${encodeURIComponent(sheetName)}&limit=100`)
      .then((page) => { if (live) { setItems(page.items); setCursor(page.next_cursor); setLoadedSheet(sheetName); setError(""); setLoading(false); } })
      .catch((cause: unknown) => { if (live) { setError(cause instanceof Error ? cause.message : "Grid spreadsheet gagal dimuat."); setLoading(false); } });
    return () => { live = false; };
  }, [fileId, sheetName]);

  async function loadMore() {
    if (cursor === null) return;
    setLoading(true); setError("");
    try {
      const page = await requestJson<SpreadsheetPage>(`/api/v1/files/${fileId}/spreadsheet-cells?sheet_name=${encodeURIComponent(sheetName)}&cursor=${cursor}&limit=100`);
      setItems((current) => [...current, ...page.items]); setCursor(page.next_cursor);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Grid spreadsheet gagal dimuat."); }
    finally { setLoading(false); }
  }

  const sheetItems = loadedSheet === sheetName ? items : [];
  const cellByCoordinate = new Map(sheetItems.map((item) => [String(item.anchor.cell_range), item]));
  const rowNumbers = [...new Set(sheetItems.map((item) => Number(item.content.row)).filter(Number.isInteger))].sort((a, b) => a - b);
  const columnNumbers = [...new Set(sheetItems.map((item) => Number(item.content.column)).filter(Number.isInteger))].sort((a, b) => a - b);
  const selected = cellByCoordinate.get(String(anchor.cell_range));

  function moveSheetFocus(event: React.KeyboardEvent<HTMLButtonElement>, currentIndex: number) {
    let next = currentIndex;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (currentIndex + 1) % sheets.length;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (currentIndex - 1 + sheets.length) % sheets.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = sheets.length - 1;
    else return;
    event.preventDefault();
    const sheet = sheets[next];
    if (!sheet) return;
    setLoading(true); setError(""); setSheetName(sheet.name);
    document.getElementById(`${tabsId}-tab-${next}`)?.focus();
  }

  return <article className="spreadsheet-cell-viewer">
    <div className="spreadsheet-sheet-tabs" role="tablist" aria-label="Sheet workbook">
      {sheets.map((sheet, index) => <button key={sheet.name} id={`${tabsId}-tab-${index}`} type="button" role="tab"
        aria-selected={sheetName === sheet.name} aria-controls={`${tabsId}-panel`} tabIndex={sheetName === sheet.name ? 0 : -1}
        onKeyDown={(event) => moveSheetFocus(event, index)}
        className={`spreadsheet-sheet-tab${sheetName === sheet.name ? " is-selected" : ""}`} onClick={() => { setLoading(true); setError(""); setSheetName(sheet.name); }}>
        {sheet.name}{sheet.state && sheet.state !== "visible" ? ` · tersembunyi (${sheet.state})` : ""}
      </button>)}
    </div>
    <div id={`${tabsId}-panel`} role="tabpanel" aria-labelledby={`${tabsId}-tab-${Math.max(0, sheets.findIndex((sheet) => sheet.name === sheetName))}`} tabIndex={0}>
    <p className="help-text">{sheetName} · {loading ? "Memuat grid…" : `${sheetItems.length} sel dimuat`} · inventori preflight: {String(preflight.nonempty_cells ?? "—")} sel</p>
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="spreadsheet-grid-scroll" role="region" aria-label={`Grid ${sheetName}`} tabIndex={0}>
      <table className="spreadsheet-readonly-grid">
        <thead><tr><th scope="col">A1</th>{columnNumbers.map((column) => <th scope="col" key={column}>{excelColumn(column)}</th>)}</tr></thead>
        <tbody>{rowNumbers.map((row) => <tr key={row}><th scope="row">{row}</th>{columnNumbers.map((column) => {
          const coordinate = `${excelColumn(column)}${row}`;
          const cell = cellByCoordinate.get(coordinate);
          const content = cell?.content ?? {};
          const formula = typeof content.formula === "string" ? content.formula : null;
          const value = content.value === null || content.value === undefined ? "" : String(content.value);
          return <td key={column} className={cell?.anchor.cell_range === anchor.cell_range ? "is-current-anchor" : undefined}>
            {cell ? <button type="button" className="spreadsheet-grid-cell" aria-label={`${coordinate}: ${formula ? `formula ${formula}, cache belum diverifikasi` : value}`}
              title={`${coordinate} · ${String(content.column_label ?? "")} · ${String(content.row_label ?? "")}${content.merged_range ? ` · merge ${String(content.merged_range)}` : ""}`}
              onClick={() => onSelectSource(cell.source_unit_id)}>{value}{formula && <small>fx · cache belum diverifikasi</small>}</button> : <span aria-hidden="true"> </span>}
          </td>;
        })}</tr>)}</tbody>
      </table>
    </div>
    {selected && <div className="spreadsheet-grid-detail" aria-label={`Detail sel ${String(anchor.cell_range)}`}>
      <strong>{String(anchor.cell_range)} · {String(selected.content.column_label ?? "—")} · {String(selected.content.row_label ?? "—")}</strong>
      <span>Nilai asli: {String(selected.content.value ?? selected.content.text ?? "(kosong)")}</span>
      <span>Format: {String(selected.content.number_format ?? "General")}{selected.content.merged_range ? ` · merged ${String(selected.content.merged_range)}` : ""}</span>
      {selected.content.formula !== null && selected.content.formula !== undefined && <><span>Formula asli: <code>{String(selected.content.formula)}</code></span><span>Nilai cache (belum diverifikasi): {String(selected.content.cached_value ?? "Tidak tersedia")}</span></>}
      {selected.limitations.map((item, index) => <span key={`${String(item.code)}-${index}`}>{String(item.message ?? item.code ?? "Limitasi sel")}</span>)}
    </div>}
    {cursor !== null && <button type="button" className="button button-quiet" disabled={loading} onClick={() => void loadMore()}>{loading ? "Memuat…" : "Muat sel berikutnya"}</button>}
    <p className="help-text">Viewer read-only. ALIGN tidak menjalankan macro, external workbook, atau formula. Sheet hidden tetap diberi label; cache formula tidak diverifikasi.</p>
    </div>
  </article>;
}

function excelColumn(number: number): string {
  let result = "";
  for (let value = number; value > 0; value = Math.floor((value - 1) / 26)) result = String.fromCharCode(65 + (value - 1) % 26) + result;
  return result;
}

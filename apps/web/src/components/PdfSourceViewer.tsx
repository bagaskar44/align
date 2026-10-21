"use client";

import { useEffect, useRef, useState } from "react";

type PdfAnchor = { page?: number; bbox?: number[]; coordinate_system?: string };

export function PdfSourceViewer({ src, anchor, name }: { src: string; anchor?: PdfAnchor | null; name: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [pdf, setPdf] = useState<import("pdfjs-dist").PDFDocumentProxy | null>(null);
  const [error, setError] = useState("");
  const [pageNumber, setPageNumber] = useState(Math.max(1, anchor?.page ?? 1));
  const [scale, setScale] = useState(1.25);
  const [overlayRect, setOverlayRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    let loadingTask: import("pdfjs-dist").PDFDocumentLoadingTask | null = null;
    let loaded: import("pdfjs-dist").PDFDocumentProxy | null = null;
    void (async () => {
      try {
        const response = await fetch(src, { cache: "no-store", credentials: "same-origin" });
        if (!response.ok) throw new Error("PDF sumber tidak dapat dibuka.");
        const data = await response.arrayBuffer();
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
        loadingTask = pdfjs.getDocument({ data, enableXfa: false, disableAutoFetch: true });
        loaded = await loadingTask.promise;
        if (!cancelled) {
          setPdf(loaded);
          setPageNumber(Math.min(Math.max(1, anchor?.page ?? 1), loaded.numPages));
          setError("");
        } else {
          await loadingTask.destroy();
        }
      } catch {
        if (!cancelled) setError("PDF tidak dapat dirender. Berkas asli tetap tersedia untuk diunduh.");
      }
    })();
    return () => {
      cancelled = true;
      if (loadingTask) void loadingTask.destroy();
    };
  }, [src, anchor?.page]);

  useEffect(() => {
    if (!pdf || !canvasRef.current) return;
    let cancelled = false;
    let task: import("pdfjs-dist").RenderTask | null = null;
    void (async () => {
      try {
        const page = await pdf.getPage(pageNumber);
        if (cancelled || !canvasRef.current) return;
        const viewport = page.getViewport({ scale });
        const canvas = canvasRef.current;
        const context = canvas.getContext("2d");
        if (!context) throw new Error("canvas unavailable");
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        setOverlayRect(null);
        if (anchor?.page === pageNumber && anchor.bbox?.length === 4) {
          const [xMin, yMin, , yMax] = page.view;
          const pageHeight = yMax - yMin;
          const [left, top, right, bottom] = anchor.bbox;
          const first = viewport.convertToViewportPoint(xMin + left, yMin + pageHeight - top);
          const second = viewport.convertToViewportPoint(xMin + right, yMin + pageHeight - bottom);
          setOverlayRect({ left: Math.min(first[0], second[0]), top: Math.min(first[1], second[1]),
            width: Math.abs(second[0] - first[0]), height: Math.abs(second[1] - first[1]) });
        }
        task = page.render({ canvas, canvasContext: context, viewport, annotationMode: 0 });
        await task.promise;
      } catch {
        if (!cancelled) setError("Halaman PDF tidak dapat dirender.");
      }
    })();
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [pdf, pageNumber, scale, anchor]);

  return (
    <section className="source-viewer" aria-label={`Viewer PDF ${name}`}>
      <div className="source-viewer-toolbar">
        <span>{name}</span>
        <button type="button" className="button button-quiet" onClick={() => setScale((value) => Math.max(0.5, value - 0.25))} aria-label="Perkecil PDF">−</button>
        <span>{Math.round(scale * 100)}%</span>
        <button type="button" className="button button-quiet" onClick={() => setScale((value) => Math.min(3, value + 0.25))} aria-label="Perbesar PDF">+</button>
        <button type="button" className="button button-quiet" disabled={!pdf || pageNumber <= 1} onClick={() => setPageNumber((value) => value - 1)}>Halaman sebelumnya</button>
        <span>Halaman {pageNumber}{pdf ? ` / ${pdf.numPages}` : ""}</span>
        <button type="button" className="button button-quiet" disabled={!pdf || pageNumber >= pdf.numPages} onClick={() => setPageNumber((value) => value + 1)}>Halaman berikutnya</button>
        <a className="button button-quiet" href={src} download={name}>Unduh asli</a>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="pdf-page-frame">
        <canvas ref={canvasRef} aria-label={`Halaman ${pageNumber} dari ${name}`} />
        {overlayRect && <div className="pdf-anchor-overlay" aria-label="Lokasi bukti" style={overlayRect} />}
      </div>
    </section>
  );
}

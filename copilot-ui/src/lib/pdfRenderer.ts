/**
 * Browser-local PDF page renderer (issue 033).
 *
 * Wraps the pdfjs-dist API in a single function the Source tab can call
 * to paint a specific page of an uploaded PDF onto a canvas. Kept in its
 * own module so component tests can mock it without paying the cost of
 * standing up the real PDF.js worker — full browser PDF rendering is not
 * reliable in jsdom and is not the focus of the source-grounding tests.
 *
 * The renderer reads the file as an ArrayBuffer and lets PDF.js own
 * everything from there. The worker URL is wired via Vite's ``?url``
 * import so the bundler emits the worker as a static asset alongside
 * the app — no separate copy step is needed in CI.
 */

import * as pdfjsLib from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

// Configure once on module load. Subsequent imports reuse the same
// global; pdfjs-dist tolerates re-assigning the same string.
pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

export interface RenderedPdfPage {
  /** Total number of pages in the PDF. Used for the "Page X of Y" label. */
  readonly numPages: number;
  /** Effective page that was rendered (clamped to [1, numPages]). */
  readonly renderedPage: number;
  /** Painted canvas pixel width. */
  readonly width: number;
  /** Painted canvas pixel height. */
  readonly height: number;
}

export interface RenderOptions {
  /**
   * CSS-pixel scale applied to the page viewport. The default of 1.5 is
   * a readable zoom for lab PDFs without making the canvas absurdly
   * large.
   */
  readonly scale?: number;
}

/**
 * Render ``pageNumber`` from ``file`` onto ``canvas``. Resolves with the
 * rendered viewport dimensions and the PDF's total page count; the
 * caller uses ``numPages`` for the "Page X of Y" caption and the
 * dimensions to size the overlay container so percent-based bbox
 * coordinates land where the user expects.
 *
 * The page is clamped into [1, numPages] so a stale ``selectedFieldPath``
 * referencing a page that no longer exists falls back to the closest
 * valid page rather than throwing.
 */
export async function renderPdfPageToCanvas(
  file: File,
  pageNumber: number,
  canvas: HTMLCanvasElement,
  options: RenderOptions = {},
): Promise<RenderedPdfPage> {
  const data = await file.arrayBuffer();
  const loadingTask = pdfjsLib.getDocument({ data });
  const doc = await loadingTask.promise;
  const safePage = Math.min(Math.max(1, pageNumber), doc.numPages);
  const page = await doc.getPage(safePage);
  const viewport = page.getViewport({ scale: options.scale ?? 1.5 });
  const ctx = canvas.getContext('2d');
  if (ctx === null) {
    throw new Error('canvas 2d context is not available');
  }
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  await page.render({
    canvasContext: ctx,
    viewport,
  }).promise;
  return {
    numPages: doc.numPages,
    renderedPage: safePage,
    width: viewport.width,
    height: viewport.height,
  };
}

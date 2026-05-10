/**
 * Document upload widget (issue 011, mismatch guard issue 024).
 *
 * Drag-and-drop or file-picker for the active patient. Validates type/size
 * client-side before sending. POSTs multipart/form-data to /upload and emits
 * the resulting extraction up to the parent via `onUploaded`.
 *
 * When the backend deterministically detects a doc-type mismatch (HTTP 409),
 * the widget surfaces a correction affordance with three actions:
 *  - Switch to detected type and retry
 *  - Continue with the originally selected type (confirms the override)
 *  - Cancel and re-pick a file
 *
 * Always rendered. When `patientId` is empty, the widget shows but disables
 * upload with a hint to pick a patient first.
 */

import {
  useCallback,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type JSX,
} from 'react';
import {
  ALLOWED_EXTENSIONS,
  ALLOWED_MIME_TYPES,
  uploadDocument,
  validateFileForUpload,
  type DocTypeMismatch,
  type UploadDocType,
  type UploadResult,
} from '../api/upload';
import type { DocType, ExtractionResponse } from '../api/extraction';

export interface FileUploadWidgetProps {
  readonly patientId: string;
  readonly patientName: string;
  readonly conversationId?: string;
  /**
   * Called after a successful upload. The second arg is the browser-local
   * ``File`` so the parent can carry it forward to the source-grounding
   * UI (issue 032). Callers that don't need the file can ignore it.
   */
  readonly onUploaded: (extraction: ExtractionResponse, file: File) => void;
  /** Test seam — overrides the network call. */
  readonly uploadFn?: typeof uploadDocument;
}

type UploadState =
  | { readonly kind: 'idle' }
  | { readonly kind: 'invalid'; readonly detail: string }
  | { readonly kind: 'uploading'; readonly fileName: string }
  | {
      readonly kind: 'mismatch';
      readonly file: File;
      readonly mismatch: DocTypeMismatch;
    }
  | { readonly kind: 'error'; readonly status: number; readonly detail: string }
  /** Canonical 200 + failed-status response (issue 025). Carries the
   * user-safe ``failure_reason`` from the wire envelope. */
  | {
      readonly kind: 'outcomeFailed';
      readonly outcomeStatus: ExtractionResponse['status'];
      readonly failureReason: string | null;
    };

const DOC_TYPE_LABEL: Record<DocType, string> = {
  lab_pdf: 'Lab PDF',
  intake_form: 'Intake form',
  hl7_oru: 'HL7 ORU (Lab)',
  hl7_adt: 'HL7 ADT',
  xlsx_workbook: 'XLSX Workbook',
  docx_referral: 'DOCX Referral',
  tiff_fax: 'TIFF Fax',
};

function docTypeLabel(docType: UploadDocType): string {
  return docType === 'auto' ? 'Auto-detect' : DOC_TYPE_LABEL[docType];
}

export function FileUploadWidget(props: FileUploadWidgetProps): JSX.Element | null {
  const {
    patientId,
    patientName,
    conversationId,
    onUploaded,
    uploadFn = uploadDocument,
  } = props;

  const [state, setState] = useState<UploadState>({ kind: 'idle' });
  const [dragActive, setDragActive] = useState<boolean>(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const runUpload = useCallback(
    async (
      file: File,
      effectiveDocType: UploadDocType,
      confirmDocType: boolean,
    ): Promise<void> => {
      setState({ kind: 'uploading', fileName: file.name });
      const result: UploadResult = await uploadFn({
        file,
        patientId,
        docType: effectiveDocType,
        conversationId,
        confirmDocType,
      });
      if (result.ok === true) {
        setState({ kind: 'idle' });
        onUploaded(result.response, file);
        return;
      }
      if (result.ok === 'mismatch') {
        setState({ kind: 'mismatch', file, mismatch: result.mismatch });
        return;
      }
      if (result.ok === 'failed') {
        setState({
          kind: 'outcomeFailed',
          outcomeStatus: result.outcome.status,
          failureReason: result.outcome.failure_reason,
        });
        return;
      }
      setState({ kind: 'error', status: result.status, detail: result.detail });
    },
    [patientId, conversationId, onUploaded, uploadFn],
  );

  const handleFile = useCallback(
    async (file: File): Promise<void> => {
      const invalid = validateFileForUpload(file);
      if (invalid) {
        setState({ kind: 'invalid', detail: invalid.detail });
        return;
      }
      await runUpload(file, 'auto', false);
    },
    [runUpload],
  );

  const onPick = useCallback(
    (e: ChangeEvent<HTMLInputElement>): void => {
      const file = e.target.files?.[0];
      // Reset the input so the same filename can be re-picked.
      e.target.value = '';
      if (file) void handleFile(file);
    },
    [handleFile],
  );

  const onDrop = useCallback(
    (e: DragEvent<HTMLDivElement>): void => {
      e.preventDefault();
      setDragActive(false);
      const file = e.dataTransfer.files?.[0];
      if (file) void handleFile(file);
    },
    [handleFile],
  );

  const onDragOver = useCallback((e: DragEvent<HTMLDivElement>): void => {
    e.preventDefault();
    setDragActive(true);
  }, []);

  const onDragLeave = useCallback((): void => {
    setDragActive(false);
  }, []);

  const onSwitchType = useCallback((): void => {
    if (state.kind !== 'mismatch') return;
    const next = state.mismatch.detectedType;
    void runUpload(state.file, next, false);
  }, [state, runUpload]);

  const onConfirmAnyway = useCallback((): void => {
    if (state.kind !== 'mismatch') return;
    void runUpload(state.file, state.mismatch.requestedType, true);
  }, [state, runUpload]);

  const onCancelMismatch = useCallback((): void => {
    setState({ kind: 'idle' });
  }, []);

  const hasPatient = Boolean(patientId);
  const busy = state.kind === 'uploading';
  const disabled = busy || !hasPatient;

  return (
    <div className="upload-widget" data-testid="upload-widget">
      <div className="upload-widget__header">
        <span className="upload-widget__title">Upload document</span>
        {hasPatient ? (
          <span className="upload-widget__patient">for {patientName}</span>
        ) : (
          <span className="upload-widget__patient">select a patient first</span>
        )}
      </div>

      <div className="upload-widget__doctype">
        <span>Auto-detect document type</span>
      </div>

      <div
        className="upload-widget__active-type"
        data-testid="upload-widget-active-type"
        aria-live="polite"
      >
        The agent will classify the document after upload.
      </div>

      <div
        className={`upload-widget__drop${dragActive ? ' upload-widget__drop--active' : ''}`}
        role="button"
        tabIndex={hasPatient ? 0 : -1}
        aria-disabled={!hasPatient}
        aria-label="drop file or click to choose"
        onClick={() => {
          if (hasPatient) inputRef.current?.click();
        }}
        onKeyDown={(e) => {
          if (!hasPatient) return;
          if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click();
        }}
        onDrop={hasPatient ? onDrop : (e) => e.preventDefault()}
        onDragOver={hasPatient ? onDragOver : (e) => e.preventDefault()}
        onDragLeave={onDragLeave}
      >
        <input
          ref={inputRef}
          type="file"
          accept={[...ALLOWED_MIME_TYPES, ...ALLOWED_EXTENSIONS].join(',')}
          onChange={onPick}
          className="upload-widget__input"
          aria-label="choose document"
          disabled={disabled}
        />
        {busy ? (
          <span className="upload-widget__hint">
            Uploading {state.fileName}…
          </span>
        ) : !hasPatient ? (
          <span className="upload-widget__hint">
            Select a patient to enable upload
          </span>
        ) : (
          <span className="upload-widget__hint">
            Drop a clinical document here · PDF, image, HL7, DOCX, XLSX, or TIFF · 20 MB max
          </span>
        )}
      </div>

      {state.kind === 'invalid' && (
        <div className="upload-widget__error" role="alert">
          {state.detail}
        </div>
      )}
      {state.kind === 'error' && (
        <div className="upload-widget__error" role="alert">
          Upload failed{state.status ? ` (HTTP ${state.status})` : ''}: {state.detail}
        </div>
      )}
      {state.kind === 'outcomeFailed' && (
        <div
          className="upload-widget__error"
          data-testid="upload-widget-outcome-failed"
          data-outcome-status={state.outcomeStatus}
          role="alert"
        >
          {state.failureReason ?? 'Upload could not be completed.'}
        </div>
      )}
      {state.kind === 'mismatch' && (
        <div
          className="upload-widget__mismatch"
          data-testid="upload-widget-mismatch"
          role="alertdialog"
          aria-labelledby="upload-mismatch-title"
        >
          <p id="upload-mismatch-title" className="upload-widget__mismatch-title">
            This file looks like a {docTypeLabel(state.mismatch.detectedType)},
            but you selected {docTypeLabel(state.mismatch.requestedType)}.
          </p>
          <p className="upload-widget__mismatch-message">{state.mismatch.message}</p>
          <div className="upload-widget__mismatch-actions">
            <button
              type="button"
              onClick={onSwitchType}
              data-testid="upload-mismatch-switch"
            >
              Switch to {docTypeLabel(state.mismatch.detectedType)} and upload
            </button>
            <button
              type="button"
              onClick={onConfirmAnyway}
              data-testid="upload-mismatch-confirm"
            >
              Upload as {docTypeLabel(state.mismatch.requestedType)} anyway
            </button>
            <button
              type="button"
              onClick={onCancelMismatch}
              data-testid="upload-mismatch-cancel"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

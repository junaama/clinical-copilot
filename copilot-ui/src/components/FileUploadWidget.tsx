/**
 * Document upload widget (issue 011).
 *
 * Drag-and-drop or file-picker for the active patient. Validates type/size
 * client-side before sending. POSTs multipart/form-data to /upload and emits
 * the resulting extraction up to the parent via `onUploaded`.
 *
 * Always rendered. When `patientId` is empty, the widget shows but disables
 * upload with a hint to pick a patient first.
 */

import { useCallback, useRef, useState, type ChangeEvent, type DragEvent, type JSX } from 'react';
import {
  ALLOWED_MIME_TYPES,
  uploadDocument,
  validateFileForUpload,
  type UploadResult,
} from '../api/upload';
import type { DocType, ExtractionResponse } from '../api/extraction';

export interface FileUploadWidgetProps {
  readonly patientId: string;
  readonly patientName: string;
  readonly conversationId?: string;
  readonly onUploaded: (extraction: ExtractionResponse) => void;
  /** Test seam — overrides the network call. */
  readonly uploadFn?: typeof uploadDocument;
}

type UploadState =
  | { readonly kind: 'idle' }
  | { readonly kind: 'invalid'; readonly detail: string }
  | { readonly kind: 'uploading'; readonly fileName: string }
  | { readonly kind: 'error'; readonly status: number; readonly detail: string };

export function FileUploadWidget(props: FileUploadWidgetProps): JSX.Element | null {
  const {
    patientId,
    patientName,
    conversationId,
    onUploaded,
    uploadFn = uploadDocument,
  } = props;

  const [state, setState] = useState<UploadState>({ kind: 'idle' });
  const [docType, setDocType] = useState<DocType>('lab_pdf');
  const [dragActive, setDragActive] = useState<boolean>(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const handleFile = useCallback(
    async (file: File): Promise<void> => {
      const invalid = validateFileForUpload(file);
      if (invalid) {
        setState({ kind: 'invalid', detail: invalid.detail });
        return;
      }
      setState({ kind: 'uploading', fileName: file.name });
      const result: UploadResult = await uploadFn({
        file,
        patientId,
        docType,
        conversationId,
      });
      if (!result.ok) {
        setState({ kind: 'error', status: result.status, detail: result.detail });
        return;
      }
      setState({ kind: 'idle' });
      onUploaded(result.response);
    },
    [patientId, docType, conversationId, onUploaded, uploadFn],
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
        <label>
          <input
            type="radio"
            name="doc_type"
            value="lab_pdf"
            checked={docType === 'lab_pdf'}
            onChange={() => setDocType('lab_pdf')}
            disabled={disabled}
          />
          Lab PDF
        </label>
        <label>
          <input
            type="radio"
            name="doc_type"
            value="intake_form"
            checked={docType === 'intake_form'}
            onChange={() => setDocType('intake_form')}
            disabled={disabled}
          />
          Intake form
        </label>
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
          accept={ALLOWED_MIME_TYPES.join(',')}
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
            Drop a PDF, PNG, or JPEG here · 20 MB max
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
    </div>
  );
}

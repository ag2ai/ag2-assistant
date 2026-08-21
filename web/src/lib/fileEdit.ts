// Browser-global-free helpers for in-place file editing in the preview rail. The
// fetch wrappers that use them live in transport/api/index.ts.
import { m } from '../paraglide/messages.js'

// What a failed save rejects with: ApiError (status + parsed body) from the
// transport, or a bare Error when the request never reached the server.
export type SaveFailure = { status?: number; body?: unknown; message?: string }

// The bare content token inside an `ETag` header — its weak-`W/` prefix and quotes
// stripped, or null when absent. Matches the unquoted etag a PUT hands back.
export function parseEtag(value: string | null | undefined): string | null {
  if (!value) return null
  let v = String(value).trim()
  if (v.startsWith('W/')) v = v.slice(2)
  return v.replace(/^"|"$/g, '')
}

// True for a stale-token save clash (ADR 0011) — the `409` the rail resolves with a
// Reload/Overwrite choice, told apart from every other save failure.
// A catch binding is `unknown` under strict mode, so both readers take `unknown` and
// read the SaveFailure shape out of it rather than making the caller assert one.
export function isConflict(err: unknown): boolean {
  return failure(err).status === 409
}

// A save failure → the line shown in the rail: a server `{error}` body wins, else a
// per-status message, else the raw transport error, else a generic fallback.
export function saveErrorMessage(err: unknown): string {
  const f = failure(err)
  const fromBody = bodyError(f.body)
  if (fromBody) return fromBody
  const status = f.status
  if (status === 404) return m.viewer_err_gone()
  if (status === 400) return m.viewer_err_invalid_path()
  if (status === 413) return m.viewer_err_too_large()
  if (!status && f.message) return f.message
  return m.viewer_err_save()
}

// The SaveFailure fields a thrown value carries, each only when it is the right type.
function failure(err: unknown): SaveFailure {
  if (!err || typeof err !== 'object') return {}
  const e = err as Record<string, unknown>
  return {
    status: typeof e.status === 'number' ? e.status : undefined,
    body: e.body,
    message: typeof e.message === 'string' ? e.message : undefined,
  }
}

// The server's `{error}` line, when the failure body actually carries one.
function bodyError(body: unknown): string {
  if (!body || typeof body !== 'object') return ''
  const e = (body as { error?: unknown }).error
  return typeof e === 'string' ? e : ''
}

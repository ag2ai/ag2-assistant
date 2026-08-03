// Browser-global-free helpers for in-place file editing in the preview rail. The
// fetch wrappers that use them live in transport/api/index.ts.

// The bare content token inside an `ETag` header — its weak-`W/` prefix and quotes
// stripped, or null when absent. Matches the unquoted etag a PUT hands back.
export function parseEtag(value) {
  if (!value) return null
  let v = String(value).trim()
  if (v.startsWith('W/')) v = v.slice(2)
  return v.replace(/^"|"$/g, '')
}

// True for a stale-token save clash (ADR 0011) — the `409` the rail resolves with a
// Reload/Overwrite choice, told apart from every other save failure.
export function isConflict(err) {
  return !!err && err.status === 409
}

// A save failure → the line shown in the rail: a server `{error}` body wins, else a
// per-status message, else the raw transport error, else a generic fallback.
export function saveErrorMessage(err) {
  const body = err && err.body
  if (body && body.error) return body.error
  const status = err && err.status
  if (status === 404) return 'This file no longer exists on disk.'
  if (status === 400) return 'Could not save — the file path is invalid.'
  if (status === 413) return 'Could not save — the file is too large.'
  if (!status && err && err.message) return err.message
  return 'Could not save the file.'
}

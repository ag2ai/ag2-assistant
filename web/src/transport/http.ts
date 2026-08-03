// The typed HTTP layer: one 410/404 profile-gone recovery, one error shape, and
// one schema gate every response passes through.
import type { z } from 'zod'
import { onProfileGone } from '../lib/profile.ts'
import { parse } from './validate.ts'

// status/body ride on the error so callers can act on structured failures
// (e.g. createSecret's 409 carries the existing Secret to snap to).
export class ApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(message: string, status: number, body: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

// A scoped route returning 410 means the active profile was archived under us;
// 404 on /api/p/ means an unknown pid. Both recover by re-resolving.
function profileGone(status: number, path: string): boolean {
  return status === 410 || (status === 404 && path.startsWith('/api/p/'))
}

// The one response check every helper shares: profile-gone recovery first, then
// the error extraction off a non-2xx body. Returns the parsed JSON on success.
async function checkResponse(r: Response, method: string, path: string): Promise<unknown> {
  if (profileGone(r.status, path)) {
    onProfileGone('fetch ' + r.status)
    throw new ApiError(`${method} ${path} -> ${r.status}`, r.status, null)
  }
  if (!r.ok) {
    let message = `${method} ${path} -> ${r.status}`
    let payload: unknown = null
    try {
      payload = await r.json()
      const error = (payload as { error?: unknown } | null)?.error
      if (error) message = String(error)
    } catch {
      // A non-JSON error body leaves the status-line message in place.
    }
    throw new ApiError(message, r.status, payload)
  }
  return r.json()
}

// An undefined body sends no payload and no Content-Type, matching the routes
// that declare no request model.
async function request<S extends z.ZodTypeAny>(
  method: string,
  path: string,
  schema: S,
  body?: unknown,
): Promise<z.infer<S>> {
  const r = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return parse(schema, await checkResponse(r, method, path), `${method} ${path}`)
}

export function get<S extends z.ZodTypeAny>(path: string, schema: S): Promise<z.infer<S>> {
  return request('GET', path, schema)
}

export function post<S extends z.ZodTypeAny>(
  path: string,
  body: unknown,
  schema: S,
): Promise<z.infer<S>> {
  return request('POST', path, schema, body)
}

export function patch<S extends z.ZodTypeAny>(
  path: string,
  body: unknown,
  schema: S,
): Promise<z.infer<S>> {
  return request('PATCH', path, schema, body)
}

export function put<S extends z.ZodTypeAny>(
  path: string,
  body: unknown,
  schema: S,
): Promise<z.infer<S>> {
  return request('PUT', path, schema, body)
}

export function del<S extends z.ZodTypeAny>(
  path: string,
  schema: S,
  body?: unknown,
): Promise<z.infer<S>> {
  return request('DELETE', path, schema, body)
}

// Multipart POST — skill and file uploads can't ride a JSON body.
export async function postForm<S extends z.ZodTypeAny>(
  path: string,
  form: FormData,
  schema: S,
): Promise<z.infer<S>> {
  const r = await fetch(path, { method: 'POST', body: form })
  return parse(schema, await checkResponse(r, 'POST', path), `POST ${path}`)
}

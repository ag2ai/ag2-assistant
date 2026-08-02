import { test, beforeEach, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { z } from 'zod'
import { setValidationMode } from './validate.ts'
import type { ApiError as ApiErrorShape } from './http.ts'

import { installBrowserGlobals } from '../testing/browserGlobals.ts'

// http.ts reaches lib/profile.js → store.js → router.js, which touches location
// and window while initialising. Install those before the chain loads, hence the
// dynamic import.
installBrowserGlobals()
const { ApiError, get, patch, post } = await import('./http.ts')

const Row = z.object({ id: z.string() })
const realFetch = globalThis.fetch

// Records the last request so a test can assert on method/body without a server.
let seen: { url: string; init: RequestInit | undefined } | null = null

function stubFetch(status: number, body: unknown): void {
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    seen = { url, init }
    return new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as unknown as typeof fetch
}

beforeEach(() => {
  seen = null
  setValidationMode('throw')
})
afterEach(() => {
  globalThis.fetch = realFetch
})

test('get returns the validated body', async () => {
  stubFetch(200, { id: 'a' })
  assert.deepEqual(await get('/api/rows', Row), { id: 'a' })
})

test('get throws ApiError carrying status and body on a non-2xx', async () => {
  stubFetch(409, { error: 'duplicate', existing: { id: 'a' } })
  const err = await get('/api/rows', Row).then(
    () => null,
    (e: unknown) => e as ApiErrorShape,
  )
  assert.ok(err instanceof ApiError)
  assert.equal(err.status, 409)
  assert.equal(err.message, 'duplicate')
  assert.deepEqual((err.body as { existing: unknown }).existing, { id: 'a' })
})

test('a non-JSON error body leaves the status-line message in place', async () => {
  globalThis.fetch = (async () => new Response('<html>502</html>', { status: 502 })) as typeof fetch
  const err = await get('/api/rows', Row).then(
    () => null,
    (e: unknown) => e as ApiErrorShape,
  )
  assert.ok(err instanceof ApiError)
  assert.equal(err.message, 'GET /api/rows -> 502')
  assert.equal(err.body, null)
})

test('get surfaces a schema mismatch in throw mode', async () => {
  stubFetch(200, { id: 42 })
  await assert.rejects(() => get('/api/rows', Row), /GET \/api\/rows/)
})

test('post sends a JSON body and patch uses the PATCH verb', async () => {
  stubFetch(200, { id: 'a' })
  await post('/api/rows', { n: 1 }, Row)
  assert.equal(seen?.init?.method, 'POST')
  assert.equal(seen?.init?.body, '{"n":1}')

  await patch('/api/rows/a', { n: 2 }, Row)
  assert.equal(seen?.init?.method, 'PATCH')
})

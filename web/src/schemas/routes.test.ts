import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { z } from 'zod'
import { defsOf, diff, flatten, type JsonSchema } from './openapi-compare.ts'
import { PENDING, ROUTES, UNMAPPED } from './routes.ts'

const spec = JSON.parse(readFileSync(new URL('../../../docs/openapi.json', import.meta.url), 'utf8'))
const defs: Record<string, JsonSchema> = spec.components?.schemas ?? {}

// Every JSON route the gateway serves, as "METHOD path" keys.
function gatewayRoutes(): string[] {
  const out: string[] = []
  for (const [path, ops] of Object.entries(spec.paths as Record<string, Record<string, unknown>>)) {
    for (const method of Object.keys(ops)) {
      out.push(`${method.toUpperCase()} ${path}`)
    }
  }
  return out.sort()
}

test('every gateway route sits in exactly one bucket', () => {
  const misfiled: string[] = []
  for (const route of gatewayRoutes()) {
    const buckets = [route in ROUTES, route in UNMAPPED, route in PENDING].filter(Boolean).length
    if (buckets !== 1) misfiled.push(`${route} is in ${buckets} buckets`)
  }
  assert.deepEqual(misfiled, [], `fix routes.ts:\n${misfiled.join('\n')}`)
})

test('no bucket names a route the gateway does not serve', () => {
  const served = new Set(gatewayRoutes())
  const stale = [...Object.keys(ROUTES), ...Object.keys(UNMAPPED), ...Object.keys(PENDING)].filter(
    (r) => !served.has(r),
  )
  assert.deepEqual(stale, [], 'these entries no longer match any route')
})

test('each mapped route agrees with its zod schema', () => {
  const failures: string[] = []
  for (const [route, schema] of Object.entries(ROUTES)) {
    const [method, path] = route.split(' ')
    const body =
      spec.paths[path][method.toLowerCase()].responses['200']?.content?.['application/json']?.schema
    if (body === undefined) {
      failures.push(`${route}: the gateway declares no 200 body — is response_model attached?`)
      continue
    }
    // `io: 'input'` because the gateway's body is what this schema PARSES. A field
    // carrying `.default(...)` is required on the output side (parse always fills
    // it) but optional on the input side — and optional is the truth about the
    // wire: `Secret.used_by` is sent by GET /api/secrets and by nothing else.
    const zodJson = z.toJSONSchema(schema, { io: 'input' }) as JsonSchema
    const issues = diff(flatten(zodJson, defsOf(zodJson)), flatten(body, defs))
    if (issues.length) failures.push(`${route}:\n  ${issues.join('\n  ')}`)
  }
  assert.deepEqual(failures, [], `zod and the gateway disagree:\n${failures.join('\n')}`)
})

test('a mapped route declares a body with real fields, not a bare `-> dict`', () => {
  // A handler still annotated `-> dict` DOES get a 200 schema — FastAPI emits
  // {type: 'object', additionalProperties: true}, which describes nothing. Merely
  // asserting a schema exists would call that route done, so the check is that the
  // schema names something: a $ref, its own properties, or a union of those.
  const undescribed = Object.keys(ROUTES).filter((route) => {
    const [method, path] = route.split(' ')
    const body =
      spec.paths[path][method.toLowerCase()].responses['200']?.content?.['application/json']?.schema
    if (body === undefined) return true
    return flatten(body, defs).size === 0
  })
  assert.deepEqual(undescribed, [], 'these routes are mapped but still describe no fields')
})

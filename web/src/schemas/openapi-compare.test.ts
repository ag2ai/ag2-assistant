import assert from 'node:assert/strict'
import { test } from 'node:test'
import { z } from 'zod'
import { defsOf, diff, flatten } from './openapi-compare.ts'

// zod and pydantic agree on the shapes below; the helper must see them as equal.
test('nullable is not mistaken for a union branch', () => {
  const zodSide = z.toJSONSchema(z.object({ email: z.string().nullable() }))
  const pydanticSide = {
    type: 'object',
    properties: { email: { anyOf: [{ type: 'string' }, { type: 'null' }] } },
    required: ['email'],
  }
  assert.deepEqual(diff(flatten(zodSide, {}), flatten(pydanticSide, {})), [])
})

test('optional fields differ from required ones', () => {
  const optional = z.toJSONSchema(z.object({ error: z.string().optional() }))
  const required = z.toJSONSchema(z.object({ error: z.string() }))
  const issues = diff(flatten(optional, {}), flatten(required, {}))
  assert.equal(issues.length, 1)
  assert.match(issues[0], /error.*required/)
})

test('a missing enum member is reported', () => {
  const narrow = z.toJSONSchema(z.object({ mode: z.enum(['read', 'read_write']) }))
  const wide = z.toJSONSchema(z.object({ mode: z.enum(['read', 'read_write', 'none']) }))
  const issues = diff(flatten(narrow, {}), flatten(wide, {}))
  assert.equal(issues.length, 1)
  assert.match(issues[0], /mode.*none/)
})

test('a missing field is reported', () => {
  const without = z.toJSONSchema(z.object({ id: z.string() }))
  const with_ = z.toJSONSchema(z.object({ id: z.string(), task_name: z.string() }))
  const issues = diff(flatten(without, {}), flatten(with_, {}))
  assert.equal(issues.length, 1)
  assert.match(issues[0], /task_name/)
})

test('nested objects are walked, not just the top level', () => {
  const flat = flatten(z.toJSONSchema(z.object({ total: z.object({ cost: z.number() }) })), {})
  assert.ok(flat.has('total.cost'))
})

test('array items and record values are walked', () => {
  const arr = flatten(z.toJSONSchema(z.object({ rows: z.array(z.object({ pid: z.string() })) })), {})
  assert.ok(arr.has('rows[].pid'))
  const rec = flatten(
    z.toJSONSchema(z.object({ by_model: z.record(z.string(), z.object({ cost: z.number() })) })),
    {},
  )
  assert.ok(rec.has('by_model{}.cost'))
})

test('$ref is resolved against defs', () => {
  const schema = { $ref: '#/components/schemas/Row' }
  const defs = { Row: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] } }
  assert.ok(flatten(schema, defs).has('id'))
})

test('integer and number are treated as equal', () => {
  const a = { type: 'object', properties: { n: { type: 'integer' } }, required: ['n'] }
  const b = { type: 'object', properties: { n: { type: 'number' } }, required: ['n'] }
  assert.deepEqual(diff(flatten(a, {}), flatten(b, {})), [])
})

test('zod closing its objects is not a difference', () => {
  // zod emits additionalProperties: false; pydantic emits no such key at all.
  // Neither says anything about the FIELDS, which is all the gate compares.
  const zodSide = z.toJSONSchema(z.object({ id: z.string() }))
  const pydanticSide = { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] }
  assert.equal(zodSide.additionalProperties, false)
  assert.deepEqual(diff(flatten(zodSide, {}), flatten(pydanticSide, {})), [])
})

test('defsOf survives a schema with no $defs', () => {
  assert.deepEqual(defsOf(z.toJSONSchema(z.object({ id: z.string() }))), {})
})

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { z } from 'zod'
import { getValidationMode, parse, SchemaError, setValidationMode } from './validate.ts'

const Row = z.object({ id: z.string(), n: z.number() })

// assert.throws() returns undefined, so the error is captured by hand.
function capture(fn: () => unknown): unknown {
  try {
    fn()
  } catch (e) {
    return e
  }
  return null
}

test('parse returns the validated value on a match', () => {
  setValidationMode('throw')
  assert.deepEqual(parse(Row, { id: 'a', n: 1 }, 'row'), { id: 'a', n: 1 })
})

test('throw mode raises SchemaError carrying the label and issues', () => {
  setValidationMode('throw')
  const err = capture(() => parse(Row, { id: 'a' }, 'GET /rows'))
  assert.ok(err instanceof SchemaError)
  assert.equal(err.label, 'GET /rows')
  assert.equal(err.issues.length, 1)
  assert.match(err.message, /GET \/rows/)
})

test('warn mode passes the raw value through instead of throwing', () => {
  setValidationMode('warn')
  const raw = { id: 'a' }
  assert.equal(parse(Row, raw, 'GET /rows'), raw)
})

test('zod strips unknown keys rather than failing', () => {
  setValidationMode('throw')
  assert.deepEqual(parse(Row, { id: 'a', n: 1, extra: true }, 'row'), { id: 'a', n: 1 })
})

test('getValidationMode reports what setValidationMode last set', () => {
  setValidationMode('warn')
  assert.equal(getValidationMode(), 'warn')
  setValidationMode('throw')
  assert.equal(getValidationMode(), 'throw')
})

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { describe } from './ag2map.ts'

test('describe: AG-UI projection events belong to the subsystem they carry', () => {
  assert.deepEqual(describe('ag-ui.TEXT_MESSAGE_CONTENT'), { sub: 'Model', label: 'TEXT_MESSAGE_CONTENT', layer: 'app' })
  assert.equal(describe('ag-ui.TOOL_CALL_RESULT').sub, 'Tool')
  assert.equal(describe('ag-ui.SUBAGENT_ERROR').sub, 'Subagent')
  assert.equal(describe('ag-ui.RUN_FINISHED').sub, 'Stream')
})

test('describe: native AG2 events keep their own tag', () => {
  assert.deepEqual(describe('ag2.events.ModelResponse'), { sub: 'Model', label: 'ModelResponse', layer: 'ag2' })
})

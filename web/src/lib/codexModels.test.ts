import { test } from 'node:test'
import assert from 'node:assert/strict'
import { splitModelId, joinModelId, effortLabel, groupModels } from './codexModels.js'

test('splitModelId decomposes name[effort] and tolerates plain ids', () => {
  assert.deepEqual(splitModelId('gpt-5.6-sol[medium]'), { family: 'gpt-5.6-sol', effort: 'medium' })
  assert.deepEqual(splitModelId('gpt-5.5'), { family: 'gpt-5.5', effort: '' })
  assert.deepEqual(splitModelId('x [ high ]'), { family: 'x', effort: 'high' })
  assert.deepEqual(splitModelId(''), { family: '', effort: '' })
})

test('joinModelId is the inverse of splitModelId', () => {
  assert.equal(joinModelId('gpt-5.6-sol', 'medium'), 'gpt-5.6-sol[medium]')
  assert.equal(joinModelId('gpt-5.5', ''), 'gpt-5.5')
  assert.equal(joinModelId('', 'high'), '')
})

test('effortLabel maps known tiers and capitalizes unknown ones', () => {
  assert.equal(effortLabel('low'), 'Light')
  assert.equal(effortLabel('xhigh'), 'Extra High')
  assert.equal(effortLabel('turbo'), 'Turbo')
  assert.equal(effortLabel(''), 'Default')
})

test('groupModels folds the flat catalog into ordered families', () => {
  const flat = [
    { id: 'gpt-5.6-sol[low]', name: 'GPT-5.6-Sol (low)', description: '' },
    { id: 'gpt-5.6-sol[medium]', name: 'GPT-5.6-Sol (medium)', description: '' },
    { id: 'gpt-5.4-mini[high]', name: 'GPT-5.4-Mini (high)', description: '' },
  ]
  const groups = groupModels(flat)
  assert.deepEqual(groups.map((g) => g.family), ['gpt-5.6-sol', 'gpt-5.4-mini'])
  assert.equal(groups[0].label, 'GPT-5.6-Sol')
  assert.deepEqual(groups[0].efforts.map((e) => e.value), ['low', 'medium'])
  assert.deepEqual(groups[0].efforts.map((e) => e.label), ['Light', 'Medium'])
})

test('groupModels dedupes efforts and skips empty ids', () => {
  const groups = groupModels([
    { id: 'a[low]', name: 'A (low)' },
    { id: 'a[low]', name: 'A (low)' },
    { id: '', name: 'ghost' },
  ])
  assert.equal(groups.length, 1)
  assert.equal(groups[0].efforts.length, 1)
})

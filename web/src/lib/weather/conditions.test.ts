// The condition vocabulary belongs to the glyph, not to any Card: whatever a Card
// binds, the primitive draws something. Run: node --test src/lib
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { WEATHER_CONDITIONS, weatherCondition } from './conditions.ts'

test('every condition in the vocabulary is drawn as itself', () => {
  for (const condition of WEATHER_CONDITIONS) assert.equal(weatherCondition(condition), condition)
})

test('a condition the vocabulary does not name is drawn as cloudy', () => {
  for (const value of ['hail', '', 'SUNNY ', null, undefined, 7, {}]) {
    assert.ok(WEATHER_CONDITIONS.includes(weatherCondition(value)))
  }
  assert.equal(weatherCondition('hail'), 'cloudy')
  assert.equal(weatherCondition(undefined), 'cloudy')
  // A Card that shouts its condition still gets the scene it named.
  assert.equal(weatherCondition('SUNNY '), 'sunny')
})

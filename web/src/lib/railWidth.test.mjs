import { test } from 'node:test'
import assert from 'node:assert/strict'
import { clampRailWidth, MIN_RAIL_WIDTH, MAX_RAIL_WIDTH, DEFAULT_RAIL_WIDTH,
  clampDrawerWidth, MIN_DRAWER_WIDTH, MAX_DRAWER_WIDTH, DEFAULT_DRAWER_WIDTH } from './railWidth.js'

test('clampRailWidth: below-min clamps up to MIN', () => {
  assert.equal(clampRailWidth(MIN_RAIL_WIDTH - 100), MIN_RAIL_WIDTH)
  assert.equal(clampRailWidth(0), MIN_RAIL_WIDTH)
  assert.equal(clampRailWidth(-50), MIN_RAIL_WIDTH)
})

test('clampRailWidth: above-max clamps down to MAX', () => {
  assert.equal(clampRailWidth(MAX_RAIL_WIDTH + 500), MAX_RAIL_WIDTH)
  assert.equal(clampRailWidth(99999), MAX_RAIL_WIDTH)
})

test('clampRailWidth: in-range passes through unchanged', () => {
  assert.equal(clampRailWidth(MIN_RAIL_WIDTH), MIN_RAIL_WIDTH)
  assert.equal(clampRailWidth(MAX_RAIL_WIDTH), MAX_RAIL_WIDTH)
  assert.equal(clampRailWidth(440), 440)
})

test('clampRailWidth: non-numeric falls back to the default', () => {
  assert.equal(clampRailWidth('not-a-number'), DEFAULT_RAIL_WIDTH)
  assert.equal(clampRailWidth(undefined), DEFAULT_RAIL_WIDTH)
  assert.equal(clampRailWidth(NaN), DEFAULT_RAIL_WIDTH)
  assert.equal(clampRailWidth(Infinity), DEFAULT_RAIL_WIDTH)
})

test('clampDrawerWidth: clamps to its own bounds', () => {
  assert.equal(clampDrawerWidth(MIN_DRAWER_WIDTH - 100), MIN_DRAWER_WIDTH)
  assert.equal(clampDrawerWidth(MAX_DRAWER_WIDTH + 100), MAX_DRAWER_WIDTH)
  assert.equal(clampDrawerWidth(300), 300)
})

test('clampDrawerWidth: non-numeric falls back to the default', () => {
  assert.equal(clampDrawerWidth('nope'), DEFAULT_DRAWER_WIDTH)
  assert.equal(clampDrawerWidth(undefined), DEFAULT_DRAWER_WIDTH)
  assert.equal(clampDrawerWidth(NaN), DEFAULT_DRAWER_WIDTH)
})

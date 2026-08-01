// The brand-mark seam: given a key, what does the app draw? Run: node --test src/lib
// Path geometry is content, not behaviour — nothing here pins a `d` string. What is
// asserted is the shape of the answer: which brands carry a colour, which do not, and
// that an unknown key is survivable.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { brandMark } from './brandMarks.js'
import { CATALOG } from './integrations.js'

test('every platform in the integrations catalogue resolves to a mark', () => {
  for (const entry of CATALOG) {
    const mark = brandMark(entry.id)
    assert.ok(mark, `${entry.id} has no brand mark`)
  }
})

test('the monochrome brands resolve with no fill declared', () => {
  for (const key of ['github']) {
    const mark = brandMark(key)
    assert.equal(mark.kind, 'mono')
    assert.equal(mark.fill, undefined, `${key} declares a fill`)
  }
})

test('the coloured brands resolve with a fill', () => {
  for (const key of ['telegram', 'discord']) {
    const mark = brandMark(key)
    assert.equal(mark.kind, 'solid')
    assert.match(mark.fill, /^#[0-9a-f]{6}$/i)
  }
})

test('Slack and Google resolve as multi-part, each part carrying its own fill', () => {
  for (const key of ['slack', 'google']) {
    const mark = brandMark(key)
    assert.equal(mark.kind, 'multi')
    assert.ok(mark.viewBox, `${key} has no viewBox`)
    assert.ok(mark.parts.length > 1, `${key} is not multi-part`)
    for (const part of mark.parts) {
      assert.ok(part.path)
      assert.match(part.fill, /^#([0-9a-f]{3}|[0-9a-f]{6})$/i)
    }
  }
})

test('an unknown key resolves to nothing rather than throwing', () => {
  // Reachable by downgrading the app while a Connection names a platform a newer
  // version added — the crash this seam exists to prevent.
  assert.equal(brandMark('a-platform-from-the-future'), null)
  assert.equal(brandMark(undefined), null)
  assert.equal(brandMark(''), null)
})

test('no brand entry declares both a flat fill and a gradient', () => {
  for (const entry of CATALOG) {
    const mark = brandMark(entry.id)
    assert.ok(!(mark.fill && mark.stops), `${entry.id} is both flat and gradient`)
  }
})

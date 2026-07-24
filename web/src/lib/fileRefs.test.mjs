import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  makePick,
  triggerAt,
  applyPick,
  reconcile,
  buildBlock,
  composeMessage,
  highlightSegments,
  parseMessage,
} from './fileRefs.js'

// Reconstruct the original text from segments (the split must be lossless) and pull
// out just the marked (highlighted) labels.
const joined = (segs) => segs.map((s) => s.text).join('')
const marks = (segs) => segs.filter((s) => s.mark).map((s) => s.text)

const pick = (path, name, kind = 'file') => makePick({ path, name, dir: '', kind })

// ---- Trigger rule (story 19: only a fresh-token `@` opens the picker) ----

test('triggerAt: `@` at start-of-input is a mention', () => {
  assert.deepEqual(triggerAt('@rep', 4), { start: 0, query: 'rep' })
})

test('triggerAt: `@` after whitespace is a mention', () => {
  assert.deepEqual(triggerAt('compare @rep', 12), { start: 8, query: 'rep' })
})

test('triggerAt: a mid-word `@` (email) does NOT open the picker', () => {
  assert.equal(triggerAt('mail a@b.com', 12), null)
})

test('triggerAt: caret before the token end narrows the query', () => {
  assert.deepEqual(triggerAt('see @report here', 7), { start: 4, query: 're' })
})

// ---- Insertion ----

test('applyPick: replaces the @query fragment with the label + trailing space', () => {
  const { text, caret } = applyPick('compare @rep', 8, 12, '@report.txt')
  assert.equal(text, 'compare @report.txt ')
  assert.equal(caret, text.length)
})

test('applyPick: keeps text after the caret intact', () => {
  const { text } = applyPick('a @r b', 2, 4, '@readme.md')
  assert.equal(text, 'a @readme.md  b')
})

// ---- Block construction ----

test('buildBlock: absolute paths, one per line, under the header', () => {
  const block = buildBlock([pick('/w/a.txt', 'a.txt'), pick('/w/docs/b.md', 'b.md')])
  assert.equal(block, 'Referenced files:\n- /w/a.txt\n- /w/docs/b.md')
})

test('buildBlock: no picks -> empty string (no block)', () => {
  assert.equal(buildBlock([]), '')
})

test('buildBlock: a directory pick is annotated distinctly from a file', () => {
  const block = buildBlock([pick('/w/src', 'src', 'directory')])
  assert.match(block, /\/w\/src \(directory/)
})

test('buildBlock: a mixed file + directory picks list annotates only the directory', () => {
  const block = buildBlock([pick('/w/a.txt', 'a.txt'), pick('/w/src', 'src', 'directory')])
  assert.equal(
    block,
    'Referenced files:\n- /w/a.txt\n- /w/src (directory — list its contents)'
  )
})

// ---- composeMessage: reconciliation + append (the observable behavior) ----

test('composeMessage: appends the block for surviving picks', () => {
  const picks = [pick('/w/a.txt', 'a.txt')]
  const out = composeMessage('look at @a.txt', picks)
  assert.equal(out, 'look at @a.txt\n\nReferenced files:\n- /w/a.txt')
})

test('composeMessage: no picks -> text unchanged, no block', () => {
  assert.equal(composeMessage('just text', []), 'just text')
})

test('composeMessage: a pick whose @label was deleted from the text is dropped', () => {
  const picks = [pick('/w/a.txt', 'a.txt'), pick('/w/b.txt', 'b.txt')]
  // the user deleted "@a.txt" but kept "@b.txt"
  const out = composeMessage('now only @b.txt', picks)
  assert.equal(out, 'now only @b.txt\n\nReferenced files:\n- /w/b.txt')
})

test('composeMessage: two same-named picks resolve to their distinct paths', () => {
  const picks = [
    makePick({ path: '/w/one/report.txt', name: 'report.txt', dir: 'one', kind: 'file' }),
    makePick({ path: '/w/two/report.txt', name: 'report.txt', dir: 'two', kind: 'file' }),
  ]
  const out = composeMessage('diff @report.txt and @report.txt', picks)
  assert.equal(
    out,
    'diff @report.txt and @report.txt\n\nReferenced files:\n- /w/one/report.txt\n- /w/two/report.txt'
  )
})

test('composeMessage: deleting one of two same-named labels keeps just one', () => {
  const picks = [
    makePick({ path: '/w/one/report.txt', name: 'report.txt', dir: 'one', kind: 'file' }),
    makePick({ path: '/w/two/report.txt', name: 'report.txt', dir: 'two', kind: 'file' }),
  ]
  const out = composeMessage('only @report.txt now', picks)
  assert.equal(out, 'only @report.txt now\n\nReferenced files:\n- /w/one/report.txt')
})

test('reconcile: independent of pending text whitespace/case of the path', () => {
  const picks = [pick('/w/a.txt', 'a.txt')]
  assert.deepEqual(reconcile(picks, 'no mention here'), [])
})

test('reconcile: a short label is NOT matched inside a longer one (@a in @analysis)', () => {
  const picks = [pick('/w/a', 'a'), pick('/w/analysis', 'analysis')]
  // user deleted "@a" but kept "@analysis" — only the analysis pick survives
  const survivors = reconcile(picks, 'see @analysis').map((p) => p.name)
  assert.deepEqual(survivors, ['analysis'])
})

test('reconcile: a label with spaces survives as a whole token', () => {
  const picks = [pick('/w/my report.txt', 'my report.txt')]
  assert.equal(reconcile(picks, 'read @my report.txt now').length, 1)
})

// ---- highlightSegments (cosmetic backdrop; whole-token, lossless split) ----

test('highlightSegments: no picks → one plain segment, no marks', () => {
  const segs = highlightSegments('compare @a.txt with @b.txt', [])
  assert.equal(joined(segs), 'compare @a.txt with @b.txt')
  assert.deepEqual(marks(segs), [])
})

test('highlightSegments: empty text → no segments', () => {
  assert.deepEqual(highlightSegments('', [pick('/w/a.txt', 'a.txt')]), [])
})

test('highlightSegments: a picked label lights up as a whole token', () => {
  const segs = highlightSegments('compare @a.txt here', [pick('/w/a.txt', 'a.txt')])
  assert.equal(joined(segs), 'compare @a.txt here')
  assert.deepEqual(marks(segs), ['@a.txt'])
})

test('highlightSegments: a short label is NOT highlighted inside a longer one', () => {
  const picks = [pick('/w/a', 'a'), pick('/w/analysis', 'analysis')]
  const segs = highlightSegments('see @analysis', picks)
  assert.deepEqual(marks(segs), ['@analysis'])
})

test('highlightSegments: a mid-word @ (email) is not highlighted', () => {
  const segs = highlightSegments('mail me@a.txt please', [pick('/w/a.txt', 'a.txt')])
  assert.equal(joined(segs), 'mail me@a.txt please')
  assert.deepEqual(marks(segs), [])
})

test('highlightSegments: two occurrences of a label both light up', () => {
  const segs = highlightSegments('@a.txt vs @a.txt', [pick('/w/a.txt', 'a.txt')])
  assert.deepEqual(marks(segs), ['@a.txt', '@a.txt'])
})

test('highlightSegments: a label with spaces lights up whole', () => {
  const segs = highlightSegments('read @my report.txt now', [pick('/w/my report.txt', 'my report.txt')])
  assert.deepEqual(marks(segs), ['@my report.txt'])
})

// ---- parseMessage (inverse of composeMessage; drives transcript highlighting) ----

test('parseMessage: round-trips composeMessage into body + refs', () => {
  const picks = [pick('/w/a.txt', 'a.txt'), pick('/w/docs', 'docs', 'directory')]
  const sent = composeMessage('compare @a.txt and @docs', picks)
  const { body, refs } = parseMessage(sent)
  assert.equal(body, 'compare @a.txt and @docs')
  assert.deepEqual(refs.map((r) => [r.name, r.kind, r.path]), [
    ['a.txt', 'file', '/w/a.txt'],
    ['docs', 'directory', '/w/docs'],
  ])
})

test('parseMessage: the parsed refs highlight the body @labels', () => {
  const sent = composeMessage('see @a.txt here', [pick('/w/a.txt', 'a.txt')])
  const { body, refs } = parseMessage(sent)
  assert.deepEqual(marks(highlightSegments(body, refs)), ['@a.txt'])
})

test('parseMessage: no block → text passes through untouched', () => {
  const { body, refs } = parseMessage('just a plain message with no refs')
  assert.equal(body, 'just a plain message with no refs')
  assert.deepEqual(refs, [])
})

test('parseMessage: a message that only mentions the phrase is not mis-parsed', () => {
  // "Referenced files:" with no following `- ` path lines is ordinary prose.
  const t = 'here are my\nReferenced files:\nand some notes'
  assert.deepEqual(parseMessage(t), { body: t, refs: [] })
})

test('parseMessage: two same-named refs keep their distinct paths', () => {
  const picks = [
    makePick({ path: '/w/one/report.txt', name: 'report.txt', dir: 'one', kind: 'file' }),
    makePick({ path: '/w/two/report.txt', name: 'report.txt', dir: 'two', kind: 'file' }),
  ]
  const sent = composeMessage('@report.txt vs @report.txt', picks)
  const { refs } = parseMessage(sent)
  assert.deepEqual(refs.map((r) => r.path), ['/w/one/report.txt', '/w/two/report.txt'])
})

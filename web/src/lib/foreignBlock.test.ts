import assert from 'node:assert/strict'
import { test } from 'node:test'
import { Marked } from 'marked'
import { foreignBlock } from './foreignBlock.ts'

const md = new Marked({ extensions: [foreignBlock] })
const parse = (s: string) => md.parse(s) as string

test('an SVG with blank and indented lines stays one element', () => {
  const svg = '<svg viewBox="0 0 10 10">\n  <defs>\n\n    <!-- hatch -->\n    <pattern id="p"/>\n  </defs>\n\n    <rect width="5" height="5"/>\n</svg>'
  const html = parse(`Here it is:\n\n${svg}\n\nDone.`)
  assert.ok(html.includes(svg))
  assert.ok(!html.includes('<pre>'))
  assert.ok(html.endsWith('<p>Done.</p>\n'))
})

test('a nested SVG closes at its matching tag', () => {
  const svg = '<svg>\n\n  <svg x="1">\n\n    <circle r="1"/>\n  </svg>\n\n  <rect/>\n</svg>'
  assert.equal(parse(svg), svg + '\n')
})

test('MathML stays one element', () => {
  const math = '<math>\n\n    <mi>x</mi>\n</math>'
  assert.equal(parse(math), math + '\n')
})

test('an SVG inside a code fence stays code', () => {
  const html = parse('```xml\n<svg>\n\n  <rect/>\n</svg>\n```')
  assert.ok(html.startsWith('<pre><code'))
  assert.ok(html.includes('&lt;svg&gt;'))
})

test('an unclosed SVG falls back to plain Markdown', () => {
  assert.ok(parse('<svg>\n\n    <rect/>').includes('<pre>'))
})

test('other HTML keeps Markdown between blank lines', () => {
  assert.ok(parse('<details>\n\n**bold**\n\n</details>').includes('<strong>bold</strong>'))
})

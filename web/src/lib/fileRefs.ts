// Pure logic for the composer's `@` File references (ADR 0012): the picks list,
// the `@`-trigger rule, token reconciliation, and the appended `Referenced files:`
// block. A File reference carries a PATH, not bytes (the counterpart to an
// Attachment). The inline `@label` is cosmetic; this picks list is the source of
// truth. Import-free so it unit-tests under node:test; the DOM glue lives in
// Composer.svelte.

// The cosmetic inline token for a pick — `@` + the file's name. Same-named files
// share a label (they read naturally in a sentence); reconciliation counts label
// occurrences so N surviving `@name`s keep N picks, and their distinct absolute
// paths both land in the block.
export type RefKind = 'file' | 'directory'

// A File reference as the composer holds it: the absolute path plus the cosmetic label.
export type FileRef = { path: string; name: string; dir: string; kind: RefKind; label: string }

// What parseMessage recovers — the sent block records paths, not their dirs.
export type ParsedRef = Omit<FileRef, 'dir'>

// One backdrop segment: `mark` flags a File-reference label.
export type Segment = { text: string; mark: boolean }

export const refLabel = (name: string | null | undefined): string => '@' + String(name ?? '')

// Build a pick record from one `/files/search` result. `kind` defaults to "file";
// a "directory" pick is annotated in the block so the agent lists it (ticket 04).
export function makePick(result: {
  path: string
  name: string
  dir?: string
  kind?: RefKind
}): FileRef {
  return {
    path: result.path,
    name: result.name,
    dir: result.dir || '',
    kind: result.kind || 'file',
    label: refLabel(result.name),
  }
}

// The active `@`-mention under the caret, or null. A mention opens ONLY at the
// start of a fresh token — the `@` is preceded by whitespace or start-of-input —
// so a mid-word `@` or an email (`a@b`) never hijacks input. Returns
// ``{ start, query }``: the index of the `@` and the text typed after it.
export function triggerAt(
  text: string | null | undefined,
  caret: number | null | undefined,
): { start: number; query: string } | null {
  const t = String(text ?? '')
  const pos = Math.max(0, Math.min(caret == null ? t.length : caret, t.length))
  let i = pos
  while (i > 0 && !/\s/.test(t[i - 1])) i-- // walk back to the token start
  if (t[i] !== '@') return null // this token isn't an @-mention
  return { start: i, query: t.slice(i + 1, pos) }
}

// Replace the active `@query` fragment (``[start, caret)``) with the cosmetic
// label plus a trailing space, so the sentence keeps flowing. Returns the new
// text and the caret position just past the inserted label.
export function applyPick(
  text: string | null | undefined,
  start: number,
  caret: number,
  label: string,
): { text: string; caret: number } {
  const t = String(text ?? '')
  const before = t.slice(0, start)
  const after = t.slice(caret)
  const insert = label + ' '
  return { text: before + insert + after, caret: before.length + insert.length }
}

const isNameChar = (c: string): boolean => c !== '' && /[A-Za-z0-9_]/.test(c)

// Count a label's occurrences as a WHOLE token — preceded by whitespace/start and
// not run into by a name char — so `@a` never matches inside `@analysis`.
const countTokens = (hay: string, label: string): number => {
  if (!label) return 0
  let n = 0
  let i = 0
  while ((i = hay.indexOf(label, i)) !== -1) {
    const before = i === 0 ? '' : hay[i - 1]
    const after = hay[i + label.length] ?? ''
    if ((before === '' || /\s/.test(before)) && !isNameChar(after)) n += 1
    i += label.length
  }
  return n
}

// Keep only the picks whose `@label` still survives in `text`; deleting the label
// drops the pick. Two same-named picks need two surviving `@name`s to both ride
// along, in pick order.
export function reconcile<T extends { label: string }>(
  picks: readonly T[] | null | undefined,
  text: string | null | undefined,
): T[] {
  const t = String(text ?? '')
  const budget = new Map<string, number>()
  const out: T[] = []
  for (const p of picks || []) {
    if (!budget.has(p.label)) budget.set(p.label, countTokens(t, p.label))
    const left = budget.get(p.label) ?? 0
    if (left > 0) {
      budget.set(p.label, left - 1)
      out.push(p)
    }
  }
  return out
}

// The `Referenced files:` block for a list of surviving picks (already reconciled),
// or '' for no picks. Each line is the pick's ABSOLUTE path; a Directory is
// annotated so the agent runs `list_folder` rather than `read_file`.
export function buildBlock(picks: readonly Pick<FileRef, 'path' | 'kind'>[] | null | undefined): string {
  if (!picks || !picks.length) return ''
  const lines = picks.map((p) =>
    p.kind === 'directory'
      ? `- ${p.path} (directory — list its contents)`
      : `- ${p.path}`
  )
  return 'Referenced files:\n' + lines.join('\n')
}

// Split `text` into segments for cosmetically highlighting the surviving `@label`
// tokens in the composer's backdrop. Returns `[{ text, mark }]` where `mark` flags a
// File-reference label. Whole-token matching mirrors reconcile()/countTokens — a
// label lights up only when preceded by whitespace/start and not run into by a name
// char, so `@a` never highlights inside `@analysis`; longer labels win on overlap.
// Purely visual (the textarea stays plain text); the picks list is still the source
// of truth for what gets sent.
export function highlightSegments(
  text: string | null | undefined,
  picks: readonly { label: string }[] | null | undefined,
): Segment[] {
  const t = String(text ?? '')
  const labels = [...new Set((picks || []).map((p) => p.label).filter(Boolean))]
    .sort((a, b) => b.length - a.length) // longest-first so `@analysis` beats `@a`
  if (!labels.length) return t ? [{ text: t, mark: false }] : []
  const segs: Segment[] = []
  let plain = 0
  let i = 0
  while (i < t.length) {
    let hit = ''
    if (t[i] === '@') {
      const before = i === 0 ? '' : t[i - 1]
      if (before === '' || /\s/.test(before)) {
        for (const label of labels) {
          if (t.startsWith(label, i) && !isNameChar(t[i + label.length] ?? '')) { hit = label; break }
        }
      }
    }
    if (hit) {
      if (plain < i) segs.push({ text: t.slice(plain, i), mark: false })
      segs.push({ text: hit, mark: true })
      i += hit.length
      plain = i
    } else i++
  }
  if (plain < t.length) segs.push({ text: t.slice(plain), mark: false })
  return segs
}

const DIR_SUFFIX = ' (directory — list its contents)'

// Parse a SENT message back into its natural body and the File references recorded in
// the trailing `Referenced files:` block (the inverse of composeMessage). Lets the
// transcript highlight the same `@labels` the composer did — the block is the source of
// truth (ADR 0012), so each path's basename gives the label to light up, and the raw
// path block can be dropped from display. Returns ``{ body, refs }``; a message without
// a well-formed block passes through untouched (``{ body: text, refs: [] }``) so a user
// who literally types "Referenced files:" isn't mis-parsed.
export function parseMessage(text: string | null | undefined): { body: string; refs: ParsedRef[] } {
  const t = String(text ?? '')
  const marker = '\nReferenced files:\n' // composeMessage appends `\n\n` + block
  const at = t.lastIndexOf(marker)
  if (at === -1) return { body: t, refs: [] }
  const refs: ParsedRef[] = []
  for (const raw of t.slice(at + marker.length).split('\n')) {
    if (raw.trim() === '') continue
    if (!raw.startsWith('- ')) return { body: t, refs: [] } // not a clean block — leave as-is
    let p = raw.slice(2).trim()
    const kind = p.endsWith(DIR_SUFFIX) ? 'directory' : 'file'
    if (kind === 'directory') p = p.slice(0, -DIR_SUFFIX.length).trim()
    const name = p.split('/').pop() ?? ''
    refs.push({ path: p, name, kind, label: refLabel(name) })
  }
  if (!refs.length) return { body: t, refs: [] }
  return { body: t.slice(0, at).replace(/\s+$/, ''), refs }
}

// The outgoing message: the user's text as written (cosmetic `@labels` intact) with
// the reconciled `Referenced files:` block appended. No picks survive → text
// unchanged (no block). No change to send()'s (text, attachments) contract — the
// block rides inside the text (ADR 0012).
export function composeMessage(
  text: string | null | undefined,
  picks: readonly ParsedRef[] | null | undefined,
): string {
  const base = String(text ?? '')
  const block = buildBlock(reconcile(picks, base))
  if (!block) return base
  return (base ? base + '\n\n' : '') + block
}

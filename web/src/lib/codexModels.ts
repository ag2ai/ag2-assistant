// Grouping helpers for the Codex model picker. The codex-acp adapter reports a
// flat catalog of modelIds like "gpt-5.6-sol[medium]" — model family plus
// reasoning effort in brackets. Codex's own UI shows those as two separate
// selectors (Model / Reasoning); these helpers decompose the flat list the same
// way so the Settings form can too. The stored entry keeps the joined id — the
// backend contract (CODEX_CONFIG derivation) doesn't change.

const EFFORT_RE = /^(.+?)\s*\[([^\]]+)\]$/

// The two catalog fields these helpers read — CatalogModel satisfies it.
export type CatalogModelRef = { id: string; name?: string }

// One model family plus every reasoning effort the catalog offers for it.
export type ModelGroup = {
  family: string
  label: string
  efforts: { value: string; label: string }[]
}

// Reasoning labels the way Codex's picker spells them; unknown efforts fall
// back to a capitalized raw value so new tiers still render.
const EFFORT_LABEL: Record<string, string> = {
  minimal: 'Minimal', low: 'Light', medium: 'Medium', high: 'High',
  xhigh: 'Extra High', max: 'Max', ultra: 'Ultra',
}

export function splitModelId(id: string | null | undefined): { family: string; effort: string } {
  // "gpt-5.6-sol[medium]" → {family: "gpt-5.6-sol", effort: "medium"};
  // no brackets → effort ''.
  const m = EFFORT_RE.exec(id || '')
  return m ? { family: m[1].trim(), effort: m[2].trim() } : { family: (id || '').trim(), effort: '' }
}

export function joinModelId(family: string, effort: string): string {
  if (!family) return ''
  return effort ? `${family}[${effort}]` : family
}

export function effortLabel(effort: string): string {
  if (!effort) return 'Default'
  return EFFORT_LABEL[effort] || effort.charAt(0).toUpperCase() + effort.slice(1)
}

// The adapter's names read "GPT-5.6-Sol (medium)" — the family label is the
// name minus the trailing effort parenthetical.
function familyLabel(name: string | undefined, family: string): string {
  const label = (name || '').replace(/\s*\([^)]*\)\s*$/, '').trim()
  return label || family
}

export function groupModels(models: readonly CatalogModelRef[] | null | undefined): ModelGroup[] {
  // Flat adapter catalog → ordered families, each with its efforts in the
  // adapter's order: [{family, label, efforts: [{value, label}]}].
  const out: ModelGroup[] = []
  const byFamily = new Map<string, ModelGroup>()
  for (const m of models || []) {
    const { family, effort } = splitModelId(m.id)
    if (!family) continue
    let entry = byFamily.get(family)
    if (!entry) {
      entry = { family, label: familyLabel(m.name, family), efforts: [] }
      byFamily.set(family, entry)
      out.push(entry)
    }
    if (effort && !entry.efforts.some((e) => e.value === effort)) {
      entry.efforts.push({ value: effort, label: effortLabel(effort) })
    }
  }
  return out
}

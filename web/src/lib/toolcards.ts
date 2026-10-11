// Project structured tool arguments into provenance and artifact metadata.

// Metadata shown in the run list for a tool call.
export type ToolCard =
  | { kind: 'file'; path: string; name: string }
  | { kind: 'search'; query: string }
  | { kind: 'skill'; name: string; script?: string; ran?: boolean }
  | { kind: 'image'; prompt: string; edit: boolean }

// A tool call's arguments — JSON on the wire, already decoded on some events.
type ToolArgs = Record<string, unknown>

const parse = (args: unknown): ToolArgs => {
  if (!args) return {}
  if (typeof args === 'object') return args as ToolArgs
  if (typeof args !== 'string') return {}
  try {
    return JSON.parse(args)
  } catch {
    return {}
  }
}

const baseName = (p: string): string => (p || '').split('/').pop() || p

// Tools whose calls produce a file the user can open/download. The reported path
// is relative to the tool's sandbox root (workspace_dir for chat → matches the
// Files API; a per-task folder for subagents → the card's view falls back to the
// Files browser when the direct fetch 404s).
const FILE_WRITE = ['write_file', 'append_file', 'edit_file', 'update_file']
// Tools whose calls are a web/data search — surface what was searched.
const SEARCH = ['duckduckgo_search', 'gmail_search', 'drive_search']

const REGISTRY: Record<string, (a: ToolArgs) => ToolCard | null> = {}
for (const name of FILE_WRITE)
  REGISTRY[name] = (a) =>
    typeof a.path === 'string' && a.path ? { kind: 'file', path: a.path, name: baseName(a.path) } : null
for (const name of SEARCH)
  REGISTRY[name] = (a) => (typeof a.query === 'string' && a.query ? { kind: 'search', query: a.query } : null)

// Skill provenance: the agent consulting a skill (load_skill) or running one of its
// scripts (run_skill_script) — surface which skill, from the call's `name` arg.
REGISTRY.load_skill = (a) => (typeof a.name === 'string' && a.name ? { kind: 'skill', name: a.name } : null)
REGISTRY.run_skill_script = (a) =>
  typeof a.name === 'string' && a.name
    ? { kind: 'skill', name: a.name, script: typeof a.script === 'string' ? a.script : '', ran: true }
    : null

// Image generation/editing — surface the prompt (the saved file shows in the Files
// browser / image preview; the path is in the result, not the args).
REGISTRY.generate_image = (a) =>
  typeof a.prompt === 'string' && a.prompt
    ? { kind: 'image', prompt: a.prompt, edit: !!a.source_image }
    : null

// Return a card descriptor for a tool call, or null if the tool has no card.
export function cardFor(name: string, args: unknown): ToolCard | null {
  const fn = REGISTRY[name]
  return fn ? fn(parse(args)) || null : null
}

export function toolDetail(name: string, args: unknown): string {
  const values = parse(args)
  const card = cardFor(name, values)
  if (card?.kind === 'skill') {
    const script = card.ran ? card.script : values.resource
    const path = parse(values.args).path
    return [card.name, script, path].filter(value => typeof value === 'string' && value).join(' · ')
  }
  if (card?.kind === 'file') return card.path
  if (card?.kind === 'search') return card.query
  if (card?.kind === 'image') return card.prompt
  return [values.name, values.resource, values.path, values.command, values.query, values.url]
    .filter(value => typeof value === 'string' && value).join(' · ')
}

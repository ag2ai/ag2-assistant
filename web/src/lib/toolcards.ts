// Tool → card registry. Maps a tool name to a function that turns the tool call's
// structured arguments into a card descriptor (or null). A card is just another
// projection of the ToolCallEvent — the args are already on the wire, so there's
// no prose parsing anywhere. Add a new card by adding an entry to REGISTRY.

// One card per tool call the thread renders alongside the chips.
export type ToolCard =
  | { kind: 'file'; path: string; name: string }
  | { kind: 'search'; query: string }
  | { kind: 'skill'; name: string; script?: string; ran?: boolean }
  | { kind: 'image'; prompt: string; edit: boolean }

// The same card once it sits in a thread item: `id` keys the rendered list.
// Distributed over the union so each variant keeps its own shape.
type WithId<T> = T extends unknown ? T & { id: number } : never
export type KeyedToolCard = WithId<ToolCard>

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
const FILE_WRITE = ['write_file', 'append_file', 'edit_file']
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

// Tool → card registry. Maps a tool name to a function that turns the tool call's
// structured arguments into a card descriptor (or null). A card is just another
// projection of the ToolCallEvent — the args are already on the wire, so there's
// no prose parsing anywhere. Add a new card by adding an entry to REGISTRY.

const parse = (args) => {
  if (!args) return {}
  if (typeof args === 'object') return args
  try {
    return JSON.parse(args)
  } catch {
    return {}
  }
}

const baseName = (p) => (p || '').split('/').pop() || p

// Tools whose calls produce a file the user can open/download. The reported path
// is relative to the tool's sandbox root (workspace_dir for chat → matches the
// Files API; a per-task folder for subagents → the card's view falls back to the
// Files browser when the direct fetch 404s).
const FILE_WRITE = ['write_file', 'append_file', 'edit_file']
// Tools whose calls are a web/data search — surface what was searched.
const SEARCH = ['duckduckgo_search', 'gmail_search', 'drive_search']

const REGISTRY = {}
for (const name of FILE_WRITE)
  REGISTRY[name] = (a) => (a.path ? { kind: 'file', path: a.path, name: baseName(a.path) } : null)
for (const name of SEARCH)
  REGISTRY[name] = (a) => (a.query ? { kind: 'search', query: a.query } : null)

// Skill provenance: the agent consulting a skill (load_skill) or running one of its
// scripts (run_skill_script) — surface which skill, from the call's `name` arg.
REGISTRY.load_skill = (a) => (a.name ? { kind: 'skill', name: a.name } : null)
REGISTRY.run_skill_script = (a) =>
  a.name ? { kind: 'skill', name: a.name, script: a.script || '', ran: true } : null

// Image generation/editing — surface the prompt (the saved file shows in the Files
// browser / image preview; the path is in the result, not the args).
REGISTRY.generate_image = (a) =>
  a.prompt ? { kind: 'image', prompt: a.prompt, edit: !!a.source_image } : null

// Return a card descriptor for a tool call, or null if the tool has no card.
export function cardFor(name, args) {
  const fn = REGISTRY[name]
  return fn ? fn(parse(args)) || null : null
}

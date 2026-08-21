// How each provider's own server-side tools are presented — the label, the line
// under it, and what changes when the switch goes on.
// Store-free and transport-free, so tests can enumerate it.
import { m } from '../paraglide/messages.js'

// The server says which tools a type HAS (`builtin_tools_by_type`); this says what
// they are called. Keyed by `${type}.${id}` because the same id is a different
// tool under each provider: one `web_fetch` is Anthropic's citing fetcher, another
// is Gemini's `url_context`. Lookups stay total — an id with no entry here reads
// as its own id rather than vanishing from the form.
export type BuiltinToolText = { label: string; description: string; note?: string }

// The table's own shape: message functions, resolved by builtinToolText() below so
// the words follow the UI language rather than freezing at module load.
type BuiltinToolMessages = { label: () => string; description: () => string; note?: () => string }

// `note` shows only while the switch is on, so a consequence appears at the moment
// it becomes true. Shared messages: the same replacement is the same sentence.
const REPLACES_SEARCH = m.bt_replaces_search
const REPLACES_FETCH = m.bt_replaces_fetch
// Code execution adds a second runner rather than replacing one — say which is
// which, because only the local sandbox can reach the user's own files.
const LOCAL_SANDBOX_STAYS = m.bt_local_sandbox_stays

export const BUILTIN_TOOL_TEXT: Record<string, BuiltinToolMessages> = {
  'anthropic.web_search': {
    label: m.bt_web_search,
    description: m.bt_anthropic_search,
    note: REPLACES_SEARCH,
  },
  'anthropic.web_fetch': {
    label: m.bt_web_fetch,
    description: m.bt_anthropic_fetch,
    note: REPLACES_FETCH,
  },
  'anthropic.code_execution': {
    label: m.bt_code_execution,
    description: m.bt_anthropic_code,
    note: LOCAL_SANDBOX_STAYS,
  },

  'openai_responses.web_search': {
    label: m.bt_web_search,
    description: m.bt_openai_search,
    note: REPLACES_SEARCH,
  },
  'openai_responses.code_execution': {
    label: m.bt_code_interpreter,
    description: m.bt_openai_code,
    note: LOCAL_SANDBOX_STAYS,
  },

  'gemini.web_search': {
    label: m.bt_google_grounding,
    description: m.bt_gemini_search,
    note: REPLACES_SEARCH,
  },
  'gemini.web_fetch': {
    label: m.bt_url_context,
    description: m.bt_gemini_fetch,
    note: REPLACES_FETCH,
  },
  'gemini.code_execution': {
    label: m.bt_code_execution,
    description: m.bt_gemini_code,
    note: LOCAL_SANDBOX_STAYS,
  },
}

// The words for one tool, resolved into the UI language. Total by construction: a
// tool the server offers but this table has no entry for still renders, labelled by
// its id.
export function builtinToolText(type: string, id: string): BuiltinToolText {
  const entry = BUILTIN_TOOL_TEXT[`${type}.${id}`]
  if (!entry) return { label: id, description: '' }
  const text: BuiltinToolText = { label: entry.label(), description: entry.description() }
  if (entry.note) text.note = entry.note()
  return text
}

// The short row chip on the models list — the label alone is too long there.
const CHIP: Record<string, () => string> = {
  web_search: m.bt_chip_search,
  web_fetch: m.bt_chip_fetch,
  code_execution: m.bt_chip_code,
}

export function builtinChip(id: string): string {
  const chip = CHIP[id]
  return chip ? chip() : id
}

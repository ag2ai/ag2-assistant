// How each provider's own server-side tools are presented — the label, the line
// under it, and what changes when the switch goes on.
// Store-free and transport-free, so tests can enumerate it.

// The server says which tools a type HAS (`builtin_tools_by_type`); this says what
// they are called. Keyed by `${type}.${id}` because the same id is a different
// tool under each provider: one `web_fetch` is Anthropic's citing fetcher, another
// is Gemini's `url_context`. Lookups stay total — an id with no entry here reads
// as its own id rather than vanishing from the form.
export type BuiltinToolText = { label: string; description: string; note?: string }

// `note` shows only while the switch is on, so a consequence appears at the moment
// it becomes true. Shared strings: the same replacement is the same sentence.
const REPLACES_SEARCH = 'Replaces the built-in DuckDuckGo search for this model.'
const REPLACES_FETCH = 'Replaces the built-in page fetcher for this model.'
// Code execution adds a second runner rather than replacing one — say which is
// which, because only the local sandbox can reach the user's own files.
const LOCAL_SANDBOX_STAYS = 'Your local sandbox stays available — it is what can reach your own files.'

export const BUILTIN_TOOL_TEXT: Record<string, BuiltinToolText> = {
  'anthropic.web_search': {
    label: 'Web search',
    description: 'Anthropic searches the web and cites its sources.',
    note: REPLACES_SEARCH,
  },
  'anthropic.web_fetch': {
    label: 'Web fetch',
    description: 'Anthropic fetches a page itself, with citations.',
    note: REPLACES_FETCH,
  },
  'anthropic.code_execution': {
    label: 'Code execution',
    description: "Runs code in Anthropic's own sandbox.",
    note: LOCAL_SANDBOX_STAYS,
  },

  'openai_responses.web_search': {
    label: 'Web search',
    description: 'OpenAI searches the web and cites its sources.',
    note: REPLACES_SEARCH,
  },
  'openai_responses.code_execution': {
    label: 'Code interpreter',
    description: 'Runs code in an OpenAI container.',
    note: LOCAL_SANDBOX_STAYS,
  },

  'gemini.web_search': {
    label: 'Google Search grounding',
    description: 'Gemini grounds its answers in Google Search.',
    note: REPLACES_SEARCH,
  },
  'gemini.web_fetch': {
    label: 'URL context',
    description: 'Gemini reads a linked page itself.',
    note: REPLACES_FETCH,
  },
  'gemini.code_execution': {
    label: 'Code execution',
    description: "Runs code on Google's servers.",
    note: LOCAL_SANDBOX_STAYS,
  },
}

// The words for one tool. Total by construction: a tool the server offers but
// this table has no entry for still renders, labelled by its id.
export function builtinToolText(type: string, id: string): BuiltinToolText {
  return BUILTIN_TOOL_TEXT[`${type}.${id}`] || { label: id, description: '' }
}

// The short row chip on the models list — the label alone is too long there.
const CHIP: Record<string, string> = {
  web_search: 'search',
  web_fetch: 'fetch',
  code_execution: 'code',
}

export function builtinChip(id: string): string {
  return CHIP[id] || id
}

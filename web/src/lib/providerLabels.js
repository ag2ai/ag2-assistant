// The human name for every provider the app can configure — text (lib/llm.js) and
// voice (lib/live.js). Store-free and transport-free, so tests can enumerate it.

// LLM config type -> the label the UI shows for it.
// `openai` was once "Chat Completions" and is now "OpenAI-compatible": the
// first-party OpenAI path is always `openai_responses`, so the only thing that
// still produces an `openai` config is pointing at someone else's OpenAI-API
// endpoint. Configs saved under the old meaning keep working and are simply
// labelled by the new one — there is no migration.
export const TYPE_LABEL = {
  openai: 'OpenAI-compatible', openai_responses: 'OpenAI · Responses',
  openai_subscription: 'OpenAI · ChatGPT subscription',
  anthropic: 'Anthropic', gemini: 'Gemini', ollama: 'Ollama',
  claude_code: 'Claude Code · CLI login', codex: 'Codex · CLI login',
}

// The heading for the options that need no API key at all. Named because the
// grouping rule and the group order both have to agree on the same string.
export const SUBSCRIPTION_GROUP = 'Subscription — no API key'

// The order the template grid renders its headings in. Reordering is this line.
// Subscription leads deliberately: it answers a question the user does not know
// to ask, and someone who already pays for ChatGPT or Claude would otherwise
// read past it and conclude they need to go and buy a key.
export const GROUP_ORDER = [SUBSCRIPTION_GROUP, 'OpenAI', 'Anthropic', 'Google', 'Ollama']

// LLM config type -> its group heading. One rule: options needing no API key
// group together, everything else groups by vendor. Deriving the group from the
// type rather than declaring it per template makes an orphaned group — a card
// filed under a heading that isn't rendered — unrepresentable.
export const TYPE_GROUP = {
  openai_subscription: SUBSCRIPTION_GROUP, claude_code: SUBSCRIPTION_GROUP, codex: SUBSCRIPTION_GROUP,
  openai_responses: 'OpenAI', openai: 'OpenAI',
  anthropic: 'Anthropic', gemini: 'Google', ollama: 'Ollama',
}

// LLM config type -> what the user must bring. Rendered on every card, not only
// the exceptions: marking exceptions alone would encode "API key" as an
// invisible default, and absence is the hardest signal for a user to read.
// ACP and OAuth live here rather than as headings — they are transports, and a
// transport should not outrank a vendor in the page's structure.
export const TYPE_CHIP = {
  openai_subscription: 'OAuth', claude_code: 'ACP', codex: 'ACP',
  openai_responses: 'API key', openai: 'API key',
  anthropic: 'API key', gemini: 'API key', ollama: 'no key',
}

// Voice provider -> its label (only realtime-capable providers exist here).
export const PROVIDER_LABEL = { gemini: 'Gemini', openai: 'OpenAI' }

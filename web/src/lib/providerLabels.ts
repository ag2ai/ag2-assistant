// How every provider the app can configure is presented — its label, its template
// group and its chip, for text (lib/llm.ts) and voice (lib/live.ts).
// Store-free and transport-free, so tests can enumerate it.

// Every map here is keyed by a config `type` the server may send, so the lookups
// stay total: an unknown type reads undefined rather than failing to compile.
export type LabelMap = Record<string, string>

// LLM config type -> the label the UI shows for it. `openai` names a wire (Chat
// Completions), not a deployment; the first-party OpenAI path is `openai_responses`.
export const TYPE_LABEL: LabelMap = {
  openai: 'OpenAI · Chat Completions', openai_responses: 'OpenAI · Responses',
  openai_subscription: 'OpenAI · ChatGPT subscription',
  anthropic: 'Anthropic', gemini: 'Gemini', ollama: 'Ollama',
  claude_code: 'Claude Code · CLI login', codex: 'Codex · CLI login',
}

// The template-grid heading for the options that need no API key at all.
export const SUBSCRIPTION_GROUP = 'Subscription — no API key'

// The order the template grid renders its headings in — reordering is this line.
export const GROUP_ORDER: string[] = [SUBSCRIPTION_GROUP, 'OpenAI', 'Anthropic', 'Google', 'Ollama']

// LLM config type -> its group heading: options needing no API key group
// together, everything else groups by vendor.
export const TYPE_GROUP: LabelMap = {
  openai_subscription: SUBSCRIPTION_GROUP, claude_code: SUBSCRIPTION_GROUP, codex: SUBSCRIPTION_GROUP,
  openai_responses: 'OpenAI', openai: 'OpenAI',
  anthropic: 'Anthropic', gemini: 'Google', ollama: 'Ollama',
}

// LLM config type -> what the user must bring, chipped on every template card.
export const TYPE_CHIP: LabelMap = {
  openai_subscription: 'OAuth', claude_code: 'ACP', codex: 'ACP',
  openai_responses: 'API key', openai: 'API key',
  anthropic: 'API key', gemini: 'API key', ollama: 'no key',
}

// Voice provider -> its label (only realtime-capable providers exist here).
export const PROVIDER_LABEL: LabelMap = { gemini: 'Gemini', openai: 'OpenAI' }

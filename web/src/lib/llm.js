// Shared vocabulary for the install-wide named LLM configurations — one source of
// truth for the provider logos, the human type label, and the client-side "can this
// run right now?" predicate. Consumed by Settings → Models (ModelsPage) and the
// composer's model switcher so the two surfaces speak identically.
import openaiLogo from '../assets/openai.svg'
import anthropicLogo from '../assets/anthropic.svg'
import geminiLogo from '../assets/gemini.svg'
import ollamaLogo from '../assets/ollama.svg'

// type -> provider logo (all three OpenAI surfaces share the OpenAI mark).
export const LOGO = {
  openai: openaiLogo, openai_responses: openaiLogo, openai_subscription: openaiLogo,
  anthropic: anthropicLogo, gemini: geminiLogo, ollama: ollamaLogo,
}

// type -> the label the UI shows for it.
export const TYPE_LABEL = {
  openai: 'OpenAI · Chat Completions', openai_responses: 'OpenAI · Responses',
  openai_subscription: 'OpenAI · ChatGPT subscription',
  anthropic: 'Anthropic', gemini: 'Gemini', ollama: 'Ollama',
}

// Whether a config can actually run right now — the signal behind the health dot.
// The API view carries no `usable` flag, so we derive it exactly as the server's
// llm_configs.usable() does: key_source 'none' means no key at all (dead), and a
// ChatGPT-subscription config is live only while signed in. Everything else
// (ollama / custom base_url / own key / shared env key) resolves to a non-'none'
// source and is runnable.
export function isUsable(c) {
  if (c.key_source === 'none') return false
  if (c.type === 'openai_subscription') return !!c.signed_in
  return true
}

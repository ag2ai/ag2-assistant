// The one-click starting points Settings → Models offers when adding a Text model.
// Store-free and transport-free, so tests can enumerate them.
import { m } from '../paraglide/messages.js'
import { typeLabel } from './providerLabels.ts'

// `name` is what the saved config gets called — DATA, not display: it is persisted
// server-side, so it stays English rather than freezing whatever language happened
// to be selected when the template was picked. `card` (the grid label, only where it
// differs from `name`) and `blurb` are display, so they are message functions read at
// render time. The rest prefills the editor; group and chip come from `type`, and
// `model` names an entry of lib/knownModels.ts, which owns every fact about it.
export type ModelTemplate = {
  name: string
  // Only where the grid label differs from the saved config's name.
  card?: () => string
  type: string
  model: string
  blurb: () => string
  base_url?: string
  host?: string
}

export const MODEL_TEMPLATES: ModelTemplate[] = [
  {
    name: 'OpenAI · ChatGPT subscription',
    card: m.tpl_card_chatgpt,
    type: 'openai_subscription', model: 'gpt-5.6-terra',
    blurb: m.tpl_blurb_chatgpt,
  },
  {
    // The card is the type's own label ("Claude Code · CLI login"), so it reads from
    // the one place that spells a CLI login out.
    name: 'Claude Code', card: () => typeLabel('claude_code'),
    type: 'claude_code', model: '',
    blurb: m.tpl_blurb_claude_code,
  },
  {
    // Names the same ChatGPT plan the card above names.
    name: 'Codex', card: () => typeLabel('codex'),
    type: 'codex', model: '',
    blurb: m.tpl_blurb_codex,
  },
  { name: 'OpenAI', type: 'openai_responses', model: 'gpt-5.6-terra', blurb: m.tpl_blurb_openai },
  {
    name: 'OpenAI-compatible',
    type: 'openai', model: '', base_url: 'http://localhost:8080/v1',
    blurb: m.tpl_blurb_openai_compatible,
  },
  { name: 'Anthropic', type: 'anthropic', model: 'claude-opus-4-8', blurb: m.tpl_blurb_anthropic },
  {
    // Seeds MiniMax's live endpoint and model; the card names neither.
    name: 'Anthropic-compatible',
    type: 'anthropic', model: 'MiniMax-M2.5', base_url: 'https://api.minimax.io/anthropic',
    blurb: m.tpl_blurb_anthropic_compatible,
  },
  { name: 'Gemini', type: 'gemini', model: 'gemini-3.6-flash', blurb: m.tpl_blurb_gemini },
  { name: 'Ollama', type: 'ollama', model: 'llama3.2', host: 'http://localhost:11434', blurb: m.tpl_blurb_ollama },
]

// The grid label for one template: its own card where it has one, else the name the
// saved config would get.
export const templateCard = (t: ModelTemplate): string => (t.card ? t.card() : t.name)

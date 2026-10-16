// What this install knows *about* a model name — its label, its price, how much it
// holds, and whether the app puts it forward. Store-free and transport-free.
// Not an inventory: a live Model catalog decides which names exist, this only says
// what one means.

// USD per million tokens, in and out.
export type ModelPrice = { in: number; out: number }

// One table row: everything this install claims to know about a model name.
export type KnownModel = {
  id: string
  label: string
  provider: string
  price: ModelPrice
  context: number
  featured: boolean
}

// A name a live catalog offered that the table has never heard of carries only an
// id and a label, so every reader below takes the partial shape.
export type ModelEntry = Partial<KnownModel>

// LLM config type -> the family whose models it can run. The CLI logins read a live
// catalog from their own adapter, so they belong to no family here.
const TYPE_FAMILY: Record<string, string> = {
  openai: 'openai', openai_responses: 'openai', openai_subscription: 'openai',
  anthropic: 'anthropic', gemini: 'gemini', ollama: 'ollama',
}

// Standard text API prices in USD per million input/output tokens, checked 2026-10-10.
// Long-context premiums, caching, processing tiers and tool charges are not included.
export const KNOWN_MODELS: KnownModel[] = [
  // https://ai.google.dev/gemini-api/docs/pricing
  // Gemini 3.6 Flash promotional rates apply through 2026-12-31.
  { id: 'gemini-3.6-flash', label: 'Gemini 3.6 Flash', provider: 'gemini', price: { in: 0.75, out: 3.75 }, context: 1_048_576, featured: true },
  { id: 'gemini-3.1-flash-lite', label: 'Gemini 3.1 Flash Lite', provider: 'gemini', price: { in: 0.25, out: 1.5 }, context: 1_048_576, featured: true },
  { id: 'gemini-3.1-pro-preview', label: 'Gemini 3.1 Pro Preview', provider: 'gemini', price: { in: 2, out: 12 }, context: 1_048_576, featured: true },

  // https://developers.openai.com/api/docs/models
  { id: 'gpt-6.1-sol', label: 'GPT-6.1 Sol', provider: 'openai', price: { in: 2, out: 10 }, context: 1_050_000, featured: true },
  { id: 'gpt-6-astra', label: 'GPT-6 Astra', provider: 'openai', price: { in: 10, out: 50 }, context: 1_050_000, featured: true },
  { id: 'gpt-6-luna', label: 'GPT-6 Luna', provider: 'openai', price: { in: 0.1, out: 0.5 }, context: 1_050_000, featured: true },
  { id: 'gpt-6-sol', label: 'GPT-6 Sol', provider: 'openai', price: { in: 2, out: 10 }, context: 1_050_000, featured: false },
  { id: 'gpt-5.6-luna', label: 'GPT-5.6 Luna', provider: 'openai', price: { in: 0.2, out: 1.2 }, context: 1_050_000, featured: false },
  { id: 'gpt-5.6-terra', label: 'GPT-5.6 Terra', provider: 'openai', price: { in: 2, out: 12 }, context: 1_050_000, featured: false },
  { id: 'gpt-5.6-sol', label: 'GPT-5.6 Sol', provider: 'openai', price: { in: 4, out: 20 }, context: 1_050_000, featured: false },
  { id: 'gpt-5.4-mini', label: 'GPT-5.4 Mini', provider: 'openai', price: { in: 0.75, out: 4.5 }, context: 400_000, featured: false },
  { id: 'gpt-5.4-nano', label: 'GPT-5.4 Nano', provider: 'openai', price: { in: 0.2, out: 1.25 }, context: 400_000, featured: false },

  // https://platform.claude.com/docs/en/about-claude/pricing
  { id: 'claude-sonnet-5', label: 'Claude Sonnet 5', provider: 'anthropic', price: { in: 2, out: 10 }, context: 1_000_000, featured: true },
  { id: 'claude-haiku-4.5', label: 'Claude Haiku 4.5', provider: 'anthropic', price: { in: 1, out: 5 }, context: 200_000, featured: true },
  { id: 'claude-opus-4-8', label: 'Claude Opus 4.8', provider: 'anthropic', price: { in: 5, out: 25 }, context: 1_000_000, featured: true },
  // https://platform.minimax.io/docs/pricing/overview
  // MiniMax uses the Anthropic protocol at its own endpoint.
  { id: 'MiniMax-M2.5', label: 'MiniMax M2.5', provider: 'anthropic', price: { in: 0.3, out: 1.2 }, context: 204_800, featured: false },

  { id: 'llama3.2', label: 'Llama 3.2', provider: 'ollama', price: { in: 0, out: 0 }, context: 128_000, featured: true },
]

// The family a config type draws its models from; '' when the type has no table here.
export function familyOf(type: string): string {
  return TYPE_FAMILY[type] || ''
}

// The table's entry for an exact model id, or undefined.
export function knownModel(id: string | null | undefined): KnownModel | undefined {
  if (!id) return undefined
  return KNOWN_MODELS.find((m) => m.id === id)
}

// Everything the table knows for a config type, in table order.
export function knownModelsFor(type: string): KnownModel[] {
  const family = familyOf(type)
  return family ? KNOWN_MODELS.filter((m) => m.provider === family) : []
}

// The subset the app puts forward — what onboarding offers and what the combobox ranks first.
export function featuredModelsFor(type: string): KnownModel[] {
  return knownModelsFor(type).filter((m) => m.featured)
}

const money = (n: number) => (n >= 1 ? `$${n}` : `$${n.toFixed(2)}`)

// What a model costs, per million tokens in and out; '' when the table has no price.
export function priceLabel(entry: ModelEntry | null | undefined): string {
  const price = entry?.price
  if (!price || typeof price.in !== 'number' || typeof price.out !== 'number') return ''
  if (!price.in && !price.out) return 'Free'
  return `${money(price.in)} / ${money(price.out)} per M`
}

// How much a model holds; '' when the table has no window for it.
export function contextLabel(entry: ModelEntry | null | undefined): string {
  const context = entry?.context
  if (typeof context !== 'number' || !context) return ''
  const size = context >= 1_000_000 ? `${+(context / 1_000_000).toFixed(2)}M` : `${Math.round(context / 1000)}K`
  return `${size} context`
}

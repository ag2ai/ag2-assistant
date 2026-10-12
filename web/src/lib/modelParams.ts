// How each type's parameters are presented — the label and the line under it.
// Store-free and transport-free, so tests can enumerate it.

// The server says which parameters a type HAS (`params_by_type`); this says what
// they are called. Keyed by `${type}.${id}`; an id with no entry reads as itself.
export type ModelParamText = { label: string; description: string }

// Words shared by every type that offers the parameter.
const TEMPERATURE: ModelParamText = {
  label: 'Temperature',
  description: 'Lower is more predictable, higher is more varied.',
}
const OUTPUT_LIMIT: ModelParamText = {
  label: 'Output limit',
  description: 'The most tokens one answer may use.',
}

export const MODEL_PARAM_TEXT: Record<string, ModelParamText> = {
  'openai.reasoning_effort': {
    label: 'Reasoning effort',
    description: 'How much the model thinks before it answers.',
  },
  'openai.temperature': TEMPERATURE,
  'openai.max_tokens': OUTPUT_LIMIT,

  'openai_responses.temperature': TEMPERATURE,
  'openai_responses.max_output_tokens': OUTPUT_LIMIT,

  'gemini.thinking_level': {
    label: 'Thinking level',
    description: 'How much Gemini thinks before it answers.',
  },
  'gemini.temperature': TEMPERATURE,
  'gemini.max_output_tokens': {
    label: 'Output limit',
    description: 'The most tokens one answer may use, thinking included.',
  },

  'anthropic.temperature': TEMPERATURE,
  'anthropic.max_tokens': OUTPUT_LIMIT,

  'ollama.temperature': TEMPERATURE,
  'ollama.max_tokens': OUTPUT_LIMIT,
}

export function modelParamText(type: string, id: string): ModelParamText {
  return MODEL_PARAM_TEXT[`${type}.${id}`] || { label: id, description: '' }
}

// Levels whose word is not just the value capitalised.
const CHOICE_LABEL: Record<string, string> = { xhigh: 'Extra high' }

export function choiceLabel(value: string): string {
  return CHOICE_LABEL[value] || value.charAt(0).toUpperCase() + value.slice(1)
}

// What one control's text means as a stored value; undefined is "provider default".
export function parseParam(kind: string, text: string): unknown {
  const t = text.trim()
  if (!t) return undefined
  if (kind === 'choice') return t
  return Number(t)
}

// The names the Advanced (JSON) box sets — each one overrides its parameter.
export function advancedKeys(text: string): Set<string> {
  try {
    const parsed: unknown = JSON.parse(text)
    const isObject = !!parsed && typeof parsed === 'object' && !Array.isArray(parsed)
    if (!isObject) return new Set()
    const options = parsed as Record<string, unknown>
    const extraBody = options.extra_body
    const bodyKeys = extraBody && typeof extraBody === 'object' && !Array.isArray(extraBody)
      ? Object.keys(extraBody) : []
    return new Set([...Object.keys(options), ...bodyKeys])
  } catch {
    return new Set()
  }
}

// How each type's parameters are presented — the label and the line under it.
// Store-free and transport-free, so tests can enumerate it.

// The server says which parameters a type HAS (`params_by_type`); this says what
// they are called. Keyed by `${type}.${id}`; an id with no entry reads as itself.
export type ModelParamText = { label: string; description: string }

export const MODEL_PARAM_TEXT: Record<string, ModelParamText> = {
  'openai.reasoning_effort': {
    label: 'Reasoning effort',
    description: 'How much the model thinks before it answers.',
  },
  'gemini.thinking_level': {
    label: 'Thinking level',
    description: 'How much Gemini thinks before it answers.',
  },
}

export function modelParamText(type: string, id: string): ModelParamText {
  return MODEL_PARAM_TEXT[`${type}.${id}`] || { label: id, description: '' }
}

// Levels whose word is not just the value capitalised.
const CHOICE_LABEL: Record<string, string> = { xhigh: 'Extra high' }

export function choiceLabel(value: string): string {
  return CHOICE_LABEL[value] || value.charAt(0).toUpperCase() + value.slice(1)
}

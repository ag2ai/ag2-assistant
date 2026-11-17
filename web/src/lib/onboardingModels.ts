import { OLLAMA_TEMPLATES, type OllamaMode } from './modelTemplates.ts'

export type OllamaOnboardingDraft = { host: string; model: string; apiKey: string }

export type OllamaOnboardingConfig = {
  name: string
  type: string
  model: string
  host?: string
  secret_id?: string
}

export function newOllamaOnboardingDraft(mode: OllamaMode): OllamaOnboardingDraft {
  const template = OLLAMA_TEMPLATES[mode]
  return { host: template.host || '', model: template.model, apiKey: '' }
}

export function ollamaOnboardingReady(mode: OllamaMode, draft: OllamaOnboardingDraft): boolean {
  if (!draft.model.trim()) return false
  if (mode === 'cloud') return !!draft.apiKey.trim()
  return !!draft.host.trim()
}

export function ollamaOnboardingConfig(
  mode: OllamaMode,
  draft: OllamaOnboardingDraft,
  secretId = '',
): OllamaOnboardingConfig | null {
  if (!ollamaOnboardingReady(mode, draft)) return null
  const template = OLLAMA_TEMPLATES[mode]
  const config = { name: template.name, type: template.type, model: draft.model.trim() }
  if (mode === 'cloud') return secretId ? { ...config, secret_id: secretId } : config
  return { ...config, host: draft.host.trim() }
}

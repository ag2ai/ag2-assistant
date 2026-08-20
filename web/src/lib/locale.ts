// UI language (see CONTEXT.md): the per-device display language of the chrome.
// Pure resolution logic — callers own localStorage and navigator. The assistant's
// reply language is not derived from this: it mirrors the user's message.

export const UI_LOCALES = ['en', 'ru', 'es', 'de', 'zh-CN', 'pt-BR', 'hi'] as const
export type UiLocale = (typeof UI_LOCALES)[number]

// Selector labels: each language in its own name, so a user stranded in the wrong
// language can still find theirs. Deliberately NOT translated per locale.
export const LOCALE_ENDONYM: Record<UiLocale, string> = {
  en: 'English',
  ru: 'Русский',
  es: 'Español',
  de: 'Deutsch',
  'zh-CN': '中文（简体）',
  'pt-BR': 'Português (Brasil)',
  hi: 'हिन्दी',
}

// Primary language subtag → the supported locale it folds onto. Variants beyond
// these (zh-TW, pt-PT, es-419, …) collapse to the one supported representative.
const PRIMARY_FOLD: Record<string, UiLocale> = {
  en: 'en',
  ru: 'ru',
  es: 'es',
  de: 'de',
  zh: 'zh-CN',
  pt: 'pt-BR',
  hi: 'hi',
}

const CANONICAL = new Map(UI_LOCALES.map((l) => [l.toLowerCase(), l]))

export function isUiLocale(value: unknown): value is UiLocale {
  return typeof value === 'string' && CANONICAL.get(value.toLowerCase()) === value
}

// stored (the per-device choice) wins; then the browser's languages in preference
// order, exact tag first and primary-subtag fold second per entry; then English.
export function resolveUiLocale(stored: string | null, browser: readonly string[]): UiLocale {
  if (stored !== null && isUiLocale(stored)) return stored
  for (const tag of browser) {
    const exact = CANONICAL.get(tag.toLowerCase())
    if (exact) return exact
    const primary = tag.toLowerCase().split('-', 1)[0] ?? ''
    const folded = PRIMARY_FOLD[primary]
    if (folded) return folded
  }
  return 'en'
}

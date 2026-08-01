// The human name for every provider the app can configure — text (lib/llm.js) and
// voice (lib/live.js). Store-free and transport-free, so tests can enumerate it.

// LLM config type -> the label the UI shows for it.
export const TYPE_LABEL = {
  openai: 'OpenAI · Chat Completions', openai_responses: 'OpenAI · Responses',
  openai_subscription: 'OpenAI · ChatGPT subscription',
  anthropic: 'Anthropic', gemini: 'Gemini', ollama: 'Ollama',
  claude_code: 'Claude Code · CLI login', codex: 'Codex · CLI login',
}

// Voice provider -> its label (only realtime-capable providers exist here).
export const PROVIDER_LABEL = { gemini: 'Gemini', openai: 'OpenAI' }

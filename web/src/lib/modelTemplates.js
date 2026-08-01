// The one-click starting points Settings → Models offers when adding a Text model.
// Store-free and transport-free, so tests can enumerate them.

// `name` is what the saved config gets called, `card` the grid label where it
// differs; the rest prefills the editor. Group and chip come from `type`.
// `model` names an entry of lib/knownModels.js, which owns every fact about it —
// knownModels.test.mjs is the gate that keeps a seed from naming a model nobody knows.
export const MODEL_TEMPLATES = [
  {
    name: 'OpenAI · ChatGPT subscription',
    card: 'OpenAI · Sign in with ChatGPT',
    type: 'openai_subscription', model: 'gpt-5.6-terra',
    blurb: 'Your ChatGPT plan, via browser sign-in — unofficial, may break OpenAI ToS. Generates images',
  },
  {
    name: 'Claude Code', card: 'Claude Code · CLI login',
    type: 'claude_code', model: '',
    blurb: 'Your Claude subscription, via the Claude Code CLI you’re already signed in to',
  },
  {
    // Names the same ChatGPT plan the card above names.
    name: 'Codex', card: 'Codex · CLI login',
    type: 'codex', model: '',
    blurb: 'Your ChatGPT plan, via the Codex CLI you’re already signed in to',
  },
  { name: 'OpenAI', type: 'openai_responses', model: 'gpt-5.6-terra', blurb: 'GPT models, direct from OpenAI — generates images' },
  {
    name: 'OpenAI-compatible',
    type: 'openai', model: '', base_url: 'http://localhost:8080/v1',
    blurb: 'Any OpenAI-API endpoint — set the model name and URL',
  },
  { name: 'Anthropic', type: 'anthropic', model: 'claude-opus-4-8', blurb: 'Claude, direct from Anthropic' },
  {
    // Seeds MiniMax's live endpoint and model; the card names neither.
    name: 'Anthropic-compatible',
    type: 'anthropic', model: 'MiniMax-M2.5', base_url: 'https://api.minimax.io/anthropic',
    blurb: 'Any Anthropic-API endpoint — set the model name and URL',
  },
  { name: 'Gemini', type: 'gemini', model: 'gemini-3.6-flash', blurb: 'Gemini models, including image generation' },
  { name: 'Ollama', type: 'ollama', model: 'llama3.2', host: 'http://localhost:11434', blurb: 'Models running locally via Ollama' },
]

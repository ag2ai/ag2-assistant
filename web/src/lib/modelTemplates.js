// The one-click starting points Settings → Models offers when adding a Text model.
// Store-free and transport-free, like providerLabels.js beside it, so one test can
// enumerate every card and assert it resolves to a heading, a chip and a mark.
//
// Each descriptor: `name` is what the saved config gets called, `card` (optional)
// is the grid label when it differs, and the rest is the prefill the editor opens
// with. A card's group and chip are NOT declared here — both follow from `type`
// (see TYPE_GROUP / TYPE_CHIP), so adding a template cannot forget either.
//
// Blurbs are authored per card rather than derived from type, which is what lets
// `OpenAI-compatible` make no image-generation claim despite sharing the `openai`
// type with configurations that can generate images. Do not derive them.
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
    // Says "your ChatGPT plan" for the same reason the card above it does: one
    // subscription, two doors — not two products.
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
    // Seeds MiniMax even though the card no longer names it: a live endpoint is a
    // better starting point than a blank one, and the URL is there to be replaced.
    name: 'Anthropic-compatible',
    type: 'anthropic', model: 'MiniMax-M2.5', base_url: 'https://api.minimax.io/anthropic',
    blurb: 'Any Anthropic-API endpoint — set the model name and URL',
  },
  { name: 'Gemini', type: 'gemini', model: 'gemini-3.6-flash', blurb: 'Gemini models, including image generation' },
  { name: 'Ollama', type: 'ollama', model: 'llama3.2', host: 'http://localhost:11434', blurb: 'Models running locally via Ollama' },
]

<script>
  import { onMount } from 'svelte'
  import { settingsOpen, voicePickerOpen, googleOpen } from '../store.js'
  import { api } from '../transport/api.js'

  let voice = $state('')
  let provider = $state('')
  let google = $state(null)

  const PROVIDER_LABEL = { gemini: 'Gemini', openai: 'OpenAI' }

  async function load() {
    try { const d = await api.voices(); voice = d.current; provider = d.provider || '' } catch {}
    try { google = await api.googleStatus() } catch {}
  }
  onMount(load)

  const close = () => ($settingsOpen = false)
  const openVoice = () => { $settingsOpen = false; $voicePickerOpen = true }
  const openGoogle = () => { $settingsOpen = false; $googleOpen = true }
</script>

<div class="modal-backdrop" onclick={close}></div>
<div class="modal settings">
  <h2>Settings</h2>

  <button class="setrow" onclick={openVoice}>
    <span class="sk">🎙 Voice</span>
    <span class="sv">{voice || '…'}{provider ? ' · ' + (PROVIDER_LABEL[provider] || provider) : ''}</span>
    <span class="sgo">Change →</span>
  </button>

  <button class="setrow" onclick={openGoogle}>
    <span class="sk">Google</span>
    <span class="sv">{google == null ? '…' : google.signed_in ? ('Connected · ' + (google.email || 'account')) : 'Not connected'}</span>
    <span class="sgo">Manage →</span>
  </button>

  <button class="modal-close" onclick={close}>Close</button>
</div>

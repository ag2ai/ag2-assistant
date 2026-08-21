<script lang="ts">
  import { m } from '../paraglide/messages.js'
  import { onMount, onDestroy } from 'svelte'
  import { voicePickerOpen, voicePickerConfig } from '../store.ts'
  import { api } from '../transport/api/index.ts'
  import { loadLiveConfigs } from '../lib/live.ts'
  import type { VoiceCatalog } from '../schemas/index.ts'

  type Voice = VoiceCatalog['voices'][number]

  // Which live config (if any) this picker targets — captured once at mount so the
  // scope is stable while open. null → the profile's legacy voice setting.
  const configId = $voicePickerConfig

  let voices: Voice[] = $state([])
  let current: string | null = $state('')
  let provider = $state('')
  let playing = $state('')   // name currently previewing
  let audio: HTMLAudioElement | null = null

  const PROVIDER_LABEL: Record<string, string | undefined> = { gemini: 'Gemini', openai: 'OpenAI' }

  async function load() {
    try { const d = await api.voices(configId); voices = d.voices; current = d.current; provider = d.provider || '' } catch {}
  }
  onMount(load)
  function _stopAudio() { if (audio) { audio.pause(); URL.revokeObjectURL(audio.src); audio = null } }
  onDestroy(_stopAudio)

  function _play(src: string, name: string) {
    return new Promise<void>((resolve, reject) => {
      _stopAudio()
      audio = new Audio(src)
      audio.onended = () => { if (playing === name) playing = '' }
      audio.onerror = reject
      audio.play().then(resolve, reject)
    })
  }

  async function choose(v: Voice) {
    current = v.name
    // persist (applies next voice session); scoped to this config when set, so the
    // Live list's voice chip updates — refresh the shared store on success.
    api.selectVoice(v.name, configId).then(() => { if (configId) loadLiveConfigs() }).catch(() => {})
    playing = v.name
    try {
      // prefer the pre-recorded sample (instant); fall back to live TTS if absent
      await _play('/voices/' + encodeURIComponent(v.name) + '.wav', v.name)
    } catch {
      try {
        const blob = await api.previewVoice(v.name, configId)
        await _play(URL.createObjectURL(blob), v.name)
      } catch { playing = '' }
    }
  }

  // Reset the target so a later legacy open isn't left scoped to this config.
  const close = () => { _stopAudio(); voicePickerConfig.set(null); $voicePickerOpen = false }
</script>

<!-- Scoped to a config → stack OVER Settings (.over) rather than replace it. -->
<!-- Backdrop: click-to-dismiss duplicates the × button, so it stays out of the
     a11y tree rather than becoming a second focusable control. -->
<div class="modal-backdrop" class:over={!!configId} role="presentation" onclick={close}></div>
<div class="modal voicepick" class:over={!!configId}>
  <button class="modal-x" aria-label={m.action_close()} onclick={close}>×</button>
  <h2>{m.voice_title()}{provider ? ' — ' + (PROVIDER_LABEL[provider] || provider) : ''}</h2>
  <p class="muted">{m.voice_hint()}</p>
  <div class="vlist">
    {#each voices as v (v.name)}
      <button class="vrow" class:on={current === v.name} onclick={() => choose(v)}>
        <span class="vn">{v.name}</span>
        <span class="vs">{v.style}</span>
        <span class="vp">{playing === v.name ? '▶ playing…' : current === v.name ? '✓ current' : ''}</span>
      </button>
    {/each}
  </div>
</div>

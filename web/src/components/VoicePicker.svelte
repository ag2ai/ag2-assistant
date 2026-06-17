<script>
  import { onMount, onDestroy } from 'svelte'
  import { voicePickerOpen } from '../store.js'
  import { api } from '../transport/api.js'

  let voices = $state([])
  let current = $state('')
  let playing = $state('')   // name currently previewing
  let audio = null

  async function load() {
    try { const d = await api.voices(); voices = d.voices; current = d.current } catch {}
  }
  onMount(load)
  function _stopAudio() { if (audio) { audio.pause(); URL.revokeObjectURL(audio.src); audio = null } }
  onDestroy(_stopAudio)

  async function choose(v) {
    current = v.name
    api.selectVoice(v.name).catch(() => {})   // persist (applies next voice session)
    playing = v.name
    try {
      const blob = await api.previewVoice(v.name)
      _stopAudio()
      audio = new Audio(URL.createObjectURL(blob))
      audio.onended = () => { if (playing === v.name) playing = '' }
      await audio.play()
    } catch { playing = '' }
  }

  const close = () => { _stopAudio(); $voicePickerOpen = false }
</script>

<div class="modal-backdrop" onclick={close}></div>
<div class="modal voicepick">
  <h2>Voice</h2>
  <p class="muted">Pick a voice — it plays a sample and is saved for your chats (applies next voice session).</p>
  <div class="vlist">
    {#each voices as v (v.name)}
      <button class="vrow" class:on={current === v.name} onclick={() => choose(v)}>
        <span class="vn">{v.name}</span>
        <span class="vs">{v.style}</span>
        <span class="vp">{playing === v.name ? '▶ playing…' : current === v.name ? '✓ current' : ''}</span>
      </button>
    {/each}
  </div>
  <button class="modal-close" onclick={close}>Close</button>
</div>

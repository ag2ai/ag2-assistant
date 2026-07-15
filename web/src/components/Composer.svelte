<script>
  import { onMount } from 'svelte'
  import { thread, settingsOpen, settingsPage, SETTINGS_PAGE } from '../store.js'
  import { send, stop, startVoice, stopVoice, voice } from '../controller.js'
  import { liveConfigs, loadLiveConfigs } from '../lib/live.js'
  import { llmConfigs } from '../lib/llm.js'
  import Icon from './Icon.svelte'
  import ModelSwitcher from './composer/ModelSwitcher.svelte'

  // The live-voice button needs an ACTIVE live config to run — load the shared store
  // (same one Settings → Live mutates, so adding/activating one enables the button
  // live). No active config → the button reads as muted and routes to Settings → Models
  // (its Live section) instead of starting a session.
  onMount(() => { loadLiveConfigs().catch(() => {}) })
  const noLiveModel = $derived(!$liveConfigs.active)

  // No Text/LLM model configured → sending is pointless (it would fail with no model /
  // key), so gate the Send button. Mirror ModelSwitcher's "No models configured" state:
  // no configs AND no env pin. Gate on `loaded` so a not-yet-fetched store doesn't
  // flash Send disabled. The shared llmConfigs store is loaded by ModelSwitcher.
  const noTextModel = $derived(
    $llmConfigs.loaded && !$llmConfigs.configs.length && !$llmConfigs.envOverride
  )

  let text = $state('')
  let pending = $state([])  // {name, payload:{name,mime,data(b64)}}
  let ta, fileInput

  function submit() {
    const t = text.trim()
    if (!t && !pending.length) return
    send(t, pending.map((p) => p.payload))
    text = ''; pending = []
    if (ta) ta.style.height = 'auto'
  }
  function key(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
  }
  // While the agent is working, Enter still sends — the message is fed to the running
  // turn (it picks it up at its next step), so say so rather than leaving it a mystery.
  function placeholder() {
    if ($thread.busy) return 'Add something while it works…'
    return $thread.kind === 'task' ? 'Tell the agent to change this task…' : 'Message AG2 Assistant…'
  }
  function grow() { if (ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 160) + 'px' } }
  // A running session always toggles off. Otherwise: no active Live model → route to
  // Settings → Models to configure one; else start the session.
  function liveClick() {
    if (!$voice.active && noLiveModel) {
      settingsPage.set(SETTINGS_PAGE.MODELS)
      settingsOpen.set(true)
      return
    }
    $voice.active ? stopVoice() : startVoice()
  }

  function toB64(file) {
    return new Promise((res, rej) => {
      const r = new FileReader()
      r.onload = () => res(String(r.result).split(',')[1] || '')
      r.onerror = rej
      r.readAsDataURL(file)
    })
  }
  async function pick(e) {
    for (const f of e.target.files) {
      const data = await toB64(f)
      pending = [...pending, { name: f.name, payload: { name: f.name, mime: f.type, data } }]
    }
    if (fileInput) fileInput.value = ''
  }
  const removeFile = (i) => { pending = pending.filter((_, j) => j !== i) }
</script>

<div class="composer">
  <div class="inputbox" class:busy={$thread.busy}>
    {#if pending.length}
      <div class="pending">
        {#each pending as p, i}
          <span class="chip"><Icon name="paperclip" size={13} /> {p.name}<button class="x" onclick={() => removeFile(i)}>×</button></span>
        {/each}
      </div>
    {/if}
    <input type="file" multiple hidden bind:this={fileInput} onchange={pick} />
    <textarea
      class="cinput"
      bind:this={ta}
      bind:value={text}
      rows="1"
      placeholder={placeholder()}
      oninput={grow}
      onkeydown={key}
    ></textarea>
    <div class="cbar">
      <button class="cbtn" onclick={() => fileInput.click()} title="Attach files" aria-label="Attach files"><Icon name="plus" size={18} /></button>
      <div class="cbar-right">
        <ModelSwitcher />
        <!-- Single live-voice control: toggles the realtime voice session. Disabled
             until a Live model is active (unless a session is already running, so it
             can still be stopped). The wrapper carries the explain-why tooltip because a
             disabled button doesn't fire hover on its own. -->
        <span class="ctip">
          <button class="cbtn live" class:on={$voice.active} class:needcfg={noLiveModel && !$voice.active}
                  onclick={liveClick}
                  title={noLiveModel ? undefined : 'Live voice'}
                  aria-label={noLiveModel ? 'Configure a Live model' : 'Live voice'}><Icon name="waveform" size={18} /></button>
          {#if noLiveModel && !$voice.active}
            <span class="ctip-bubble" role="tooltip">No Live model yet — click to configure one in Settings and enable Live support.</span>
          {/if}
        </span>
        <!-- Primary action. While a turn runs it's Stop; Enter still sends (feeds the
             running turn), so "add while it works" survives via the keyboard. Idle it's
             Send, disabled until there's text or an attachment. -->
        {#if $thread.busy}
          <button class="csend stop" onclick={stop} title="Stop the agent" aria-label="Stop the agent"><Icon name="square" size={15} /></button>
        {:else}
          <span class="ctip">
            <button class="csend" onclick={submit}
                    disabled={noTextModel || (!text.trim() && !pending.length)}
                    title={noTextModel ? undefined : 'Send'} aria-label="Send"><Icon name="arrow-up" size={18} /></button>
            {#if noTextModel}
              <span class="ctip-bubble" role="tooltip">No model configured — add one in Settings → Models to send messages.</span>
            {/if}
          </span>
        {/if}
      </div>
    </div>
  </div>
  <div class="cnote">AG2 Assistant is AI and can make mistakes. Check important info.</div>
</div>

<style>
  /* Hover-explain tooltip for the composer's gated buttons (Live mic, Send). Anchored to
     the button's right edge (both live at the right of the composer) so the bubble never
     runs off-screen. The wrapper carries the hover because a disabled button can't. */
  .ctip { position: relative; display: inline-flex; }
  /* Muted (no Live model yet) but still clickable — it routes to Settings, so it
     stays a pointer, not a dead disabled control. */
  .cbtn.needcfg { opacity: .45; }
  .ctip-bubble {
    position: absolute; bottom: calc(100% + 8px); right: 0;
    width: max-content; max-width: 220px; text-align: left; line-height: 1.35;
    background: var(--surface-elevated); color: var(--ink);
    border: 1px solid var(--line); border-radius: var(--radius-sm);
    box-shadow: var(--shadow-lg); padding: 7px 10px; font-size: 12px;
    opacity: 0; pointer-events: none; transform: translateY(3px);
    transition: opacity 120ms ease, transform 120ms ease; z-index: 40;
  }
  .ctip:hover .ctip-bubble { opacity: 1; transform: translateY(0); }
</style>

<script>
  import { thread } from '../store.js'
  import { send, stop, startVoice, stopVoice, voice } from '../controller.js'
  import Icon from './Icon.svelte'
  import ModelSwitcher from './composer/ModelSwitcher.svelte'

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
  function toggleMic() { $voice.active ? stopVoice() : startVoice() }

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
        <!-- Single live-voice control: toggles the realtime voice session. -->
        <button class="cbtn live" class:on={$voice.active} onclick={toggleMic}
                title="Live voice" aria-label="Live voice"><Icon name="waveform" size={18} /></button>
        <!-- Primary action. While a turn runs it's Stop; Enter still sends (feeds the
             running turn), so "add while it works" survives via the keyboard. Idle it's
             Send, disabled until there's text or an attachment. -->
        {#if $thread.busy}
          <button class="csend stop" onclick={stop} title="Stop the agent" aria-label="Stop the agent"><Icon name="square" size={15} /></button>
        {:else}
          <button class="csend" onclick={submit} disabled={!text.trim() && !pending.length}
                  title="Send" aria-label="Send"><Icon name="arrow-up" size={18} /></button>
        {/if}
      </div>
    </div>
  </div>
  <div class="cnote">AG2 Assistant is AI and can make mistakes. Check important info.</div>
</div>

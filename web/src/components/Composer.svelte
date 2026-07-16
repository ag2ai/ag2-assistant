<script>
  import { thread } from '../store.js'
  import { send, stop, startVoice, stopVoice, voice } from '../controller.js'
  import Icon from './Icon.svelte'

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
  {#if pending.length}
    <div class="pending">
      {#each pending as p, i}
        <span class="chip"><Icon name="paperclip" size={13} /> {p.name}<button class="x" onclick={() => removeFile(i)}>×</button></span>
      {/each}
    </div>
  {/if}
  <div class="crow">
    <button class="icon" onclick={() => fileInput.click()} title="Attach files" aria-label="Attach files"><Icon name="paperclip" size={18} /></button>
    <input type="file" multiple hidden bind:this={fileInput} onchange={pick} />
    <textarea
      bind:this={ta}
      bind:value={text}
      rows="1"
      placeholder={placeholder()}
      oninput={grow}
      onkeydown={key}
    ></textarea>
    <button class="icon mic" class:live={$voice.active} onclick={toggleMic} title="Talk to AG2 Assistant" aria-label="Talk to AG2 Assistant"><Icon name="mic" size={18} /></button>
    <!-- Stop sits BESIDE Send while a turn runs: sending mid-turn feeds the running
         turn, so the composer must stay usable — stopping is a separate choice. -->
    {#if $thread.busy}
      <button class="icon stop" onclick={stop} title="Stop the agent" aria-label="Stop the agent"><Icon name="square" size={14} /></button>
    {/if}
    <button class="send" onclick={submit}><Icon name="send" size={16} /> Send</button>
  </div>
  {#if $voice.active}
    <div class="voicebar"><span class="vdot"></span>{$voice.status}</div>
  {/if}
</div>

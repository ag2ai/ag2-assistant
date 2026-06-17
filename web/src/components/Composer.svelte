<script>
  import { thread } from '../store.js'
  import { send } from '../controller.js'

  let text = $state('')
  let ta

  function submit() {
    const t = text.trim()
    if (!t) return
    send(t)
    text = ''
    if (ta) ta.style.height = 'auto'
  }
  function key(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
  }
  function grow() { if (ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 160) + 'px' } }
</script>

<div class="composer">
  <div class="crow">
    <textarea
      bind:this={ta}
      bind:value={text}
      rows="1"
      placeholder={$thread.kind === 'task' ? 'Tell the agent to change this task…' : 'Message AGClaw…'}
      oninput={grow}
      onkeydown={key}
    ></textarea>
    <button class="send" onclick={submit}>Send</button>
  </div>
</div>

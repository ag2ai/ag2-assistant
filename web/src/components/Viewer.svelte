<script>
  // Full-view modal. Two ways to open it:
  //   $viewer = { title, text }        → rendered as markdown (deliverables)
  //   $viewer = { title, name, path }  → rendered by file type (Files / tool cards)
  // File types: html/image/pdf render natively (iframe/img against the raw
  // endpoint); md/code/text render in-app; unknown types offer a download.
  import { viewer } from '../store.js'
  import { api } from '../transport/api.js'
  import Markdown from './Markdown.svelte'
  import { viewerKind } from '../lib/preview.js'

  const close = () => ($viewer = null)
  const kind = $derived($viewer?.path ? viewerKind($viewer.name || $viewer.path) : 'markdown')
  const url = $derived($viewer?.path ? api.fileUrl($viewer.path) : '')
  const native = $derived(kind === 'html' || kind === 'pdf' || kind === 'image')

  // Text-backed kinds need the file contents; native kinds load the URL directly.
  let text = $state('')
  let err = $state('')
  $effect(() => {
    const v = $viewer
    text = ''
    err = ''
    if (v?.text != null) {
      text = v.text
      return
    }
    if (v?.path && (kind === 'markdown' || kind === 'code' || kind === 'text')) {
      api.fileText(v.path).then((t) => (text = t)).catch((e) => (err = String(e.message || e)))
    }
  })
</script>

<div class="modal-backdrop over" onclick={close}></div>
<div class="modal viewer over">
  <button class="modal-x" aria-label="Close" onclick={close}>×</button>
  <div class="vhead">
    <h2>{$viewer.title || 'Preview'}</h2>
    {#if $viewer.path}<a class="dl" href={api.fileUrl($viewer.path, true)}>download</a>{/if}
  </div>
  <div class="vbody" class:native>
    {#if err}
      <p class="muted" style="color:#d8552f">{err}</p>
    {:else if kind === 'html'}
      <!-- agent HTML: scripts run but in an opaque origin (no allow-same-origin) -->
      <iframe class="vframe" title={$viewer.title} src={url} sandbox="allow-scripts"></iframe>
    {:else if kind === 'pdf'}
      <iframe class="vframe" title={$viewer.title} src={url}></iframe>
    {:else if kind === 'image'}
      <img class="vimg" src={url} alt={$viewer.title} />
    {:else if kind === 'code'}
      <pre class="vcode">{text}</pre>
    {:else if kind === 'text'}
      <pre class="vtext">{text}</pre>
    {:else if kind === 'download'}
      <p class="muted">
        No preview for this file type — <a class="dl" href={api.fileUrl($viewer.path, true)}>download it</a>.
      </p>
    {:else}
      <Markdown {text} />
    {/if}
  </div>
</div>

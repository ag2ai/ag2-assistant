<script>
  // The docked preview rail: renders the route's `aside` file (or the path-less
  // transient `viewer` body) beside the conversation in the grid's right column.
  // html/image/pdf render natively; md/code/text in-app; unknown types → download.
  import { onDestroy } from 'svelte'
  import { route, closeAside } from '../router.js'
  import { viewer, previewWidth, previewExpanded, resetPreviewView, revealFile } from '../store.js'
  import { api } from '../transport/api.js'
  import Markdown from './Markdown.svelte'
  import RailResizer from './RailResizer.svelte'
  import Icon from './Icon.svelte'
  import { viewerKind } from '../lib/preview.js'

  // A URL-addressed file wins; a path-less transient body is the fallback when no
  // file is addressed. The rail shows exactly one of them.
  const file = $derived($route.aside?.kind === 'file' ? $route.aside : null)
  const transient = $derived(!file && $viewer?.text != null ? $viewer : null)

  const path = $derived(file?.path || null)
  const name = $derived(path ? path.split('/').pop() : null)
  const title = $derived(file ? (name || 'Preview') : (transient?.title || 'Preview'))
  const kind = $derived(path ? viewerKind(name) : 'markdown')
  const url = $derived(path ? api.fileUrl(path) : '')
  const native = $derived(kind === 'html' || kind === 'pdf' || kind === 'image')
  // Offer a copy button for text-backed kinds and html (copy the source), and for
  // images (copy the pixels). markdown additionally gets a rendered/raw switcher.
  const copyable = $derived(kind === 'markdown' || kind === 'code' || kind === 'text' || kind === 'html' || kind === 'image')

  // Close strips the aside key from the URL for a file; clears the store for a
  // transient body (it was never in the URL).
  function close() {
    if (file) closeAside()
    else $viewer = null
  }

  // Unmounting (any close path) forgets the preview's width + expanded; the next open
  // starts docked. A reload skips onDestroy, so a file preview survives it (App boot reconciles).
  onDestroy(resetPreviewView)

  // Text-backed kinds need the file contents; native kinds load the URL directly.
  let text = $state('')
  let err = $state('')
  let raw = $state(false)   // markdown only: show the source instead of the render
  $effect(() => {
    const p = path, tr = transient
    text = ''
    err = ''
    raw = false             // each newly-opened file starts on the rendered view
    if (tr) { text = tr.text; return }
    if (p && (kind === 'markdown' || kind === 'code' || kind === 'text')) {
      api.fileText(p).then((t) => (text = t)).catch((e) => (err = String(e.message || e)))
    }
  })

  // Copy to the clipboard, with a brief ✓ confirmation: text-backed kinds copy the
  // source; images copy the pixels.
  let copied = $state(false)
  async function copy() {
    try {
      if (kind === 'image') {
        // The clipboard only accepts PNG across browsers, so hand ClipboardItem a
        // promise that fetches + rasterises the image — resolving it inside write()
        // keeps the call in the user-gesture tick (Safari requires that).
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': imagePng() })])
      } else if (kind === 'html') {
        // html renders natively (iframe), so its source isn't held in `text` — fetch it.
        await navigator.clipboard.writeText(await api.fileText(path))
      } else {
        await navigator.clipboard.writeText(text || '')
      }
      copied = true
      setTimeout(() => (copied = false), 1400)
    } catch { /* clipboard blocked / unsupported — no-op */ }
  }

  // Draw the current image onto a canvas and export a PNG blob. The image is
  // same-origin (/api/files/raw), so the canvas stays untainted and exportable.
  async function imagePng() {
    const img = new Image()
    img.src = url
    await img.decode()
    const canvas = document.createElement('canvas')
    canvas.width = img.naturalWidth
    canvas.height = img.naturalHeight
    canvas.getContext('2d').drawImage(img, 0, 0)
    return await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'))
  }
</script>

<aside class="rail viewer">
  <RailResizer width={previewWidth} onGrab={() => previewExpanded.set(false)} />
  <div class="vhead">
    {#if kind === 'markdown'}
      <div class="vseg" role="group" aria-label="View mode">
        <button class="vsegbtn" class:on={!raw} aria-pressed={!raw}
                title="Preview" aria-label="Preview" onclick={() => (raw = false)}>
          <Icon name="eye" size={14} />
        </button>
        <button class="vsegbtn" class:on={raw} aria-pressed={raw}
                title="Raw source" aria-label="Raw source" onclick={() => (raw = true)}>
          <Icon name="code" size={14} />
        </button>
      </div>
    {/if}
    {#if path}
      <!-- A path-backed preview: the filename Reveals the file in the Files tree
           (switch Tab, expand its Directories, scroll it into view). -->
      <button class="vtitle" title={`Reveal ${name} in Files`} onclick={() => revealFile(path)}>{title}</button>
    {:else}
      <!-- Path-less transient body: no tree row to reveal, so a plain heading. -->
      <h2 title={title}>{title}</h2>
    {/if}
    {#if copyable}
      <button class="cp" class:copied
              title={copied ? 'Copied' : 'Copy'} aria-label="Copy" onclick={copy}>
        <Icon name={copied ? 'check' : 'copy'} size={15} />
      </button>
    {/if}
    <button class="exp" aria-pressed={$previewExpanded}
            title={$previewExpanded ? 'Collapse preview' : 'Expand preview'}
            aria-label={$previewExpanded ? 'Collapse preview' : 'Expand preview'}
            onclick={() => previewExpanded.update((v) => !v)}>
      <Icon name={$previewExpanded ? 'minimize-2' : 'maximize-2'} size={15} />
    </button>
    {#if path}<a class="dl" href={api.fileUrl(path, true)} title="Download file" aria-label="Download file"><Icon name="download" size={15} /></a>{/if}
    <button class="rail-x" aria-label="Close" onclick={close}>×</button>
  </div>
  <div class="vbody" class:native>
    {#if err}
      <p class="muted" style="color:#d8552f">{err}</p>
    {:else if kind === 'html'}
      <!-- agent HTML: scripts run but in an opaque origin (no allow-same-origin) -->
      <iframe class="vframe" title={title} src={url} sandbox="allow-scripts"></iframe>
    {:else if kind === 'pdf'}
      <iframe class="vframe" title={title} src={url}></iframe>
    {:else if kind === 'image'}
      <img class="vimg" src={url} alt={title} />
    {:else if kind === 'code'}
      <pre class="vcode">{text}</pre>
    {:else if kind === 'text'}
      <pre class="vtext">{text}</pre>
    {:else if kind === 'download'}
      <p class="muted">
        No preview for this file type — <a class="dl" href={api.fileUrl(path, true)}>download it</a>.
      </p>
    {:else if raw}
      <pre class="vtext">{text}</pre>
    {:else}
      <Markdown {text} />
    {/if}
  </div>
</aside>

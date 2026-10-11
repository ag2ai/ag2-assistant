<script lang="ts">
  import { onDestroy } from 'svelte'
  import Icon from '../Icon.svelte'
  import { cardImage } from '../../lib/cardImage.ts'

  let { target }: { target: () => HTMLElement | undefined } = $props()
  let busy = $state(false)
  let copied = $state(false)
  let error = $state('')
  let timer: ReturnType<typeof setTimeout> | undefined
  let disposed = false
  onDestroy(() => { disposed = true; clearTimeout(timer) })

  async function copy() {
    const node = target()
    if (busy || !node) return
    clearTimeout(timer)
    busy = true; copied = false; error = ''
    try {
      if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') {
        throw new Error('Image copying is unavailable in this browser. Use HTTPS or localhost in a browser that supports copying images.')
      }
      // Start the clipboard write in the click's user activation, before rendering finishes.
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': cardImage(node) })])
      if (disposed) return
      copied = true
      timer = setTimeout(() => { copied = false }, 1800)
    } catch (cause) {
      if (disposed) return
      error = cause instanceof Error && cause.name !== 'NotAllowedError'
        ? cause.message : 'Could not copy the image. Allow clipboard access and try again.'
    } finally { if (!disposed) busy = false }
  }
</script>

<button class="card-save-trigger card-image-copy" class:copied disabled={busy} aria-busy={busy}
  title={busy ? 'Copying image…' : copied ? 'Image copied' : 'Copy as image'}
  aria-label="Copy card as image" onclick={copy}>
  <Icon name={busy ? 'rotate-cw' : copied ? 'check' : 'copy'} size={16} />
</button>
{#if copied}<span class="card-image-feedback copied-feedback" role="status">Image copied</span>{/if}
{#if error}<p class="card-image-feedback" role="alert">{error}</p>{/if}

<style>
  .copied { color: var(--accent); }
  .card-image-feedback { margin: 8px 0 0; font-size: 12px; color: var(--danger); }
  .copied-feedback { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
</style>

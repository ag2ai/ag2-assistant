<script lang="ts">
  import type { Snippet } from 'svelte'
  import Icon from '../Icon.svelte'

  type Props = {
    label: string; icon: string; triggerClass: string; alwaysVisible?: boolean;
    opened?: boolean; onOpen: () => void; children: Snippet;
  }
  let { label, icon, triggerClass, alwaysVisible = false, opened = $bindable(false), onOpen, children }: Props = $props()
  const id = $props.id()
  let panel: HTMLDivElement | undefined = $state()
  let button: HTMLButtonElement | undefined = $state()
  let left = $state(12)
  let top = $state(12)

  function position() {
    if (!panel || !button) return
    const anchor = button.getBoundingClientRect()
    const size = panel.getBoundingClientRect()
    left = Math.max(12, Math.min(anchor.right - size.width, window.innerWidth - size.width - 12))
    let nextTop = anchor.bottom + 8
    if (nextTop + size.height > window.innerHeight - 12) nextTop = anchor.top - size.height - 8
    top = Math.max(12, Math.min(nextTop, window.innerHeight - size.height - 12))
  }

  $effect(() => {
    if (!panel) return
    if (opened) {
      if (!panel.matches(':popover-open')) panel.showPopover()
      position()
    } else if (panel.matches(':popover-open')) panel.hidePopover()
  })

  $effect(() => {
    if (!opened) return
    window.addEventListener('resize', position)
    window.addEventListener('scroll', position, true)
    const observer = new ResizeObserver(position)
    if (panel) observer.observe(panel)
    return () => {
      window.removeEventListener('resize', position)
      window.removeEventListener('scroll', position, true)
      observer.disconnect()
    }
  })
</script>

<button bind:this={button} class="card-save-trigger {triggerClass}" class:always-visible={alwaysVisible}
  onclick={() => { if (opened) opened = false; else { onOpen(); opened = true } }}
  title={label} aria-label={label} aria-haspopup="dialog" aria-expanded={opened} aria-controls={id}>
  <Icon name={icon} size={16} />
</button>
<div bind:this={panel} {id} class="card-save-menu" popover="auto" role="dialog" aria-label={label}
  style:left="{left}px" style:top="{top}px"
  onbeforetoggle={(event) => { if (event.newState === 'closed' && panel?.contains(document.activeElement)) button?.focus() }}
  ontoggle={(event) => { opened = event.newState === 'open' }}>
  {@render children()}
</div>

<style>
  .card-save-menu :global(form) { display: flex; flex-direction: column; gap: 12px; }
  .card-save-menu :global(h2) { margin: 0; font-size: 18px; font-weight: var(--fw-semibold); line-height: 1.3; }
  .card-save-menu :global(label) { display: flex; flex-direction: column; gap: 6px; font-size: 12px; }
  .card-save-menu :global(input), .card-save-menu :global(select) { width: 100%; box-sizing: border-box; padding: 8px 10px; border: 1px solid var(--line); border-radius: var(--radius-sm); color: var(--text); background: var(--bg); font: inherit; }
  .card-save-menu :global(p) { margin: 0; font-size: 12px; }
  .card-save-menu :global(.card-save-actions) { display: flex; flex-wrap: wrap; justify-content: end; gap: 8px; margin-top: 4px; }
  .card-save-menu { position: fixed; inset: auto; margin: 0; box-sizing: border-box; width: min(340px, calc(100vw - 24px)); max-height: calc(100dvh - 24px); overflow: auto; padding: 16px; border: 1px solid var(--line); border-radius: 12px; color: var(--text); background: var(--panel); box-shadow: 0 12px 32px #0004; }
</style>

<script lang="ts">
  // Resize a pane from its outer edge; pointer capture tracks drags across iframes.
  // Left panes measure from their own origin, right panes from the viewport edge.
  import type { Writable } from 'svelte/store'
  import { railWidth } from '../store.ts'
  import { clampRailWidth } from '../lib/railWidth.ts'

  // onGrab fires once when a drag begins; the preview passes it to exit expanded mode.
  // Optional — noop otherwise.
  type Props = {
    side?: 'left' | 'right'
    width?: Writable<number>
    clamp?: (px: number) => number
    onGrab?: () => void
  }
  let { side = 'right', width = railWidth, clamp = clampRailWidth, onGrab }: Props = $props()
  let dragging = $state(false)

  function onPointerDown(e: PointerEvent & { currentTarget: HTMLElement }) {
    onGrab?.()
    dragging = true
    e.currentTarget.setPointerCapture(e.pointerId)
    e.preventDefault()
  }
  function onPointerMove(e: PointerEvent & { currentTarget: HTMLElement }) {
    if (!dragging) return
    // A release can be missed (pointer crossing into the preview iframe, lost
    // capture) — if no button is held anymore, stop rather than track forever.
    if (e.buttons === 0) { stop(e); return }
    const px = side === 'left'
      ? e.clientX - (e.currentTarget.parentElement?.getBoundingClientRect().left ?? 0)
      : window.innerWidth - e.clientX
    width.set(clamp(px))
  }
  // pointerup and pointercancel both end the drag; the browser fires cancel
  // (not up) when capture is lost, which is what left the drag stuck.
  function stop(e: PointerEvent & { currentTarget: HTMLElement }) {
    dragging = false
    e.currentTarget.releasePointerCapture?.(e.pointerId)
  }
</script>

<div
  class="rail-resizer"
  class:dragging
  class:left={side === 'left'}
  role="separator"
  aria-orientation="vertical"
  aria-label="Resize panel"
  onpointerdown={onPointerDown}
  onpointermove={onPointerMove}
  onpointerup={stop}
  onpointercancel={stop}
></div>

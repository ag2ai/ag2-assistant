<script lang="ts">
  import { m } from '../paraglide/messages.js'
  // A vertical drag handle for a resizable pane. Defaults resize the right rail
  // (handle on its left edge, width = viewport-right − pointer-x); pass side="left"
  // with the drawer's store/clamp to resize the left drawer (width = pointer-x).
  // Pointer capture keeps the drag tracking across a preview <iframe>.
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
    const px = side === 'left' ? e.clientX : window.innerWidth - e.clientX
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
  aria-label={m.rail_resize_aria()}
  onpointerdown={onPointerDown}
  onpointermove={onPointerMove}
  onpointerup={stop}
  onpointercancel={stop}
></div>

<script>
  // A vertical drag handle for a resizable pane. Defaults resize the right rail
  // (handle on its left edge, width = viewport-right − pointer-x); pass side="left"
  // with the drawer's store/clamp to resize the left drawer (width = pointer-x).
  // Pointer capture keeps the drag tracking across a preview <iframe>.
  import { railWidth } from '../store.ts'
  import { clampRailWidth } from '../lib/railWidth.js'

  // onGrab fires once when a drag begins; the preview passes it to exit expanded mode.
  // Optional — noop otherwise.
  let { side = 'right', width = railWidth, clamp = clampRailWidth, onGrab } = $props()
  let dragging = $state(false)

  function onPointerDown(e) {
    onGrab?.()
    dragging = true
    e.currentTarget.setPointerCapture(e.pointerId)
    e.preventDefault()
  }
  function onPointerMove(e) {
    if (!dragging) return
    // A release can be missed (pointer crossing into the preview iframe, lost
    // capture) — if no button is held anymore, stop rather than track forever.
    if (e.buttons === 0) { stop(e); return }
    const px = side === 'left' ? e.clientX : window.innerWidth - e.clientX
    width.set(clamp(px))
  }
  // pointerup and pointercancel both end the drag; the browser fires cancel
  // (not up) when capture is lost, which is what left the drag stuck.
  function stop(e) {
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

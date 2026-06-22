<script>
  // Typographic AG2 logomark — replaces the retired pixel-robot image. Accent
  // fill, display font, on-accent ink; size scales the tile + glyph together.
  let { size = 30 } = $props()
</script>

<span class="monogram" style="--mono-size:{size}px;font-size:{size * 0.42}px">AG2</span>

<style>
  .monogram {
    display: inline-flex; align-items: center; justify-content: center;
    width: var(--mono-size); height: var(--mono-size); flex: none;
    border-radius: var(--radius-sm);
    background: var(--accent); color: var(--text-on-accent);
    font-family: var(--font-display); font-weight: 800;
    letter-spacing: -0.03em; user-select: none;
  }
</style>

// Pane width clamps — pure helpers (the tested seam); the drag glue and the
// persisted stores build on them. Widths are view-state, kept in localStorage not
// the URL. The right rail and the left drawer each get their own bounds.

export const MIN_RAIL_WIDTH = 280
export const MAX_RAIL_WIDTH = 760
export const DEFAULT_RAIL_WIDTH = 380

export const DEFAULT_DRAWER_WIDTH = 270
export const MAX_DRAWER_WIDTH = 480
// The default IS the floor — the drawer can only be widened, never shrunk below it.
export const MIN_DRAWER_WIDTH = DEFAULT_DRAWER_WIDTH

// Clamp a candidate pixel width into [min, max]. A non-finite / non-numeric input
// (undefined, NaN, a bad localStorage value) falls back to the safe default.
function clamp(px: number | string | null | undefined, min: number, max: number, def: number): number {
  const n = typeof px === 'number' ? px : Number(px)
  if (!Number.isFinite(n)) return def
  return Math.min(max, Math.max(min, n))
}

export function clampRailWidth(px: number | string | null | undefined): number {
  return clamp(px, MIN_RAIL_WIDTH, MAX_RAIL_WIDTH, DEFAULT_RAIL_WIDTH)
}

export function clampDrawerWidth(px: number | string | null | undefined): number {
  return clamp(px, MIN_DRAWER_WIDTH, MAX_DRAWER_WIDTH, DEFAULT_DRAWER_WIDTH)
}

import { a2uiValue } from './a2ui.ts'
import type { A2UIComponent, A2UIData } from './a2ui.ts'

const TRACKS = {
  narrow: 'minmax(64px, .65fr)',
  regular: 'minmax(120px, 1fr)',
  wide: 'minmax(240px, 3fr)',
} as const

export function tableColumn(component: A2UIComponent, data: A2UIData, scope: string) {
  const width = String(a2uiValue(component.columnWidth, data, scope) ?? '')
  const align = a2uiValue(component.columnAlign, data, scope)
  const overflow = a2uiValue(component.columnOverflow, data, scope)
  return {
    track: Object.hasOwn(TRACKS, width) ? TRACKS[width as keyof typeof TRACKS] : 'minmax(94px, 1fr)',
    align: align === 'center' || align === 'end' ? align : 'start',
    overflow: overflow === 'nowrap' || overflow === 'ellipsis' ? overflow : 'wrap',
  }
}

export function tableRowVariant(component: A2UIComponent, data: A2UIData, scope: string) {
  const variant = a2uiValue(component.rowVariant, data, scope)
  return variant === 'summary' || variant === 'baseline' ? variant : 'body'
}

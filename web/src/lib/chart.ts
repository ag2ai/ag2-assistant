import { ChartData } from '../schemas/chart.ts'

export type ChartVariant = 'bar' | 'line'
type Point = { x: number; y: number; value: number; category: number }
type Bar = Point & { series: number; width: number; height: number; start: number; end: number }
export type ChartGeometry = {
  width: number; height: number; left: number; right: number; top: number; bottom: number
  min: number; max: number; baseline: number; ticks: { value: number; y: number }[]
  categories: { index: number; label: string; x: number }[]
  bars: Bar[]; lines: { series: number; paths: string[]; points: Point[] }[]
  annotations: { label: string; x: number }[]
}

export function readChart(labels: unknown, series: unknown, annotations: unknown = [], variant: unknown = 'bar', stacked: unknown = false): { data: ChartData | null; error: string } {
  if (variant !== 'bar' && variant !== 'line') return { data: null, error: 'Use variant bar or line.' }
  if (stacked && variant !== 'bar') return { data: null, error: 'Only bar charts support stacked=true.' }
  const parsed = ChartData.safeParse({ labels, series, annotations })
  return parsed.success ? { data: parsed.data, error: '' } : { data: null, error: parsed.error.issues[0].message }
}

function domain(min: number, max: number): { min: number; max: number; ticks: number[] } {
  if (min === max) max = min + 1
  const rawStep = Math.max(1e-12, (max - min) / 4)
  const power = 10 ** Math.floor(Math.log10(rawStep))
  const ratio = rawStep / power
  const step = (ratio <= 1 ? 1 : ratio <= 2 ? 2 : ratio <= 5 ? 5 : 10) * power
  min = Math.floor(min / step) * step
  max = Math.ceil(max / step) * step
  const ticks = Array.from({ length: Math.round((max - min) / step) + 1 }, (_, i) => Number((min + i * step).toPrecision(12)))
  return { min, max, ticks }
}

export function chartGeometry(data: ChartData, variant: ChartVariant, stacked: boolean, width = 640, height = 280, hidden: ReadonlySet<number> = new Set()): ChartGeometry {
  const visible = data.series.map((series, index) => ({ ...series, index })).filter(series => !hidden.has(series.index))
  let min = 0, max = 0
  for (let category = 0; category < data.labels.length; category++) {
    let positive = 0, negative = 0
    for (const series of visible) {
      const value = series.values[category]
      if (value === null) continue
      if (variant === 'bar' && stacked) {
        if (value >= 0) positive += value
        else negative += value
      } else {
        min = Math.min(min, value); max = Math.max(max, value)
      }
    }
    min = Math.min(min, negative); max = Math.max(max, positive)
  }
  const scale = domain(min, max)
  const left = 76, right = Math.max(360, width, left + 24 + data.labels.length * 36) - 24
  const top = 36, bottom = height - 48
  const pitch = (right - left) / data.labels.length
  const x = (index: number) => left + (index + 0.5) * pitch
  const y = (value: number) => bottom - (value - scale.min) / (scale.max - scale.min) * (bottom - top)
  const bars: Bar[] = [], lines: ChartGeometry['lines'] = []
  if (variant === 'bar') {
    for (let category = 0; category < data.labels.length; category++) {
      let positive = 0, negative = 0
      visible.forEach((series, slot) => {
        const value = series.values[category]
        if (value === null) return
        const start = stacked ? (value >= 0 ? positive : negative) : 0
        const end = start + value
        if (stacked) { if (value >= 0) positive = end; else negative = end }
        const width = pitch * 0.7 / (stacked ? 1 : visible.length)
        bars.push({ series: series.index, category, value, start, end, width,
          x: x(category) - pitch * 0.35 + (stacked ? 0 : slot * width),
          y: Math.min(y(start), y(end)), height: Math.abs(y(end) - y(start)) })
      })
    }
  } else {
    for (const series of visible) {
      const points: Point[] = [], paths: string[] = []
      let path = ''
      series.values.forEach((value, category) => {
        if (value === null) { if (path) paths.push(path); path = ''; return }
        const point = { x: x(category), y: y(value), value, category }
        points.push(point)
        path += `${path ? ' L' : 'M'}${point.x},${point.y}`
      })
      if (path) paths.push(path)
      lines.push({ series: series.index, paths, points })
    }
  }
  return {
    width: right + 24, height, left, right, top, bottom, min: scale.min, max: scale.max,
    baseline: y(0), ticks: scale.ticks.map(value => ({ value, y: y(value) })),
    categories: data.labels.map((label, index) => ({ index, label, x: x(index) })),
    bars, lines, annotations: data.annotations.map(annotation => ({ label: annotation.label, x: x(annotation.index) })),
  }
}

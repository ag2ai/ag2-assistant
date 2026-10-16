import { z } from 'zod'

const Label = z.string().min(1).max(200)
export const ChartSeries = z.object({
  label: Label,
  values: z.array(z.number().min(-1e12).max(1e12).nullable()).min(1).max(366),
  tone: z.enum(['neutral', 'muted', 'accent', 'positive', 'negative']).optional(),
}).strict()
export type ChartSeries = z.infer<typeof ChartSeries>

export const ChartData = z.object({
  labels: z.array(Label).min(1).max(366),
  series: z.array(ChartSeries).min(1).max(8),
  annotations: z.array(z.object({ index: z.number().min(0), label: Label }).strict()).max(8),
}).strict().superRefine((data, ctx) => {
  if (data.series.some(series => series.values.length !== data.labels.length)) {
    ctx.addIssue({ code: 'custom', message: 'Each series needs one value per label; use null for gaps.' })
  }
  if (data.annotations.some(annotation => annotation.index > data.labels.length - 1)) {
    ctx.addIssue({ code: 'custom', message: 'Annotation index must lie within the labels axis.' })
  }
})
export type ChartData = z.infer<typeof ChartData>

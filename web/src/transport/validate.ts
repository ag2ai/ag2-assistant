// The one gate every API response passes through: validate against its schema,
// then return the typed value. Dev throws so a mismatch is loud; prod warns.
import { z } from 'zod'

export type ValidationMode = 'throw' | 'warn'

export class SchemaError extends Error {
  readonly label: string
  readonly issues: z.core.$ZodIssue[]

  constructor(label: string, error: z.ZodError) {
    const detail = error.issues.map((i) => `${i.path.join('.') || '<root>'}: ${i.message}`)
    super(`${label} — ${detail.join('; ')}`)
    this.name = 'SchemaError'
    this.label = label
    this.issues = error.issues
  }
}

// Vite defines import.meta.env; under `node --test` it is absent, hence the guard.
let mode: ValidationMode = import.meta.env?.DEV ? 'throw' : 'warn'

export function setValidationMode(next: ValidationMode): void {
  mode = next
}

export function getValidationMode(): ValidationMode {
  return mode
}

// Generic over the schema, not its output: a caller holding an opaque
// `S extends z.ZodTypeAny` (the transport helpers) can't satisfy z.ZodType<T>.
export function parse<S extends z.ZodTypeAny>(
  schema: S,
  data: unknown,
  label: string,
): z.infer<S> {
  const result = schema.safeParse(data)
  if (result.success) return result.data
  if (mode === 'throw') throw new SchemaError(label, result.error)
  console.warn(`[schema] ${label}`, result.error.issues)
  return data as z.infer<S>
}

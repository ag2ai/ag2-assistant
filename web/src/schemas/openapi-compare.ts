// Reduce a zod-derived JSON Schema and an OpenAPI component to one comparable
// shape. Full JSON Schema equality is not the goal: zod and pydantic disagree on
// title, description, format and integer-vs-number, and comparing those would
// make the gate a false-positive generator. What IS compared — field names,
// requiredness and enum members — is exactly what the four defects recorded in
// ADR 0026 were made of.

export type JsonSchema = Record<string, unknown>

export type FieldInfo = { required: boolean; values: string[] | null }

// zod 4.4.3 inlines nested objects, so $defs is normally empty — but relying on
// that would break the day a cyclic schema forces zod to emit refs.
export function defsOf(schema: JsonSchema): Record<string, JsonSchema> {
  return (schema.$defs as Record<string, JsonSchema> | undefined) ?? {}
}

// A $ref points either into an OpenAPI document (#/components/schemas/X) or into
// a zod bundle (#/$defs/X); the last segment is the key in both.
function deref(schema: JsonSchema, defs: Record<string, JsonSchema>): JsonSchema {
  let current = schema
  const seen = new Set<string>()
  while (typeof current.$ref === 'string') {
    const key = current.$ref.split('/').pop() as string
    if (seen.has(key)) return {}
    seen.add(key)
    const target = defs[key]
    if (target === undefined) return {}
    current = target
  }
  return current
}

// `z.string().nullable()` and `str | None` both render as an anyOf carrying a
// null member. That is a nullability marker, not a discriminated union, so the
// null member is stripped and a lone survivor is unwrapped.
function stripNull(schema: JsonSchema): JsonSchema {
  const branches = schema.anyOf ?? schema.oneOf
  if (!Array.isArray(branches)) return schema
  const real = (branches as JsonSchema[]).filter((b) => b.type !== 'null')
  if (real.length === branches.length) return schema
  if (real.length === 1) return real[0]
  return { anyOf: real }
}

// Strip nullability, then deref AGAIN: the lone survivor of an anyOf is often a
// $ref, and pydantic renders `Model | None` exactly that way. Dereferencing only
// before the strip would leave that ref unresolved, and a body of one nullable
// model would read as describing no fields at all.
function resolve(schema: JsonSchema, defs: Record<string, JsonSchema>): JsonSchema {
  return deref(stripNull(deref(schema, defs)), defs)
}

function valuesOf(schema: JsonSchema): string[] | null {
  if (Array.isArray(schema.enum)) return schema.enum.map(String).sort()
  if ('const' in schema) return [String(schema.const)]
  return null
}

export function flatten(
  schema: JsonSchema,
  defs: Record<string, JsonSchema>,
  prefix = '',
  out = new Map<string, FieldInfo>(),
): Map<string, FieldInfo> {
  const node = resolve(schema, defs)

  const branches = node.anyOf ?? node.oneOf
  if (Array.isArray(branches)) {
    ;(branches as JsonSchema[]).forEach((branch, i) => flatten(branch, defs, `${prefix}|${i}`, out))
    return out
  }

  if (node.items !== undefined) {
    flatten(node.items as JsonSchema, defs, `${prefix}[]`, out)
    return out
  }

  const props = node.properties as Record<string, JsonSchema> | undefined
  if (props !== undefined) {
    const required = new Set((node.required as string[] | undefined) ?? [])
    for (const [key, raw] of Object.entries(props)) {
      const path = prefix ? `${prefix}.${key}` : key
      const child = resolve(raw, defs)
      out.set(path, { required: required.has(key), values: valuesOf(child) })
      flatten(child, defs, path, out)
    }
  }

  // A record/dict: one value schema standing for every key.
  const additional = node.additionalProperties
  if (additional !== undefined && typeof additional === 'object' && additional !== null) {
    flatten(additional as JsonSchema, defs, `${prefix}{}`, out)
  }

  return out
}

export function diff(
  zodSide: Map<string, FieldInfo>,
  openapiSide: Map<string, FieldInfo>,
): string[] {
  const issues: string[] = []
  for (const path of [...new Set([...zodSide.keys(), ...openapiSide.keys()])].sort()) {
    const inZod = zodSide.get(path)
    const inApi = openapiSide.get(path)
    if (inZod === undefined) {
      issues.push(`${path}: in the gateway, missing from the zod schema`)
      continue
    }
    if (inApi === undefined) {
      issues.push(`${path}: in the zod schema, missing from the gateway`)
      continue
    }
    if (inZod.required !== inApi.required) {
      const zodWord = inZod.required ? 'required' : 'optional'
      const apiWord = inApi.required ? 'required' : 'optional'
      issues.push(`${path}: ${zodWord} in zod, ${apiWord} in the gateway`)
    }
    if (inZod.values !== null && inApi.values !== null) {
      const onlyZod = inZod.values.filter((v) => !inApi.values!.includes(v))
      const onlyApi = inApi.values!.filter((v) => !inZod.values!.includes(v))
      if (onlyZod.length || onlyApi.length) {
        issues.push(
          `${path}: enum members differ — only in zod [${onlyZod}], only in the gateway [${onlyApi}]`,
        )
      }
    }
  }
  return issues
}

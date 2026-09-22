// The conditions the WeatherGlyph primitive draws — every tier (WebGPU scene, vector
// glyph, emoji) draws these eight. Mirrors assistant/a2ui.py's WEATHER_CONDITIONS.
export const WEATHER_CONDITIONS = [
  'sunny',
  'partly-cloudy',
  'cloudy',
  'foggy',
  'rainy',
  'thunderstorm',
  'snow',
  'windy',
] as const

export type WeatherCondition = (typeof WEATHER_CONDITIONS)[number]

const NAMED: readonly string[] = WEATHER_CONDITIONS

/** The condition a value names, lower-cased; anything the vocabulary does not
 *  name is drawn as `cloudy`. */
export function weatherCondition(value: unknown): WeatherCondition {
  const word = String(value ?? '').trim().toLowerCase()
  return (NAMED.includes(word) ? word : 'cloudy') as WeatherCondition
}

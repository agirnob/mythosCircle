/**
 * P1 portrait generation options (2026-09-15) — the vocabulary mirror
 * for the backend's app.media.service.portrait_options: style themes,
 * framing, and background on an image job. The backend owns the closed
 * vocabulary gate (a value off the list is a 422 at enqueue); this file
 * mirrors the lists + labels so the pickers can never emit an off-book
 * value, and maps the camelCase UI shape to the snake_case payload keys
 * the backend validates.
 */

export type PortraitStyle = 'photorealistic' | 'cartoonish' | 'illustration' | 'custom'
export type PortraitFraming = 'portrait' | 'headshot' | 'full_body'
export type PortraitBackground = 'scene' | 'plain' | 'dark' | 'transparent'

export interface PortraitOptions {
  style?: PortraitStyle
  framing?: PortraitFraming
  background?: PortraitBackground
  /** DM-owned free-text styling, only meaningful with style: 'custom'. */
  customStyle?: string
}

export const PORTRAIT_STYLES: readonly PortraitStyle[] = [
  'photorealistic',
  'cartoonish',
  'illustration',
  'custom',
]
export const PORTRAIT_FRAMINGS: readonly PortraitFraming[] = ['portrait', 'headshot', 'full_body']
export const PORTRAIT_BACKGROUNDS: readonly PortraitBackground[] = [
  'scene',
  'plain',
  'dark',
  'transparent',
]

export const PORTRAIT_STYLE_LABELS: Record<PortraitStyle, string> = {
  photorealistic: 'Photorealistic',
  cartoonish: 'Cartoonish',
  illustration: 'Illustration',
  custom: 'Custom',
}
export const PORTRAIT_FRAMING_LABELS: Record<PortraitFraming, string> = {
  portrait: 'Portrait (waist-up)',
  headshot: 'Headshot',
  full_body: 'Full body',
}
export const PORTRAIT_BACKGROUND_LABELS: Record<PortraitBackground, string> = {
  scene: 'Scenic',
  plain: 'Plain light',
  dark: 'Dark',
  transparent: 'Transparent (no background)',
}

/** The backend payload keys for the present options — only non-empty
 * values are emitted (an absent option is the spec-4.1 default). */
export function toPortraitPayload(options: PortraitOptions): Record<string, string> {
  const payload: Record<string, string> = {}
  if (options.style) payload['style'] = options.style
  if (options.framing) payload['framing'] = options.framing
  if (options.background) payload['background'] = options.background
  const custom = options.customStyle?.trim()
  if (custom) payload['custom_style'] = custom
  return payload
}

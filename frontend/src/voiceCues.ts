import type { VoiceFeature, VoiceReadings } from './protocol.ts'

interface Cue {
  feature: VoiceFeature
  name: string
  /** What a caption is tagged with when this is above or below usual. */
  higher: string
  lower: string
  /** Follows the measured value, and the change from usual, in the tooltip. */
  unit: string
  changeUnit: string
}

const CUES: Cue[] = [
  {
    feature: 'pitch',
    name: 'Pitch',
    higher: '▲ pitch',
    lower: '▼ pitch',
    unit: ' Hz',
    changeUnit: ' semitones',
  },
  {
    feature: 'speed',
    name: 'Speed',
    higher: '▲ faster',
    lower: '▼ slower',
    unit: ' syllables/s',
    changeUnit: '%',
  },
]

function tag(text: string, trend: string): HTMLSpanElement {
  const element = document.createElement('span')
  element.className = `cue cue-${trend}`
  element.textContent = text
  return element
}

/**
 * Tags saying how a caption was spoken, compared with the speaker's usual voice.
 * Only what changed gets a tag; hovering lists every measurement.
 */
export function voiceCues(name: string, voice: VoiceReadings): HTMLSpanElement | null {
  const tags: HTMLSpanElement[] = []
  const details: string[] = []
  let compared = false

  for (const cue of CUES) {
    const reading = voice[cue.feature]
    if (!reading) continue
    if (reading.change === null) {
      details.push(`${cue.name}: ${reading.value}${cue.unit}, still learning`)
      continue
    }
    compared = true
    const sign = reading.change > 0 ? '+' : ''
    details.push(
      `${cue.name}: ${reading.value}${cue.unit}, ${sign}${reading.change}${cue.changeUnit}`,
    )
    if (reading.trend === 'higher') tags.push(tag(cue.higher, 'higher'))
    if (reading.trend === 'lower') tags.push(tag(cue.lower, 'lower'))
  }

  if (details.length === 0) return null
  if (tags.length === 0) {
    tags.push(compared ? tag('• steady', 'steady') : tag('… learning voice', 'calibrating'))
  }

  const cues = document.createElement('span')
  cues.className = 'cues'
  cues.title = [`Compared with ${name}'s usual voice`, ...details].join('\n')
  for (const [index, element] of tags.entries()) {
    if (index > 0) cues.append(' ')
    cues.append(element)
  }
  return cues
}

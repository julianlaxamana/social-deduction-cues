import { audioContext } from './audio.ts'

const TARGET_SAMPLE_RATE = 16_000
const BATCH_SAMPLES = 2048

// Below this loudness counts as silence.
const SPEECH_LEVEL = 0.015
const PRE_ROLL_SECONDS = 0.3
const HANGOVER_SECONDS = 0.7
const MAX_UTTERANCE_SECONDS = 8
const SEND_INTERVAL_SECONDS = 0.5

const WORKLET_SOURCE = `
registerProcessor('speech-capture', class extends AudioWorkletProcessor {
  buffer = new Float32Array(${BATCH_SAMPLES})
  filled = 0

  process([input]) {
    const samples = input[0]
    if (!samples) return true
    for (const sample of samples) {
      this.buffer[this.filled++] = sample
      if (this.filled === this.buffer.length) {
        this.port.postMessage(this.buffer.slice())
        this.filled = 0
      }
    }
    return true
  }
})
`

export interface SpeechEvents {
  /** A piece of the current utterance, as 16 kHz 16-bit mono PCM. */
  onAudio: (pcm: ArrayBuffer) => void
  /** The player stopped talking. */
  onSpeechEnd: () => void
}

/**
 * Listens to the microphone and passes on only the parts with someone talking,
 * so that nothing leaves the browser during silence or while muted.
 */
export class SpeechCapture {
  enabled = true

  private readonly events: SpeechEvents
  private source: MediaStreamAudioSourceNode | null = null
  private node: AudioWorkletNode | null = null
  private inputRate = 48_000

  private resampleSum = 0
  private resampleCount = 0
  private resamplePosition = 0

  private preRoll: Int16Array[] = []
  private pending: Int16Array[] = []
  private speaking = false
  private silenceSeconds = 0
  private utteranceSeconds = 0

  constructor(events: SpeechEvents) {
    this.events = events
  }

  async start(stream: MediaStream): Promise<void> {
    const context = audioContext()
    const url = URL.createObjectURL(
      new Blob([WORKLET_SOURCE], { type: 'text/javascript' }),
    )
    await context.audioWorklet.addModule(url)
    URL.revokeObjectURL(url)

    this.inputRate = context.sampleRate
    this.source = context.createMediaStreamSource(stream)
    this.node = new AudioWorkletNode(context, 'speech-capture', {
      numberOfOutputs: 0,
    })
    this.node.port.onmessage = (event) => this.handleBatch(event.data)
    this.source.connect(this.node)
  }

  stop(): void {
    this.source?.disconnect()
    if (this.node) this.node.port.onmessage = null
    this.source = null
    this.node = null
    this.preRoll = []
    this.pending = []
    this.speaking = false
  }

  private handleBatch(batch: Float32Array): void {
    if (!this.enabled) {
      if (this.speaking) this.endUtterance()
      this.preRoll = []
      return
    }

    let energy = 0
    for (const sample of batch) energy += sample * sample
    const loud = Math.sqrt(energy / batch.length) > SPEECH_LEVEL

    const chunk = this.resample(batch)
    const seconds = chunk.length / TARGET_SAMPLE_RATE

    if (!this.speaking) {
      // Keep a little audio from before the speech, or first words get clipped.
      this.preRoll.push(chunk)
      while (this.preRoll.length * seconds > PRE_ROLL_SECONDS) this.preRoll.shift()
      if (loud) {
        this.speaking = true
        this.silenceSeconds = 0
        this.utteranceSeconds = this.preRoll.length * seconds
        this.pending = this.preRoll
        this.preRoll = []
      }
      return
    }

    this.pending.push(chunk)
    this.utteranceSeconds += seconds
    this.silenceSeconds = loud ? 0 : this.silenceSeconds + seconds

    if (
      this.silenceSeconds >= HANGOVER_SECONDS ||
      this.utteranceSeconds >= MAX_UTTERANCE_SECONDS
    ) {
      this.endUtterance()
    } else if (this.pending.length * seconds >= SEND_INTERVAL_SECONDS) {
      this.flush()
    }
  }

  private endUtterance(): void {
    this.flush()
    this.speaking = false
    this.events.onSpeechEnd()
  }

  private flush(): void {
    const length = this.pending.reduce((sum, chunk) => sum + chunk.length, 0)
    if (length === 0) return
    const pcm = new Int16Array(length)
    let offset = 0
    for (const chunk of this.pending) {
      pcm.set(chunk, offset)
      offset += chunk.length
    }
    this.pending = []
    this.events.onAudio(pcm.buffer)
  }

  /** Averages samples down to the target rate, carrying over between batches. */
  private resample(batch: Float32Array): Int16Array {
    const ratio = this.inputRate / TARGET_SAMPLE_RATE
    const output: number[] = []
    for (const sample of batch) {
      this.resampleSum += sample
      this.resampleCount++
      this.resamplePosition++
      if (this.resamplePosition >= ratio) {
        this.resamplePosition -= ratio
        const average = this.resampleSum / this.resampleCount
        output.push(Math.max(-1, Math.min(1, average)) * 32767)
        this.resampleSum = 0
        this.resampleCount = 0
      }
    }
    return Int16Array.from(output)
  }
}

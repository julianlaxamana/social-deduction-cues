let context: AudioContext | null = null

/** The shared audio context. Browsers keep it silent until `resumeAudio`. */
export function audioContext(): AudioContext {
  context ??= new AudioContext()
  return context
}

/** Call from a user gesture such as a click. */
export function resumeAudio(): void {
  void audioContext().resume()
}

function noiseBurst(
  duration: number,
  filterHz: number,
  volume: number,
  delay = 0,
): void {
  const ctx = audioContext()
  const samples = Math.floor(ctx.sampleRate * duration)
  const buffer = ctx.createBuffer(1, samples, ctx.sampleRate)
  const data = buffer.getChannelData(0)
  for (let i = 0; i < samples; i++) {
    data[i] = (Math.random() * 2 - 1) * (1 - i / samples) ** 3
  }
  const source = ctx.createBufferSource()
  source.buffer = buffer
  const filter = ctx.createBiquadFilter()
  filter.type = 'lowpass'
  filter.frequency.value = filterHz
  const gain = ctx.createGain()
  gain.gain.value = volume
  source.connect(filter).connect(gain).connect(ctx.destination)
  source.start(ctx.currentTime + delay)
}

export const sounds = {
  card: () => noiseBurst(0.07, 3500, 0.25),
  deal: () => {
    for (let i = 0; i < 5; i++) noiseBurst(0.06, 3500, 0.2, i * 0.09)
  },
  flip: () => noiseBurst(0.12, 2500, 0.3),
  liar: () => noiseBurst(0.25, 500, 0.7),
  spin: () => {
    for (let i = 0; i < 12; i++) noiseBurst(0.02, 6000, 0.3, i * i * 0.012)
  },
  click: () => noiseBurst(0.03, 7000, 0.9),
  bang: () => {
    noiseBurst(1.4, 900, 2.5)
    noiseBurst(0.15, 9000, 1.5)
  },
}

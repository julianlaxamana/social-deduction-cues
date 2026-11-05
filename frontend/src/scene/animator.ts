interface Tween {
  start: number
  duration: number
  update: (t: number) => void
  ease: (t: number) => number
  resolve: () => void
}

export const easeOut = (t: number) => 1 - (1 - t) ** 3
export const easeInOut = (t: number) =>
  t < 0.5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2

/** Runs tweens from the render loop. */
export class Animator {
  private tweens: Tween[] = []

  /** `update` receives eased progress from 0 to 1. Durations are in seconds. */
  add(
    duration: number,
    update: (t: number) => void,
    { delay = 0, ease = easeOut } = {},
  ): Promise<void> {
    return new Promise((resolve) => {
      this.tweens.push({
        start: performance.now() + delay * 1000,
        duration: duration * 1000,
        update,
        ease,
        resolve,
      })
    })
  }

  tick(now: number): void {
    this.tweens = this.tweens.filter((tween) => {
      if (now < tween.start) return true
      const t = Math.min(1, (now - tween.start) / tween.duration)
      tween.update(tween.ease(t))
      if (t === 1) tween.resolve()
      return t < 1
    })
  }

  clear(): void {
    this.tweens = []
  }
}

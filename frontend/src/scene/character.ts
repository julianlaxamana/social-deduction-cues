import * as THREE from 'three'
import { REVOLVER_CHAMBERS } from '../protocol.ts'
import { createCardBack } from './cards.ts'

interface Animal {
  fur: number
  coat: number
  addFeatures: (head: THREE.Group, fur: THREE.Material) => void
}

const PIVOT_Y = 0.6
const HEAD_Y = 0.72
const HEAD_RADIUS = 0.19
const SHOULDER_Y = 0.42
const SLUMP_ANGLE = 0.98

function mesh(
  geometry: THREE.BufferGeometry,
  material: THREE.Material,
  x = 0,
  y = 0,
  z = 0,
): THREE.Mesh {
  const result = new THREE.Mesh(geometry, material)
  result.position.set(x, y, z)
  result.castShadow = true
  return result
}

function standard(color: number, roughness = 0.85): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({ color, roughness })
}

/** Stretches a unit cylinder between two points. */
function placeLimb(limb: THREE.Mesh, from: THREE.Vector3, to: THREE.Vector3): void {
  const delta = to.clone().sub(from)
  limb.position.copy(from).addScaledVector(delta, 0.5)
  limb.scale.set(1, delta.length(), 1)
  limb.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), delta.normalize())
}

const ANIMALS: Animal[] = [
  {
    // Pig
    fur: 0xe39a9a,
    coat: 0x34466b,
    addFeatures(head, fur) {
      const snout = mesh(new THREE.CylinderGeometry(0.075, 0.085, 0.09, 16), fur, 0, -0.03, 0.2)
      snout.rotation.x = Math.PI / 2
      const nostril = standard(0x6d3a3a)
      const nostrilShape = new THREE.SphereGeometry(0.015, 8, 8)
      head.add(
        snout,
        mesh(nostrilShape, nostril, -0.03, -0.03, 0.245),
        mesh(nostrilShape, nostril, 0.03, -0.03, 0.245),
      )
      for (const side of [-1, 1]) {
        const ear = mesh(new THREE.ConeGeometry(0.06, 0.11, 10), fur, side * 0.13, 0.17, 0)
        ear.rotation.z = -side * 0.5
        head.add(ear)
      }
    },
  },
  {
    // Fox
    fur: 0xc9682a,
    coat: 0x2d4a33,
    addFeatures(head, fur) {
      const snout = mesh(new THREE.ConeGeometry(0.085, 0.2, 14), fur, 0, -0.04, 0.24)
      snout.rotation.x = Math.PI / 2
      head.add(snout, mesh(new THREE.SphereGeometry(0.024, 8, 8), standard(0x111111), 0, -0.04, 0.34))
      for (const side of [-1, 1]) {
        const ear = mesh(new THREE.ConeGeometry(0.065, 0.2, 4), fur, side * 0.12, 0.22, 0)
        ear.rotation.z = -side * 0.2
        head.add(ear)
      }
    },
  },
  {
    // Dog
    fur: 0x8d6a45,
    coat: 0x6b2f2f,
    addFeatures(head, fur) {
      const snout = mesh(new THREE.BoxGeometry(0.13, 0.1, 0.15), standard(0xc2a27c), 0, -0.05, 0.19)
      head.add(snout, mesh(new THREE.SphereGeometry(0.03, 8, 8), standard(0x111111), 0, -0.01, 0.27))
      for (const side of [-1, 1]) {
        const ear = mesh(new THREE.BoxGeometry(0.06, 0.2, 0.1), fur, side * 0.2, 0, -0.02)
        ear.rotation.z = side * 0.14
        head.add(ear)
      }
    },
  },
  {
    // Bull
    fur: 0x4a3a33,
    coat: 0x54503a,
    addFeatures(head, fur) {
      const snout = mesh(new THREE.BoxGeometry(0.17, 0.11, 0.12), standard(0x9c7f70), 0, -0.05, 0.18)
      const ring = mesh(new THREE.TorusGeometry(0.03, 0.006, 8, 16), standard(0xd8b24a, 0.3), 0, -0.1, 0.24)
      head.add(snout, ring)
      const horn = standard(0xddd3bb, 0.5)
      for (const side of [-1, 1]) {
        const hornMesh = mesh(new THREE.ConeGeometry(0.035, 0.2, 10), horn, side * 0.22, 0.11, 0)
        hornMesh.rotation.z = -side * 0.7
        head.add(hornMesh)
        head.add(mesh(new THREE.BoxGeometry(0.09, 0.05, 0.05), fur, side * 0.2, 0.02, -0.02))
      }
    },
  },
]

/** A player at the table, facing along +z. */
export class Character {
  readonly group = new THREE.Group()
  /** Everything that slumps forward on death. */
  readonly upper = new THREE.Group()
  /** Where the revolver goes, in `upper` coordinates, and what it points at. */
  readonly gunPosition = new THREE.Vector3(0.38, HEAD_Y, 0.04)
  readonly headCenter = new THREE.Vector3(0, HEAD_Y, 0)
  dead = false

  private readonly name: string
  private readonly head = new THREE.Group()
  private readonly mouth: THREE.Mesh
  private readonly fan = new THREE.Group()
  private readonly rightArm: THREE.Mesh
  private readonly rightHand: THREE.Mesh
  private readonly rightShoulder = new THREE.Vector3(0.25, SHOULDER_Y, 0)
  private readonly rightRest = new THREE.Vector3(0.14, 0.27, 0.42)
  private readonly eyeWhites: THREE.Mesh[] = []
  private readonly deadEyes: THREE.Mesh[] = []
  private readonly tag: THREE.Sprite
  private readonly tagContext: CanvasRenderingContext2D
  private readonly tagTexture: THREE.CanvasTexture
  private tagKey = ''
  private shots = 0
  private isTurn = false
  private talk = 0
  private readonly phase = Math.random() * 10

  constructor(name: string, seatIndex: number) {
    this.name = name
    const animal = ANIMALS[seatIndex % ANIMALS.length]
    const fur = standard(animal.fur)
    const coat = standard(animal.coat)

    this.upper.position.y = PIVOT_Y
    this.group.add(this.upper)

    const hips = mesh(new THREE.CylinderGeometry(0.2, 0.22, 0.5, 16), coat, 0, 0.3, -0.02)
    const chairBack = mesh(new THREE.BoxGeometry(0.5, 0.75, 0.05), standard(0x2a170c), 0, 0.82, -0.3)
    const chairSeat = mesh(new THREE.CylinderGeometry(0.26, 0.26, 0.05, 18), standard(0x2a170c), 0, 0.46, -0.05)
    this.group.add(hips, chairBack, chairSeat)

    const torso = mesh(new THREE.CapsuleGeometry(0.2, 0.26, 6, 16), coat, 0, 0.2, 0)
    const collar = mesh(new THREE.CylinderGeometry(0.09, 0.14, 0.09, 12), standard(0xd8d0bd), 0, 0.5, 0)
    this.upper.add(torso, collar)

    this.head.position.copy(this.headCenter)
    this.head.add(mesh(new THREE.SphereGeometry(HEAD_RADIUS, 24, 18), fur))
    animal.addFeatures(this.head, fur)
    this.upper.add(this.head)

    const white = new THREE.MeshStandardMaterial({ color: 0xf2eee0, roughness: 0.4 })
    const pupil = new THREE.MeshBasicMaterial({ color: 0x0c0a08 })
    for (const side of [-1, 1]) {
      const eye = mesh(new THREE.SphereGeometry(0.038, 12, 12), white, side * 0.08, 0.05, 0.155)
      eye.add(mesh(new THREE.SphereGeometry(0.019, 10, 10), pupil, 0, 0, 0.029))
      this.eyeWhites.push(eye)

      const cross = new THREE.Group()
      for (const angle of [Math.PI / 4, -Math.PI / 4]) {
        const bar = mesh(new THREE.BoxGeometry(0.075, 0.014, 0.01), pupil)
        bar.rotation.z = angle
        cross.add(bar)
      }
      const deadEye = new THREE.Mesh()
      deadEye.add(cross)
      deadEye.position.set(side * 0.08, 0.05, 0.185)
      deadEye.visible = false
      this.deadEyes.push(deadEye)
      this.head.add(eye, deadEye)
    }

    this.mouth = mesh(new THREE.BoxGeometry(0.1, 0.02, 0.03), new THREE.MeshBasicMaterial({ color: 0x1a0505 }), 0, -0.115, 0.15)
    this.head.add(this.mouth)

    const limb = new THREE.CylinderGeometry(0.055, 0.05, 1, 10)
    const handShape = new THREE.SphereGeometry(0.058, 12, 12)
    const leftArm = mesh(limb, coat)
    placeLimb(leftArm, new THREE.Vector3(-0.25, SHOULDER_Y, 0), new THREE.Vector3(-0.14, 0.27, 0.42))
    this.rightArm = mesh(limb, coat)
    this.rightHand = mesh(handShape, fur)
    this.upper.add(leftArm, mesh(handShape, fur, -0.14, 0.27, 0.42), this.rightArm, this.rightHand)
    this.setRightHand(this.rightRest)

    // Cards held up with their backs to the rest of the table.
    this.fan.position.set(0, 0.36, 0.44)
    this.fan.rotation.x = -0.2
    this.upper.add(this.fan)

    const canvas = document.createElement('canvas')
    canvas.width = 512
    canvas.height = 128
    this.tagContext = canvas.getContext('2d')!
    this.tagTexture = new THREE.CanvasTexture(canvas)
    this.tagTexture.colorSpace = THREE.SRGBColorSpace
    this.tag = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: this.tagTexture,
        transparent: true,
        depthTest: false,
        fog: false,
        // Stay the same size on screen, so far seats remain readable.
        sizeAttenuation: false,
      }),
    )
    this.tag.scale.set(0.32, 0.08, 1)
    this.tag.position.y = 1.72
    this.group.add(this.tag)
    this.drawTag(false)
  }

  setCardCount(count: number): void {
    if (this.fan.children.length === count) return
    this.fan.clear()
    for (let i = 0; i < count; i++) {
      const card = createCardBack()
      const offset = i - (count - 1) / 2
      card.position.set(offset * 0.05, -Math.abs(offset) * 0.008, -i * 0.003)
      card.rotation.z = -offset * 0.14
      // Turn the card's back towards the other players.
      card.rotation.y = Math.PI
      card.scale.setScalar(0.85)
      this.fan.add(card)
    }
  }

  setShots(shots: number): void {
    this.shots = shots
  }

  setTurn(isTurn: boolean): void {
    this.isTurn = isTurn
  }

  /** Moves the gun hand between resting (0) and at the temple (1). */
  raiseGunHand(amount: number): void {
    const grip = this.gunPosition.clone().add(new THREE.Vector3(0.02, -0.06, -0.02))
    this.setRightHand(this.rightRest.clone().lerp(grip, amount))
  }

  /** Slumps onto the table; 0 is upright and 1 is face-down. */
  setSlump(amount: number): void {
    this.upper.rotation.x = amount * SLUMP_ANGLE
  }

  markDead(): void {
    this.dead = true
    this.setCardCount(0)
    for (const eye of this.eyeWhites) eye.visible = false
    for (const eye of this.deadEyes) eye.visible = true
    this.group.traverse((part) => {
      if (part instanceof THREE.Mesh && part.material instanceof THREE.MeshStandardMaterial) {
        part.material.color.lerp(new THREE.Color(0x4a4a4a), 0.6)
      }
    })
    this.raiseGunHand(0)
  }

  /** Animates the head; `lookAt` is a world position, and `level` voice loudness. */
  update(time: number, level: number, lookAt: THREE.Vector3 | null): void {
    this.talk += (level - this.talk) * (level > this.talk ? 0.6 : 0.2)
    this.drawTag(this.talk > 0.12 && !this.dead)
    if (this.dead) {
      this.mouth.scale.y = 1
      return
    }

    this.mouth.scale.y = 1 + this.talk * 6

    let yaw = 0
    let pitch = 0
    if (lookAt) {
      const local = this.group.worldToLocal(lookAt.clone())
      const fromHead = local.sub(new THREE.Vector3(0, PIVOT_Y + HEAD_Y, 0))
      yaw = Math.atan2(fromHead.x, fromHead.z)
      pitch = -Math.atan2(fromHead.y, Math.hypot(fromHead.x, fromHead.z))
    }
    const t = time + this.phase
    const targetYaw = THREE.MathUtils.clamp(yaw, -1.1, 1.1) + Math.sin(t * 0.6) * 0.05
    const targetPitch =
      THREE.MathUtils.clamp(pitch, -0.3, 0.5) +
      Math.sin(t * 0.9) * 0.02 +
      Math.sin(t * 13) * this.talk * 0.06
    this.head.rotation.y += (targetYaw - this.head.rotation.y) * 0.08
    this.head.rotation.x += (targetPitch - this.head.rotation.x) * 0.08

    // Blink every few seconds.
    const blink = Math.sin(t * 1.7) > 0.992 ? 0.1 : 1
    for (const eye of this.eyeWhites) eye.scale.y = blink
    this.upper.position.y = PIVOT_Y + Math.sin(t * 1.2) * 0.004
  }

  dispose(): void {
    this.group.traverse((part) => {
      if (part instanceof THREE.Mesh) {
        part.geometry.dispose()
        const materials = Array.isArray(part.material) ? part.material : [part.material]
        for (const material of materials) material.dispose()
      }
    })
    this.tag.material.dispose()
    this.tagTexture.dispose()
  }

  private setRightHand(position: THREE.Vector3): void {
    placeLimb(this.rightArm, this.rightShoulder, position)
    this.rightHand.position.copy(position)
  }

  private drawTag(speaking: boolean): void {
    const key = `${this.shots}|${this.dead}|${this.isTurn}|${speaking}`
    if (key === this.tagKey) return
    this.tagKey = key

    const ctx = this.tagContext
    const { width, height } = ctx.canvas
    ctx.clearRect(0, 0, width, height)
    ctx.fillStyle = 'rgba(12, 8, 5, 0.72)'
    ctx.beginPath()
    ctx.roundRect(4, 4, width - 8, height - 8, 22)
    ctx.fill()
    ctx.lineWidth = 6
    ctx.strokeStyle = speaking ? '#6fe08a' : this.isTurn ? '#f0b64a' : 'rgba(255,255,255,0.15)'
    ctx.stroke()

    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.font = 'bold 40px Georgia, serif'
    ctx.fillStyle = this.dead ? '#8a8078' : '#f4e9d4'
    ctx.fillText(this.name.slice(0, 18), width / 2, 42, width - 40)

    // One pip per chamber: filled once the trigger has been pulled on it.
    for (let i = 0; i < REVOLVER_CHAMBERS; i++) {
      const x = width / 2 + (i - (REVOLVER_CHAMBERS - 1) / 2) * 34
      ctx.beginPath()
      ctx.arc(x, 90, 11, 0, Math.PI * 2)
      if (i < this.shots) {
        ctx.fillStyle = this.dead && i === this.shots - 1 ? '#d8352f' : '#d8c8a0'
        ctx.fill()
      } else {
        ctx.lineWidth = 3
        ctx.strokeStyle = '#d8c8a0'
        ctx.stroke()
      }
    }
    this.tagTexture.needsUpdate = true
  }
}

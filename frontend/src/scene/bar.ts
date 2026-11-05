import * as THREE from 'three'
import { RANK_NAMES, type Rank } from '../protocol.ts'

export const TABLE_RADIUS = 1
export const TABLE_TOP_Y = 0.78

const ROOM_WIDTH = 8
const ROOM_DEPTH = 8
const ROOM_HEIGHT = 3.2
const LAMP_INTENSITY = 11

function plankTexture(base: string, repeat: number): THREE.CanvasTexture {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 512
  const ctx = canvas.getContext('2d')!
  ctx.fillStyle = base
  ctx.fillRect(0, 0, 512, 512)
  for (let i = 0; i < 2500; i++) {
    ctx.fillStyle = `rgba(0,0,0,${Math.random() * 0.08})`
    ctx.fillRect(Math.random() * 512, Math.random() * 512, 2, 20 + Math.random() * 90)
  }
  ctx.fillStyle = 'rgba(0,0,0,0.55)'
  for (let x = 0; x < 512; x += 64) ctx.fillRect(x, 0, 3, 512)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping
  texture.repeat.set(repeat, repeat)
  return texture
}

function feltTexture(): THREE.CanvasTexture {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 512
  const ctx = canvas.getContext('2d')!
  ctx.fillStyle = '#1d4a33'
  ctx.fillRect(0, 0, 512, 512)
  for (let i = 0; i < 9000; i++) {
    ctx.fillStyle = `rgba(${Math.random() < 0.5 ? '0,0,0' : '255,255,255'},0.04)`
    ctx.fillRect(Math.random() * 512, Math.random() * 512, 2, 2)
  }
  // Old stains and burns.
  for (let i = 0; i < 9; i++) {
    ctx.fillStyle = 'rgba(20,10,0,0.14)'
    ctx.beginPath()
    ctx.arc(Math.random() * 512, Math.random() * 512, 8 + Math.random() * 30, 0, 7)
    ctx.fill()
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  return texture
}

function buildRoom(scene: THREE.Scene): void {
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(ROOM_WIDTH, ROOM_DEPTH),
    new THREE.MeshStandardMaterial({
      map: plankTexture('#3a2415', 3),
      roughness: 0.9,
    }),
  )
  floor.rotation.x = -Math.PI / 2
  floor.receiveShadow = true
  scene.add(floor)

  const walls = new THREE.Mesh(
    new THREE.BoxGeometry(ROOM_WIDTH, ROOM_HEIGHT, ROOM_DEPTH),
    new THREE.MeshStandardMaterial({
      map: plankTexture('#33201a', 2),
      roughness: 1,
      side: THREE.BackSide,
    }),
  )
  walls.position.y = ROOM_HEIGHT / 2
  scene.add(walls)
}

function buildTable(scene: THREE.Scene): void {
  const wood = new THREE.MeshStandardMaterial({ color: 0x3d2312, roughness: 0.6 })
  const rim = new THREE.Mesh(
    new THREE.CylinderGeometry(TABLE_RADIUS, TABLE_RADIUS * 0.97, 0.07, 128),
    wood,
  )
  rim.position.y = TABLE_TOP_Y - 0.035
  rim.castShadow = rim.receiveShadow = true
  scene.add(rim)

  const felt = new THREE.Mesh(
    new THREE.CircleGeometry(TABLE_RADIUS * 0.92, 128),
    new THREE.MeshStandardMaterial({ map: feltTexture(), roughness: 1 }),
  )
  felt.rotation.x = -Math.PI / 2
  felt.position.y = TABLE_TOP_Y + 0.001
  felt.receiveShadow = true
  scene.add(felt)

  const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.12, TABLE_TOP_Y, 16), wood)
  leg.position.y = TABLE_TOP_Y / 2
  scene.add(leg)
  const foot = new THREE.Mesh(new THREE.CylinderGeometry(0.42, 0.48, 0.05, 24), wood)
  foot.position.y = 0.025
  scene.add(foot)
}

function buildBackBar(scene: THREE.Scene): void {
  const z = -ROOM_DEPTH / 2 + 0.2
  const wood = new THREE.MeshStandardMaterial({ color: 0x24140b, roughness: 0.8 })
  const colors = [0x2f6b3c, 0x7a3b12, 0x8a1d1d, 0xb08a2e, 0x2c4a7a, 0xd9d2b8]

  for (const y of [1.05, 1.55]) {
    const shelf = new THREE.Mesh(new THREE.BoxGeometry(5.5, 0.05, 0.3), wood)
    shelf.position.set(0, y, z)
    scene.add(shelf)
    for (let x = -2.5; x <= 2.5; x += 0.22 + Math.random() * 0.2) {
      const height = 0.2 + Math.random() * 0.12
      const bottle = new THREE.Mesh(
        new THREE.CylinderGeometry(0.03, 0.045, height, 10),
        new THREE.MeshStandardMaterial({
          color: colors[Math.floor(Math.random() * colors.length)],
          roughness: 0.2,
          metalness: 0.2,
          emissive: 0x220e04,
        }),
      )
      bottle.position.set(x, y + 0.025 + height / 2, z)
      const neck = new THREE.Mesh(
        new THREE.CylinderGeometry(0.012, 0.02, 0.08, 8),
        bottle.material,
      )
      neck.position.y = height / 2 + 0.04
      bottle.add(neck)
      scene.add(bottle)
    }
  }

  const counter = new THREE.Mesh(new THREE.BoxGeometry(5.8, 0.9, 0.5), wood)
  counter.position.set(0, 0.45, z + 0.05)
  scene.add(counter)

  // A dim glow behind the bottles, so the shelves read in the dark.
  const glow = new THREE.PointLight(0xff7a30, 3, 5)
  glow.position.set(0, 1.5, z + 0.6)
  scene.add(glow)
}

/** Dust drifting through the lamp's light. */
function buildDust(scene: THREE.Scene): THREE.Points {
  const positions = new Float32Array(160 * 3)
  for (let i = 0; i < positions.length; i += 3) {
    const angle = Math.random() * Math.PI * 2
    const radius = Math.sqrt(Math.random()) * 1.3
    positions[i] = Math.cos(angle) * radius
    positions[i + 1] = 0.8 + Math.random() * 1.4
    positions[i + 2] = Math.sin(angle) * radius
  }
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  const dust = new THREE.Points(
    geometry,
    new THREE.PointsMaterial({
      color: 0xffd9a0,
      size: 0.0035,
      transparent: true,
      opacity: 0.35,
      depthWrite: false,
    }),
  )
  scene.add(dust)
  return dust
}

/** The room and everything in it that is not a player or a card. */
export class Bar {
  private readonly lamp: THREE.SpotLight
  private readonly lampBulb: THREE.Mesh
  private readonly lampGroup = new THREE.Group()
  private readonly dust: THREE.Points
  private readonly sign: THREE.Mesh
  private readonly signContext: CanvasRenderingContext2D
  private readonly signTexture: THREE.CanvasTexture

  constructor(scene: THREE.Scene) {
    scene.background = new THREE.Color(0x070504)
    scene.fog = new THREE.Fog(0x0b0705, 3.5, 9)
    buildRoom(scene)
    buildTable(scene)
    buildBackBar(scene)
    this.dust = buildDust(scene)

    scene.add(new THREE.HemisphereLight(0xffb070, 0x2a140c, 1.5))

    const lampY = 2.25
    const shade = new THREE.Mesh(
      new THREE.ConeGeometry(0.3, 0.2, 24, 1, true),
      new THREE.MeshStandardMaterial({
        color: 0x2c3b2a,
        side: THREE.DoubleSide,
        metalness: 0.5,
      }),
    )
    this.lampBulb = new THREE.Mesh(
      new THREE.SphereGeometry(0.05, 12, 12),
      new THREE.MeshBasicMaterial({ color: 0xffe2a8 }),
    )
    this.lampBulb.position.y = -0.07
    const cord = new THREE.Mesh(
      new THREE.CylinderGeometry(0.006, 0.006, ROOM_HEIGHT - lampY, 6),
      new THREE.MeshBasicMaterial({ color: 0x111111 }),
    )
    cord.position.y = (ROOM_HEIGHT - lampY) / 2
    this.lampGroup.add(shade, this.lampBulb, cord)
    this.lampGroup.position.y = lampY
    scene.add(this.lampGroup)

    this.lamp = new THREE.SpotLight(0xffd39a, LAMP_INTENSITY, 7, 1.2, 0.5, 1.6)
    this.lamp.position.set(0, lampY - 0.05, 0)
    this.lamp.target.position.set(0, 0, 0)
    this.lamp.castShadow = true
    this.lamp.shadow.mapSize.set(1024, 1024)
    this.lamp.shadow.bias = -0.0008
    scene.add(this.lamp, this.lamp.target)

    const canvas = document.createElement('canvas')
    canvas.width = 1024
    canvas.height = 256
    this.signContext = canvas.getContext('2d')!
    this.signTexture = new THREE.CanvasTexture(canvas)
    this.signTexture.colorSpace = THREE.SRGBColorSpace
    this.sign = new THREE.Mesh(
      new THREE.PlaneGeometry(2.4, 0.6),
      new THREE.MeshBasicMaterial({
        map: this.signTexture,
        transparent: true,
        fog: false,
      }),
    )
    this.sign.position.set(0, 2.35, -ROOM_DEPTH / 2 + 0.05)
    scene.add(this.sign)
    this.setTableRank(null)
  }

  /** The neon sign on the back wall announces the round's table card. */
  setTableRank(rank: Rank | null): void {
    const ctx = this.signContext
    const { width, height } = ctx.canvas
    ctx.clearRect(0, 0, width, height)
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.font = 'italic bold 118px Georgia, serif'
    ctx.shadowBlur = 36
    ctx.shadowColor = rank ? '#ff2a4a' : '#ff9a2a'
    ctx.fillStyle = rank ? '#ffd5da' : '#ffe6c8'
    const text = rank ? `${RANK_NAMES[rank]}'s Table` : "Liar's Table"
    // Drawn twice to thicken the glow.
    ctx.fillText(text, width / 2, height / 2)
    ctx.fillText(text, width / 2, height / 2)
    this.signTexture.needsUpdate = true
  }

  update(time: number): void {
    const flicker = Math.sin(time * 37) * Math.sin(time * 11.3) > 0.93 ? 0.72 : 1
    this.lamp.intensity = LAMP_INTENSITY * flicker
    this.lampGroup.rotation.z = Math.sin(time * 0.7) * 0.012
    this.lampGroup.rotation.x = Math.cos(time * 0.5) * 0.012
    this.dust.rotation.y = time * 0.015
    this.dust.position.y = Math.sin(time * 0.2) * 0.04
  }
}

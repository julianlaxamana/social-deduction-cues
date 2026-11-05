import * as THREE from 'three'

const MUZZLE_Z = 0.19

/** A revolver with its barrel along +z. */
export class Revolver {
  readonly group = new THREE.Group()
  private readonly drum: THREE.Mesh
  private readonly flash: THREE.Sprite
  private readonly flashLight: THREE.PointLight

  constructor() {
    const steel = new THREE.MeshStandardMaterial({
      // Fully metallic surfaces render black without an environment to reflect.
      color: 0x8a8a92,
      metalness: 0.45,
      roughness: 0.35,
    })
    const grip = new THREE.MeshStandardMaterial({ color: 0x3b1f10, roughness: 0.7 })

    const barrel = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, 0.15, 12), steel)
    barrel.rotation.x = Math.PI / 2
    barrel.position.set(0, 0.012, 0.11)

    this.drum = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.05, 6), steel)
    this.drum.rotation.x = Math.PI / 2

    const frame = new THREE.Mesh(new THREE.BoxGeometry(0.022, 0.05, 0.1), steel)
    frame.position.set(0, -0.002, -0.015)

    const handle = new THREE.Mesh(new THREE.BoxGeometry(0.026, 0.1, 0.038), grip)
    handle.position.set(0, -0.06, -0.06)
    handle.rotation.x = -0.35

    this.group.add(barrel, this.drum, frame, handle)
    this.group.traverse((part) => (part.castShadow = true))

    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 128
    const ctx = canvas.getContext('2d')!
    const gradient = ctx.createRadialGradient(64, 64, 2, 64, 64, 64)
    gradient.addColorStop(0, 'rgba(255,255,230,1)')
    gradient.addColorStop(0.3, 'rgba(255,190,70,0.9)')
    gradient.addColorStop(1, 'rgba(255,90,0,0)')
    ctx.fillStyle = gradient
    ctx.fillRect(0, 0, 128, 128)
    this.flash = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: new THREE.CanvasTexture(canvas),
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    )
    this.flash.scale.setScalar(0.35)
    this.flash.position.z = MUZZLE_Z
    this.flash.visible = false
    this.flashLight = new THREE.PointLight(0xffb060, 0, 6)
    this.flashLight.position.z = MUZZLE_Z
    // Keeps the gun readable wherever it is held, even outside the lamp's cone.
    const glint = new THREE.PointLight(0xffd8a8, 0.25, 1.2)
    glint.position.set(0.05, 0.12, 0.1)
    this.group.add(this.flash, this.flashLight, glint)
    this.group.scale.setScalar(1.3)
    this.group.visible = false
  }

  /** Places the gun in its parent's space, with the barrel on the target. */
  aim(parent: THREE.Object3D, position: THREE.Vector3, target: THREE.Vector3): void {
    parent.add(this.group)
    this.group.position.copy(position)
    this.group.quaternion.setFromUnitVectors(
      new THREE.Vector3(0, 0, 1),
      target.clone().sub(position).normalize(),
    )
    this.group.visible = true
  }

  spinDrum(angle: number): void {
    this.drum.rotation.y = angle
  }

  setFlash(strength: number): void {
    this.flash.visible = strength > 0
    this.flash.material.opacity = strength
    this.flashLight.intensity = strength * 30
  }

  hide(): void {
    this.setFlash(0)
    this.group.visible = false
  }
}

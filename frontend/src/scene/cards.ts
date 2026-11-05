import * as THREE from 'three'
import type { Rank } from '../protocol.ts'

export const CARD_WIDTH = 0.14
export const CARD_HEIGHT = 0.2

const geometry = new THREE.BoxGeometry(CARD_WIDTH, CARD_HEIGHT, 0.002)
const edgeMaterial = new THREE.MeshStandardMaterial({ color: 0xd8cfb8 })
const FRONT_FACE = 4

const RANK_STYLE: Record<Rank, { label: string; suit: string; color: string }> = {
  Q: { label: 'Q', suit: '♥', color: '#a11d1d' },
  K: { label: 'K', suit: '♠', color: '#1c1c22' },
  A: { label: 'A', suit: '♦', color: '#a11d1d' },
  J: { label: '★', suit: 'JOKER', color: '#6a2a9c' },
}

function canvasTexture(
  width: number,
  height: number,
  draw: (ctx: CanvasRenderingContext2D) => void,
): THREE.CanvasTexture {
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  draw(canvas.getContext('2d')!)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.anisotropy = 8
  return texture
}

function drawFace(ctx: CanvasRenderingContext2D, rank: Rank): void {
  const { label, suit, color } = RANK_STYLE[rank]
  const { width: w, height: h } = ctx.canvas
  ctx.fillStyle = '#efe6cf'
  ctx.fillRect(0, 0, w, h)
  ctx.strokeStyle = color
  ctx.lineWidth = 6
  ctx.strokeRect(14, 14, w - 28, h - 28)

  ctx.fillStyle = color
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.font = 'bold 170px Georgia, serif'
  ctx.fillText(label, w / 2, h / 2 - 20)
  ctx.font = rank === 'J' ? 'bold 44px Georgia, serif' : '90px Georgia, serif'
  ctx.fillText(suit, w / 2, h / 2 + 105)
  ctx.font = 'bold 48px Georgia, serif'
  ctx.fillText(label, 50, 56)
  ctx.save()
  ctx.translate(w - 50, h - 56)
  ctx.rotate(Math.PI)
  ctx.fillText(label, 0, 0)
  ctx.restore()
}

function drawBack(ctx: CanvasRenderingContext2D): void {
  const { width: w, height: h } = ctx.canvas
  ctx.fillStyle = '#5c1414'
  ctx.fillRect(0, 0, w, h)
  ctx.strokeStyle = '#d9b36a'
  ctx.lineWidth = 6
  ctx.strokeRect(16, 16, w - 32, h - 32)
  ctx.lineWidth = 2
  for (let i = -h; i < w + h; i += 28) {
    ctx.beginPath()
    ctx.moveTo(i, 28)
    ctx.lineTo(i + h, h - 28)
    ctx.moveTo(i + h, 28)
    ctx.lineTo(i, h - 28)
    ctx.stroke()
  }
}

const faceTextures = new Map<Rank, THREE.Texture>()
function faceTexture(rank: Rank): THREE.Texture {
  let texture = faceTextures.get(rank)
  if (!texture) {
    texture = canvasTexture(360, 512, (ctx) => drawFace(ctx, rank))
    faceTextures.set(rank, texture)
  }
  return texture
}

let backMaterial: THREE.MeshStandardMaterial | null = null
function sharedBackMaterial(): THREE.MeshStandardMaterial {
  backMaterial ??= new THREE.MeshStandardMaterial({
    map: canvasTexture(360, 512, drawBack),
    roughness: 0.7,
  })
  return backMaterial
}

/** A card whose face points along +z. Without a rank, its face is blank. */
export function createCard(rank: Rank | null = null): THREE.Mesh {
  const front = new THREE.MeshBasicMaterial({ color: 0xffffff })
  const mesh = new THREE.Mesh(geometry, [
    edgeMaterial,
    edgeMaterial,
    edgeMaterial,
    edgeMaterial,
    front,
    sharedBackMaterial(),
  ])
  mesh.rotation.order = 'YXZ'
  mesh.castShadow = true
  if (rank) setCardFace(mesh, rank)
  return mesh
}

export function setCardFace(card: THREE.Mesh, rank: Rank): void {
  const materials = card.material as THREE.Material[]
  const front = materials[FRONT_FACE] as THREE.MeshBasicMaterial
  front.map = faceTexture(rank)
  front.needsUpdate = true
}

export function tintCardFace(card: THREE.Mesh, color: number): void {
  const materials = card.material as THREE.Material[]
  ;(materials[FRONT_FACE] as THREE.MeshBasicMaterial).color.set(color)
}

/** Used by anything that shows card backs only. */
export function createCardBack(): THREE.Mesh {
  return new THREE.Mesh(geometry, sharedBackMaterial())
}

/** Frees the material unique to this card. */
export function disposeCard(card: THREE.Mesh): void {
  const materials = card.material as THREE.Material[]
  materials[FRONT_FACE]?.dispose()
}

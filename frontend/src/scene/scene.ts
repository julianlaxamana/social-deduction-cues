import * as THREE from 'three'
import { sounds } from '../audio.ts'
import type { Card, GameView, PeerInfo } from '../protocol.ts'
import { Animator, easeInOut } from './animator.ts'
import { Bar, TABLE_TOP_Y } from './bar.ts'
import {
  CARD_WIDTH,
  createCard,
  disposeCard,
  setCardFace,
  tintCardFace,
} from './cards.ts'
import { Character } from './character.ts'
import { Revolver } from './revolver.ts'

const SEAT_RADIUS = 1.45
const CAMERA_POSITION = new THREE.Vector3(0, 1.42, 1.98)
const CAMERA_TARGET = new THREE.Vector3(0, 0.98, 0)
const DEAD_CAMERA_POSITION = new THREE.Vector3(0.12, 1.02, 1.55)
const DEAD_SLUMP_SECONDS = 2.2
const LOOK_YAW = 0.55
const LOOK_PITCH = 0.22
const HAND_SPACING = 0.118
const HAND_Y = -0.3
const HAND_Z = -0.72
const CARD_LIFT = 0.055
const FACE_DOWN = Math.PI / 2
const PILE_Y = TABLE_TOP_Y + 0.006

/** Where the viewer's own gun sits, in camera space. */
const SELF_GUN_POSITION = new THREE.Vector3(0.27, -0.02, -0.5)
/** Turned across the view, so you see the gun's profile rather than its muzzle. */
const SELF_GUN_TARGET = new THREE.Vector3(-0.1, 0.09, -0.3)

interface HandCard {
  card: Card
  mesh: THREE.Mesh
  lift: number
}

export interface SceneEvents {
  /** Which cards are selected changed. */
  onSelectionChanged: () => void
  /** How loudly another player is talking. */
  voiceLevel: (playerId: string) => number
}

/** The 3D table. Everything it shows comes from the server's state via `sync`. */
export class BarScene {
  private readonly events: SceneEvents
  private readonly renderer: THREE.WebGLRenderer
  private readonly scene = new THREE.Scene()
  private readonly camera = new THREE.PerspectiveCamera(60, 1, 0.05, 30)
  private readonly animator = new Animator()
  private readonly bar: Bar
  private readonly revolver = new Revolver()
  private readonly turnMarker: THREE.Mesh
  private readonly raycaster = new THREE.Raycaster()
  private readonly pointer = new THREE.Vector2(0, 0)
  private readonly look = new THREE.Vector2(0, 0)

  private readonly characters = new Map<string, Character>()
  private readonly hand: HandCard[] = []
  private readonly selected = new Set<number>()
  private pile: THREE.Mesh[] = []
  private lastPlayCards: THREE.Mesh[] = []
  private selfId = ''
  private seatKey = ''
  private previous: GameView | null = null
  private hovered: HandCard | null = null
  private shake = 0
  private cameraDeath = 0

  constructor(canvas: HTMLCanvasElement, events: SceneEvents) {
    this.events = events
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true })
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2))
    this.renderer.shadowMap.enabled = true
    this.renderer.shadowMap.type = THREE.PCFShadowMap
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping
    this.renderer.toneMappingExposure = 1.15

    this.bar = new Bar(this.scene)
    this.scene.add(this.camera)

    this.turnMarker = new THREE.Mesh(
      new THREE.TorusGeometry(0.12, 0.006, 8, 32),
      new THREE.MeshBasicMaterial({ color: 0xf0b64a }),
    )
    this.turnMarker.rotation.x = Math.PI / 2
    this.turnMarker.position.y = TABLE_TOP_Y + 0.005
    this.turnMarker.visible = false
    this.scene.add(this.turnMarker)

    addEventListener('resize', () => this.resize())
    canvas.addEventListener('pointermove', (event) => this.movePointer(event))
    canvas.addEventListener('pointerdown', (event) => {
      this.movePointer(event)
      const card = this.cardUnderPointer()
      if (card) this.toggleCard(this.hand.indexOf(card))
    })
    this.resize()
    this.renderer.setAnimationLoop(() => this.render())
  }

  /** Brings the scene up to date with the server's state. */
  sync(selfId: string, players: PeerInfo[], game: GameView | null): void {
    this.selfId = selfId
    this.syncSeats(players, game)

    if (!game) {
      this.resetTable()
      this.previous = null
      this.bar.setTableRank(null)
      this.turnMarker.visible = false
      this.syncHand([])
      return
    }

    const previous = this.previous
    if (game.round !== previous?.round) {
      this.resetTable()
      this.bar.setTableRank(game.tableRank)
      sounds.deal()
    }

    this.syncHand(game.hand)
    for (const player of game.players) {
      const character = this.characters.get(player.id)
      if (!character) continue
      character.setCardCount(player.cards)
      character.setShots(player.shots)
      character.setTurn(player.id === game.currentPlayerId)
    }
    this.syncTurnMarker(game)

    if (game.lastPlay && game.lastPlay.seq !== previous?.lastPlay?.seq) {
      this.throwCards(game.lastPlay.playerId, game.lastPlay.count)
    }
    if (game.phase !== previous?.phase) {
      if (game.phase === 'reveal') this.showReveal(game)
      if (game.phase === 'roulette') this.raiseGun(game.reveal!.loserId)
      if (game.phase === 'shot') this.fireGun(game.shot!)
    }
    // Players who left the room forfeit without any gunfire.
    for (const player of game.players) {
      const character = this.characters.get(player.id)
      if (!player.alive && character && !character.dead && game.shot?.playerId !== player.id) {
        character.markDead()
        this.animator.add(0.6, (t) => character.setSlump(t))
      }
    }
    this.previous = game
  }

  /** Returns to an empty room after leaving. */
  clear(): void {
    this.sync('', [], null)
    this.setCameraDeath(0)
  }

  selectedCardIds(): number[] {
    return this.hand.filter(({ card }) => this.selected.has(card.id)).map(({ card }) => card.id)
  }

  /** Selects or deselects a card by its position in the hand. */
  toggleCard(index: number): void {
    const entry = this.hand[index]
    if (!entry) return
    if (this.selected.has(entry.card.id)) this.selected.delete(entry.card.id)
    else this.selected.add(entry.card.id)
    sounds.card()
    this.events.onSelectionChanged()
  }

  private resize(): void {
    const width = innerWidth
    const height = innerHeight
    this.renderer.setSize(width, height, false)
    this.camera.aspect = width / height
    // Keep the table in view on narrow screens.
    this.camera.fov = width / height < 1 ? 85 : 60
    this.camera.updateProjectionMatrix()
  }

  private movePointer(event: PointerEvent): void {
    this.pointer.set(
      (event.clientX / innerWidth) * 2 - 1,
      -(event.clientY / innerHeight) * 2 + 1,
    )
  }

  private cardUnderPointer(): HandCard | null {
    this.raycaster.setFromCamera(this.pointer, this.camera)
    const hits = this.raycaster.intersectObjects(this.hand.map(({ mesh }) => mesh))
    return this.hand.find(({ mesh }) => mesh === hits[0]?.object) ?? null
  }

  private syncSeats(players: PeerInfo[], game: GameView | null): void {
    const seated = game?.players ?? players
    const ids = seated.map(({ id }) => id)
    // A rematch needs fresh characters, since the dead stay dead until then.
    const key = `${game ? 'game' : 'lobby'}:${this.selfId}:${ids.join()}`
    if (key === this.seatKey) return
    const keepCharacters = this.seatKey.startsWith('lobby') && key.startsWith('game') && this.seatKey.slice(6) === key.slice(5)
    this.seatKey = key
    if (keepCharacters) return

    this.revolver.hide()
    this.scene.add(this.revolver.group)
    for (const character of this.characters.values()) {
      this.scene.remove(character.group)
      character.dispose()
    }
    this.characters.clear()
    this.setCameraDeath(0)

    const selfIndex = Math.max(0, ids.indexOf(this.selfId))
    seated.forEach((player, index) => {
      if (player.id === this.selfId) return
      const angle = (((index - selfIndex + ids.length) % ids.length) / ids.length) * Math.PI * 2
      const character = new Character(player.name, index)
      character.group.position.set(Math.sin(angle) * SEAT_RADIUS, 0, Math.cos(angle) * SEAT_RADIUS)
      character.group.lookAt(0, 0, 0)
      this.scene.add(character.group)
      this.characters.set(player.id, character)
    })
  }

  private syncHand(cards: Card[]): void {
    const unchanged =
      cards.length === this.hand.length &&
      cards.every((card, index) => this.hand[index].card.id === card.id)
    if (unchanged) return

    for (const { mesh } of this.hand) {
      this.camera.remove(mesh)
      disposeCard(mesh)
    }
    this.hand.length = 0
    this.hovered = null
    for (const card of cards) {
      const mesh = createCard(card.rank)
      mesh.castShadow = false
      this.camera.add(mesh)
      this.hand.push({ card, mesh, lift: 0 })
    }
    for (const id of [...this.selected]) {
      if (!cards.some((card) => card.id === id)) this.selected.delete(id)
    }
    this.events.onSelectionChanged()
  }

  private syncTurnMarker(game: GameView): void {
    const id = game.currentPlayerId
    this.turnMarker.visible = id !== null
    if (id === null) return
    const seat = this.seatPosition(id)
    this.turnMarker.position.x = seat.x * 0.5
    this.turnMarker.position.z = seat.z * 0.5
  }

  private seatPosition(playerId: string): THREE.Vector3 {
    const character = this.characters.get(playerId)
    return character
      ? character.group.position.clone()
      : new THREE.Vector3(0, 0, SEAT_RADIUS)
  }

  private resetTable(): void {
    for (const card of [...this.pile, ...this.lastPlayCards]) {
      this.scene.remove(card)
      disposeCard(card)
    }
    this.pile = []
    this.lastPlayCards = []
    this.revolver.hide()
  }

  private throwCards(playerId: string, count: number): void {
    sounds.card()
    this.pile.push(...this.lastPlayCards)
    this.lastPlayCards = []
    const seat = this.seatPosition(playerId)
    for (let i = 0; i < count; i++) {
      const card = createCard()
      const from = new THREE.Vector3(seat.x * 0.75, 1.05, seat.z * 0.75)
      const to = new THREE.Vector3(
        (Math.random() - 0.5) * 0.22,
        PILE_Y + (this.pile.length + i) * 0.003,
        (Math.random() - 0.5) * 0.22,
      )
      card.position.copy(from)
      card.rotation.x = FACE_DOWN
      card.rotation.y = Math.random() * Math.PI
      this.scene.add(card)
      this.lastPlayCards.push(card)
      this.animator.add(
        0.4,
        (t) => {
          card.position.lerpVectors(from, to, t)
          card.position.y += Math.sin(t * Math.PI) * 0.12
        },
        { delay: i * 0.07 },
      )
    }
  }

  /** Lifts the accused player's cards towards the viewer and flips them. */
  private showReveal(game: GameView): void {
    const { ranks } = game.reveal!
    sounds.liar()
    this.lastPlayCards.forEach((card, index) => {
      const rank = ranks[index]
      setCardFace(card, rank)
      const honest = rank === game.tableRank || rank === 'J'
      const from = card.position.clone()
      const fromYaw = card.rotation.y
      const to = new THREE.Vector3(
        (index - (ranks.length - 1) / 2) * (CARD_WIDTH * 1.9),
        1.08,
        0.55,
      )
      this.animator.add(
        0.7,
        (t) => {
          card.position.lerpVectors(from, to, t)
          card.rotation.y = fromYaw * (1 - t)
          card.rotation.x = FACE_DOWN + (-0.55 - FACE_DOWN) * t
          card.scale.setScalar(1 + t * 0.7)
        },
        { delay: 0.25 + index * 0.18, ease: easeInOut },
      ).then(() => {
        sounds.flip()
        // Once everyone has seen the cards, mark the ones that were a lie.
        if (!honest) tintCardFace(card, 0xff8f8f)
      })
    })
  }

  /** Clears the revealed cards out of the way of the player about to shoot. */
  private layDownRevealedCards(): void {
    this.lastPlayCards.forEach((card, index) => {
      const from = card.position.clone()
      const fromPitch = card.rotation.x
      const to = new THREE.Vector3(
        (index - (this.lastPlayCards.length - 1) / 2) * (CARD_WIDTH * 1.15),
        PILE_Y + 0.02,
        0.32,
      )
      this.animator.add(0.5, (t) => {
        card.position.lerpVectors(from, to, t)
        card.rotation.x = fromPitch + (-Math.PI / 2 - fromPitch) * t
        card.scale.setScalar(1.7 - t * 0.7)
      })
    })
  }

  private raiseGun(playerId: string): void {
    sounds.spin()
    this.layDownRevealedCards()
    const character = this.characters.get(playerId)
    if (character) {
      this.revolver.aim(character.upper, character.gunPosition, character.headCenter)
      this.animator.add(0.7, (t) => character.raiseGunHand(t))
    } else {
      this.revolver.aim(this.camera, SELF_GUN_POSITION, SELF_GUN_TARGET)
      const position = SELF_GUN_POSITION.clone()
      this.animator.add(0.7, (t) => {
        this.revolver.group.position.y = position.y - 0.3 * (1 - t)
      })
    }
    this.animator.add(1.6, (t) => this.revolver.spinDrum(t * Math.PI * 8))
  }

  private fireGun(shot: { playerId: string; fired: boolean }): void {
    const character = this.characters.get(shot.playerId)
    const lowerGun = () => {
      this.revolver.hide()
      character?.raiseGunHand(0)
    }

    if (!shot.fired) {
      sounds.click()
      this.animator.add(0.1, (t) => {
        this.revolver.group.rotateX(-0.015 * (1 - t))
      })
      this.animator.add(0.5, () => {}, { delay: 1.8 }).then(lowerGun)
      return
    }

    sounds.bang()
    this.shake = 1
    this.revolver.setFlash(1)
    this.animator.add(0.18, (t) => this.revolver.setFlash(1 - t)).then(() => {
      this.revolver.hide()
    })
    if (character) {
      character.markDead()
      this.animator.add(0.45, (t) => character.setSlump(t), { ease: (t) => t * t })
    } else {
      // Slump onto the table, then rise again to watch the rest of the game.
      this.animator.add(0.9, (t) => this.setCameraDeath(t), { ease: (t) => t * t })
      this.animator.add(1.2, (t) => this.setCameraDeath(1 - t), {
        delay: DEAD_SLUMP_SECONDS,
        ease: easeInOut,
      })
    }
  }

  private setCameraDeath(amount: number): void {
    this.cameraDeath = amount
  }

  private updateHand(): void {
    const hovered = this.cardUnderPointer()
    if (hovered !== this.hovered) {
      this.hovered = hovered
      this.renderer.domElement.style.cursor = hovered ? 'pointer' : ''
    }

    const middle = (this.hand.length - 1) / 2
    this.hand.forEach((entry, index) => {
      const offset = index - middle
      const target =
        (this.selected.has(entry.card.id) ? CARD_LIFT : 0) +
        (entry === hovered ? 0.015 : 0)
      entry.lift += (target - entry.lift) * 0.25
      entry.mesh.position.set(
        offset * HAND_SPACING,
        HAND_Y + entry.lift - Math.abs(offset) * 0.012,
        HAND_Z + index * 0.002,
      )
      entry.mesh.rotation.z = -offset * 0.05
    })
  }

  private render(): void {
    const now = performance.now()
    const time = now / 1000
    this.animator.tick(now)
    this.bar.update(time)

    // Looking around follows the pointer, which also lets you read side seats.
    this.look.x += (this.pointer.x - this.look.x) * 0.06
    this.look.y += (this.pointer.y - this.look.y) * 0.06
    this.camera.position.lerpVectors(CAMERA_POSITION, DEAD_CAMERA_POSITION, this.cameraDeath)
    this.camera.lookAt(CAMERA_TARGET)
    this.camera.rotateY(-this.look.x * LOOK_YAW)
    this.camera.rotateX(this.look.y * LOOK_PITCH)
    this.camera.rotateZ(this.cameraDeath * 0.45)
    if (this.shake > 0.001) {
      this.camera.rotateX((Math.random() - 0.5) * 0.05 * this.shake)
      this.camera.rotateY((Math.random() - 0.5) * 0.05 * this.shake)
      this.shake *= 0.92
    }
    this.camera.updateMatrixWorld()

    this.updateHand()

    const currentId = this.previous?.currentPlayerId ?? null
    const focusId = currentId ?? this.previous?.reveal?.loserId ?? null
    const focus = focusId ? this.seatPosition(focusId).setY(1.3) : null
    for (const [id, character] of this.characters) {
      character.update(time, this.events.voiceLevel(id), id === focusId ? this.camera.position : focus)
    }

    this.renderer.render(this.scene, this.camera)
  }
}

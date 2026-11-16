export interface PeerInfo {
  id: string
  name: string
}

export type Rank = 'Q' | 'K' | 'A' | 'J'

export interface Card {
  id: number
  rank: Rank
}

export interface SeatedPlayer extends PeerInfo {
  alive: boolean
  cards: number
  shots: number
}

export interface GameView {
  phase: 'playing' | 'reveal' | 'roulette' | 'shot' | 'over'
  round: number
  tableRank: Rank
  currentPlayerId: string | null
  hand: Card[]
  players: SeatedPlayer[]
  lastPlay: { playerId: string; count: number; seq: number } | null
  canCall: boolean
  mustCall: boolean
  reveal: {
    callerId: string
    accusedId: string
    ranks: Rank[]
    lied: boolean
    loserId: string
  } | null
  shot: { playerId: string; fired: boolean } | null
  winnerId: string | null
}

export interface PlayerRecap extends PeerInfo {
  outcome: 'won' | 'shot' | 'left' | 'survived'
  plays: number
  lies: number
  caught: number
  calls: number
  correctCalls: number
  pulls: number
}

export interface Recap {
  id: string
  rounds: number
  winnerId: string | null
  players: PlayerRecap[]
  highlights: { title: string; text: string }[]
  tells: string[]
}

export interface RoomState {
  type: 'state'
  version: number
  players: PeerInfo[]
  game: GameView | null
  secondsLeft: number | null
  recap: Recap | null
}

/** One measurement of an utterance, compared with the speaker's usual voice. */
export interface VoiceReading {
  value: number
  /** Null while the server is still learning the speaker's voice. */
  change: number | null
  trend: 'calibrating' | 'higher' | 'lower' | 'steady'
}

export type VoiceFeature = 'pitch' | 'speed'

/** A reading is null when that feature could not be measured. */
export type VoiceReadings = Record<VoiceFeature, VoiceReading | null>

export type SignalData =
  | { description: RTCSessionDescriptionInit }
  | { candidate: RTCIceCandidateInit }

export type ServerMessage =
  | { type: 'welcome'; id: string; peers: PeerInfo[] }
  | { type: 'peer-joined'; peer: PeerInfo }
  | { type: 'peer-left'; id: string }
  | { type: 'signal'; from: string; data: SignalData }
  | { type: 'error'; message: string }
  | {
      type: 'caption'
      playerId: string
      name: string
      text: string
      voice: VoiceReadings
    }
  | RoomState

export type ClientMessage =
  | { type: 'signal'; to: string; data: SignalData }
  | { type: 'start' }
  | { type: 'play'; cards: number[] }
  | { type: 'call' }
  | { type: 'speech-end' }

export const REVOLVER_CHAMBERS = 6
export const RANK_NAMES: Record<Rank, string> = {
  Q: 'Queen',
  K: 'King',
  A: 'Ace',
  J: 'Joker',
}

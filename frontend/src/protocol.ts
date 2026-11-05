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

export interface RoomState {
  type: 'state'
  version: number
  players: PeerInfo[]
  game: GameView | null
  secondsLeft: number | null
}

export type SignalData =
  | { description: RTCSessionDescriptionInit }
  | { candidate: RTCIceCandidateInit }

export type ServerMessage =
  | { type: 'welcome'; id: string; peers: PeerInfo[] }
  | { type: 'peer-joined'; peer: PeerInfo }
  | { type: 'peer-left'; id: string }
  | { type: 'signal'; from: string; data: SignalData }
  | { type: 'error'; message: string }
  | RoomState

export type ClientMessage =
  | { type: 'signal'; to: string; data: SignalData }
  | { type: 'start' }
  | { type: 'play'; cards: number[] }
  | { type: 'call' }

export const REVOLVER_CHAMBERS = 6
export const RANK_NAMES: Record<Rank, string> = {
  Q: 'Queen',
  K: 'King',
  A: 'Ace',
  J: 'Joker',
}

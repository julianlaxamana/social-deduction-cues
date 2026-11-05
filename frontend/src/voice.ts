import { audioContext } from './audio.ts'
import type {
  ClientMessage,
  PeerInfo,
  ServerMessage,
  SignalData,
} from './protocol.ts'

export interface VoiceEvents {
  /** A connection to another player changed state. */
  onConnectionsChanged: () => void
  /** The browser refused to start playback until the user interacts. */
  onPlaybackBlocked: () => void
}

interface Peer {
  info: PeerInfo
  pc: RTCPeerConnection
  audio: HTMLAudioElement
  meter: LevelMeter | null
}

/** Measures how loud a stream is, to animate whoever is speaking. */
class LevelMeter {
  private readonly source: MediaStreamAudioSourceNode
  private readonly analyser: AnalyserNode
  private readonly samples: Uint8Array<ArrayBuffer>

  constructor(stream: MediaStream) {
    const context = audioContext()
    this.source = context.createMediaStreamSource(stream)
    this.analyser = context.createAnalyser()
    this.analyser.fftSize = 512
    this.samples = new Uint8Array(this.analyser.fftSize)
    this.source.connect(this.analyser)
  }

  /** Current loudness, from 0 to about 1. */
  level(): number {
    this.analyser.getByteTimeDomainData(this.samples)
    let sum = 0
    for (const sample of this.samples) sum += ((sample - 128) / 128) ** 2
    return Math.min(1, Math.sqrt(sum / this.samples.length) * 4)
  }

  disconnect(): void {
    this.source.disconnect()
  }
}

// STUN only finds a direct route between players. Players behind strict NATs
// also need a TURN server, which can be supplied as JSON in VITE_ICE_SERVERS.
const ICE_SERVERS: RTCIceServer[] = import.meta.env.VITE_ICE_SERVERS
  ? JSON.parse(import.meta.env.VITE_ICE_SERVERS)
  : [{ urls: 'stun:stun.l.google.com:19302' }]

export class VoiceChat {
  private readonly events: VoiceEvents
  private readonly peers = new Map<string, Peer>()
  private send: (message: ClientMessage) => void = () => {}
  private microphone: MediaStream | null = null
  private microphoneMeter: LevelMeter | null = null

  constructor(events: VoiceEvents) {
    this.events = events
  }

  /** Asks for the microphone. Without one, the player can still listen. */
  async startMicrophone(): Promise<boolean> {
    try {
      this.microphone = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
      this.microphoneMeter = new LevelMeter(this.microphone)
      return true
    } catch (error) {
      console.warn('No microphone available', error)
      return false
    }
  }

  get microphoneStream(): MediaStream | null {
    return this.microphone
  }

  setSender(send: (message: ClientMessage) => void): void {
    this.send = send
  }

  stop(): void {
    this.send = () => {}
    for (const id of [...this.peers.keys()]) this.removePeer(id)
    this.microphoneMeter?.disconnect()
    this.microphoneMeter = null
    for (const track of this.microphone?.getTracks() ?? []) track.stop()
    this.microphone = null
  }

  setMuted(muted: boolean): void {
    for (const track of this.microphone?.getAudioTracks() ?? []) {
      track.enabled = !muted
    }
  }

  /** How loudly this player is talking right now; null means this client. */
  level(playerId: string | null): number {
    if (playerId === null) return this.microphoneMeter?.level() ?? 0
    return this.peers.get(playerId)?.meter?.level() ?? 0
  }

  connectionState(playerId: string): RTCPeerConnectionState | null {
    return this.peers.get(playerId)?.pc.connectionState ?? null
  }

  handleMessage(message: ServerMessage): void {
    switch (message.type) {
      case 'welcome':
        for (const peer of message.peers) this.addPeer(peer, true)
        break
      case 'peer-joined':
        this.addPeer(message.peer, false)
        break
      case 'peer-left':
        this.removePeer(message.id)
        break
      case 'signal': {
        const peer = this.peers.get(message.from)
        if (peer) {
          this.handleSignal(peer, message.data).catch((error) =>
            console.error('Failed to handle signal', error),
          )
        }
        break
      }
    }
  }

  resumePlayback(): void {
    for (const { audio } of this.peers.values()) {
      if (audio.srcObject) audio.play().catch(console.error)
    }
  }

  /**
   * Only the player who just joined makes offers. Offers can then never collide,
   * which matters because rolling back a colliding offer can leave Chrome stuck
   * gathering ICE candidates, with the connection never leaving the `new` state.
   */
  private addPeer(info: PeerInfo, isOfferer: boolean): void {
    const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS })
    const audio = new Audio()
    audio.autoplay = true
    const peer: Peer = { info, pc, audio, meter: null }
    this.peers.set(info.id, peer)

    pc.ontrack = ({ streams: [stream] }) => {
      audio.srcObject = stream
      audio.play().catch(() => this.events.onPlaybackBlocked())
      peer.meter = new LevelMeter(stream)
    }
    pc.onicecandidate = ({ candidate }) => {
      if (candidate) this.sendSignal(info.id, { candidate: candidate.toJSON() })
    }
    pc.onconnectionstatechange = () => {
      if (isOfferer && pc.connectionState === 'failed') pc.restartIce()
      this.events.onConnectionsChanged()
    }

    if (isOfferer) {
      pc.onnegotiationneeded = async () => {
        try {
          await pc.setLocalDescription()
          this.sendSignal(info.id, { description: pc.localDescription! })
        } catch (error) {
          console.error('Failed to create offer', error)
        }
      }
      this.addMicrophone(pc)
      // Without a microphone there is no track to trigger negotiation, but this
      // player still needs to hear everyone else.
      if (!this.microphone) pc.addTransceiver('audio', { direction: 'recvonly' })
    }
    this.events.onConnectionsChanged()
  }

  private addMicrophone(pc: RTCPeerConnection): void {
    for (const track of this.microphone?.getAudioTracks() ?? []) {
      pc.addTrack(track, this.microphone!)
    }
  }

  private removePeer(id: string): void {
    const peer = this.peers.get(id)
    if (!peer) return
    peer.pc.close()
    peer.audio.srcObject = null
    peer.meter?.disconnect()
    this.peers.delete(id)
    this.events.onConnectionsChanged()
  }

  private async handleSignal(peer: Peer, data: SignalData): Promise<void> {
    const { pc } = peer
    if ('candidate' in data) {
      await pc.addIceCandidate(data.candidate)
      return
    }

    await pc.setRemoteDescription(data.description)
    if (data.description.type === 'offer') {
      // A later offer is an ICE restart, and the microphone is already added.
      if (pc.getSenders().every((sender) => !sender.track)) {
        this.addMicrophone(pc)
      }
      await pc.setLocalDescription()
      this.sendSignal(peer.info.id, { description: pc.localDescription! })
    }
  }

  private sendSignal(to: string, data: SignalData): void {
    this.send({ type: 'signal', to, data })
  }
}

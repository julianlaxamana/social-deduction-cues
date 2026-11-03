export type ConnectionState = RTCPeerConnectionState

export interface PlayerState {
  id: string
  name: string
  connection: ConnectionState
}

export interface VoiceRoomEvents {
  /** The list of other players, or the state of a connection, changed. */
  onPlayersChanged: (players: PlayerState[]) => void
  /** The connection to the server was lost or refused. */
  onDisconnected: (reason: string) => void
  /** The browser refused to start playback until the user interacts. */
  onPlaybackBlocked: () => void
}

interface PeerInfo {
  id: string
  name: string
}

type SignalData =
  | { description: RTCSessionDescriptionInit }
  | { candidate: RTCIceCandidateInit }

type ServerMessage =
  | { type: 'welcome'; id: string; peers: PeerInfo[] }
  | { type: 'peer-joined'; peer: PeerInfo }
  | { type: 'peer-left'; id: string }
  | { type: 'signal'; from: string; data: SignalData }

interface Peer {
  info: PeerInfo
  pc: RTCPeerConnection
  audio: HTMLAudioElement
}

// STUN only finds a direct route between players. Players behind strict NATs
// also need a TURN server, which can be supplied as JSON in VITE_ICE_SERVERS.
const ICE_SERVERS: RTCIceServer[] = import.meta.env.VITE_ICE_SERVERS
  ? JSON.parse(import.meta.env.VITE_ICE_SERVERS)
  : [{ urls: 'stun:stun.l.google.com:19302' }]

export class VoiceRoom {
  private readonly events: VoiceRoomEvents
  private readonly peers = new Map<string, Peer>()
  private socket: WebSocket | null = null
  private microphone: MediaStream | null = null

  constructor(events: VoiceRoomEvents) {
    this.events = events
  }

  /** Asks for the microphone, then connects to the room. */
  async join(room: string, name: string): Promise<void> {
    this.microphone = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    })

    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    const query = new URLSearchParams({ name })
    const socket = new WebSocket(
      `${scheme}://${location.host}/ws/${encodeURIComponent(room)}?${query}`,
    )
    this.socket = socket
    socket.onmessage = (event) => this.handleMessage(JSON.parse(event.data))
    socket.onclose = (event) => {
      // leave() clears this.socket, so this only runs for unexpected closes.
      if (this.socket !== socket) return
      this.cleanUp()
      this.events.onDisconnected(event.reason || 'Lost connection to the server')
    }
  }

  leave(): void {
    const socket = this.socket
    this.cleanUp()
    socket?.close()
  }

  setMuted(muted: boolean): void {
    for (const track of this.microphone?.getAudioTracks() ?? []) {
      track.enabled = !muted
    }
  }

  private cleanUp(): void {
    this.socket = null
    for (const id of [...this.peers.keys()]) this.removePeer(id)
    for (const track of this.microphone?.getTracks() ?? []) track.stop()
    this.microphone = null
  }

  private handleMessage(message: ServerMessage): void {
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

  /**
   * Only the player who just joined makes offers. Offers can then never collide,
   * which matters because rolling back a colliding offer can leave Chrome stuck
   * gathering ICE candidates, with the connection never leaving the `new` state.
   */
  private addPeer(info: PeerInfo, isOfferer: boolean): void {
    const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS })
    const audio = new Audio()
    audio.autoplay = true
    const peer: Peer = { info, pc, audio }
    this.peers.set(info.id, peer)

    pc.ontrack = ({ streams: [stream] }) => {
      audio.srcObject = stream
      audio.play().catch(() => this.events.onPlaybackBlocked())
    }
    pc.onicecandidate = ({ candidate }) => {
      if (candidate) this.sendSignal(info.id, { candidate: candidate.toJSON() })
    }
    pc.onconnectionstatechange = () => {
      if (isOfferer && pc.connectionState === 'failed') pc.restartIce()
      this.notifyPlayersChanged()
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
    }
    this.notifyPlayersChanged()
  }

  private addMicrophone(pc: RTCPeerConnection): void {
    for (const track of this.microphone!.getAudioTracks()) {
      pc.addTrack(track, this.microphone!)
    }
  }

  private removePeer(id: string): void {
    const peer = this.peers.get(id)
    if (!peer) return
    peer.pc.close()
    peer.audio.srcObject = null
    this.peers.delete(id)
    this.notifyPlayersChanged()
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
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ to, data }))
    }
  }

  private notifyPlayersChanged(): void {
    this.events.onPlayersChanged(
      [...this.peers.values()].map(({ info, pc }) => ({
        ...info,
        connection: pc.connectionState,
      })),
    )
  }

  /** Retries playback; call this from a user gesture. */
  resumePlayback(): void {
    for (const { audio } of this.peers.values()) {
      if (audio.srcObject) audio.play().catch(console.error)
    }
  }
}

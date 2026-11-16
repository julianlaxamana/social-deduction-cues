import type { ClientMessage, ServerMessage } from './protocol.ts'

export interface ConnectionEvents {
  onMessage: (message: ServerMessage) => void
  /** The connection to the server was lost or refused. */
  onClosed: (reason: string) => void
}

export class RoomConnection {
  private readonly socket: WebSocket
  private closedByUs = false

  constructor(
    room: string,
    name: string,
    saveSpeech: boolean,
    aiRecap: boolean,
    geminiAnalysis: boolean,
    events: ConnectionEvents,
  ) {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    const query = new URLSearchParams({
      name,
      save_speech: String(saveSpeech),
      ai_recap: String(aiRecap),
      gemini_analysis: String(geminiAnalysis),
    })
    this.socket = new WebSocket(
      `${scheme}://${location.host}/ws/${encodeURIComponent(room)}?${query}`,
    )
    this.socket.onmessage = (event) => events.onMessage(JSON.parse(event.data))
    this.socket.onclose = (event) => {
      if (!this.closedByUs) {
        events.onClosed(event.reason || 'Lost connection to the server')
      }
    }
  }

  send(message: ClientMessage): void {
    if (this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message))
    }
  }

  sendBinary(data: ArrayBuffer): void {
    if (this.socket.readyState === WebSocket.OPEN) this.socket.send(data)
  }

  close(): void {
    this.closedByUs = true
    this.socket.close()
  }
}

import './style.css'
import { resumeAudio } from './audio.ts'
import { SpeechCapture } from './captions.ts'
import { RoomConnection } from './connection.ts'
import { HUD_HTML, Hud } from './hud.ts'
import type { PlayReference, RoomState, ServerMessage } from './protocol.ts'
import { BarScene } from './scene/scene.ts'
import { VoiceChat } from './voice.ts'

document.querySelector<HTMLDivElement>('#app')!.innerHTML = HUD_HTML

const SPEAKING_LEVEL = 0.12

let connection: RoomConnection | null = null
let selfId = ''
let roomName = ''
let latest: RoomState | null = null
let muted = false
let hasMicrophone = false
let captionsEnabled = true
let geminiAnalysis = false

const voice = new VoiceChat({
  onConnectionsChanged: render,
  onPlaybackBlocked() {
    hud.showToast('Click anywhere to hear the other players.', 8000)
    document.addEventListener('click', () => voice.resumePlayback(), { once: true })
  },
})

const speech = new SpeechCapture({
  onSpeechStart() {
    const game = latest?.game
    if (!geminiAnalysis || !latest?.gameId || game?.currentPlayerId !== selfId) return
    connection?.send({
      type: 'speech-start',
      gameId: latest.gameId,
      round: game.round,
      previousPlaySeq: game.lastPlay?.seq ?? null,
    })
  },
  onAudio: (pcm) => connection?.sendBinary(pcm),
  onSpeechEnd: () => connection?.send({ type: 'speech-end' }),
})

const hud = new Hud({
  onJoin: join,
  onStart: () => connection?.send({ type: 'start' }),
  onPlay: playSelected,
  onCall: () => connection?.send({ type: 'call' }),
  onToggleMute: toggleMute,
  onToggleCaptions: toggleCaptions,
  onLeave: () => leave(),
})

const scene = new BarScene(document.querySelector<HTMLCanvasElement>('#scene')!, {
  onSelectionChanged: () => hud.setSelectedCount(scene.selectedCardIds().length),
  voiceLevel: (playerId) => voice.level(playerId),
})

function render(): void {
  if (!latest) return
  if (latest.game) {
    hud.showGame(selfId, latest.game, latest.secondsLeft ?? 0)
  } else {
    hud.showLobby(roomName, selfId, latest.players, (id) => voice.connectionState(id))
  }
  const reference = latest.playReference
  hud.showPlayReference(geminiAnalysis && !!latest.game && latest.game.phase !== 'over',
    reference && isCurrentReference(reference) ? reference : null)
  hud.showRecap(latest.recap, selfId)
}

function isCurrentReference(reference: PlayReference): boolean {
  const game = latest?.game
  return latest?.gameId === reference.gameId &&
    game?.phase === 'playing' &&
    game.round === reference.round &&
    game.lastPlay?.seq === reference.playSeq
}

function handleMessage(message: ServerMessage): void {
  voice.handleMessage(message)
  switch (message.type) {
    case 'welcome':
      selfId = message.id
      break
    case 'error':
      hud.showToast(message.message)
      break
    case 'caption':
      if (captionsEnabled) hud.addCaption(message.name, message.text, message.voice)
      break
    case 'state':
      // A newer state may already have arrived; never step backwards.
      if (latest && message.version < latest.version) return
      latest = message
      updateSpeechCapture()
      scene.sync(selfId, message.players, message.game)
      render()
      break
  }
}

async function join(
  name: string,
  room: string,
  saveSpeech: boolean,
  aiRecap: boolean,
  useGeminiAnalysis: boolean,
): Promise<void> {
  resumeAudio()
  hud.setJoining(true)
  hasMicrophone = await voice.startMicrophone()
  muted = false
  hud.showMuted(muted, hasMicrophone)
  if (!hasMicrophone) {
    hud.showToast("No microphone: you can listen, but nobody will hear you.", 6000)
  }
  roomName = room
  geminiAnalysis = useGeminiAnalysis
  connection = new RoomConnection(room, name, saveSpeech, aiRecap, geminiAnalysis, captionsEnabled, {
    onMessage: handleMessage,
    onClosed: (reason) => leave(reason),
  })
  voice.setSender((message) => connection?.send(message))
  hud.showCaptionsEnabled(captionsEnabled)
  updateSpeechCapture()
  const stream = voice.microphoneStream
  // Captions are a bonus: the game and voice chat carry on without them.
  if (stream) speech.start(stream).catch(console.error)
}

function leave(reason = ''): void {
  connection?.close()
  connection = null
  latest = null
  hud.showPlayReference(false, null)
  selfId = ''
  speech.stop()
  voice.stop()
  scene.clear()
  hud.showJoinForm(reason)
}

function playSelected(): void {
  if (!hud.canPlay) return
  if (geminiAnalysis) speech.finishForPlay()
  connection?.send({ type: 'play', cards: scene.selectedCardIds() })
}

function toggleMute(): void {
  if (!hasMicrophone) return
  muted = !muted
  voice.setMuted(muted)
  updateSpeechCapture()
  hud.showMuted(muted, hasMicrophone)
}

setInterval(() => {
  hud.showMicrophoneActive(!muted && voice.level(null) > SPEAKING_LEVEL)
}, 100)

function toggleCaptions(): void {
  speech.finishForPlay()
  captionsEnabled = !captionsEnabled
  connection?.send({ type: 'caption-preference', enabled: captionsEnabled })
  updateSpeechCapture()
  hud.showCaptionsEnabled(captionsEnabled)
}

function updateSpeechCapture(): void {
  const myTurn = latest?.game?.currentPlayerId === selfId
  speech.enabled = !muted && (captionsEnabled || (geminiAnalysis && myTurn))
}

addEventListener('keydown', (event) => {
  if (!connection || event.repeat || event.target instanceof HTMLInputElement) return
  const key = event.key.toLowerCase()
  if (key >= '1' && key <= '5') scene.toggleCard(Number(key) - 1)
  else if (key === 'enter') {
    // A focused button would otherwise also treat Enter as a click on itself.
    event.preventDefault()
    playSelected()
  }
  else if (key === 'l' && hud.canCall) connection.send({ type: 'call' })
  else if (key === 'm') toggleMute()
  else if (key === 'c') toggleCaptions()
})

import type { AiSummary, GameView, PeerInfo, PlayerRecap, Recap, VoiceReadings} from './protocol.ts'
import { RANK_NAMES, REVOLVER_CHAMBERS } from './protocol.ts'
import { voiceCues } from './voiceCues.ts'

export interface HudActions {
  onJoin: (name: string, room: string, saveSpeech: boolean, aiRecap: boolean) => void
  onStart: () => void
  onPlay: () => void
  onCall: () => void
  onToggleMute: () => void
  onToggleCaptions: () => void
  onLeave: () => void
}

export const HUD_HTML = `
<form id="join-form" class="panel">
  <h1>Liar's Table</h1>
  <p class="tagline">Bluff with your voice. Pay with your head.</p>
  <label>
    Your name
    <input id="name" required maxlength="32" autocomplete="nickname" />
  </label>
  <label>
    Table
    <input id="room" required maxlength="64" pattern="[A-Za-z0-9_\\-]+"
      title="Letters, numbers, hyphens and underscores" value="lobby" />
  </label>
  <label class="checkbox">
    <input id="save-speech" type="checkbox" />
    Save a transcript of what I say, for post-game stats. Never the audio.
  </label>
  <label class="checkbox">
    <input id="ai-recap" type="checkbox" />
    Send the finished game, and my saved transcript, to Claude (Anthropic) for
    an AI summary. Only happens if everyone at the table ticks this.
  </label>
  <button type="submit" id="join" class="primary">Sit down</button>
  <p class="rules">
    Play 1–3 cards face down, claiming they match the table card, or call the
    last player a liar. Whoever is wrong pulls the trigger. Jokers are always
    honest. Your microphone is live for the whole table, and what you say is
    sent to the game server to make captions, unless you turn captions off.
    Plays and hands are logged on the server after each game.
  </p>
  <p id="join-status" role="status"></p>
</form>

<section id="lobby" class="panel" hidden>
  <h2>Table <span id="lobby-room"></span></h2>
  <ul id="lobby-players"></ul>
  <p id="lobby-hint"></p>
  <button type="button" id="start" class="primary">Deal</button>
</section>

<section id="recap" class="panel" hidden>
  <h2 id="recap-title"></h2>
  <p id="recap-summary"></p>
  <div class="recap-table">
    <table>
      <thead>
        <tr>
          <th scope="col">Player</th>
          <th scope="col">Lies</th>
          <th scope="col">Caught</th>
          <th scope="col">Calls right</th>
          <th scope="col">Trigger pulls</th>
          <th scope="col">Result</th>
        </tr>
      </thead>
      <tbody id="recap-players"></tbody>
    </table>
  </div>
  <ul id="recap-highlights"></ul>
  <ul id="recap-tells"></ul>
  <div id="recap-ai" hidden>
    <h3>Claude's take</h3>
    <p id="recap-ai-text"></p>
  </div>
  <button type="button" id="recap-close">Close</button>
</section>

<div id="top-bar" hidden>
  <div id="table-chip"></div>
  <div id="prompt" role="status"></div>
  <div class="top-controls">
    <button type="button" id="mute" aria-pressed="false" title="Toggle microphone (M)">Mute</button>
    <button type="button" id="captions-toggle" aria-pressed="true" title="Toggle captions and stop sending your speech for them (C)">Captions on</button>
    <button type="button" id="leave">Leave</button>
  </div>
</div>

<div id="banner" role="alert"></div>
<div id="flash"></div>
<div id="captions" aria-live="polite"></div>

<div id="actions" hidden>
  <p id="hand-hint">Click cards or press 1–5 · Enter plays · L calls</p>
  <button type="button" id="play" class="primary">Play</button>
  <button type="button" id="call" class="danger">Liar!</button>
</div>

<p id="toast" role="alert"></p>
`

const TURN_WARNING_SECONDS = 10
const OUTCOME_LABELS: Record<PlayerRecap['outcome'], string> = {
  won: 'Won',
  shot: 'Shot',
  left: 'Left',
  survived: 'Survived',
}
const CAPTION_SECONDS = 8
const MAX_CAPTIONS = 4

function element<T extends HTMLElement>(id: string): T {
  return document.getElementById(id) as T
}

/** The 2D overlay on top of the 3D scene. */
export class Hud {
  readonly nameInput = element<HTMLInputElement>('name')
  readonly roomInput = element<HTMLInputElement>('room')
  private readonly joinForm = element<HTMLFormElement>('join-form')
  private readonly joinButton = element<HTMLButtonElement>('join')
  private readonly joinStatus = element<HTMLParagraphElement>('join-status')
  private readonly lobby = element<HTMLElement>('lobby')
  private readonly lobbyRoom = element<HTMLSpanElement>('lobby-room')
  private readonly lobbyPlayers = element<HTMLUListElement>('lobby-players')
  private readonly lobbyHint = element<HTMLParagraphElement>('lobby-hint')
  private readonly startButton = element<HTMLButtonElement>('start')
  private readonly topBar = element<HTMLDivElement>('top-bar')
  private readonly tableChip = element<HTMLDivElement>('table-chip')
  private readonly prompt = element<HTMLDivElement>('prompt')
  private readonly muteButton = element<HTMLButtonElement>('mute')
  private readonly banner = element<HTMLDivElement>('banner')
  private readonly flash = element<HTMLDivElement>('flash')
  private readonly actions = element<HTMLDivElement>('actions')
  private readonly playButton = element<HTMLButtonElement>('play')
  private readonly callButton = element<HTMLButtonElement>('call')
  private readonly toast = element<HTMLParagraphElement>('toast')
  private readonly captions = element<HTMLDivElement>('captions')
  private readonly recap = element<HTMLElement>('recap')
  private closedRecapId = ''
  private shownRecapId = ''
  private readonly captionsButton = element<HTMLButtonElement>('captions-toggle')

  private deadline = 0
  private promptText = ''
  private toastTimer = 0
  private previous: GameView | null = null
  private selectedCount = 0
  private selfId = ''

  constructor(actions: HudActions) {
    this.joinForm.addEventListener('submit', (event) => {
      event.preventDefault()
      actions.onJoin(
        this.nameInput.value,
        this.roomInput.value,
        element<HTMLInputElement>('save-speech').checked,
        element<HTMLInputElement>('ai-recap').checked,
      )
    })
    this.startButton.addEventListener('click', actions.onStart)
    this.playButton.addEventListener('click', actions.onPlay)
    this.callButton.addEventListener('click', actions.onCall)
    this.muteButton.addEventListener('click', actions.onToggleMute)
    this.captionsButton.addEventListener('click', actions.onToggleCaptions)
    element('leave').addEventListener('click', actions.onLeave)
    element('recap-close').addEventListener('click', () => this.closeRecap())
    setInterval(() => this.renderPrompt(), 250)
  }

  get canPlay(): boolean {
    return !this.playButton.disabled && !this.actions.hidden
  }

  get canCall(): boolean {
    return !this.callButton.disabled && !this.actions.hidden
  }

  setJoining(joining: boolean): void {
    this.joinButton.disabled = joining
    this.joinButton.textContent = joining ? 'Allow your microphone…' : 'Sit down'
    if (joining) this.joinStatus.textContent = ''
  }

  showJoinForm(reason = ''): void {
    this.joinForm.hidden = false
    this.lobby.hidden = true
    this.topBar.hidden = true
    this.actions.hidden = true
    this.setJoining(false)
    this.joinStatus.textContent = reason
    this.banner.textContent = ''
    this.flash.className = ''
    this.previous = null
    this.recap.hidden = true
    this.clearCaptions()
  }

  showMuted(muted: boolean, hasMicrophone: boolean): void {
    this.muteButton.disabled = !hasMicrophone
    this.muteButton.textContent = !hasMicrophone ? 'No mic' : muted ? 'Unmute' : 'Mute'
    this.muteButton.setAttribute('aria-pressed', String(muted))
    this.muteButton.classList.toggle('active', muted)
  }

  /** Lights the mute button while the microphone is picking up sound. */
  showMicrophoneActive(active: boolean): void {
    this.muteButton.classList.toggle('speaking', active)
  }

  showCaptionsEnabled(enabled: boolean): void {
    this.captionsButton.textContent = enabled ? 'Captions on' : 'Captions off'
    this.captionsButton.setAttribute('aria-pressed', String(enabled))
    if (!enabled) this.clearCaptions()
  }

  addCaption(name: string, text: string, voice: VoiceReadings): void {
    const line = document.createElement('p')
    const speaker = document.createElement('strong')
    speaker.textContent = `${name}: `
    line.append(speaker, text)
    const cues = voiceCues(name, voice)
    if (cues) line.append(' ', cues)
    this.captions.append(line)
    while (this.captions.childElementCount > MAX_CAPTIONS) {
      this.captions.firstElementChild?.remove()
    }
    setTimeout(() => line.remove(), CAPTION_SECONDS * 1000)
  }

  private clearCaptions(): void {
    this.captions.replaceChildren()
  }

  showToast(message: string, duration = 3500): void {
    this.toast.textContent = message
    this.toast.classList.add('visible')
    clearTimeout(this.toastTimer)
    this.toastTimer = window.setTimeout(() => {
      this.toast.classList.remove('visible')
    }, duration)
  }

  setSelectedCount(count: number): void {
    this.selectedCount = count
    this.renderActions()
  }

  /** Shows the room before a game, including whose voice has connected. */
  showLobby(
    room: string,
    selfId: string,
    players: PeerInfo[],
    voiceState: (playerId: string) => RTCPeerConnectionState | null,
  ): void {
    this.joinForm.hidden = true
    this.lobby.hidden = false
    this.topBar.hidden = false
    this.actions.hidden = true
    this.tableChip.textContent = ''
    this.promptText = ''
    this.deadline = 0
    this.banner.textContent = ''
    this.flash.className = ''
    this.previous = null
    this.renderPrompt()

    this.lobbyRoom.textContent = room
    this.lobbyPlayers.replaceChildren(
      ...players.map((player) => {
        const item = document.createElement('li')
        const state = document.createElement('span')
        item.textContent = player.name
        if (player.id === selfId) {
          state.textContent = 'you'
        } else {
          const voice = voiceState(player.id)
          state.textContent = voice === 'connected' ? 'voice on' : 'voice connecting…'
          state.classList.toggle('good', voice === 'connected')
        }
        item.append(state)
        return item
      }),
    )
    const enough = players.length >= 2
    this.startButton.disabled = !enough
    this.lobbyHint.textContent = enough
      ? 'Anyone can deal once everyone has sat down.'
      : 'Waiting for at least one more player. Share the table name.'
  }

  showGame(selfId: string, game: GameView, secondsLeft: number): void {
    this.joinForm.hidden = true
    this.lobby.hidden = true
    this.topBar.hidden = false
    this.selfId = selfId
    this.deadline = performance.now() + secondsLeft * 1000

    const name = (id: string | null | undefined) =>
      id === selfId ? 'You' : (game.players.find((p) => p.id === id)?.name ?? 'Someone')
    const table = RANK_NAMES[game.tableRank]
    this.tableChip.textContent = `Round ${game.round} · ${table}s`

    const isNewPhase = game.phase !== this.previous?.phase
    const me = game.players.find((player) => player.id === selfId)

    switch (game.phase) {
      case 'playing':
        this.banner.textContent = ''
        if (game.mustCall) {
          this.promptText = 'Everyone else is out of cards. You must call Liar!'
        } else if (game.currentPlayerId === selfId) {
          this.promptText = game.lastPlay
            ? `${name(game.lastPlay.playerId)} played ${game.lastPlay.count} as ${table}s. Believe it?`
            : `Your turn. Play 1–3 cards as ${table}s.`
        } else {
          this.promptText = `${name(game.currentPlayerId)} is deciding…`
        }
        break
      case 'reveal': {
        const { callerId, accusedId, lied } = game.reveal!
        if (isNewPhase) {
          this.banner.textContent = `${name(callerId)} called Liar on ${name(accusedId) === 'You' ? 'you' : name(accusedId)}!`
        }
        this.promptText = lied ? `${name(accusedId)} lied.` : `${name(accusedId)} told the truth.`
        break
      }
      case 'roulette': {
        const loser = game.players.find((p) => p.id === game.reveal!.loserId)
        const odds = REVOLVER_CHAMBERS - (loser?.shots ?? 0)
        this.banner.textContent = `${name(loser?.id)} — 1 in ${odds}`
        this.promptText = `${name(loser?.id)} must pull the trigger.`
        break
      }
      case 'shot': {
        const { playerId, fired } = game.shot!
        const who = name(playerId)
        this.banner.textContent = fired
          ? playerId === selfId ? 'You are dead.' : `${who} is dead.`
          : '*click*'
        this.promptText = fired ? '' : `${who} survived.`
        if (isNewPhase && fired) {
          this.flash.className = playerId === selfId ? 'flash-self' : 'flash-other'
        }
        break
      }
      case 'over':
        this.banner.textContent =
          game.winnerId === selfId ? 'You win the table!' : `${name(game.winnerId)} wins the table.`
        this.promptText = 'Back to the lobby shortly…'
        break
    }

    if (game.phase !== 'shot') {
      this.flash.className = me && !me.alive ? 'ghost' : ''
    }
    if (me && !me.alive && game.phase === 'playing') {
      this.promptText = `You're dead, but you can still talk. ${this.promptText}`
    }
    this.previous = game
    this.renderActions()
    this.renderPrompt()
  }

  /** Shows the last game's stats to the people who played in it. */
  showRecap(recap: Recap | null, selfId: string): void {
    const visible =
      !!recap &&
      recap.id !== this.closedRecapId &&
      recap.players.some((player) => player.id === selfId)
    this.recap.hidden = !visible
    this.banner.hidden = visible
    if (!visible) return

    // It covers the lobby, and says who won itself instead of the banner.
    this.lobby.hidden = true

    const winner = recap.players.find((player) => player.id === recap.winnerId)
    element('recap-title').textContent =
      recap.winnerId === selfId ? 'You won' : `${winner?.name ?? 'Nobody'} won`
    element('recap-summary').textContent =
      `${recap.rounds} ${recap.rounds === 1 ? 'round' : 'rounds'}`

    element('recap-players').replaceChildren(
      ...recap.players.map((player) => {
        const row = document.createElement('tr')
        const cells = [
          player.id === selfId ? `${player.name} (you)` : player.name,
          `${player.lies} of ${player.plays}`,
          String(player.caught),
          `${player.correctCalls} of ${player.calls}`,
          String(player.pulls),
          OUTCOME_LABELS[player.outcome],
        ]
        row.append(
          ...cells.map((text) => {
            const cell = document.createElement('td')
            cell.textContent = text
            return cell
          }),
        )
        return row
      }),
    )

    element('recap-highlights').replaceChildren(
      ...recap.highlights.map(({ title, text }) => {
        const item = document.createElement('li')
        const label = document.createElement('strong')
        label.textContent = `${title}: `
        item.append(label, text)
        return item
      }),
    )
    element('recap-tells').replaceChildren(
      ...recap.tells.map((tell) => {
        const item = document.createElement('li')
        item.textContent = tell
        return item
      }),
    )
    this.renderAiSummary(recap.ai)
    this.shownRecapId = recap.id
  }

  private renderAiSummary(summary: AiSummary | null): void {
    const box = element('recap-ai')
    box.hidden = !summary
    if (!summary) return
    box.dataset.status = summary.status
    element('recap-ai-text').textContent = {
      pending: 'Claude is reading the game…',
      ready: summary.text ?? '',
      failed: `No summary: ${summary.text ?? 'something went wrong'}.`,
      off: 'No summary: not everyone at the table opted in.',
    }[summary.status]
  }

  private closeRecap(): void {
    this.closedRecapId = this.shownRecapId
    this.recap.hidden = true
    this.banner.hidden = false
    this.lobby.hidden = this.previous !== null
  }

  private renderActions(): void {
    const game = this.previous
    const myTurn = game?.currentPlayerId === this.selfId
    this.actions.hidden = !myTurn
    if (!game) return
    const count = this.selectedCount
    this.playButton.textContent = count ? `Play ${count} as ${RANK_NAMES[game.tableRank]}${count > 1 ? 's' : ''}` : 'Pick cards'
    this.playButton.disabled = game.mustCall || count < 1 || count > 3
    this.playButton.hidden = game.mustCall
    this.callButton.disabled = !game.canCall
    this.callButton.hidden = !game.canCall
  }

  private renderPrompt(): void {
    let text = this.promptText
    let urgent = false
    if (this.previous?.phase === 'playing') {
      const seconds = Math.max(0, Math.ceil((this.deadline - performance.now()) / 1000))
      text = `${text} ${seconds}s`
      urgent = seconds <= TURN_WARNING_SECONDS
    }
    if (this.prompt.textContent !== text) this.prompt.textContent = text
    this.prompt.classList.toggle('urgent', urgent)
  }
}

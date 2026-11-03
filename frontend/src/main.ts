import './style.css'
import { VoiceRoom, type PlayerState } from './voice.ts'

document.querySelector<HTMLDivElement>('#app')!.innerHTML = `
<main>
  <h1>Voice room</h1>

  <form id="join-form">
    <label>
      Your name
      <input id="name" required maxlength="32" autocomplete="nickname" />
    </label>
    <label>
      Room
      <input id="room" required maxlength="64" pattern="[A-Za-z0-9_\\-]+"
        title="Letters, numbers, hyphens and underscores" value="lobby" />
    </label>
    <button type="submit" id="join">Join with microphone</button>
  </form>

  <section id="in-room" hidden>
    <ul id="players"></ul>
    <div class="controls">
      <button type="button" id="mute" aria-pressed="false">Mute</button>
      <button type="button" id="leave">Leave</button>
    </div>
  </section>

  <p id="status" role="status"></p>
</main>
`

function element<T extends HTMLElement>(id: string): T {
  return document.getElementById(id) as T
}

const joinForm = element<HTMLFormElement>('join-form')
const nameInput = element<HTMLInputElement>('name')
const roomInput = element<HTMLInputElement>('room')
const joinButton = element<HTMLButtonElement>('join')
const inRoom = element<HTMLElement>('in-room')
const playerList = element<HTMLUListElement>('players')
const muteButton = element<HTMLButtonElement>('mute')
const leaveButton = element<HTMLButtonElement>('leave')
const status = element<HTMLParagraphElement>('status')

const CONNECTION_LABELS: Record<PlayerState['connection'], string> = {
  new: 'Connecting…',
  connecting: 'Connecting…',
  connected: 'Connected',
  disconnected: 'Reconnecting…',
  failed: 'Connection failed',
  closed: 'Disconnected',
}

let muted = false

const voice = new VoiceRoom({
  onPlayersChanged: renderPlayers,
  onDisconnected(reason) {
    showJoinForm()
    status.textContent = reason
  },
  onPlaybackBlocked() {
    status.textContent = 'Click anywhere to hear the other players.'
    document.addEventListener(
      'click',
      () => {
        voice.resumePlayback()
        status.textContent = ''
      },
      { once: true },
    )
  },
})

function renderPlayers(players: PlayerState[]): void {
  const self = document.createElement('li')
  self.textContent = `${nameInput.value} (you)`
  playerList.replaceChildren(
    self,
    ...players.map((player) => {
      const item = document.createElement('li')
      const state = document.createElement('span')
      state.className = `state ${player.connection}`
      state.textContent = CONNECTION_LABELS[player.connection]
      item.append(player.name, state)
      return item
    }),
  )
}

function setMuted(value: boolean): void {
  muted = value
  voice.setMuted(muted)
  muteButton.textContent = muted ? 'Unmute' : 'Mute'
  muteButton.setAttribute('aria-pressed', String(muted))
}

function showJoinForm(): void {
  inRoom.hidden = true
  joinForm.hidden = false
  joinButton.disabled = false
}

joinForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  joinButton.disabled = true
  status.textContent = ''
  try {
    await voice.join(roomInput.value, nameInput.value)
  } catch (error) {
    console.error(error)
    joinButton.disabled = false
    status.textContent =
      'Could not use your microphone. Check the browser permission and try again.'
    return
  }
  setMuted(false)
  renderPlayers([])
  joinForm.hidden = true
  inRoom.hidden = false
})

muteButton.addEventListener('click', () => setMuted(!muted))

leaveButton.addEventListener('click', () => {
  voice.leave()
  showJoinForm()
  status.textContent = ''
})

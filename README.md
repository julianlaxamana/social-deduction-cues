# Liar's Table

A 3D bluffing game for 2–4 players with live voice chat, inspired by Liar's Bar.

## How to play

Each round everyone is dealt 5 cards and a table card is announced: Queen, King
or Ace. On your turn, either:

- **Play 1–3 cards** face down, claiming they all match the table card.
- **Call Liar** on the previous player, which reveals what they played.

Jokers always count as honest. If any revealed card doesn't match, the player
who played them loses; otherwise the player who called loses. The loser points
their own revolver at their head and pulls the trigger. It has one bullet in six
chambers and keeps its position between rounds, so the odds get worse with every
pull. The last player alive wins.

Turns last 30 seconds, after which a random card is played for you. If everyone
else is out of cards you must call. Leaving the table forfeits the game.

**Controls:** click cards or press 1–5 to select them, Enter to play, L to call,
M to mute, C to toggle captions, and move the pointer to look around.

## Running it

You need [uv](https://docs.astral.sh/uv/) with Python 3.12 or newer, and
[Node.js](https://nodejs.org/). Run the server and the frontend in separate
terminals.

1. Start the game server. It listens on port 8000, and the first start downloads
   the captions model (about 140 MB):

   ```bash
   cd backend && uv run main.py
   ```

2. Start the frontend:

   ```bash
   cd frontend && npm install && npm run dev
   ```

3. Open http://localhost:5173 and allow microphone access. Without a microphone
   you can still play and listen.

4. Get at least 2 players to enter the same table name, then press **Deal**. To
   try it alone, use two browser tabs. Wear headphones, or the tabs will echo.

Restart the server after changing backend code, then reload the browser tabs. The
frontend updates by itself.

To run the backend tests:

```bash
cd backend && uv run pytest
```

## How it works

- `backend/game.py` holds the rules and every secret. Each browser only receives
  its own hand, so nobody can cheat by reading network traffic.
- `backend/main.py` runs rooms and phase timers, and relays WebRTC signaling.
- `backend/game_log.py` records each game, and `backend/recap.py` sums it up.
  `backend/ai_summary.py` asks Claude for a written summary, if everyone agreed.
- `frontend/src/scene/` is the Three.js bar, built entirely in code with no
  external assets.
- `frontend/src/voice.ts` is peer-to-peer WebRTC audio, which never passes
  through the server. `frontend/src/captions.ts` separately sends speech to
  `backend/captions.py` to be transcribed.

## Captions

Speech is captioned live with [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
running on the game server's CPU. The first server start downloads the model
(about 140 MB). Set `WHISPER_MODEL` to change it, e.g. `small.en` for better
accuracy or `base` for languages other than English.

Each browser only sends audio while its player is talking, and never while
muted. The server transcribes it and sends the text to the table. It never keeps
the audio, and only keeps the text for players who opt in (see below). Pressing C or the Captions button hides captions and stops
sending your speech.

## Pitch

Each caption also shows whether the speaker's pitch was higher, lower or steady
compared with their usual voice. `backend/voice_features.py` measures the median
pitch of each utterance with [Praat](https://parselmouth.readthedocs.io/) and
compares it, in semitones, with everything that player has said since joining.
The first 5 utterances only build that baseline, so they show as calibrating.
Only speech that Whisper found words in counts, and turning captions off stops
pitch too. A change in pitch is a hint, not proof of a lie.

## Gemini play reference

Set `GEMINI_API_KEY` in the backend process environment before running
`uv run --locked main.py`. Set `GEMINI_MODEL` to override the default
`gemini-3.5-flash-lite`. Run `uv run --locked probe_gemini.py` from
`backend/` to list models that support `generateContent` and try the configured
model with a synthetic claim.

The join form lets each player opt in to sending one relevant caption transcript
and available speech measurements to Google per play. Captions must be on.
Say the claim during your turn and play the cards shortly afterwards. Other
players see the AI reference and its reason for up to five seconds, provided
the play is still face down and can be called. Players make their own calls.

The game log records the category, play sequence, elapsed milliseconds from
the accepted play, and whether the server published the reference. The
explanation stays in memory because it may quote the transcript.
The model probe uses only synthetic text. Review the Gemini service's data
terms before sending real player speech.

## Game logs

Each game is written to `backend/logs/` as one JSON Lines file, ignored by git.
Set `GAME_LOG_DIR` to save them elsewhere. Every line has `t`, the seconds since
the game started, and `event`. Speech is written once it has been transcribed, so
sort by `t` to read a game in order.

| Event | What it records |
|---|---|
| `log-start` | Room and start time |
| `game-start` | Player ids and names |
| `round-start` | Table card, who goes first, every hand dealt |
| `play` | Cards played, whether it was a lie, cards left, who is next |
| `call` | Who called whom, the cards, who lost, whether the call was forced |
| `shot` | Who pulled the trigger, whether it fired, which pull it was |
| `speech` | What a player said and when it ended, for players who opted in |
| `player-left`, `game-over` | Who left, and who won |

`play` and `call` also have `auto`, which is true when the turn timer made the
move. Speech is only saved during a game, and only for players who tick the box
when joining. Speech times are estimates based on when the audio reached the
server. To delete a game's data, delete its file.

## Post-game recap

When a game ends, `backend/recap.py` turns its events into stats for everyone who
played: lies told and caught, calls, trigger pulls and a few awards. Moves made by
the turn timer and forced calls don't count.

It also points out differences between a player's lies and honest plays, in
thinking time and, if they opted in to saving speech, in words per turn. These
need at least 2 examples of each and a clear gap, but one game is very little
data, so treat them as a talking point rather than evidence of a real tell.

### Claude's take

If every player ticks **Send the finished game to Claude** when joining, the
server also asks Claude for a short written summary of the game: the turning
points, and one thing each player could try next time. It appears at the bottom
of the recap a few seconds after the stats. If anyone at the table didn't tick
the box, nothing is sent.

`backend/ai_summary.py` turns the game's events into plain English and pipes them
into the Claude Code CLI (`claude -p`) with every tool turned off, so Claude can
only read the game and reply with text. What it sees is everything in the game
log: all hands, every play, and the saved words of players who opted in to
saving speech. This goes to Anthropic, and each summary uses some of the host's
Claude usage.

To turn it on, the person running the server needs
[Claude Code](https://claude.com/claude-code) installed and signed in. Run
`claude` once in a terminal and sign in (or set `ANTHROPIC_API_KEY`), then start
the server from that terminal. Without it, the recap says why there is no
summary, and everything else still works. Optional settings:

| Variable | Default | Meaning |
| --- | --- | --- |
| `CLAUDE_BIN` | `claude` | Path to the Claude Code CLI |
| `CLAUDE_SUMMARY_MODEL` | `sonnet` | Model to use |
| `CLAUDE_SUMMARY_TIMEOUT` | `120` | Seconds to wait before giving up |

## Playing across machines

Browsers only allow microphone access on `localhost` or HTTPS, so playing across
machines requires serving the app over HTTPS, and the server only listens on
`127.0.0.1` until you change that in `backend/main.py`. Players on restrictive
networks also need a TURN server, set as JSON in `VITE_ICE_SERVERS`, e.g.
`[{"urls":"turn:turn.example.com:3478","username":"u","credential":"p"}]`.

# claude-voice

Talk to [Claude Code](https://claude.com/claude-code) on WSL (and, untested so far, macOS) and hear it answer. Say "Hey Claude", say what you want,
and the request goes to the Claude Code session you picked; its reply is read aloud as a short spoken summary. You
start `claude` exactly as usual, in any project and any WSL distro, and type `/speak on`. Everything runs locally:
Whisper on the GPU for the wake phrase ("Hey Claude", configurable) and dictation, Piper for the voice, and a local ollama model that is
Jarvis's brain (it answers questions about your sessions and decides where a request goes) and turns written replies
into something worth hearing. Jarvis itself uses no Claude tokens; only the sessions doing the work do, plus, if you
choose, one session you make Jarvis's Claude brain for harder questions (`/speak jarvis`).

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/EmmN/claude-voice/main/install.sh | bash                    # the distro with the microphone
curl -fsSL https://raw.githubusercontent.com/EmmN/claude-voice/main/install.sh | bash -s -- --no-listener # every other distro
```

The installer adds the apt packages (`ffmpeg jq tmux sox libsox-fmt-pulse`, sudo once), `uv`, Piper and the
`en_US-lessac-medium` voice, the listener's Python environment (openWakeWord, faster-whisper, CUDA libraries; skipped
with `--no-listener`), links the commands into `~/.local/bin`, copies the `/speak` skill into `~/.claude/skills`,
registers five hooks in `~/.claude/settings.json` and speaks a test sentence. Re-running, or `claude-voice update`,
updates everything. `claude-voice register` is the `--no-listener` setup for a distro that already has the repo.
Hooks load when a session starts: start a new session, or open `/hooks` once in a running one.

On macOS the same command installs with Homebrew (`ffmpeg jq coreutils flock`); see [macOS](#macos).

Nothing is installed into Windows itself (no programs, services or registry keys). The scripts keep a little shared
state in your Windows user folder (see [State](#state)) and run one hidden PowerShell process there, the speech player
(`windows/player.ps1`, copied to `C:\Users\<you>\.claude-voice\player.ps1` and started by setup and
`claude-voice start`). `windows/register-autostart.ps1` (optional, run by you) runs `claude-voice start` at logon.

Requirements: WSL2 with WSLg (`/mnt/wslg/PulseServer` exists), Python 3.12, an NVIDIA GPU for fast dictation (CPU
works, slower), ollama at `localhost:11434` with a chat model for the spoken summaries (optional; without it the reply
is read as plain text).

## Quick start

```bash
voice mic-test          # speak for five seconds, hear it played back
claude-voice start      # in the microphone distro: start the hey-Jarvis listener
claude                  # as usual, anywhere; then type /speak on
```

Say "Hey Claude" (the wake phrase is configurable, see [Wake phrase](#wake-phrase)). It lists the open sessions ("Which session: web app e6 or mobile app 3c?"; with a single
session it goes straight to "OK, <session>. What do you want me to do?"). Name one, with or without the request
("mobile app, check the open tasks"); when you pause for 2.5 seconds it beeps (heard you, working on it), delivers
the request, says where it went, and stops listening. The session wakes up, works, and its reply is spoken ("From
<session>: …" when several have voice on). Nothing is sent to a session you did not name in that conversation. `claude-voice status`, `claude-voice log` (what it heard, with
timestamps), `claude-voice stop`.

## Talking to it

| You say | What happens |
|---|---|
| "Hey Claude." … "What did we decide about refunds?" | goes to the focused session |
| "Hey Claude." … "Tell db upgrade to check the replication lag." | goes to `db_upgrade`, which becomes the focus |
| "Hey Claude." … "For the docs site session, what did we ship?" (or "on the docs site one, …") | goes to `docs_site` |
| "Hey Claude." … "Switch to db upgrade." (or just "db upgrade") | focuses it and asks what it should do |
| "Hey Claude." … "Switch to web app and run the tests." | focuses it and sends "run the tests" |
| "Hey Claude." … "List sessions." / "Which sessions are open?" | reads the open sessions and the focused one |
| "Hey Claude." … "How many sessions are open? Which one is busy?" | the local brain answers from the session list |
| "Hey Claude." … "What is db upgrade doing?" | the local brain answers from its last prompt and reply |
| "Hey Claude." … "Explain the CAP theorem." | the local brain answers short ones; deeper ones go to Jarvis's Claude brain if you set one |
| "Hey Claude." … "All sessions, summarize what you are doing." | sent to every voice session |
| "Hey Claude, <request>" while a session's reply is being read | the reply stops, your request goes out, and the reply is read again afterwards |
| "Hey Claude, stop" (or never mind, or nothing at all) while a reply is being read | the reply and the ones queued behind it are dropped; an interrupted reply is read again only if you then send a request to a session |
| talking while Jarvis asks something ("Which session: …") | it stops talking and takes what you say as the answer |
| "Hey Claude." … "Stop." / "Never mind." | ends there; nothing is sent |
| "Hey Claude." … "Payments, run the tests." | a session name followed by a comma addresses it, like "tell payments to run the tests" |
| "… tell payments to check the logs, never mind" | a sentence that ends by taking itself back ("never mind", "forget it", "cancel that", "scratch that") is a cancel |
| "Hey Claude." … "Stell payments …" / "Tel payments …" | a misheard "tell" in front of a session name still means tell |
| "Hey Claude." … "Jarvis." (only the wake word again) | "Yes?", and it listens again |
| "Hey Claude." … "Tell payments that." (and nothing after it) | "What should I tell it?" |
| "Hey Claude." … "Start a session in teamhub deploy called billing and ask it to check the failing specs." | asks "Start a new session called billing in teamhub deploy, and ask it to …?"; on "yes" it opens a visible terminal tab with `claude` in that folder, voice already on |
| "… start a session in blog on the debian distro" | the same, in that WSL distro |
| "Hey Claude." … "Start a new session." | "Which project? Most used: webshop, teamhub deploy or claude voice. Or name any other folder." |
| "Hey Claude." … "List the old sessions [in webshop]." | reads the five newest ("One, flaky tests, in webshop, today. …") and asks which one: say a number or words of the title |
| "Hey Claude." … "Resume flaky tests." | finds it anywhere in your history (exact title, else fuzzy, else the local model); "Resume flaky tests, in webshop, from today?" → yes opens it in a new tab |
| "Hey Claude." … "Close the billing session." (or "stop billing") | asks "Close the billing session? Anything it is in the middle of stops."; on "yes" it ends that Claude (its tab closes) |
| "Hey Claude." … "Check the open tasks." (no session named, several open) | "Which session? …": nothing is sent until you name one |
| "Hey Claude." … "mobile app" | "OK, mobile app 3c. What do you want me to do?" |

Session names are Claude Code's own (`/rename` sets one; otherwise the generated `webapp-ae` style name) and match
on words, so "db upgrade" finds `db_upgrade`. `/speak on` makes that session the focus. An unknown name gets
"Which session?" and the list. `dispatch --text "…"` shows how a sentence would be routed without sending it.

## Opening and closing sessions by voice

"Start / open / create a session in <project> [called <name>] [on the <distro> distro] [and ask it to <request>]".
The project is matched by its words against the folders in `~/projects` (`VOICE_PROJECTS`, colon-separated, in the
target distro); without "called", the folder name is the session name. Jarvis repeats what it understood and only a
clear "yes" (yes, yeah, sure, go ahead, …) goes ahead; anything else, or silence, cancels. `session-open` then opens a
new tab (Windows Terminal via `wt.exe` on WSL, Terminal on macOS, the desktop's terminal on Linux) that runs
`claude -n <name> [request]` in that folder, with your normal Claude Code settings and permissions, exactly as if you
had typed it: the tab starts an interactive login shell, so your `~/.bashrc` (nvm and node tools, rvm, aliases, cd
hooks) applies just like in a tab you open yourself; on macOS your own shell (`$SHELL`). Its environment carries `CLAUDE_VOICE_AUTO=1`, and a SessionStart hook (`hook-start`) turns that into
`/speak on`: registered, focused, replies spoken.

Past sessions come from Claude Code's own transcripts (`~/.claude/projects/*/<id>.jsonl`, in every distro): their
title is your `/rename` name, else the title Claude gave them. "List the old sessions [in X]" reads the five newest;
"resume <words> [in X]" (also "reopen", "go back to") searches all of them: an exact title wins, then a clear fuzzy
match, then a few close ones to choose from, and when the words match nothing the local model looks through the last
150 titles. "Resume the last session in X" takes the newest. A session already open somewhere is not opened twice.
"Continue …" only resumes on a near-exact title, so "continue with the tests" is still a request. Resuming runs
`claude --resume <id>` in a new tab, with voice on.

You do not have to use these exact words: when no fixed phrase matches (also with no session open), the local brain
recognizes the same four commands in free speech ("what was I doing about webhook retries", "pull up that thing I
did on flaky", "fire up a new one for the widgets repo and have it run the tests", "I'm done with db upgrade, shut
it") and hands them back in the canonical form, so the search, folder matching and confirmation are the same.

When a folder name does not match by words (or with the spaces removed: "team hub widgets" → `teamhub-widgets`), the
local model picks from the folder list too; you confirm either way.

"Close / end / stop / quit the <name> session" (every word must name a voice session, so "close the pull request" still
goes to a session as a request) asks first as well; `session-close` sends Claude SIGTERM (SIGKILL after 8 s), which
ends it and closes the tab it was opened in, and forgets the session.

## Wake phrase

The default is "Hey Claude". Change it with `claude-voice config VOICE_WAKE "<phrase>"` (the listener restarts):

- **any phrase** ("hey claude", "computer", "okay house"): spotted by Whisper. The listener cuts the microphone stream
  into short bursts of speech and transcribes each (about 0.2 s on the GPU); a burst that starts with the phrase is a
  wake. No model to train, and you can say the request in the same breath ("Hey Claude, ask mobile app to run the
  tests"). Costs a little GPU for every burst of speech in the room, wakes once you have finished the phrase, and
  speech that sounds like it can wake it.
- **a bundled openWakeWord model** (`hey_jarvis`, `alexa`, `hey_mycroft`, `hey_rhasspy`): a tiny CPU model that
  hears only that phrase, at no GPU cost (`VOICE_WAKE_THRESHOLD` sets its sensitivity). The request can follow in
  the same breath here too: the listener keeps the last 2.5 s of audio, so what you said right after the word is not
  lost; it waits about a second after the wake to see whether you go on, and greets you only if you do not.

Settings live in `~/.claude/voice/config` (`claude-voice config` lists them; `claude-voice config KEY ""` removes
one); a variable set in the environment overrides the file.

## In a session

| Prompt | Effect |
|---|---|
| `/speak on` | read this session's replies aloud and make it reachable by voice (registers it, sets the focus) |
| `/speak off` | silent and unreachable again (the default) |
| `/speak status` | machine mute state, this session's state, how many sessions are on |
| `/speak mute` / `/speak unmute` | silence or restore every session on this machine |
| `/speak jarvis` / `/speak jarvis off` (with a brain set, anything you say without naming a session goes to it instead of "which session?") | make this session Jarvis's Claude brain: it gets the questions the local model passes up, and its answers are spoken as Jarvis (no "From …") |

### A private home for Jarvis's brain

The brain session gets smarter with notes: your session names and how Whisper mishears them, the projects and tools
you ask about, the people you mention. Keep those out of this public repo in a private folder of your own:
`claude-voice home ~/jarvis-home` creates it with a CLAUDE.md template, a copy of your voice settings and a git repo
(push it to a private remote). Start the brain session there and type `/speak jarvis`; it reads the notes first.

Replies are read as written, cleaned for speech: no markdown, code blocks, links, ticket numbers or commit hashes;
paths are read as their file name and `SAY_LOCK_WAIT` as "say lock wait". Very long replies stop after about 400
words ("the rest is on screen"). `SPEECHIFY_MODE=summary` has the local model retell them instead (slower: about 25 s
for a long reply on an 8 GB card).
With several voice sessions, each reply starts with "From <session name>:". (`/voice`, with no s, is Claude Code's own
hold-to-talk dictation into the session in front of you; the installer's `sox` packages make it work on WSLg.)

## How it works

### Jarvis's brain: three layers

```
 what you said
   │
   ├─ 1. fixed phrases (dispatch, instant): stop · list sessions · switch to X · tell/ask X … · for the X session … · all sessions …
   │
   ├─ 2. local brain (bin/brain → ollama gemma4:e4b, ~0.8–1.5 s warm, no Claude tokens), given what Jarvis knows.
   │      It answers only questions about the sessions as a group (how many, which, busy/idle, the time, greetings);
   │      anything about a session's work or project (summaries, "how does X work", tasks) goes to the session.
   │        every session's name, distro, project, busy/idle, last prompt, start of its last reply, which is focused
   │      ├─ "route"    a request for a session  ─► the named one, else the focused one
   │      ├─ "answer"   a question for Jarvis    ─► spoken at once ("two sessions are open; db upgrade is busy")
   │      └─ "escalate" a deeper question        ─► Jarvis's Claude brain, if a session did /speak jarvis
   │
   └─ 3. Jarvis's Claude brain (a session you picked with /speak jarvis): answers in a few spoken sentences
```

Ollama down or slow (`JARVIS_TIMEOUT`): the sentence goes to the focused session, as before the brain existed. The
listener asks ollama to keep the model loaded (`JARVIS_KEEP_ALIVE`) and warms it at every "Hey Claude", so a model
ollama unloaded loads while you are still talking. The session facts come from the hooks: `hook-prompt` marks a
session busy and records the prompt, `hook-stop` marks it idle and records the start of the reply, in its registry
entry. `brain "<sentence>"` and `dispatch --text "<sentence>"` show the decision without sending anything.

### The pieces

```
 Windows ──────────────────────────────────────────────────────────────────────────────────────────────────────
   microphone / speakers ◄──► WSLg PulseAudio (RDPSource / default sink), shared by every distro

   C:\Users\<you>\
     .claude-voice\sessions\<distro>__<session id>.json   who can be talked to (name, distro, id, pid, cwd)
     .claude-voice\inbox\<distro>__<session id>.msg       requests waiting for a session
     .claude-voice.lock                                   one voice at a time, across all distros
     .claude-voice.stop                                   touched on the wake phrase or `voice stop`: stop talking now
     .claude-voice\play\*.wav                            speech waiting for the player
     .claude-voice\jarvis_brain                          which session is Jarvis's Claude brain (/speak jarvis)
     .claude-voice\awaiting\<distro>__<id>, followup     only with VOICE_FOLLOWUP=1: "anything else for X?" after a reply
     .claude-voice\player.ps1 (hidden PowerShell)        plays those through Windows audio, writes player.alive

 WSL distro A (microphone distro) ──────────────────┐   WSL distro B ──────────────────────────────────────
   listener: listen.py (one per machine)            │     no listener
     wake phrase ─► Whisper ─► dispatch ─► brain    │
   ollama (localhost, shared by all distros)        │
   claude (any terminal) + 4 hooks                  │     claude (any terminal) + the same 4 hooks
   say: Piper ─► play ─► Windows player             │     say: Piper ─► play ─► Windows player
 ───────────────────────────────────────────────────┘   ────────────────────────────────────────────────────
```

The two distros never talk to each other directly. Everything they share is a file in the Windows folder: the
listener in A writes a request into B's session's inbox file, and the hook in B's session picks it up.

Speech goes out through Windows, not WSL: WSLg breaks playback up (gaps every few words, slow motion) while a
recording stream is open in the same distro, and the listener keeps the microphone open. `play` copies the Piper WAV
into the player's queue and waits until the player deletes it. Until the player is up (PowerShell can take 15 s to
start), `play` falls back to WSLg and the listener closes the microphone while speech plays.

### The hooks (registered in `~/.claude/settings.json`)

| Hook | Script | When | What it does |
|---|---|---|---|
| UserPromptSubmit | `hook-prompt` | every prompt | handles `/speak …`; registry entry: current name, busy, last prompt |
| Stop (async) | `hook-stop` | end of every turn | registry entry: idle, last reply; speaks the reply: transcript → `speechify` (ollama) → `say` |
| Stop (async, `asyncRewake`) | `hook-inbox` | end of every turn | waits for the inbox file; when a request lands, exits 2 → Claude wakes with it |
| SessionStart | `hook-start` | session starts | voice on when Jarvis opened it (`CLAUDE_VOICE_AUTO=1`, set by `session-open`) |
| SessionEnd | `hook-end` | session closes, `/clear` | forgets the session: registry entry, inbox, voice flag |

`asyncRewake` is the Claude Code hook option that makes this possible: the hook runs in the background after the turn,
and when it exits with code 2 its output wakes the idle session as a new message. Your terminal is never touched; the
request shows up in the session as a collapsed "🎤 Voice request (Jarvis)" line (Claude Code shows a hook's own text
there only for plugins), so Claude is asked to begin its reply with `🎤 You said: "…"`; that line is not read aloud. The same note asks for
a reply written for listening: the answer first, short plain sentences, no tables, lists, code, paths, URLs or ticket
numbers in the final message (the work itself, files and tools, is unaffected).

### `/speak on`

```
you type /speak on
  └─► hook-prompt ─► voice on <session id>
        ├─ ~/.claude/voice/sessions/<id>            voice flag: replies are spoken
        ├─ registry  <distro>__<id>.json            name + pid from Claude's ~/.claude/sessions/<pid>.json
        └─ ~/.claude/voice/focus                    this session is now the default target
  └─► /speak skill confirms ─► turn ends ─► hook-inbox starts waiting on <distro>__<id>.msg
```

### One request, end to end

```
 "Hey Claude"
   │  listen.py: ffmpeg 16 kHz mic stream ─► bursts of speech ─► Whisper: does it start with "hey claude"?
   │  ("Hey Claude, <request>" in one breath skips the greeting; VOICE_WAKE=hey_jarvis uses openWakeWord instead)
   │  something talking? ─► touch .claude-voice.stop ─► the player and every `say`, in every distro, stop at once
   │  take .claude-voice.lock (so nothing speaks while you talk and Jarvis never hears itself)
   ▼
 "Which session: A or B?"                                  cached wav, plays at once
   │  (one session only: "OK, A. What do you want me to do?")
   ▼
 you talk … until 2.5 s of quiet (VOICE_SILENCE), max 45 s
   ▼
 beep                                                      heard you, working on it
   │  Whisper small on the GPU (~0.2 s), primed with "Claude Code" and the session names
   ▼
 dispatch "<transcript>"
   ├─ "stop" / "never mind"            ─► nothing
   ├─ "list sessions"                  ─► "2 open: A and B. Talking to A."
   ├─ "switch to B" (alone)            ─► focus B, "OK, B. What do you want me to do?" ─► one more round
   ├─ "tell B …" / "for the B session" ─► target B
   ├─ "all sessions …"                 ─► every session
   └─ anything else                    ─► local brain: route (focused or named session) · answer (spoken now) · escalate
   │  no session named yet and several open ─► "Which session?" and Jarvis listens again; an answer ends it
   │  append the request to C:\…\.claude-voice\inbox\<distro>__<id>.msg
   ▼
 "Sent to db upgrade."                                  then the lock is released
   ▼
 hook-inbox in that session (any distro) sees the file within 0.5 s, prints it, exits 2
   ▼
 Claude Code wakes the session with the request ─► Claude works ─► turn ends
   ├─ hook-stop: reply ─► speechify (the reply, cleaned for speech) ─► say ─► Piper ─► speakers   ("From B: …" if several)
   │     (VOICE_FOLLOWUP=1 only: the listener then asks "Anything else for B?" and listens again)
   └─ hook-inbox: starts waiting again
```

A request sent while the session is busy waits in the inbox; the waiter started at the end of that turn picks it up
immediately. Replies from several sessions queue on the lock and are read one after another, never two at once and never while
you are talking to Jarvis, with a 5 s pause between them (`VOICE_REPLY_GAP`); a conversation you start in that pause
goes first. "Hey Claude" stops the one being read; it is read again from the start only if you then send a request to a
session, otherwise (stop, never mind, a question to Jarvis, silence) it and the queued ones are dropped, as with
`voice stop`. The words you say right after the wake word are found in the transcript even when the reply's own
voice came first.

Talking over Jarvis's own questions cuts them short: while a prompt plays, the listener keeps measuring the
microphone; a level that stays above three times the prompt's own echo (and the speech threshold) for 0.3 s counts as
you, stops the prompt and becomes the start of your answer. A voice quieter than the echo (loud speakers, quiet mic)
does not interrupt; `claude-voice log` shows `prompt echo median …, max …` for every prompt to tune by.

### Cleanup

| Event | Who cleans up |
|---|---|
| you exit Claude, or `/clear` | `hook-end` removes the registry entry, inbox and voice flag |
| Claude crashes while idle | the waiting `hook-inbox` sees its pid vanish and does the same |
| `wsl --shutdown`, crash mid-turn | `sessions` (run by `claude-voice start` and `status`) drops entries whose distro is stopped or whose pid no longer belongs to that session |

After `claude --resume` or `/clear`, type `/speak on` again.

### State

Per distro, `~/.claude/voice/`: `sessions/<id>` (voice-on flags), `muted`, `current_session`, `focus`,
`listen.pid`, `listen.log`, `cache/` (pre-rendered prompts). Shared, in the Windows user folder: the registry, the
inboxes, the lock and the stop file shown above. All of it is disposable: delete it and it is recreated on the next run
(you would type `/speak on` again).

## macOS

Written for macOS but not yet tried on a Mac. The same scripts run there through a small platform layer in
`bin/lib.sh` (`PLATFORM=mac`):

| | WSL | macOS |
|---|---|---|
| microphone | `ffmpeg -f pulse -i RDPSource` | `ffmpeg -f avfoundation -i :0` (`VOICE_MIC` = device index; list them with `ffmpeg -f avfoundation -list_devices true -i ""`) |
| playback | Windows-side player (`windows/player.ps1`) | `afplay` |
| mic during playback | open only while the Windows player runs | always open (barge-in works) |
| Whisper | faster-whisper on CUDA | faster-whisper on the CPU; `VOICE_WHISPER_BACKEND=mlx` uses the Apple Silicon GPU (mlx-whisper, installed on arm64) |
| shared state | `C:\Users\<you>\.claude-voice*` (all distros) | `~/.claude/voice/shared`, `~/.claude/voice/speech.lock` |
| GNU tools | built in | Homebrew `coreutils` (lib.sh puts its gnubin first on PATH, also for hooks), `flock` |
| system voice without Piper | Windows SAPI | macOS `say` |
| autostart | `windows/register-autostart.ps1` | `mac/register-autostart.sh` (launchd agent) |

macOS asks for microphone access the first time the terminal (or the launchd agent) records. `claude-session`
(tmux-hosted sessions) is not supported there. Plain Linux (PulseAudio/PipeWire) takes the same paths as macOS except
for audio, which goes through PulseAudio.

## Commands

| Command | Purpose |
|---|---|
| `claude-voice home <dir>` | create a private folder for Jarvis's Claude brain: a CLAUDE.md template for your notes, a copy of your voice settings, a git repo to push to a private remote |
| `claude-voice config [KEY [VALUE]]` | show or change settings in `~/.claude/voice/config` (restarts the listener) |
| `claude-voice start\|stop\|status\|register\|mic-test\|log\|update` (stop also ends the Windows speech player and unloads the brain model from the GPU, to save battery) | front door: listener, state, set up a distro without the listener, pull the latest version |
| `voice on\|off\|status\|mute\|unmute [session_id]` | per-session state (what `/speak` calls) |
| `voice stop` | stop the reply being read, and the ones queued behind it, in every distro |
| `voice sessions`, `voice focus <name>` | voice sessions across all distros, the default target |
| `voice listen [--daemon\|--status\|--stop]`, `voice mic-test` | the listener and the microphone check |
| `say "text"`, `echo … \| say`, `say -f file.md` | speak text (markdown stripped) |
| `play file.wav` | play a WAV through the Windows player (or WSLg) and wait for it |
| `speechify < reply.md` | the spoken-summary rewrite via ollama |
| `recent-sessions [--query …] [--project …] [--limit N] [--all-distros]`, `--top-projects N` | past sessions (newest or best match) and your most used folders, as JSON lines |
| `session-open <dir> <name> [request] [--distro D] [--resume ID] [--dry-run]`, `session-close <key>` | what "start/close a session" runs after you confirm |
| `dispatch --text "…"`, `dispatch --state`, `sessions` | how a sentence would be routed; sessions and focus; the session list |
| `brain "…"`, `brain --warm` | the local brain's decision as JSON; load its model |
| `voice jarvis on\|off` | what `/speak jarvis` calls |
| `claude-session <name> [claude args]` | older alternative: run Claude inside a tmux session and type requests into its pane |

## Tuning

| Variable | Default | Meaning |
|---|---|---|
| `VOICE_VAD` | `0.5` | how sure the voice detector (Silero VAD) must be that a loud sound is a person speaking; noise, thuds and hum score near 0. `0` = loudness only |
| `VOICE_SILENCE` | `2.5` | seconds of quiet that end a spoken request; raise it if Jarvis cuts you off |
| `VOICE_MAX_RECORD` | `45` | longest request in seconds |
| `VOICE_FIRST_WAIT` | `8` | how long Jarvis waits for your first answer after the session list; then "I didn't hear anything" |
| `VOICE_WAIT` | `3.5` | silence after a later Jarvis prompt ("Which session?") that ends the conversation |
| `VOICE_FOLLOWUP=1` | off | after a spoken request's reply is read, ask "Anything else for <session>?" and listen (confusing with several sessions, hence off) |
| `VOICE_WAKE` | `hey claude` | the wake phrase: any words (spotted by Whisper) or a bundled openWakeWord model name (`hey_jarvis`, `alexa`, …) |
| `VOICE_WAKE_THRESHOLD` | `0.5` | openWakeWord models only: sensitivity; lower is more sensitive |
| `VOICE_WAKE_THRESHOLD_PLAYING` | `0.4` | the same while a session's reply is playing (its echo masks your voice); near misses are logged |
| `VOICE_REPLY_GAP` | `5` | seconds of quiet between two session replies, to tell them apart and say "hey Jarvis" in between (Jarvis's own prompts are not delayed) |
| `VOICE_REPLY_VOLUME` | `0.6` | volume of session replies (Jarvis's own prompts stay at full volume); quieter replies let "hey Jarvis" through |
| `VOICE_WHISPER_MODEL`, `VOICE_WHISPER_DEVICE` | `small`, `cuda` | dictation model and device |
| `VOICE_MIC` | `RDPSource` | PulseAudio source |
| `VOICE_INBOX_POLL` | `1` | seconds between inbox checks in a waiting session |
| `SAY_SPEED` | `1.0` | Piper length scale; lower is faster |
| `SAY_VOICE` | `~/.local/share/piper/en_US-lessac-medium.onnx` | another Piper voice |
| `SPEECHIFY_MODEL` | `gemma4:e4b` | ollama model for the summary (see `dev/bench-models`) |
| `JARVIS_MODEL` | `SPEECHIFY_MODEL` | ollama model for the local brain; one model for both avoids swapping on an 8 GB card |
| `JARVIS_TIMEOUT`, `JARVIS_KEEP_ALIVE` | `8`, `5m` | give up on the brain after this many seconds; how long ollama keeps the model loaded |
| `JARVIS_BRAIN=0` | | no local brain: unmatched sentences go straight to the focused session |
| `SPEECHIFY_MODE` | `read` | `read`: the reply word for word, cleaned for speech (instant); `summary`: retold by the ollama model |
| `SPEECHIFY_MAX_WORDS`, `SPEECHIFY_MIN_CHARS` | `200`, `350` | summary mode only: length of the retelling; shorter replies are read as is |
| `SAY_MAX_CHARS` | `2400` | longest text read aloud (about 400 words); the rest "is on screen" |
| `SPEECHIFY_DISABLE=1` | | read the stripped reply verbatim |
| `SPEECHIFY_RETRY` | `60` | seconds to skip ollama after a failed or timed-out call |
| `VOICE_PLAYER` | `windows` | `wsl` plays through WSLg only (the mic is then closed during speech) |
| `VOICE_NUM_CTX` | `4096` | ollama context size for both brain and summaries (the same value avoids reloads) |
| `SAY_LOCK_WAIT` | `180` | seconds a reply waits for the speech lock (a conversation with Jarvis holds it) before playing anyway |
| `VOICE_BARGE_IN=0` | | do not stop Jarvis's questions when you talk over them |
| `VOICE_WHISPER_BACKEND` | `faster` | `mlx`: Whisper on the Apple Silicon GPU (mlx-whisper) |
| `CLAUDE_VOICE_PLATFORM` | detected | force `wsl`, `mac` or `linux` |
| `VOICE_PROJECTS` | `~/projects` | where "start a session in <project>" looks for the folder (colon-separated) |
| `VOICE_DEAD_MIC_LEVEL` | `10` | while waiting for your answer, 2 s with nothing louder than this is a dead microphone: reopened, and you are asked again |
| `VOICE_PLAYER_IDLE` | `600` | (Windows env) seconds the Windows speech player stays without a clip once no listener runs |
| `VOICE_SHARED`, `VOICE_LOCK` | your Windows user folder | where the shared state lives |

Set listener variables when starting it, e.g. `VOICE_SILENCE=3.5 claude-voice start`. GPU budget: Whisper `small`
holds about 1 GB of an 8 GB card. `dev/bench-models <model> …` scores models on Jarvis's real jobs (routing accuracy
on 20 labelled sentences, latency, spoken summaries). On an RTX 5070 8 GB with ollama 0.35 and the listener running:

| model | routing accuracy | typical brain time | load |
|---|---|---|---|
| `gemma4:e4b` (default) | 100 % | 0.46 s | 13 s |
| `qwen3.5:4b` | 90 % | 0.57 s | 5 s |
| `qwen3.5:9b` | 95 % | 1.29 s | 24 s |

`gemma4:e4b` fits entirely in VRAM next to Whisper from ollama 0.35 on (older versions kept most of it in system RAM,
and the brain took 3 to 6 s).

## Logs

`claude-voice log` follows `~/.claude/voice/listen.log`: one dated line per event, tagged with the conversation it
belongs to, ending with where the time went:

```
2026-10-05 15:39:01 config: platform wsl, mic default, silence 2.5s, first wait 8.0s, wait 3.5s, barge-in on, brain gemma4:e4b
2026-10-05 15:39:01 [153901] wake (0.69)
2026-10-05 15:39:02 [153901] heard: How many Claude sessions are open, and which one is busy?
2026-10-05 15:39:06 [153901] dispatch: answer | Four sessions are open: …
2026-10-05 15:39:06 [153901] done: prompt 2.1s, listen 0.5s, whisper 0.2s, dispatch 3.7s; total 6.5s
```

Problems carry a level (`WARN`, `ERROR`, with a traceback for a failed conversation, after which the listener simply
carries on). ffmpeg's harmless WSLg timestamp warnings are summarized once an hour instead of logged one by one. The
log rolls over to `listen.log.1` past 5 MB (`VOICE_LOG_MAX`). Spoken replies have their own step log in
`~/.claude/voice/speech.log`.

## Battery

Nothing runs in the background until you start it. `claude-voice start` runs the listener (the wake-word model on the
CPU, about 2 % of a core; Whisper resident on the GPU) and the Windows speech player; `claude-voice stop` ends both and
unloads the brain's model from the GPU. Otherwise:

- the Windows player starts only when something has to be said and quits after 10 idle minutes when no listener runs;
- each voice-on session's inbox hook checks once a second with shell builtins only;
- ollama drops the brain's model from the GPU 5 minutes after its last use (`JARVIS_KEEP_ALIVE`);
- the "hey claude" phrase wake runs Whisper on every burst of speech in the room (noise is skipped by the voice
  detector); a bundled openWakeWord model (`claude-voice config VOICE_WAKE hey_jarvis`) is the lighter choice.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "Hey Claude" then nothing, you have to call it twice | `claude-voice log`: `no speech for 8 s (loudest …, threshold …)` shows whether your voice stayed under the threshold (raise your voice or lower the capped threshold) or nobody spoke in time (`VOICE_FIRST_WAIT`) |
| "Hey Jarvis" is hard to get through while a reply plays | the speakers' echo is louder than your voice (`prompt echo … max` in the log). Lower `VOICE_REPLY_VOLUME` or `VOICE_WAKE_THRESHOLD_PLAYING`; `near miss during a reply` lines show how close it got |
| you often have to say the wake word twice | `claude-voice log`: `microphone dead while listening` / `stalled` lines are the WSLg capture dropping out (it is reopened and you are asked again); `near miss: wake score` lines are wake words that almost made it (lower `VOICE_WAKE_THRESHOLD`, e.g. 0.45) |
| "Hey Claude" does nothing | `claude-voice log`: no `wake` lines means the mic is silent. `voice mic-test`; if silent, `wsl --shutdown` in PowerShell (closes every distro) or check Windows microphone privacy |
| speech breaks up every few words | it is playing through WSLg while something records: is the Windows player up? `ls -l C:\Users\<you>\.claude-voice\player.alive` must be seconds old; `claude-voice start` starts it |
| clicks, stalls, a clip is missing | WSLg audio hiccup: playback gives up after the clip length + 5 s (`audio playback timed out` in the log). Persistent: `wsl --shutdown` |
| `/speak on` says the hook did not run | `claude-voice update`, then start a new session or open `/hooks` |
| request delivered but the session does nothing | the session's hooks predate the install: start it again; `sessions` should list it |
| replies are spoken late, or one turn behind | `~/.claude/voice/speech.log` timestamps every step of each reply (hook start, summary, lock, played). The hook takes the reply from Claude Code's `last_assistant_message`, not the transcript, which may not have the final message yet |
| replies are spoken minutes late | something held the speech lock: `claude-voice log` (the listener now reopens a dead microphone capture and is restarted if it exits); a reply waits at most `SAY_LOCK_WAIT` |
| a closed session is still listed | `claude-voice status` prunes it |
| a question went to a session instead of being answered | `brain "<the sentence>"` shows the decision; if ollama is down it is `exit 1` and the focused session gets it |
| Jarvis mishears names | `/rename` the session to plain words; "Claude" is primed, other jargon is not |

## Known limits

- One listener, one microphone: run the listener in a single distro.
- The inbox waiter gives up after a day without a turn; the next turn starts it again.
- Anything that can write to `C:\Users\<you>\.claude-voice\inbox` can put a prompt into a voice-on session: the same
  trust boundary as the rest of that folder (your Windows user).
- Windows SAPI is used only when Piper is missing; PowerShell can take 15 s to start, which is why the player is one
  long-running process.
- Dictation and phrase spotting are English. The phrase spotter can be woken by speech that sounds like the phrase.

## Development

`tests/run` runs the test suite (128 tests, under a minute): routing of spoken sentences, past-session search, the
hooks and their registration, the launcher, speech queueing (interrupt, replay, stop, the listening mark), the local
brain and summary mode against a fake ollama, the supervised listener, the commands, and whole conversations with
the listener driven by a fake microphone and a fake Whisper. It is offline and isolated: every test works in a
throwaway home, with stand-ins for piper, the audio player, ollama and listen.py where needed, and never touches your
real sessions, settings or listener. GitHub Actions runs it on every push. Not covered, and checked by hand: real
Windows Terminal tabs, the Windows player, two WSL distros at once, and macOS. `dev/bench-models`
scores ollama models for the brain.

## Layout

`install.sh` (curl entry point) · `bin/` (all commands; `setup` wires the machine; `listen.py` is the listener) ·
`skills/speak/` (the `/speak` skill copied to `~/.claude/skills`) · `windows/register-autostart.ps1` ·
`mac/register-autostart.sh` · `templates/home-CLAUDE.md` (what `claude-voice home` starts from) · `tests/` (the test suite) · `requirements.txt` (listener venv; `-cuda` with an NVIDIA GPU, `-mac` on Apple
Silicon) · `CLAUDE.md` (notes for working on the code).

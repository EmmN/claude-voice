#!/usr/bin/env python3
"""Always-on voice listener: wake phrase (VOICE_WAKE, default "hey claude") → record the request → Whisper on the GPU → dispatch to a
Claude Code session → spoken acknowledgement. Run through the `listen` wrapper (venv + CUDA libs)."""
import argparse, fcntl, hashlib, json, os, re, signal, subprocess, sys, threading, time, traceback, warnings, wave
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PLATFORM = os.environ.get("PLATFORM", "wsl")      # set by lib.sh (the `listen` wrapper): wsl, mac or linux
RATE, FRAME = 16000, 1280                      # openWakeWord wants 80 ms frames at 16 kHz
SILENCE_TO_STOP = float(os.environ.get("VOICE_SILENCE", "2.5"))   # pause that ends a request; short pauses mid-sentence are fine
MIN_SPEECH, MAX_RECORD = 0.6, float(os.environ.get("VOICE_MAX_RECORD", "45"))
NO_SPEECH = float(os.environ.get("VOICE_WAIT", "3.5"))   # silence after a prompt that ends the conversation
FIRST_WAIT = float(os.environ.get("VOICE_FIRST_WAIT", "8"))   # … after the opening list of sessions
BARGE_IN = os.environ.get("VOICE_BARGE_IN", "1") != "0"
PLAYING_THRESHOLD = float(os.environ.get("VOICE_WAKE_THRESHOLD_PLAYING", "0.4"))   # openWakeWord during a reply        # talking over Jarvis's questions cuts them short

warnings.filterwarnings("ignore", message=".*CUDAExecutionProvider.*")   # onnxruntime: openWakeWord runs on the CPU
CONV = ""   # id of the conversation in progress, prefixed to its log lines
WAKE_NAME = ["hey claude"]   # the wake phrase, for log lines

def log(msg, level="info"):
    """One line per event: date, time, level (when not info), conversation id. `claude-voice log` follows it."""
    tag = "" if level == "info" else f"{level.upper()} "
    print(time.strftime("%Y-%m-%d %H:%M:%S ") + tag + (f"[{CONV}] " if CONV else "") + msg, flush=True)

class Timer:
    """Step timings for one conversation: mark("whisper") records the seconds since the previous mark."""
    def __init__(self): self.t0 = self.last = time.time(); self.steps = []
    def mark(self, name):
        now = time.time(); self.steps.append(f"{name} {now - self.last:.1f}s"); self.last = now
    def summary(self): return ", ".join(self.steps) + f"; total {time.time() - self.t0:.1f}s"


CACHE = os.path.expanduser("~/.claude/voice/cache")
PIPER_VOICE = os.environ.get("SAY_VOICE", os.path.expanduser("~/.local/share/piper/en_US-lessac-medium.onnx"))

def prompt_wav(text, cache=True):
    """The prompt as a WAV: each distinct text is synthesized once and kept, so the greeting plays immediately. The
    voice and speed are part of the key: after SAY_VOICE changes, cached prompts must not keep the old voice."""
    os.makedirs(CACHE, exist_ok=True)
    key = f"{os.path.basename(PIPER_VOICE)}|{os.environ.get('SAY_SPEED', '1.0')}|{text}"
    wav = os.path.join(CACHE, (hashlib.sha1(key.encode()).hexdigest()[:16] if cache else f"once-{os.getpid()}") + ".wav")
    if not cache and os.path.exists(wav): os.remove(wav)
    if not os.path.exists(wav):
        tmp = f"{wav}.{threading.get_ident()}.tmp"   # the background warm-up may render the same prompt at once
        r = subprocess.run(["piper", "-m", PIPER_VOICE, "--length-scale", os.environ.get("SAY_SPEED", "1.0"), "-f", tmp],
                           input=text.encode(), capture_output=True)
        if r.returncode != 0: return None
        os.replace(tmp, wav)
    return wav

def prompt(text, cache=True):
    """Speak a short prompt while we hold the speech lock (so not through `say`, which takes it)."""
    wav = prompt_wav(text, cache)
    if not wav: chime(); return
    subprocess.run([os.path.join(HERE, "play"), wav], check=False, stdin=subprocess.DEVNULL)
    if not cache: os.remove(wav)

# fixed prompts, synthesized in the background at start so the first "Yes?" after a voice change plays at once
COMMON_PROMPTS = [
    "Yes?", "OK.", "Anything else?", "I didn't hear anything.", "What should I tell it?", "Let me think about that.",
    "Sent to all sessions.", "I could not deliver that.", "I didn't get which one.", "In which project?",
    "OK, no new session.", "OK, I won't resume it.", "OK, I won't open it.", "I found no earlier sessions there.",
    "No sessions.", "Yes? No session has voice on yet. You can start or resume one.",
    "No session has voice on. Say start a session, or resume one, or type slash speak on in a Claude session.",
]

def warm_prompts():
    def run():
        for text in COMMON_PROMPTS:
            try: prompt_wav(text)
            except Exception as e: log(f"prompt cache: {text!r}: {e}", "warn"); return
    threading.Thread(target=run, daemon=True).start()

def warm_brain():
    """Load the local brain's ollama model in the background (it is unloaded when idle; loading takes seconds)."""
    subprocess.Popen([os.path.join(HERE, "brain"), "--warm"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def mic_safe(shared):
    """Can the mic stay open while we play audio? Yes on macOS and plain Linux. On WSL only while windows/player.ps1
    runs (speech then goes out through Windows): WSLg breaks playback up while a recording stream is open in the same
    distro, so without the player the mic is closed during speech (and barge-in is off)."""
    if PLATFORM != "wsl": return True
    try: return time.time() - os.path.getmtime(os.path.join(shared, "player.alive")) <= 3
    except OSError: return False

def mic_input(mic):
    """ffmpeg input arguments for the microphone (the same choice as mic_input_args in lib.sh)."""
    if PLATFORM == "mac": return ["-f", "avfoundation", "-i", f":{mic or '0'}"]
    return ["-f", "pulse", "-i", mic or ("RDPSource" if PLATFORM == "wsl" else "default")]

def state():
    r = subprocess.run([os.path.join(HERE, "dispatch"), "--state"], capture_output=True, text=True)
    try: return json.loads(r.stdout)
    except Exception: return {"sessions": [], "focus": ""}

def humanize(title, words=8):
    t = re.sub(r"[_-]+", " ", title).strip()
    w = t.split(); return " ".join(w[:words]) + ("…" if len(w) > words else "")

def when(ts):
    days = (time.time() - ts) / 86400
    if days < 1 and time.localtime(ts).tm_mday == time.localtime().tm_mday: return "today"
    if days < 2: return "yesterday"
    if days < 7: return time.strftime("%A", time.localtime(ts))
    return time.strftime("%B %-d", time.localtime(ts))

NUMBERS = {"one": 1, "first": 1, "1": 1, "two": 2, "second": 2, "2": 2, "three": 3, "third": 3, "3": 3,
           "four": 4, "fourth": 4, "4": 4, "five": 5, "fifth": 5, "5": 5}

def pick(answer, items):
    """Which of the read-out sessions did you choose: by number ("two", "the second one") or by words of its title."""
    import difflib
    said = [w for w in re.findall(r"[a-z0-9]+", answer.lower()) if w not in {"the", "one", "session", "resume", "open", "please", "number"}]
    if not [w for w in said if w not in NUMBERS]:      # only a number ("two", "the second one", "number 3")
        for w in re.findall(r"[a-z0-9]+", answer.lower()):
            if w in NUMBERS and NUMBERS[w] <= len(items): return items[NUMBERS[w] - 1]
        return None
    best, score = None, 0.0
    for it in items:
        have = re.findall(r"[a-z0-9]+", humanize(it["title"], 99).lower())
        sc = sum(max((difflib.SequenceMatcher(None, w, h).ratio() for h in have), default=0) for w in said) / max(1, len(said))
        if sc > score: best, score = it, sc
    return best if score >= 0.7 else None

def spoken_list(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

def chime():
    wav = os.path.join(CACHE, "chime.wav")      # rendered once, then played like any prompt (Windows player or WSLg)
    if not os.path.exists(wav):
        os.makedirs(CACHE, exist_ok=True)
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=880:duration=0.18",
                        "-ar", "22050", "-ac", "1", "-y", wav], check=False)
    subprocess.run([os.path.join(HERE, "play"), wav], check=False, stdin=subprocess.DEVNULL)

VAD_MIN = float(os.environ.get("VOICE_VAD", "0.5"))   # speech probability a loud frame needs to count as you talking
_vad = None
def speech_prob(frame):
    """How likely this 80 ms frame is human speech (Silero VAD, shipped with openWakeWord; ~0.3 ms on the CPU). Thuds,
    clicks and hum score near 0, a voice near 1. 1.0 when the model is unavailable or VOICE_VAD=0: loudness alone."""
    global _vad
    if VAD_MIN <= 0: return 1.0
    if _vad is None:
        try:
            from openwakeword.vad import VAD; _vad = VAD()
        except Exception: _vad = False
    return float(_vad.predict(frame, frame_size=640)) if _vad else 1.0

def is_voice(frame, threshold, level=None):
    """Loud enough AND sounds like a person: background noise neither starts nor prolongs anything."""
    level = rms(frame) if level is None else level
    return level > threshold and speech_prob(frame) >= VAD_MIN

def rms(frame):
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2))) if len(frame) else 0.0

class Lock:
    """The same flock `say` uses: held while a reply is spoken, and by us from wake to dispatch, so the listener
    never hears the speakers as a command and a reply never starts mid-sentence."""
    def __init__(self, path): self.fd = open(path, "a+")
    def held_by_someone(self):
        try: fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError: return True
        fcntl.flock(self.fd, fcntl.LOCK_UN); return False
    def acquire(self, wait=3.0):
        """Take the lock; after `wait` seconds go ahead without it (a stuck player elsewhere must not block us)."""
        t0 = time.time()
        while True:
            try: fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB); return True
            except OSError:
                if time.time() - t0 > wait: return False
                time.sleep(0.05)
    def release(self): fcntl.flock(self.fd, fcntl.LOCK_UN)
    def interrupt(self, why="wake"):
        """Stop whatever is being spoken, in every distro (`say`, `play` and the Windows player watch this file).
        "wake"/"barge" only pause a session's reply: `say` plays it again after the conversation."""
        stop = (self.fd.name[:-5] if self.fd.name.endswith(".lock") else self.fd.name) + ".stop"
        with open(stop, "w") as f: f.write(why)

class Source:
    """The microphone: ffmpeg capture read by a background thread into a queue of 80 ms frames, so the capture never
    stalls while the main thread is busy speaking or transcribing (a full pipe made it deliver nothing usable after
    Jarvis talked). frame() takes the next frame; drain() drops what piled up (Jarvis's own voice); a capture that
    dies is reopened by the thread. pause()/resume() close and reopen it (only for WSLg playback, which breaks up
    while a recording stream is open in the same distro)."""
    def __init__(self, wav=None, mic=None):
        if wav:
            w = wave.open(wav); assert w.getframerate() == RATE and w.getnchannels() == 1, "need 16 kHz mono wav"
            self.data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16); self.pos = 0; self.mic = None; self.wav = True
            return
        import queue, threading
        self.mic, self.proc, self.paused = mic or "", None, False
        self.q = queue.Queue(maxsize=int(30 * RATE / FRAME))       # 30 s; the oldest frames go first when full
        self.resume()
        threading.Thread(target=self._reader, daemon=True).start()
    def _open(self):
        # tagged (metadata) so `listen --stop` finds the capture on every platform, and nothing else
        self.proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error"] + mic_input(self.mic) +
                                     ["-ac", "1", "-ar", str(RATE), "-metadata", "comment=claude-voice-capture", "-f", "s16le", "-"],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=self._stderr, args=(self.proc,), daemon=True).start()
    def _stderr(self, proc):
        """ffmpeg's messages. WSLg's capture keeps producing 'non monotonically increasing dts' (harmless timestamp
        jitter, thousands an hour): those are counted and summarized once an hour; anything else is logged."""
        jitter, since = 0, time.time()
        for raw in proc.stderr:
            line = raw.decode(errors="replace").strip()
            if "non monotonically increasing dts" in line: jitter += 1
            elif line and "Immediate exit requested" not in line and "Last message repeated" not in line and "Error muxing" not in line and "Error writing trailer" not in line \
                    and "Error closing file" not in line and "Error submitting a packet" not in line:
                log("ffmpeg: " + line[:200], "warn")
            if jitter and time.time() - since >= 3600:
                log(f"microphone: {jitter} timestamp-jitter warnings from ffmpeg in the last hour (harmless)")
                jitter, since = 0, time.time()
    def _reader(self):
        import queue
        while True:
            proc = self.proc
            if proc is None: time.sleep(0.05); continue
            b = proc.stdout.read(FRAME * 2)
            if len(b) == FRAME * 2:
                f = np.frombuffer(b, dtype=np.int16)
                try: self.q.put_nowait(f)
                except queue.Full:
                    try: self.q.get_nowait()
                    except queue.Empty: pass
                    self.q.put_nowait(f)
                continue
            if self.paused or proc is not self.proc: continue      # closed on purpose
            log("microphone capture ended; reopening it")
            proc.wait(); time.sleep(0.2)                       # short: you are deaf until it is back
            if not self.paused: self._open()
    def pause(self):
        if getattr(self, "wav", False): return
        self.paused = True
        if self.proc: p, self.proc = self.proc, None; p.terminate(); p.wait()
    def resume(self):
        if getattr(self, "wav", False): return
        self.paused = False
        if self.proc is None: self._open()
    def drain(self):
        if getattr(self, "wav", False): return
        import queue
        try:
            while True: self.q.get_nowait()
        except queue.Empty: pass
    def frame(self):
        if getattr(self, "wav", False):
            if self.pos >= len(self.data): return None
            f = self.data[self.pos:self.pos + FRAME]; self.pos += FRAME
            return np.pad(f, (0, FRAME - len(f)))
        import queue
        try: return self.q.get(timeout=2)
        except queue.Empty:
            # no audio for 2 s: paused, reopening, or a capture that stalled without ending (WSLg does that); end a
            # stalled one so the reader thread reopens it
            proc = self.proc
            if not self.paused and proc is not None and proc.poll() is None:
                log("microphone stalled (no audio for 2 s); reopening it", "warn"); proc.terminate()
            return np.zeros(FRAME, dtype=np.int16)

class PhraseSpotter:
    """Wake on any phrase, no trained model: cut the microphone stream into bursts of speech (level above the speech
    threshold, ending after 0.45 s of quiet or at 2.6 s) and run each burst through the already loaded Whisper. A burst
    whose first words sound like the phrase ("hey claude", also heard as "hey cloud") is a wake. feed() returns
    (rest, frames, still_talking): the words said after the phrase, the burst's audio, and whether you kept talking."""
    def __init__(self, phrase, transcribe):
        import collections, difflib
        self.words = re.findall(r"[a-z']+", phrase.lower()); self.hint = phrase.strip().capitalize() + "."
        self.transcribe, self.difflib = transcribe, difflib
        self.pre = collections.deque(maxlen=4); self.buf = []; self.silent = 0.0; self.active = False; self.loud = 0
    def reset(self): self.pre.clear(); self.buf = []; self.silent = 0.0; self.active = False; self.loud = 0
    def match(self, text):
        """→ the text after the phrase, or None when the burst does not start with it (a word or two of lead-in is fine)."""
        found = list(re.finditer(r"[A-Za-z']+", text)); low = [m.group(0).lower() for m in found]; k = len(self.words)
        for i in range(0, min(3, max(1, len(low) - k + 1))):
            said = " ".join(low[i:i + k])
            if len(low) >= i + k and self.difflib.SequenceMatcher(None, said, " ".join(self.words)).ratio() >= 0.75:
                return text[found[i + k - 1].end():].lstrip(" ,.!?;:-") if len(found) > i + k - 1 else ""
        return None
    def feed(self, f, threshold):
        lv = rms(f)
        if not self.active:
            self.pre.append(f)
            if is_voice(f, threshold, lv): self.active, self.buf, self.silent, self.loud = True, list(self.pre), 0.0, 1
            return None
        self.buf.append(f)
        if is_voice(f, threshold, lv): self.silent, self.loud = 0.0, self.loud + 1
        else: self.silent += FRAME / RATE
        dur = len(self.buf) * FRAME / RATE
        if self.silent < 0.45 and dur < 2.6: return None
        frames, talking, loud = self.buf, self.silent < 0.45, self.loud
        self.reset()
        if loud * FRAME / RATE < 0.3: return None                     # a click or a cough: under 0.3 s of actual sound
        text = self.transcribe(np.concatenate(frames).astype(np.float32) / 32768.0, self.hint)
        rest = self.match(text)
        return None if rest is None else (rest, frames, talking)

def strip_wake(text, phrase, anywhere=False):
    """Drop the wake phrase (or what is left of it: "Jarvis, …", "…vis, …") from the start of a transcript. With
    `anywhere` (a wake that interrupted a reply: the audio before it is Jarvis's own voice) the last place the phrase
    is heard counts, wherever it is."""
    import difflib
    found = list(re.finditer(r"[A-Za-z']+", text)); low = [m.group(0).lower() for m in found]
    words = re.findall(r"[a-z']+", phrase.lower().replace("_", " "))
    for k in (len(words), 1):                    # the whole phrase, else just its last word
        want = " ".join(words[-k:])
        # the audio may start a few words before the phrase (the ring buffer of an openWakeWord wake)
        for i in (reversed(range(len(low))) if anywhere else range(0, min(8, len(low)))):
            if len(low) >= i + k and difflib.SequenceMatcher(None, " ".join(low[i:i + k]), want).ratio() >= (0.7 if k > 1 else 0.6):
                return text[found[i + k - 1].end():].lstrip(" ,.!?;:-")
    return text

def is_wake_fragment(text):
    """Nothing but (part of) the wake phrase: "Jarvis.", "jar", "hey". A request it would waste a turn on."""
    words = re.findall(r"[a-z']+", text.lower())
    wake = re.findall(r"[a-z']+", WAKE_NAME[0].lower())
    return 0 < len(words) <= 2 and all(w in wake or any(len(w) >= 3 and x.startswith(w) for x in wake) for w in words)

def keeps_talking(src, noise_floor, window=1.2):
    """Right after an openWakeWord wake: do you go on talking ("hey Jarvis, ask mobile app to …")? Listens up to
    `window` seconds → (True, frames) as soon as there are 0.25 s of speech after a pause, else (False, frames)."""
    threshold = min(max(120.0, noise_floor * 3), 900.0)
    frames, quiet, loud, ended, t = [], 0.0, 0, False, 0.0
    while t < window:
        f = src.frame(); frames.append(f); t += FRAME / RATE
        if is_voice(f, threshold):
            quiet = 0.0
            if ended: loud += 1
            if loud >= 3: return True, frames
        else:
            quiet += FRAME / RATE
            if quiet >= 0.2: ended, loud = True, 0           # the wake word itself is over
    # no pause at all ("hey Jarvis how many…" in one go) but speech right up to the end of the window: still talking
    return any(is_voice(f, threshold) for f in frames[-4:]), frames

DEAD_MIC = float(os.environ.get("VOICE_DEAD_MIC_LEVEL", "10"))   # a live mic never stays this quiet for 2 s

class MicDead(Exception):
    """The microphone delivered (near) digital silence while we waited for an answer: a stalled or broken capture."""

def record_request(src, noise_floor, wait=None, prefix=None):
    """Record one answer: start counting once the level passes the speech threshold, stop after VOICE_SILENCE of
    quiet. Nobody speaking for `wait` seconds (default VOICE_WAIT) gives an empty recording; the levels are logged."""
    # time is counted in audio (80 ms per frame), not on the clock: a backlog of queued frames, a test or a wav file
    # arrive faster than real time, and the clock would run the recording past the end of what you said
    frames, speech_seen, silent, peak = list(prefix or []), bool(prefix), 0.0, 0.0
    wait = NO_SPEECH if wait is None else wait
    threshold = min(max(120.0, noise_floor * 3), 900.0)   # capped: a noisy room must not out-shout your voice
    heard = lambda: len(frames) * FRAME / RATE
    while heard() < MAX_RECORD:
        f = src.frame()
        if f is None: break                        # only a --from-wav file ends
        frames.append(f); level = rms(f); peak = max(peak, level)
        if is_voice(f, threshold, level): speech_seen, silent = True, 0.0
        else: silent += FRAME / RATE
        if speech_seen and silent >= SILENCE_TO_STOP and heard() > MIN_SPEECH: break
        if not speech_seen and heard() >= 2.0 and peak <= DEAD_MIC:
            # 2 s without even room noise: the capture is dead (WSLg), not you silent; the caller reopens and asks again
            log(f"microphone dead while listening (loudest {peak:.0f} in {heard():.1f} s)", "warn")
            raise MicDead()
        if not speech_seen and silent >= wait:              # nobody answered
            log(f"no speech for {wait:.0f} s (loudest {peak:.0f}, threshold {threshold:.0f}, floor {noise_floor:.0f})")
            return np.zeros(0, dtype=np.int16)
    return np.concatenate(frames) if frames else np.zeros(0, dtype=np.int16)

def load_whisper(model, device):
    """→ (transcribe(audio_f32, hint) -> text, where it runs). VOICE_WHISPER_BACKEND: faster (default; faster-whisper,
    CUDA when available, else CPU int8) or mlx (mlx-whisper on the Apple Silicon GPU; pip install mlx-whisper)."""
    if os.environ.get("VOICE_WHISPER_BACKEND", "faster") == "mlx":
        import mlx_whisper
        repo = model if "/" in model else f"mlx-community/whisper-{model}-mlx"
        def run(audio, hint):
            return (mlx_whisper.transcribe(audio, path_or_hf_repo=repo, language="en", initial_prompt=hint or None)
                    .get("text") or "").strip()
        return run, "mlx"
    from faster_whisper import WhisperModel
    try:
        if device != "cuda": raise RuntimeError("cpu requested")
        w = WhisperModel(model, device="cuda", compute_type="float16"); dev = "cuda"
    except Exception as e:
        log(f"cuda unavailable ({e.__class__.__name__}: {str(e)[:120]}); Whisper on the cpu (int8)", "warn")
        w = WhisperModel(model, device="cpu", compute_type="int8"); dev = "cpu"
    def run(audio, hint):
        segs, _ = w.transcribe(audio, language="en", beam_size=1, initial_prompt=hint or None)
        return " ".join(s.text for s in segs).strip()
    return run, dev

class Jarvis:
    """The listener's conversations. main() builds the real pieces (microphone Source, Whisper, wake-word model) and
    calls run(); tests build it with a fake source and transcriber and replace the seams below, which are the only
    places it reaches the outside world: say_prompt / play_interruptible (speech), beep, dispatch, run_tool."""
    def __init__(self, args, src, lock, transcribe, oww=None, wake_key=None, spotter=None):
        import collections
        self.args, self.src, self.lock, self.transcribe = args, src, lock, transcribe
        self.oww, self.wake_key, self.spotter, self.shared = oww, wake_key, spotter, args.shared
        self.floor, self.n, self.playing_until, self.last_near = 50.0, 0, 0.0, 0.0
        self.ring = collections.deque(maxlen=int(2.5 * RATE / FRAME))   # the last 2.5 s, for a request said with the wake word
        self.followup = os.path.join(self.shared, "followup")   # written by hook-stop after a spoken request's reply (opt-in)
        self.listening_mark = os.path.join(self.shared, "listening")
        self.request_sent = False
        self._speaking_mtime, self._speaking_has_wake = None, False

    def heartbeat(self):
        try: open(os.path.join(self.shared, "listener.alive"), "w").close()
        except OSError: pass

    def reply_says_wake_word(self):
        """Does the session reply being played (say writes its text to <shared>/speaking) contain the wake word? Then
        its own voice could wake us: be stricter for it instead of more sensitive."""
        p = os.path.join(self.shared, "speaking")
        try: m = os.path.getmtime(p)
        except OSError: return False
        if m != self._speaking_mtime:
            self._speaking_mtime = m
            try: self._speaking_has_wake = WAKE_NAME[0].split()[-1] in open(p).read().lower()
            except OSError: self._speaking_has_wake = False
        return self._speaking_has_wake

    # --- seams: the outside world ----------------------------------------------------------------------------------
    def say_prompt(self, text, cache=True): prompt(text, cache)
    def beep(self): chime()
    def dispatch(self, text, env):
        return subprocess.run([os.path.join(HERE, "dispatch"), text], capture_output=True, text=True, env=env)
    def run_tool(self, name, *argv):
        return subprocess.run([os.path.join(HERE, name), *argv], capture_output=True, text=True)

    def speak(self, text, cache=True, interruptible=False):
        """Our own prompts. Through WSLg the mic is closed meanwhile. With the Windows player it stays open: the audio
        heard during the prompt (mostly Jarvis's own echo) is dropped, unless `interruptible` and you start talking
        over it: then the prompt stops and the frames from just before you started are returned, to begin your answer.
        You count as talking when the level stays above 3x the prompt's own echo level (its running median) and the
        speech threshold for 4 frames (0.3 s); a voice quieter than the echo simply does not interrupt."""
        src, shared = self.src, self.shared
        if not mic_safe(shared):
            src.pause()
            try: self.say_prompt(text, cache)
            finally: src.resume()
            return None
        if not (interruptible and BARGE_IN): self.say_prompt(text, cache); src.drain(); return None
        return self.play_interruptible(text, cache)

    def play_interruptible(self, text, cache=True):
        """Play a prompt while listening; you talking over it stops it and returns the frames that start your answer."""
        src, lock, floor = self.src, self.lock, self.floor
        wav = prompt_wav(text, cache)
        if not wav: self.beep(); return None
        src.drain()
        player = subprocess.Popen([os.path.join(HERE, "play"), wav], stdin=subprocess.DEVNULL)
        recent, levels, loud, got, started = [], [], 0, None, None
        threshold = min(max(120.0, floor * 3), 900.0)
        while player.poll() is None:
            f = src.frame(); recent = (recent + [f])[-10:]; lv = rms(f); levels.append(lv)
            # the player takes a moment to start: judge only once the prompt is audible, against its echo since then
            # (counting the silent start made the echo look tiny, and Jarvis's own voice "talked over" itself)
            if started is None and lv > threshold: started = len(levels) - 1
            heard = levels[started:] if started is not None else []
            echo = float(np.median(heard[-25:])) if heard else 0.0
            # louder than the prompt's own echo AND scored as speech, 4 frames running: a cup or a door does not count
            if len(heard) > 10 and lv > max(threshold, echo * 3) and speech_prob(f) >= max(VAD_MIN, 0.6): loud += 1
            else: loud = 0
            if loud >= 4:
                # keep only what led up to your voice (more would carry the prompt's echo, e.g. a session name, into Whisper)
                lock.interrupt("barge"); got = recent[-(loud + 2):]; player.wait(); break
        if not cache and os.path.exists(wav): os.remove(wav)
        if levels: log(f"prompt echo median {float(np.median(levels)):.0f}, max {max(levels):.0f}" + (", you talked over it" if got else ""))
        if got is None: src.drain()
        return got

    def converse(self, st, opening=None, timer=None, first_text=None, first_frames=None, strip=False):
        """One conversation: say `opening` (the session list), then listen and act until a request has been sent, a
        question answered, or you say no / stay quiet. With several sessions nothing is sent until you have named one
        in this conversation (VOICE_NO_FOCUS for dispatch); naming one alone ("mobile app") asks what it should do."""
        src, lock, transcribe, oww, args, floor = self.src, self.lock, self.transcribe, self.oww, self.args, self.floor
        speak, confirm = self.speak, self.confirm
        timer = timer or Timer()
        said = speak(opening, interruptible=True) if opening else first_frames
        if opening: timer.mark("prompt")
        picked, retried_empty = len(st["sessions"]) <= 1, False
        for turn in range(6):
            # the first answer comes after a list of sessions you may need a moment to think about
            if turn == 0 and first_text:   # "hey claude, <request>" in one breath, already transcribed: no recording
                audio = None
            else:
                audio = self.listen_answer(wait=FIRST_WAIT if turn == 0 else None, prefix=said); said = None
                if oww: oww.reset()
                if audio is None: return               # the microphone stayed dead and you were told
            timer.mark("listen")
            if audio is None:
                text = first_text; first_text = None
                if not args.no_ack: self.beep()
            else:
                if len(audio) < RATE * 0.5:
                    if turn == 0 and not args.no_ack: speak("I didn't hear anything.")
                    return
                if not args.no_ack: self.beep()          # the beep: heard you, working on it
                hint = "Claude Code sessions: " + ", ".join(st["sessions"]) + "."   # spells "Claude" (not "cloud") and the names
                text = transcribe(audio.astype(np.float32) / 32768.0, hint); timer.mark("whisper")
                if turn == 0 and strip:                  # the recording began with the wake phrase itself
                    text = strip_wake(text, WAKE_NAME[0])
            log(f"heard: {text}")
            if not text.strip():                       # sound, but no words (echo, a cough): ask once, then let go
                if retried_empty: return
                retried_empty = True; said = speak("Sorry, say that again?", interruptible=True); continue
            if is_wake_fragment(text):                   # "Jarvis.", "jar": only the wake word again, no request
                said = speak("Yes?", interruptible=True); continue
            env = os.environ | ({} if picked else {"VOICE_NO_FOCUS": "1"})
            r = self.dispatch(text, env)
            parts = [p.strip() for p in (r.stdout or "").strip().split("|")]
            out = parts[0]
            timer.mark("dispatch"); log(f"dispatch: {' | '.join(parts)}")
            if r.returncode not in (0, 1) or (r.stderr or "").strip():
                log(f"dispatch exit {r.returncode}: {(r.stderr or '').strip()[:300]}", "warn")
            if args.no_ack: return
            if out == "cancel":
                if re.search(r"\b(stop|quiet|shut up|silence|enough|hush)\b", text, flags=re.I):
                    # "hey Jarvis, stop" / "be quiet": drop the reply that was interrupted and the ones queued behind
                    # it ("stop" in the stop file; `say` skips its replay). "never mind" only ends the conversation.
                    lock.interrupt("stop"); log("stop: interrupted and queued replies dropped")
                speak("OK."); return
            if out.startswith("focus ") and len(parts) == 1:
                picked = True; said = speak(f"OK, {out[6:]}. What do you want me to do?", interruptible=True); continue
            if out == "answer": speak(parts[1] if len(parts) > 1 else "I have no answer.", cache=False); return
            if out == "pickproject":           # "start a new session" without a project: offer the most used folders
                info = json.loads(parts[1]); top = [t["project"].replace("-", " ").replace("_", " ") for t in info["top"]]
                q = f"Which project? Most used: {spoken_list(top).replace(' and ', ' or ')}. Or name any other folder." if top else "In which project?"
                said = speak(q, interruptible=True)
                audio = self.listen_answer(wait=FIRST_WAIT, prefix=said)
                if audio is None: return
                if len(audio) < RATE * 0.4: speak("OK, no new session."); return
                self.beep(); answer = transcribe(audio.astype(np.float32) / 32768.0, "Projects: " + ", ".join(top) + ".")
                log(f"project: {answer!r}")
                again = f"start a session in {answer}" + (f" called {info['name']}" if info.get("name") else "") + \
                        (f" on the {info['distro']} distro" if info.get("distro") else "") + (f" and ask it to {info['request']}" if info.get("request") else "")
                r = self.dispatch(again, env)
                parts = [p.strip() for p in (r.stdout or "").strip().split("|")]; out = parts[0]
                log(f"dispatch: {' | '.join(parts)[:300]}")
                if not out.startswith("open "): speak(f"I could not find a project called {answer}."); return
            if out == "history":               # past sessions to choose from
                items = json.loads(parts[1])[:5]
                lines = [f"{['One', 'Two', 'Three', 'Four', 'Five'][i]}, {humanize(it['title'])}, in {it['project'].replace('-', ' ')}, {when(it['mtime'])}"
                         + (", already open" if it.get("open") else "") for i, it in enumerate(items)]
                said = speak(". ".join(lines) + ". Which one?", cache=False, interruptible=True)
                audio = self.listen_answer(wait=FIRST_WAIT, prefix=said)
                if audio is None: return
                if len(audio) < RATE * 0.4: speak("OK."); return
                self.beep(); answer = transcribe(audio.astype(np.float32) / 32768.0, "One, two, three, four, five.")
                chosen = pick(answer, items); log(f"pick: {answer!r} → {chosen['title'] if chosen else None}")
                if not chosen: speak("I didn't get which one."); return
                parts, out = ["resume", json.dumps(chosen)], "resume"
            if out == "resume":                # resume | <session row>
                it = json.loads(parts[1]); title = humanize(it["title"])
                if it.get("open"): speak(f"{title} is already open in its own tab."); return
                where = it.get("distro", "")
                if confirm(f"Resume {title}, in {it['project'].replace('-', ' ')}, from {when(it['mtime'])}?"):
                    r = self.run_tool("session-open", it["cwd"], it["title"][:40], "--resume", it["id"], "--distro", where)
                    log(f"session-open --resume: {(r.stdout or r.stderr).strip()[:200]}")
                    speak(f"Resuming {title}." if r.returncode == 0 else "I could not resume it.")
                else: speak("OK, I won't resume it.")
                return
            if out.startswith("nomatch no past sessions"): speak("I found no earlier sessions there."); return
            if out.startswith("nomatch no sessions"):
                speak("No session has voice on. Say start a session, or resume one, or type slash speak on in a Claude session."); return
            if out.startswith("open "):        # open <dir> | <name> | <request> | <distro>
                folder, name, request, where = (parts + ["", "", "", ""])[:4]; folder = folder[5:]
                spoken_name = name.replace("_", " ").replace("-", " ")
                q = f"Start a new session called {spoken_name} in {os.path.basename(folder).replace('_', ' ').replace('-', ' ')}"
                q += f" on {where.replace('-', ' ')}" if where else ""     # dispatch names it only when it is another distro
                q += (f", and ask it to {request}" if request else "") + "?"
                if confirm(q):
                    r = self.run_tool("session-open", folder, name, request, *(["--distro", where] if where else []))
                    log(f"session-open: {(r.stdout or r.stderr).strip()[:200]}")
                    speak(f"Opening {spoken_name}. Its replies will be read out." if r.returncode == 0 else "I could not open it.")
                else: speak("OK, I won't open it.")
                return
            if out.startswith("close "):       # close <spoken name> | <key>
                spoken_name, key = out[6:], (parts[1] if len(parts) > 1 else "")
                if confirm(f"Close the {spoken_name} session? Anything it is in the middle of stops."):
                    r = self.run_tool("session-close", key)
                    log(f"session-close: {(r.stdout or r.stderr).strip()[:200]}")
                    speak(f"Closed {spoken_name}." if r.returncode == 0 else f"I could not close {spoken_name}.")
                else: speak(f"OK, {spoken_name} stays open.")
                return
            if out.startswith("nomatch which project"):
                phrase = parts[1] if len(parts) > 1 else ""
                said = speak(f"Which project? I found no folder called {phrase}." if phrase else "In which project?", interruptible=True)
                continue
            if out.startswith("list "):
                names = parts[1].split(", ") if len(parts) > 1 else []
                ack = f"{len(names)} open: {spoken_list(names)}." if names else "No sessions."
                said = speak(ack + " Which one?", interruptible=True); continue
            if out.startswith("nomatch empty request"):   # "tell jarvis that." and nothing after it
                said = speak("What should I tell it?", interruptible=True); continue
            if "which session" in out: said = speak(f"Which session? Open: {spoken_list(st['sessions'])}.", interruptible=True); continue
            if out.startswith(("ask ", "focus ", "sent ", "broadcast")): self.request_sent = True   # the reply may be read again
            if out.startswith("ask "): speak("Let me think about that.")
            elif out.startswith("focus "): speak(f"Switched to {out[6:]} and sent it.")
            elif out.startswith("sent ") and parts[-1] == "queued":   # its inbox hook is not running yet
                speak(f"Sent to {out[5:]}. It will get it after its next turn.")
            elif out.startswith("sent "): speak(f"Sent to {out[5:]}.")
            elif out.startswith("broadcast"): speak("Sent to all sessions.")
            else: speak("I could not deliver that.")
            return

    def confirm(self, question):
        """Ask a yes/no question and listen for the answer. Only a clear yes counts; anything else (no, silence,
        something unclear) is a no, because what follows starts or ends a Claude session."""
        src, transcribe, floor = self.src, self.transcribe, self.floor
        speak = self.speak
        said = speak(question, interruptible=True)
        audio = self.listen_answer(wait=FIRST_WAIT, prefix=said)
        if audio is None or len(audio) < RATE * 0.4: log("confirmation: no answer"); return False
        self.beep()
        answer = transcribe(audio.astype(np.float32) / 32768.0, "Yes. No.")
        yes = bool(re.match(r"\W*(yes|yeah|yep|yup|sure|do it|go ahead|go for it|correct|confirm(ed)?|please do|absolutely|ok(ay)?|right|definitely)\b",
                            answer, flags=re.I)) and not re.search(r"\b(no|not|don'?t|cancel|wait)\b", answer, flags=re.I)
        log(f"confirmation: {answer!r} → {'yes' if yes else 'no'}")
        return yes

    def listen_answer(self, wait=None, prefix=None):
        """record_request, but a dead microphone (MicDead) is reopened and you are asked again once; still dead:
        tell you and give up (empty audio)."""
        try: return record_request(self.src, self.floor, wait=wait, prefix=prefix)
        except MicDead: pass
        self.src.pause(); self.src.resume()                # a fresh capture
        said = self.speak("Sorry, I lost you for a moment. Say that again?", interruptible=True)
        try: return record_request(self.src, self.floor, wait=wait, prefix=said)
        except MicDead:
            self.speak("I can't hear you. Please check the microphone."); return None   # None: already told you

    def request_after_wake(self, st, timer, interrupted=False):
        """openWakeWord fires a little after the word, so part of "hey Jarvis, <request>" may already be past: the
        last 2.5 s (`ring`) start the request. Still talking → keep recording; stopped → the ring's transcript after
        the wake word is the request; nothing after it → False (the caller greets you)."""
        src, transcribe, floor, ring = self.src, self.transcribe, self.floor, self.ring
        converse = self.converse
        talking, more = keeps_talking(src, floor)
        audio = list(ring) + more
        if talking:
            log("still talking after the wake word: taking it as the request")
            converse(st, timer=timer, first_frames=audio, strip=True); return True
        text = transcribe(np.concatenate(audio).astype(np.float32) / 32768.0, WAKE_NAME[0].capitalize() + ".")
        rest = strip_wake(text, WAKE_NAME[0], anywhere=interrupted)
        if rest == text or not re.search(r"[A-Za-z]{2,}", rest): return False    # just the wake word
        log(f"request said with the wake word: {rest}")
        converse(st, timer=timer, first_text=rest); return True

    def forget_wake(self):
        """oww.reset() clears the scores but not the detector's own audio buffer, which still holds the wake word
        that started the conversation: the next frames would fire again. Flush it with two seconds of silence."""
        oww, spotter, ring = self.oww, self.spotter, self.ring
        ring.clear()
        if spotter: spotter.reset(); return
        z = np.zeros(FRAME, dtype=np.int16)
        for _ in range(int(2 * RATE / FRAME)): oww.predict(z)
        oww.reset()

    def run(self):
        """The main loop: one 80 ms frame at a time until the source ends (only a --from-wav file does)."""
        while self.step(): pass

    def step(self):
        """Process one frame: follow-ups, closing the mic for WSLg playback, the noise floor, the wake word."""
        self.n += 1; n = self.n
        if n % 375 == 1: self.heartbeat()                  # every ~30 s: the Windows player stays while we run
        src, lock, args, shared, followup = self.src, self.lock, self.args, self.shared, self.followup
        spotter, oww, wake_key = self.spotter, self.oww, self.wake_key
        converse, forget_wake = self.converse, self.forget_wake
        if n % 3 == 0 and os.environ.get("VOICE_FOLLOWUP") == "1" and os.path.exists(followup) and not lock.held_by_someone():
            # a reply to something you asked by voice has just been read out: keep the conversation going
            try: name, age = open(followup).read().strip(), time.time() - os.path.getmtime(followup); os.remove(followup)
            except OSError: name, age = "", 999
            if age < 60 and lock.acquire():
                try: log(f"follow-up {name}"); converse(state(), f"Anything else for {name}?" if name else "Anything else?")
                finally: lock.release(); src.drain(); forget_wake()
            return True
        if n % 3 == 0 and not mic_safe(shared) and lock.held_by_someone():   # WSLg playback: mic off until done
            src.pause()
            while lock.held_by_someone(): time.sleep(0.2)
            src.resume(); forget_wake()
        f = src.frame()
        if f is None: return False                 # only a --from-wav file ends
        self.ring.append(f)
        level = rms(f)
        if level < self.floor * 4 or level < 300:      # background only: speech and played replies would inflate it
            self.floor = 0.98 * self.floor + 0.02 * level
        rest, frames, talking = None, None, False
        if spotter:
            hit = spotter.feed(f, min(max(120.0, self.floor * 3), 900.0))
            if not hit: return True
            (rest, frames, talking), score = hit, 1.0
        else:
            score = oww.predict(f)[wake_key]
            # while a session's reply plays, its echo masks your voice: be more sensitive then (replies seldom say
            # the wake word), and log near misses to tune by
            playing = n % 3 == 0 and lock.held_by_someone() or (self.playing_until > time.time())
            if playing and n % 3 == 0: self.playing_until = time.time() + 0.5
            needed = PLAYING_THRESHOLD if playing else args.threshold
            if playing and self.reply_says_wake_word(): needed = max(args.threshold, 0.7)   # the reply itself says "Jarvis"
            if 0.3 <= score < needed and time.time() - self.last_near > 2:   # a wake word that almost made it
                log(f"near miss{' during a reply' if playing else ''}: wake score {score:.2f} (needs {needed})")
                self.last_near = time.time()
            if score < needed: return True
        self.handle_wake(score, rest, frames, talking)
        return True

    def handle_wake(self, score, rest=None, frames=None, talking=False):
        """A wake: interrupt a reply, take the lock, then one conversation (greeting, or the request said with it)."""
        args, src, lock, spotter, listening_mark, followup = self.args, self.src, self.lock, self.spotter, self.listening_mark, self.followup
        converse, request_after_wake, forget_wake = self.converse, self.request_after_wake, self.forget_wake
        global CONV
        CONV = time.strftime("%H%M%S"); timer = Timer()
        log(f"wake ({score:.2f})" + (f": \"{WAKE_NAME[0]}\"" + (f" + \"{rest}\"" if rest else "") + (", still talking" if talking else "") if spotter else ""))
        try: open(listening_mark, "w").close()     # queued replies wait until this conversation is over (say)
        except OSError: pass
        interrupted = lock.held_by_someone()
        if interrupted: lock.interrupt()           # the wake phrase during a reply: stop it (and the queued ones)
        self.request_sent = False
        lock.acquire(); warm_brain()               # the model loads while you talk, if ollama unloaded it
        try:
            if os.path.exists(followup): os.remove(followup)   # you called: a pending "anything else?" is moot
            st = state()
            if talking:          # "hey claude, tell …" and still going: record the rest, then drop the phrase
                converse(st, timer=timer, first_frames=frames, strip=True)
            elif rest:             # "hey claude, how many sessions are open?" (or just a session name) said in one go
                converse(st, timer=timer, first_text=rest)
            elif not spotter and request_after_wake(st, timer, interrupted):
                pass
            elif args.no_ack: self.beep(); converse(st, timer=timer)
            elif not st["sessions"]:  # nothing to talk to yet, but you may want to start or resume one
                converse(st, "Yes? No session has voice on yet. You can start or resume one.", timer)
            elif len(st["sessions"]) == 1: converse(st, f"OK, {st['sessions'][0]}. What do you want me to do?", timer)
            else: converse(st, f"Which session: {spoken_list(st['sessions']).replace(' and ', ' or ')}?", timer)
        except Exception as e:      # a bug in one conversation must not take the listener down
            log(f"conversation failed: {e.__class__.__name__}: {e}\n" + traceback.format_exc().rstrip(), "error")
        finally:
            if interrupted and not self.request_sent:
                # you cut a reply short and did not send anything: you wanted it quiet, so it is not read again
                lock.interrupt("stop"); log("interrupted reply dropped (no request sent)")
            try: os.remove(listening_mark)
            except OSError: pass
            lock.release(); src.drain(); forget_wake()   # drop audio buffered while we talked, and the old wake word
            log("done: " + timer.summary()); CONV = ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-wav", help="process one wav instead of the microphone (16 kHz mono)")
    ap.add_argument("--mic", default=os.environ.get("VOICE_MIC", ""))   # empty: the platform's default input
    ap.add_argument("--threshold", type=float, default=float(os.environ.get("VOICE_WAKE_THRESHOLD", "0.5")))
    # a bundled openWakeWord model (hey_jarvis, alexa, hey_mycroft, …) or any phrase, spotted with Whisper
    ap.add_argument("--wake", default=os.environ.get("VOICE_WAKE") or os.environ.get("VOICE_WAKE_MODEL") or "hey claude")
    ap.add_argument("--whisper", default=os.environ.get("VOICE_WHISPER_MODEL", "small"))
    ap.add_argument("--whisper-device", default=os.environ.get("VOICE_WHISPER_DEVICE", "cpu" if PLATFORM == "mac" else "cuda"),
                    choices=["cuda", "cpu"])
    ap.add_argument("--lock", required=True)
    ap.add_argument("--shared", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ap.add_argument("--no-ack", action="store_true")
    args = ap.parse_args()

    from openwakeword.model import Model
    import openwakeword
    model_name = args.wake.strip().lower().replace(" ", "_")
    wake_path = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models", f"{model_name}_v0.1.onnx")
    transcribe, dev = load_whisper(args.whisper, args.whisper_device)
    transcribe(np.zeros(RATE, dtype=np.float32), "")   # warm up
    if os.path.exists(wake_path):
        oww = Model(wakeword_model_paths=[wake_path]); spotter = None
        wake_key = next((k for k in oww.models if model_name in k), next(iter(oww.models)))
        log(f"listening for '{wake_key}' (openWakeWord, threshold {args.threshold}, whisper {args.whisper} on {dev})")
    else:
        oww, wake_key, spotter = None, args.wake, PhraseSpotter(args.wake, transcribe)
        log(f"listening for '{args.wake}' (phrase spotted by whisper {args.whisper} on {dev})")
    WAKE_NAME[0] = args.wake.replace("_", " ")
    log(f"config: platform {PLATFORM}, mic {args.mic or 'default'}, silence {SILENCE_TO_STOP}s, first wait {FIRST_WAIT}s, "
        f"wait {NO_SPEECH}s, barge-in {'on' if BARGE_IN else 'off'}, "
        f"brain {os.environ.get('JARVIS_MODEL') or os.environ.get('SPEECHIFY_MODEL') or 'gemma4:e4b'}"
        + (f", source {args.from_wav}" if args.from_wav else ""))

    lock, src = Lock(args.lock), Source(args.from_wav, args.mic)
    def stop(*_):
        src.pause(); os._exit(0)   # not sys.exit: CUDA / onnxruntime threads would keep the process (and its lock) alive
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    warm_brain(); warm_prompts()
    Jarvis(args, src, lock, transcribe, oww=oww, wake_key=wake_key, spotter=spotter).run()

if __name__ == "__main__":
    main()
    os._exit(0)   # CUDA / onnxruntime threads must not keep a finished listener (and its lock file) alive

"""Shared test fixtures: run the real scripts in a throwaway home, with fake sessions, transcripts and projects.

Every test gets its own HOME, VOICE_HOME, VOICE_SHARED and VOICE_LOCK under a temporary directory, the platform forced
to `linux` (no Windows folder, no wsl.exe), and the local brain off (JARVIS_BRAIN=0), so nothing touches the real
machine and nothing needs audio, a microphone or ollama.
"""
import json, os, socket, subprocess, sys, tempfile, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin")
sys.path.insert(0, BIN)   # listen.py's pure helpers are imported directly


class FakeOllama:
    """A local stand-in for ollama's HTTP API. `chat(prompt_text) -> str` answers /api/chat (the brain and ai_pick),
    `generate(prompt) -> str` answers /api/generate (speechify; an empty request just loads the model). `fail = True`
    makes every call an HTTP 500. `calls` records the prompts."""
    def __init__(self):
        import http.server, threading
        fake = self; self.chat = lambda text: "{}"; self.generate = lambda prompt: ""; self.fail = False; self.calls = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                if fake.fail: self.send_response(500); self.end_headers(); return
                if self.path == "/api/chat":
                    text = "\n".join(m["content"] for m in body.get("messages", [])); fake.calls.append(text)
                    out = {"message": {"role": "assistant", "content": fake.chat(text)}}
                else:
                    fake.calls.append(body.get("prompt", "")); out = {"response": fake.generate(body.get("prompt", ""))}
                data = json.dumps(out).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
    def close(self): self.server.shutdown(); self.server.server_close()


class VoiceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="claude-voice-test-")
        t = self.tmp = self._tmp.name
        self.home = os.path.join(t, "home")
        self.voice_home = os.path.join(self.home, ".claude", "voice")
        self.shared = os.path.join(t, "shared")
        self.projects = os.path.join(self.home, "projects")
        for d in (self.voice_home, os.path.join(self.voice_home, "sessions"), os.path.join(self.shared, "sessions"),
                  os.path.join(self.shared, "inbox"), self.projects, os.path.join(self.home, ".claude", "sessions")):
            os.makedirs(d, exist_ok=True)
        self.distro = subprocess.run(["hostname", "-s"], capture_output=True, text=True).stdout.strip() or socket.gethostname()
        self.env = {**os.environ, "HOME": self.home, "VOICE_HOME": self.voice_home, "VOICE_SHARED": self.shared,
                    "VOICE_LOCK": os.path.join(t, "speech.lock"), "CLAUDE_VOICE_PLATFORM": "linux",
                    "JARVIS_BRAIN": "0", "VOICE_PROJECTS": self.projects}
        for k in ("VOICE_WAKE", "VOICE_NO_FOCUS", "VOICE_FOLLOWUP", "DISPATCH_NESTED", "CLAUDE_VOICE_AUTO"):
            self.env.pop(k, None)

    # --- stand-ins for external programs ----------------------------------------------------------------------------
    def fake_audio(self, clip_seconds=1.0):
        """piper and ffmpeg stand-ins on PATH: piper writes a silent WAV of `clip_seconds` and records the text;
        ffmpeg 'plays' (-f pulse) by sleeping for the clip's length, or copies the file for a volume filter. Every call
        is appended to self.audio_log, so tests can see what was synthesized, filtered and played."""
        fake = os.path.join(self.tmp, "fakebin"); os.makedirs(fake, exist_ok=True)
        self.audio_log = os.path.join(self.tmp, "audio.log")
        with open(os.path.join(fake, "piper"), "w") as f:
            f.write(f"""#!/usr/bin/env python3
import sys, wave
out = sys.argv[sys.argv.index("-f") + 1]; text = sys.stdin.read()
open({self.audio_log!r}, "a").write("piper " + text.replace(chr(10), " ") + chr(10))
w = wave.open(out, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
w.writeframes(bytes(int(22050 * {clip_seconds}) * 2)); w.close()
""")
        with open(os.path.join(fake, "ffmpeg"), "w") as f:
            f.write(f"""#!/usr/bin/env python3
import os, shutil, sys, time
a = sys.argv[1:]; src = a[a.index("-i") + 1] if "-i" in a else ""
log = open({self.audio_log!r}, "a")
if "-af" in a and "silencedetect" in a[a.index("-af") + 1]:   # pauses between sentences: one every 0.5 s
    log.write("silencedetect" + chr(10)); n = (os.path.getsize(src) - 44) / 44100
    sys.stderr.write("".join(f"[silencedetect] silence_end: {{t / 2:.2f}} | silence_duration: 0.3" + chr(10) for t in range(1, int(n * 2) + 1)))
elif "-ss" in a:
    log.write("cut " + a[a.index("-ss") + 1] + chr(10)); shutil.copy(src, a[-1])
elif "-filter:a" in a:
    log.write("filter " + a[a.index("-filter:a") + 1] + chr(10)); log.close(); shutil.copy(src, a[-1])
elif "pulse" in a:
    log.write("play " + os.path.basename(src) + chr(10)); log.close(); time.sleep(max(0.1, (os.path.getsize(src) - 44) / 44100))
""")
        for n in ("piper", "ffmpeg"): os.chmod(os.path.join(fake, n), 0o755)
        voice = os.path.join(self.tmp, "voice.onnx")
        with open(voice, "w") as f: f.write("x")
        self.env.update(PATH=fake + ":" + self.env["PATH"], SAY_VOICE=voice, XDG_RUNTIME_DIR=self.tmp)
        self.stop_file = os.path.join(self.tmp, "speech.stop")

    def audio_events(self):
        try:
            with open(self.audio_log) as f: return [l.rstrip("\n") for l in f]
        except FileNotFoundError: return []

    def speech_log(self):
        try:
            with open(os.path.join(self.voice_home, "speech.log")) as f: return [l.split(" ", 1)[1].rstrip() for l in f]
        except FileNotFoundError: return []

    def fake_ollama(self):
        """Turn the local brain on against a FakeOllama (brain, ai_pick and speechify's summary mode use it)."""
        self.ollama = FakeOllama(); self.addCleanup(self.ollama.close)
        self.env.update(JARVIS_BRAIN="1", OLLAMA_URL=self.ollama.url, JARVIS_TIMEOUT="5")
        return self.ollama

    def tearDown(self):
        self._tmp.cleanup()

    # --- running scripts -------------------------------------------------------------------------------------------
    def run_bin(self, name, *args, stdin=None, env=None, check=False):
        r = subprocess.run([os.path.join(BIN, name), *args], input=stdin, capture_output=True, text=True,
                           env={**self.env, **(env or {})}, timeout=60)
        if check: self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def dispatch(self, text, *, dry=True, no_focus=False, env=None):
        e = {"VOICE_NO_FOCUS": "1"} if no_focus else {}
        r = self.run_bin("dispatch", *(["--text"] if dry else []), text, env={**e, **(env or {})})
        return r.stdout.strip()

    # --- fixtures --------------------------------------------------------------------------------------------------
    def add_session(self, name, sid=None, distro=None, pid=None, **extra):
        """A registered voice session (the registry entry /speak on writes); alive by default (this test's pid)."""
        sid = sid or f"sid-{name}"
        d = {"name": name, "distro": distro or self.distro, "session_id": sid, "pid": pid or os.getpid(), "cwd": "", **extra}
        key = f"{d['distro']}__{sid}"
        with open(os.path.join(self.shared, "sessions", key + ".json"), "w") as f: json.dump(d, f)
        return key

    def claude_session_file(self, sid, name, cwd="/tmp", pid=None):
        """Claude Code's own ~/.claude/sessions/<pid>.json, which register_session reads."""
        pid = pid or os.getpid()
        with open(os.path.join(self.home, ".claude", "sessions", f"{pid}.json"), "w") as f:
            json.dump({"pid": pid, "sessionId": sid, "cwd": cwd, "name": name}, f)
        return pid

    def project(self, *names):
        for n in names: os.makedirs(os.path.join(self.projects, n), exist_ok=True)

    def transcript(self, project_dir, sid, *, ai_title=None, custom_title=None, age_days=0.0):
        """A past session: ~/.claude/projects/<encoded cwd>/<sid>.jsonl with a cwd and its titles."""
        enc = project_dir.replace("/", "-")
        d = os.path.join(self.home, ".claude", "projects", enc); os.makedirs(d, exist_ok=True)
        p = os.path.join(d, sid + ".jsonl")
        lines = [{"type": "user", "cwd": project_dir, "sessionId": sid, "message": {"content": "hi"}}]
        if ai_title: lines.append({"type": "ai-title", "aiTitle": ai_title, "sessionId": sid})
        if custom_title: lines.append({"type": "custom-title", "customTitle": custom_title, "sessionId": sid})
        with open(p, "w") as f:
            for l in lines: f.write(json.dumps(l, separators=(",", ":")) + "\n")
        t = time.time() - age_days * 86400; os.utime(p, (t, t))
        return p

    def read(self, *parts):
        with open(os.path.join(*parts)) as f: return f.read()

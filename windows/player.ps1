# claude-voice player: plays the WAV files WSL drops into <shared>\play, one at a time, through Windows audio.
# Speech then never opens a WSLg audio output stream, which breaks up while the listener keeps the microphone open.
# Started hidden by bin/play (setup and `claude-voice start` start it too); one instance per Windows session.
# Interrupt: a .claude-voice.stop file touched after a clip started (`voice stop`, "hey Jarvis") stops it at once.
# Battery: it checks for clips every 250 ms when idle and quits after VOICE_PLAYER_IDLE seconds (default 600) without a
# clip while no listener runs (listener.alive older than 2 minutes); `play` starts it again when it is needed.
param([string]$Dir = (Join-Path $env:USERPROFILE ".claude-voice"))
$ErrorActionPreference = "SilentlyContinue"
$mutex = New-Object System.Threading.Mutex($false, "Local\claude-voice-player")
if (-not $mutex.WaitOne(0)) { exit }
$queue = Join-Path $Dir "play"; New-Item -ItemType Directory -Force $queue | Out-Null
$alive = Join-Path $Dir "player.alive"; $stop = "$Dir.stop"; $quit = Join-Path $Dir "player.quit"   # setup asks an old copy to quit
$sp = New-Object System.Media.SoundPlayer
$beat = [DateTime]::MinValue; $born = Get-Date; $lastClip = Get-Date
$idleLimit = if ($env:VOICE_PLAYER_IDLE) { [int]$env:VOICE_PLAYER_IDLE } else { 600 }
$listener = Join-Path $Dir "listener.alive"
function ListenerRunning { (Test-Path $listener) -and (((Get-Date) - (Get-Item $listener).LastWriteTime).TotalSeconds -lt 120) }
function Heartbeat { if (((Get-Date) - $script:beat).TotalMilliseconds -ge 1000) { [IO.File]::WriteAllText($alive, "$PID"); $script:beat = Get-Date } }
function Stopped($since) { (Test-Path $stop) -and ((Get-Item $stop).LastWriteTime -gt $since) }
while ($true) {
  if ((Test-Path $quit) -and ((Get-Item $quit).LastWriteTime -gt $born)) { Remove-Item $quit, $alive -Force; exit }
  Heartbeat
  $f = Get-ChildItem $queue -Filter *.wav | Sort-Object Name | Select-Object -First 1
  if (-not $f) {
    if (((Get-Date) - $lastClip).TotalSeconds -gt $idleLimit -and -not (ListenerRunning)) { Remove-Item $alive -Force; exit }
    Start-Sleep -Milliseconds 250; continue
  }
  $lastClip = Get-Date
  $start = Get-Date
  try {
    $bytes = [IO.File]::ReadAllBytes($f.FullName)
    $rate = [BitConverter]::ToUInt32($bytes, 28)                       # WAV header: bytes per second
    $seconds = if ($rate -gt 0) { ($bytes.Length - 44) / $rate } else { 0 }
    $sp.Stream = New-Object IO.MemoryStream(, $bytes); $sp.Load(); $sp.Play()
    $end = $start.AddSeconds($seconds + 0.15)
    while ((Get-Date) -lt $end) {
      if (Stopped $start) { $sp.Stop(); break }
      Heartbeat; Start-Sleep -Milliseconds 40
    }
  } catch { }
  Remove-Item $f.FullName -Force                                        # tells the waiting `play` it is done
}

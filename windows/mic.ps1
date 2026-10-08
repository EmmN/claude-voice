# claude-voice microphone on WSL: records the Windows default input (waveIn, 16 kHz mono 16-bit) and writes raw PCM
# to stdout, which listen.py reads like ffmpeg's. The WSLg route (RDP audio redirection into PulseAudio) adds delay
# and stalls for seconds at a time; this skips it. Exits when stdout closes (the listener stopped or reopened it).
# Usage (from WSL): powershell.exe -NoProfile -ExecutionPolicy Bypass -File mic.ps1 [-BufferMs 20] [-Device N]
param([int]$BufferMs = 20, [int]$Device = -1)   # -1: the Windows default recording device
$ErrorActionPreference = "Stop"
# compiled in memory at every start (about 10 s; a cached DLL would load faster, but Windows Application Control
# blocks an unsigned one). The listener waits for it and reopens it only after a stall.
Add-Type -TypeDefinition @"
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
public static class ClaudeVoiceMic {
    [StructLayout(LayoutKind.Sequential)]
    struct WaveFormat { public ushort tag, channels; public uint rate, bytesPerSec; public ushort align, bits, size; }
    [DllImport("winmm.dll")] static extern int waveInOpen(out IntPtr h, int dev, ref WaveFormat f, IntPtr cb, IntPtr inst, int flags);
    [DllImport("winmm.dll")] static extern int waveInPrepareHeader(IntPtr h, IntPtr hdr, int size);
    [DllImport("winmm.dll")] static extern int waveInUnprepareHeader(IntPtr h, IntPtr hdr, int size);
    [DllImport("winmm.dll")] static extern int waveInAddBuffer(IntPtr h, IntPtr hdr, int size);
    [DllImport("winmm.dll")] static extern int waveInStart(IntPtr h);
    [DllImport("winmm.dll")] static extern int waveInReset(IntPtr h);
    [DllImport("winmm.dll")] static extern int waveInClose(IntPtr h);
    const int HDR = 48, DONE = 1;    // WAVEHDR on x64: lpData, dwBufferLength, dwBytesRecorded, dwUser, dwFlags, ...
    public static int Run(int bufferMs, int device) {
        var f = new WaveFormat { tag = 1, channels = 1, rate = 16000, bytesPerSec = 32000, align = 2, bits = 16, size = 0 };
        IntPtr h; int r = waveInOpen(out h, device, ref f, IntPtr.Zero, IntPtr.Zero, 0);
        if (r != 0) { Console.Error.WriteLine("waveInOpen failed: " + r); return 2; }
        int bytes = 32 * bufferMs, n = Math.Max(8, 400 / bufferMs);    // ~400 ms of buffers in flight
        var hdrs = new IntPtr[n];
        for (int i = 0; i < n; i++) {
            hdrs[i] = Marshal.AllocHGlobal(HDR);
            for (int o = 0; o < HDR; o++) Marshal.WriteByte(hdrs[i], o, 0);
            Marshal.WriteIntPtr(hdrs[i], 0, Marshal.AllocHGlobal(bytes));
            Marshal.WriteInt32(hdrs[i], 8, bytes);
            waveInPrepareHeader(h, hdrs[i], HDR); waveInAddBuffer(h, hdrs[i], HDR);
        }
        var output = Console.OpenStandardOutput(); var chunk = new byte[bytes];
        waveInStart(h);
        try {
            for (int next = 0; ; ) {      // buffers complete in order: wait for the next one, write it, hand it back
                IntPtr hdr = hdrs[next];
                if ((Marshal.ReadInt32(hdr, 24) & DONE) == 0) { Thread.Sleep(2); continue; }
                int got = Marshal.ReadInt32(hdr, 12);
                Marshal.Copy(Marshal.ReadIntPtr(hdr, 0), chunk, 0, got);
                output.Write(chunk, 0, got); output.Flush();
                Marshal.WriteInt32(hdr, 24, Marshal.ReadInt32(hdr, 24) & ~DONE);
                waveInAddBuffer(h, hdr, HDR);
                next = (next + 1) % n;
            }
        } catch (IOException) { return 0; }   // the reader went away
        finally { waveInReset(h); foreach (var p in hdrs) waveInUnprepareHeader(h, p, HDR); waveInClose(h); }
    }
}
"@
exit [ClaudeVoiceMic]::Run($BufferMs, $Device)

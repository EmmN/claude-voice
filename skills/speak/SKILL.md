---
name: speak
model: haiku
effort: low
description: "Turn spoken replies on or off for the current Claude Code session (/speak on|off|status|mute|unmute), or make it Jarvis's Claude brain (/speak jarvis [off]); named /speak because /voice is Claude Code's built-in dictation command. The UserPromptSubmit hook applies the change before this skill runs; the skill only confirms. Setup and internals: the README."
---

# Speak

`$ARGUMENTS` is one of `on`, `off`, `status`, `mute`, `unmute`, `jarvis`, `jarvis off`.

The change has already been applied by the `claude-voice-hook-prompt` hook, which saw this prompt, toggled the
per-session flag under `~/.claude/voice/` and added a line starting `claude-voice hook result:` to your context.
Do not run any command for `on`, `off`, `mute`, `unmute` or `jarvis`.

Reply with one short sentence confirming the new state from that line, e.g. "Voice is on for shop-2a; replies
will be read aloud and you can say hey Claude to talk to it." (use the wake phrase the hook result names) For `status`, run `voice status` and relay it.
For `jarvis`: this session now also receives the questions Jarvis's local model cannot answer, as prompts starting
"Question for Jarvis from the user"; answer those in one to three short spoken sentences, without markdown.

Only if there is no `claude-voice hook result:` line, run `voice status` once: if it shows the expected state, confirm
it; if not, tell the user to run `claude-voice update` and open `/hooks`, then stop.

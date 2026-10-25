"""Sound engines for the strings, behind one small interface so the musical logic never knows which is in use."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

# MuseScore Studio's bundled SoundFont (MIT licence). Installed with `brew install --cask musescore`.
DEFAULT_SOUNDFONT = Path("/Applications/MuseScore 4.app/Contents/Resources/sound/MS Basic.sf3")
CHANNELS = {"Violin": 0, "Viola": 1, "Violoncello": 2, "Piano": 3}
PROGRAMS = {"Violin": 40, "Viola": 41, "Violoncello": 42, "Piano": 0}  # General MIDI


class SoundEngine(Protocol):
    def note_on(self, part: str, pitch: int, velocity: int) -> None: ...
    def note_off(self, part: str, pitch: int) -> None: ...
    def close(self) -> None: ...


class FluidSynthEngine:
    """Real-time strings through FluidSynth and Core Audio, with a small audio buffer for low latency."""

    def __init__(self, soundfont: Path = DEFAULT_SOUNDFONT, period_size: int = 64, periods: int = 8, gain: float = 0.6):
        import fluidsynth

        self.fs = fluidsynth.Synth(samplerate=48000.0, gain=gain)
        # 64 frames x 8 periods at 48 kHz is about 10.7 ms of buffering. On the MacBook Pro speakers,
        # 2 and 4 periods were silent (no error, voices running); 8 plays.
        self.fs.setting("audio.period-size", period_size)
        self.fs.setting("audio.periods", periods)
        self.fs.start(driver="coreaudio")
        sfid = self.fs.sfload(str(soundfont))
        for part, ch in CHANNELS.items():
            self.fs.program_select(ch, sfid, 0, PROGRAMS[part])

    def note_on(self, part: str, pitch: int, velocity: int) -> None:
        self.fs.noteon(CHANNELS[part], pitch, velocity)

    def note_off(self, part: str, pitch: int) -> None:
        self.fs.noteoff(CHANNELS[part], pitch)

    def close(self) -> None:
        for ch in CHANNELS.values():
            self.fs.all_notes_off(ch)
        self.fs.delete()


def render_wav(midi_path: Path, wav_path: Path, soundfont: Path = DEFAULT_SOUNDFONT) -> Path:
    """Render a MIDI file to audio with the fluidsynth command-line tool (for listening to offline runs)."""
    subprocess.run(["fluidsynth", "-ni", "-g", "0.6", "-r", "48000", "-F", str(wav_path), str(soundfont), str(midi_path)],
                   check=True, capture_output=True)
    return wav_path

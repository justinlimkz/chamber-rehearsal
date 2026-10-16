from rehearsal import midi_io
from rehearsal.midi_io import MidiEvent


def test_take_roundtrip(tmp_path):
    evs = [
        MidiEvent(0.0, "note_on", 60, 80),
        MidiEvent(0.5, "note_off", 60, 0),
        MidiEvent(0.75, "pedal", 64, 127),
        MidiEvent(1.25, "note_on", 62, 64),
    ]
    p = tmp_path / "take.mid"
    midi_io.write_take(evs, p)
    back = midi_io.read_take(p)
    assert [(e.kind, e.note, e.value) for e in back] == [(e.kind, e.note, e.value) for e in evs]
    assert all(abs(a.time - b.time) < 0.002 for a, b in zip(evs, back))


def test_replay_offline_is_instant():
    evs = [MidiEvent(t, "note_on", 60, 80) for t in (0, 10, 20)]
    assert list(midi_io.replay(evs)) == evs

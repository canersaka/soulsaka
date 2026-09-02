from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from soulsaka.listener.segmenter import FRAME_SIZE, SAMPLE_RATE, Segment, Segmenter
from soulsaka.listener.vad import EnergyVAD, frame_dbfs

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
PAD = 0.25
FRAME_S = FRAME_SIZE / SAMPLE_RATE


def synth(
    bursts: list[tuple[float, float]], total_s: float, *, amp: float = 0.2, noise: float = 0.0
) -> np.ndarray:
    """Silence (or white noise) with 220 Hz tone bursts at ``[(start_s, duration_s), ...]``."""
    n = int(total_s * SAMPLE_RATE)
    n += (-n) % FRAME_SIZE
    rng = np.random.default_rng(0)
    x = rng.normal(0.0, noise, n).astype(np.float32) if noise else np.zeros(n, np.float32)
    t = np.arange(n) / SAMPLE_RATE
    for start, dur in bursts:
        a, b = int(start * SAMPLE_RATE), int((start + dur) * SAMPLE_RATE)
        x[a:b] += (amp * np.sin(2 * np.pi * 220.0 * t[a:b])).astype(np.float32)
    return x


def run(x: np.ndarray, segmenter: Segmenter, vad: EnergyVAD | None = None) -> list[Segment]:
    vad = vad or EnergyVAD()
    out: list[Segment] = []
    for i in range(0, len(x), FRAME_SIZE):
        frame = x[i : i + FRAME_SIZE]
        seg = segmenter.feed(frame, vad.prob(frame), T0 + timedelta(seconds=i / SAMPLE_RATE))
        if seg is not None:
            out.append(seg)
    seg = segmenter.flush()
    if seg is not None:
        out.append(seg)
    return out


def make_segmenter(**kw) -> Segmenter:
    defaults = dict(
        threshold=0.5, min_speech_s=0.6, silence_end_s=0.8, max_segment_s=30.0, pad_s=PAD
    )
    defaults.update(kw)
    return Segmenter(**defaults)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


def test_bursts_become_segments_with_pre_roll_and_tail():
    bursts = [(1.0, 1.0), (3.5, 1.5), (6.5, 0.8)]
    segs = run(synth(bursts, 9.0), make_segmenter())
    assert len(segs) == 3
    for seg, (start, dur) in zip(segs, bursts, strict=True):
        assert abs(seg.duration_s - (dur + 2 * PAD)) < 0.1, (seg.duration_s, dur)
        assert seg.samples.dtype == np.float32
        assert abs(seg.samples.shape[0] / SAMPLE_RATE - seg.duration_s) < 1e-6
        offset = (seg.started_at - T0).total_seconds()
        assert abs(offset - (start - PAD)) < 0.1, (offset, start)
        # pre-roll and tail are (near) silence, the middle is the tone
        head = seg.samples[: int(0.2 * SAMPLE_RATE)]
        tail = seg.samples[-int(0.2 * SAMPLE_RATE) :]
        mid = seg.samples[int(0.4 * SAMPLE_RATE) : int(0.6 * SAMPLE_RATE)]
        assert rms(head) < 0.01 and rms(tail) < 0.01
        assert rms(mid) > 0.1


def test_short_blips_are_dropped():
    segmenter = make_segmenter()
    segs = run(synth([(1.0, 0.2), (2.5, 0.3), (4.0, 0.1)], 6.0), segmenter)
    assert segs == []
    assert segmenter.dropped == 3 and segmenter.emitted == 0


def test_long_speech_is_split_at_max_segment():
    total = 7.0
    segmenter = make_segmenter(max_segment_s=3.0)
    segs = run(synth([(0.5, total)], 9.0), segmenter)
    assert len(segs) == 3
    for seg in segs:
        assert seg.duration_s <= 3.0 + FRAME_S + 1e-9
    assert abs(segs[0].duration_s - 3.0) < 0.1 and abs(segs[1].duration_s - 3.0) < 0.1
    assert abs(sum(s.duration_s for s in segs) - (total + 2 * PAD)) < 0.1
    # pieces are contiguous in time
    for a, b in zip(segs, segs[1:], strict=False):
        gap = (b.started_at - a.started_at).total_seconds() - a.duration_s
        assert abs(gap) < FRAME_S + 1e-6, gap
    # the continuation has no pre-roll: it starts straight into the tone
    assert rms(segs[1].samples[: int(0.1 * SAMPLE_RATE)]) > 0.1


def test_energy_vad_adapts_to_steady_noise():
    bursts = [(1.0, 1.0), (3.5, 1.5), (6.5, 0.8)]
    noisy = synth(bursts, 9.0, noise=0.01)  # about -40 dBFS, above the absolute gate
    vad = EnergyVAD()
    segs = run(noisy, make_segmenter(), vad)
    assert len(segs) == 3
    assert vad.noise_floor_db is not None and -45 < vad.noise_floor_db < -35
    # noise alone never produces a segment
    assert run(synth([], 9.0, noise=0.01), make_segmenter(), EnergyVAD()) == []


def test_energy_vad_probability_is_calibrated_to_threshold():
    vad = EnergyVAD()
    silence = np.zeros(FRAME_SIZE, np.float32)
    loud = (0.2 * np.sin(np.linspace(0, 50, FRAME_SIZE))).astype(np.float32)
    for _ in range(10):
        assert vad.prob(silence) == 0.0
    assert vad.prob(loud) > 0.99
    assert -20 < frame_dbfs(loud) < -14
    assert frame_dbfs(silence) == -100.0


def test_flush_emits_the_open_segment_and_drops_blips():
    x = synth([(0.5, 1.5)], 2.0)  # tone runs to the end of the file, no trailing silence
    segmenter = make_segmenter()
    segs = run(x, segmenter)
    assert len(segs) == 1 and abs(segs[0].duration_s - (1.5 + PAD)) < 0.1
    assert segmenter.flush() is None
    segmenter = make_segmenter()
    assert run(synth([(1.8, 0.2)], 2.0), segmenter) == [] and segmenter.dropped == 1


def test_state_machine_with_scripted_probabilities():
    segmenter = make_segmenter(pad_s=0.0, min_speech_s=0.1, silence_end_s=0.1)
    frame = np.ones(FRAME_SIZE, np.float32)
    assert not segmenter.in_speech
    assert segmenter.feed(frame, 0.5) is None  # threshold is strict
    assert not segmenter.in_speech
    for _ in range(4):
        assert segmenter.feed(frame, 0.9) is None
    assert segmenter.in_speech and segmenter.confirmed
    seg = None
    for _ in range(4):
        seg = seg or segmenter.feed(frame, 0.0)
    assert seg is not None and not segmenter.in_speech
    assert abs(seg.duration_s - 4 * FRAME_S) < 1e-9  # no pad: just the speech frames
    assert segmenter.emitted == 1


def test_feed_rejects_wrong_frame_size():
    segmenter = make_segmenter()
    try:
        segmenter.feed(np.zeros(100, np.float32), 1.0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")

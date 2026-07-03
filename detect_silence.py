"""
detect_silence — silence detection for DaVinci Gate.

Stdlib only (wave/struct/math). Works in Resolve's embedded Python 3.13+ without
pydub, pyaudioop, or numpy. Handles 16-bit and 24-bit PCM WAV exports from Resolve.

Imported by DaVinciGate.py as: ``from detect_silence import detect_silence``
"""

import json
import math
import os
import struct
import sys
import wave


def _sample_24bit_le(b0, b1, b2):
    val = b0 | (b1 << 8) | (b2 << 16)
    if val & 0x800000:
        val -= 0x1000000
    return val / 8388608.0


def _decode_pcm_mono(raw, sampwidth, nchannels):
    """Decode interleaved PCM to normalized mono floats in [-1, 1]."""
    if nchannels < 1:
        nchannels = 1

    if sampwidth == 1:
        step = nchannels
        count = len(raw) // step
        out = []
        for i in range(count):
            b = raw[i * step]
            v = (b - 128) / 128.0
            if nchannels > 1:
                acc = v
                for ch in range(1, nchannels):
                    b2 = raw[i * step + ch]
                    acc += (b2 - 128) / 128.0
                v = acc / nchannels
            out.append(v)
        return out

    if sampwidth == 2:
        frame_bytes = 2 * nchannels
        if len(raw) % frame_bytes != 0:
            raw = raw[: len(raw) - (len(raw) % frame_bytes)]
        count = len(raw) // frame_bytes
        out = []
        for i in range(count):
            off = i * frame_bytes
            v = struct.unpack_from("<h", raw, off)[0] / 32768.0
            if nchannels > 1:
                acc = v
                for ch in range(1, nchannels):
                    acc += struct.unpack_from("<h", raw, off + ch * 2)[0] / 32768.0
                v = acc / nchannels
            out.append(v)
        return out

    if sampwidth == 3:
        frame_bytes = 3 * nchannels
        if len(raw) % frame_bytes != 0:
            raw = raw[: len(raw) - (len(raw) % frame_bytes)]
        count = len(raw) // frame_bytes
        out = []
        for i in range(count):
            off = i * frame_bytes
            b0, b1, b2 = raw[off], raw[off + 1], raw[off + 2]
            v = _sample_24bit_le(b0, b1, b2)
            if nchannels > 1:
                acc = v
                for ch in range(1, nchannels):
                    o2 = off + ch * 3
                    acc += _sample_24bit_le(raw[o2], raw[o2 + 1], raw[o2 + 2])
                v = acc / nchannels
            out.append(v)
        return out

    if sampwidth == 4:
        frame_bytes = 4 * nchannels
        if len(raw) % frame_bytes != 0:
            raw = raw[: len(raw) - (len(raw) % frame_bytes)]
        count = len(raw) // frame_bytes
        out = []
        for i in range(count):
            off = i * frame_bytes
            v = struct.unpack_from("<i", raw, off)[0] / 2147483648.0
            if nchannels > 1:
                acc = v
                for ch in range(1, nchannels):
                    acc += struct.unpack_from("<i", raw, off + ch * 4)[0] / 2147483648.0
                v = acc / nchannels
            out.append(v)
        return out

    raise ValueError(f"Unsupported WAV sample width: {sampwidth} bytes")


def _load_wav(path):
    with wave.open(path, "rb") as w:
        nchannels = w.getnchannels()
        sampwidth = w.getsampwidth()
        framerate = w.getframerate()
        nframes = w.getnframes()
        comptype = w.getcomptype()
        raw = w.readframes(nframes)

    if comptype != "NONE":
        raise ValueError(f"Unsupported WAV compression: {comptype!r} (expected PCM)")

    samples = _decode_pcm_mono(raw, sampwidth, nchannels)
    duration_ms = int(round(len(samples) * 1000.0 / framerate)) if framerate else 0
    return samples, framerate, duration_ms, sampwidth, nchannels


def _chunk_dbfs(samples, framerate, start_ms, end_ms):
    """RMS dBFS for sample range [start_ms, end_ms)."""
    if framerate <= 0 or not samples:
        return -120.0
    start_i = int(start_ms * framerate / 1000.0)
    end_i = int(end_ms * framerate / 1000.0)
    start_i = max(0, min(start_i, len(samples)))
    end_i = max(start_i, min(end_i, len(samples)))
    chunk = samples[start_i:end_i]
    if not chunk:
        return -120.0
    sq = 0.0
    for s in chunk:
        sq += s * s
    rms = math.sqrt(sq / len(chunk))
    if rms <= 1e-10:
        return -120.0
    return 20.0 * math.log10(rms)


def _detect_nonsilent_ms(duration_ms, framerate, samples, min_silence_ms, silence_thresh_db, seek_step_ms=20):
    """Return list of (start_ms, end_ms) speech regions (pydub-compatible semantics)."""
    if duration_ms <= 0:
        return []

    nonsilent = []
    t = 0
    while t < duration_ms:
        t_end = min(duration_ms, t + seek_step_ms)
        db = _chunk_dbfs(samples, framerate, t, t_end)
        if db > silence_thresh_db:
            nonsilent.append((t, t_end))
        else:
            nonsilent.append(None)
        t += seek_step_ms

    # Merge adjacent nonsilent chunks
    speech = []
    cur = None
    for part in nonsilent:
        if part is None:
            if cur is not None:
                speech.append(cur)
                cur = None
            continue
        s, e = part
        if cur is None:
            cur = [s, e]
        elif s <= cur[1] + seek_step_ms:
            cur[1] = e
        else:
            speech.append(tuple(cur))
            cur = [s, e]
    if cur is not None:
        speech.append(tuple(cur))

    # Drop speech gaps shorter than min_silence_ms (merge across brief silence)
    if not speech:
        return []

    merged = [speech[0]]
    for s, e in speech[1:]:
        prev_s, prev_e = merged[-1]
        if s - prev_e < min_silence_ms:
            merged[-1] = (prev_s, max(prev_e, e))
        else:
            merged.append((s, e))
    return merged


def detect_silence(
    wav_path,
    min_sil_ms=600,
    pad_ms=120,
    out_json=None,
    silence_thresh_db=-50.0,
    fps_hint=30,
    hold_ms=500,
):
    """Detect silence segments in a WAV file; write JSON sidecar if requested."""
    if not os.path.exists(wav_path):
        print(f"detect_silence: ERROR - File does not exist: {wav_path}")
        return []

    try:
        samples, framerate, duration_ms, sampwidth, nchannels = _load_wav(wav_path)
    except Exception as e:
        print(f"detect_silence: ERROR loading audio file: {e}")
        return []

    print(
        f"detect_silence: loaded {wav_path} "
        f"({duration_ms} ms, {framerate} Hz, {sampwidth * 8}-bit, ch={nchannels})"
    )

    speech = _detect_nonsilent_ms(
        duration_ms,
        framerate,
        samples,
        min_sil_ms,
        silence_thresh_db,
        seek_step_ms=20,
    )

    speech = [(max(0, s - pad_ms), min(duration_ms, e + pad_ms + hold_ms)) for s, e in speech]
    speech.sort()
    merged = []
    for s, e in speech:
        if merged and s <= merged[-1][1] + 100:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    fps = float(os.environ.get("FPS_HINT", str(fps_hint)))
    frame_ms = 1000.0 / fps if fps > 0 else 33.33
    min_sil_gap_ms = int(round(1 * frame_ms))
    coalesced = []
    for s, e in merged:
        if coalesced and s - coalesced[-1][1] <= min_sil_gap_ms:
            coalesced[-1] = (coalesced[-1][0], max(coalesced[-1][1], e))
        else:
            coalesced.append((s, e))

    pts = [0]
    for s, e in coalesced:
        pts += [s, e]
    pts.append(duration_ms)

    segs = []
    for i in range(len(pts) - 1):
        s, e = pts[i], pts[i + 1]
        if e <= s:
            continue
        is_silence = i % 2 == 0
        entry = {
            "start_sec": s / 1000.0,
            "end_sec": e / 1000.0,
            "is_silence": is_silence,
        }
        if fps > 0:
            entry["startF"] = int(round(s / 1000.0 * fps))
            entry["endF"] = int(round(e / 1000.0 * fps))
        segs.append(entry)

    n_sil = sum(1 for x in segs if x.get("is_silence"))
    n_speech = len(segs) - n_sil
    print(f"detect_silence: {len(segs)} segments ({n_speech} speech, {n_sil} silence)")

    if out_json:
        try:
            with open(out_json, "w") as f:
                json.dump(segs, f, indent=2)
            if not os.path.exists(out_json):
                print(f"detect_silence: ERROR - Failed to create {out_json}")
        except Exception as e:
            print(f"detect_silence: ERROR writing JSON file: {e}")
            import traceback

            print(f"detect_silence: Traceback: {traceback.format_exc()}")

    return segs


if __name__ == "__main__":
    wav = sys.argv[1]
    min_sil_ms = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    pad_ms = int(sys.argv[3]) if len(sys.argv) > 3 else 120
    out_json = sys.argv[4] if len(sys.argv) > 4 else None
    detect_silence(wav, min_sil_ms, pad_ms, out_json)

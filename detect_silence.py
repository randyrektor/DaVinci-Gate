from pydub import AudioSegment
from pydub.silence import detect_nonsilent
import os, sys, json

def detect_silence(wav_path, min_sil_ms=600, pad_ms=120, out_json=None, silence_thresh_db=-50.0, fps_hint=30):
    """Detect silence segments in audio file and return segments list."""
    if not os.path.exists(wav_path):
        print(f"detect_silence: ERROR - File does not exist: {wav_path}")
        return []
    
    try:
        a = AudioSegment.from_file(wav_path)
    except Exception as e:
        print(f"detect_silence: ERROR loading audio file: {e}")
        return []
    
    # Settings matching Resolve: -50dB threshold, 150ms min silence
    speech = detect_nonsilent(a, min_silence_len=min_sil_ms, silence_thresh=silence_thresh_db, seek_step=20)
    
    # expand speech by pad, clamp, sort & merge (tolerance ~ 100 ms)
    # Add extra hold time at the end to prevent snipping
    hold_ms = 500  # 500ms hold at end of speech segments
    speech = [(max(0, s - pad_ms), min(len(a), e + pad_ms + hold_ms)) for s, e in speech]
    speech.sort()
    merged = []
    for s, e in speech:
        if merged and s <= merged[-1][1] + 100:  # Increased from 50ms to 100ms
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    
    # Optional: swallow micro-silence between speech if it's tiny (≈ one frame)
    fps = float(os.environ.get("FPS_HINT", str(fps_hint)))
    frame_ms = 1000.0 / fps
    min_sil_gap_ms = int(round(1 * frame_ms))
    coalesced = []
    for s, e in merged:
        if coalesced and s - coalesced[-1][1] <= min_sil_gap_ms:
            coalesced[-1] = (coalesced[-1][0], max(coalesced[-1][1], e))
        else:
            coalesced.append((s, e))
    
    # Build alternating segments deterministically (no overlap scans)
    pts = [0]
    for s, e in coalesced: pts += [s, e]
    pts.append(len(a))
    
    segs = []
    for i in range(len(pts) - 1):
        s, e = pts[i], pts[i+1]
        if e <= s: continue
        # Even indices are silence, odd are speech because pts alternates 0, s1, e1, s2, e2, ..., len
        is_silence = (i % 2 == 0)
        segs.append({"start_sec": s/1000.0, "end_sec": e/1000.0, "is_silence": is_silence})
    
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
    # Usage: python3 detect_silence.py <wav> <min_sil_ms> <pad_ms> <out_json>
    wav = sys.argv[1]
    min_sil_ms = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    pad_ms = int(sys.argv[3]) if len(sys.argv) > 3 else 120
    out_json = sys.argv[4] if len(sys.argv) > 4 else None
    detect_silence(wav, min_sil_ms, pad_ms, out_json)
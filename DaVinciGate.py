#!/usr/bin/env python3
"""
DaVinci Gate — Audio Processing Script

Uses stdlib silence detection (``detect_silence``), 24-bit WAV support, and stronger
clip muting. Re-fetches MediaPool/timeline between append batches; one processed audio
track per speaker; per-clip host discovery.

Silence analysis uses temporary WAV exports; the timeline is rebuilt **in place** from
existing source clips. **No** compound clips or ``*_Gated`` Media Pool items — only new
timeline audio tracks with muted silence.

Run from Workspace → Scripts → Utility → DaVinciGate.
"""

import os
import re
import sys
import json
import time
import glob
import shutil
import tempfile

# Add detect_silence to path (same Utility folder as this script)
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # __file__ not available in DaVinci Resolve, try to find the script directory
    script_dir = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv else os.getcwd()

# Add multiple possible paths
possible_paths = [
    script_dir,
    os.path.join(script_dir, "Utility"),
    os.path.expanduser("~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"),
    os.getcwd()
]

_detect_module = "detect_silence"
for path in possible_paths:
    if os.path.exists(os.path.join(path, f"{_detect_module}.py")):
        sys.path.insert(0, path)
        break
else:
    print(f"ERROR: Could not find {_detect_module}.py in any of these locations:")
    for path in possible_paths:
        print(f"  - {path}")
    sys.exit(1)

try:
    from detect_silence import detect_silence
except ImportError as e:
    print(f"ERROR: Could not import {_detect_module}.py: {e}")
    sys.exit(1)

# --- Resolve API bootstrap (Cross-platform) ---
candidates = []

# Add environment variable path if set
if os.environ.get("RESOLVE_SCRIPT_API"):
    candidates.append(os.path.join(os.environ.get("RESOLVE_SCRIPT_API"), "Modules"))

# macOS paths
if sys.platform == "darwin":
    candidates.extend([
        "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Resources/Developer/Scripting/Modules",
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
        os.path.expanduser("~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"),
    ])
# Windows paths
elif sys.platform == "win32":
    candidates.extend([
        os.path.expanduser("~/AppData/Roaming/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting/Modules"),
        "C:/Program Files/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
        "C:/Program Files (x86)/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
    ])
# Linux paths
elif sys.platform.startswith("linux"):
    candidates.extend([
        os.path.expanduser("~/.local/share/DaVinciResolve/Developer/Scripting/Modules"),
        "/opt/resolve/Developer/Scripting/Modules",
        "/usr/local/DaVinciResolve/Developer/Scripting/Modules",
    ])
for p in candidates:
    if p and os.path.isdir(p) and p not in sys.path:
        sys.path.append(p)

try:
    import DaVinciResolveScript as dvr
    resolve = dvr.scriptapp("Resolve")
except Exception as e:
    print(f"ERROR: DaVinci Resolve API not available: {e}")
    sys.exit(1)

# Configuration
RENDER_PRESET = "AudioOnly_IndividualClips"

# Load configuration
try:
    # Try to import from the same directory as this script
    import sys
    import os
    
    # Get the directory where this script is located
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        # __file__ not available in DaVinci Resolve, use script_dir from earlier
        script_dir = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv else os.getcwd()
    
    # Add script directory to Python path
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    
    import config
    CONFIG = {
        "render_preset": config.RENDER_PRESET,
        "output_format": config.OUTPUT_FORMAT,
        "audio_codec": config.AUDIO_CODEC,
        "audio_bit_depth": config.AUDIO_BIT_DEPTH,
        "audio_sample_rate": config.AUDIO_SAMPLE_RATE,
        "silence_threshold_db": config.SILENCE_THRESHOLD_DB,
        "min_silence_ms": config.MIN_SILENCE_MS,
        "padding_ms": config.PADDING_MS,
        "hold_ms": config.HOLD_MS,
        "crossfade_ms": config.CROSSFADE_MS,
        "batch_size": config.BATCH_SIZE,
        "fps_hint": config.FPS_HINT,
        "script_dir": config.SCRIPT_DIR,
        "temp_dir": config.TEMP_DIR,
        "track_name_normalize": True,
        "use_compound_processing": True,
    }
except ImportError as e:
    # Default configuration
    CONFIG = {
        "render_preset": "AudioOnly_IndividualClips",
        "output_format": "wav",
        "audio_codec": "lpcm", 
        "audio_bit_depth": "24",
        "audio_sample_rate": "48000",
        "silence_threshold_db": -50.0,
        "min_silence_ms": 600,
        "padding_ms": 120,
        "hold_ms": 500,
        "crossfade_ms": 20,
        "batch_size": 250,
        "fps_hint": 30,
        "script_dir": None,
        "temp_dir": None,
        "track_name_normalize": True,
        "use_compound_processing": True,
    }

# Use temporary directory for safer handling
if CONFIG["temp_dir"]:
    OUTDIR = CONFIG["temp_dir"]
    os.makedirs(OUTDIR, exist_ok=True)
else:
    OUTDIR = tempfile.mkdtemp(prefix="_temp_gate_")


def disable_timeline_clip(clip):
    """Mute/disable a timeline clip using whichever Resolve API accepts."""
    attempts = (
        ("SetClipEnabled", lambda: clip.SetClipEnabled(False)),
        ("Enabled", lambda: clip.SetProperty("Enabled", False)),
        ("AudioEnabled", lambda: clip.SetProperty("AudioEnabled", False)),
        ("Volume", lambda: clip.SetProperty("Volume", 0.0)),
    )
    for name, fn in attempts:
        try:
            fn()
            return name
        except Exception:
            continue
    return None


def refresh_handles(resolve_obj):
    """Refresh object handles to stabilize the API."""
    resolve_obj.OpenPage("edit")
    time.sleep(0.5)
    p = resolve_obj.GetProjectManager().GetCurrentProject()
    if not p:
        raise RuntimeError("Could not get current project")
    tl = p.GetCurrentTimeline()
    if not tl:
        raise RuntimeError("Could not get current timeline")
    mp = p.GetMediaPool()
    if not mp:
        raise RuntimeError("Could not get media pool")
    return p, tl, mp


def refresh_pool_handles(resolve_obj):
    """Re-fetch project / timeline / media pool without switching pages (use between AppendToTimeline batches)."""
    p = resolve_obj.GetProjectManager().GetCurrentProject()
    if not p:
        raise RuntimeError("Could not get current project")
    tl = p.GetCurrentTimeline()
    if not tl:
        raise RuntimeError("Could not get current timeline")
    mp = p.GetMediaPool()
    if not mp:
        raise RuntimeError("Could not get media pool")
    return p, tl, mp


def normalize_name(raw):
    base = raw.strip()
    return base.title()


def _fs_safe_stem(s, max_len=56):
    """ASCII-ish basename for JSON sidecars (unique per clip)."""
    s = re.sub(r"[^\w\-]+", "_", s.strip(), flags=re.UNICODE).strip("_")
    return (s or "clip")[:max_len]


def append_in_chunks(infos, mp, size=None, resolve=None):
    """Append timeline items in chunks to avoid large batch failures.

    If ``resolve`` is the Resolve app object, MediaPool / timeline handles are
    re-fetched between chunks. Resolve often invalidates the previous MediaPool
    pointer after AppendToTimeline, which makes the *next* append ignore
    ``trackIndex`` or return fewer items than requested (the \"second speaker\" glitch).
    """
    if size is None:
        size = CONFIG["batch_size"]
    out = []
    n_chunks = (len(infos) + size - 1) // size if size else 1
    chunk_idx = 0
    for i in range(0, len(infos), size):
        chunk = infos[i : i + size]
        chunk_idx += 1
        if resolve is not None and i > 0:
            proj, tl, mp = refresh_pool_handles(resolve)
            time.sleep(0.05)
        result = mp.AppendToTimeline(chunk) or []
        if len(result) != len(chunk):
            print(
                f">>> WARNING: AppendToTimeline returned {len(result)}/{len(chunk)} items "
                f"(chunk {chunk_idx}/{n_chunks})"
            )
        out.extend(result)
        print(f">>> Appended chunk {chunk_idx}/{n_chunks} ({len(chunk)} items)")
    return out


def discover_hosts(tl):
    """Find all audio clips on audio tracks (one host per clip; unique JSON basenames)."""
    from collections import defaultdict

    hosts = []
    per_track_stem_count = defaultdict(int)

    for i in range(1, tl.GetTrackCount("audio") + 1):
        items = tl.GetItemListInTrack("audio", i) or []
        for item in items:
            try:
                raw_name = item.GetName()
                if not raw_name or not raw_name.strip():
                    continue
                if CONFIG.get("track_name_normalize", True):
                    compound_label = normalize_name(raw_name.strip())
                else:
                    compound_label = raw_name.strip()
                start_f = int(item.GetStart())
                stem = _fs_safe_stem(compound_label)
                per_track_stem_count[(i, stem)] += 1
                n = per_track_stem_count[(i, stem)]
                json_name = f"A{i:02d}_{stem}" + (f"_{n}" if n > 1 else "")
                hosts.append(
                    {
                        "name": json_name,
                        "clip": raw_name.strip(),
                        "compound_label": compound_label,
                        "track": i,
                        "item": item,
                        "start_f": start_f,
                    }
                )
            except Exception:
                continue

    if not hosts:
        raise RuntimeError(
            "No audio tracks with clips found. Please ensure your timeline has audio tracks with named clips."
        )
    return hosts

def load_segments(json_path, fps):
    """Load segments from JSON file with frame conversion."""
    import json
    segs = json.load(open(json_path))
    out = []
    for s in segs:
        sF = int(s.get("startF", s.get("start_sec", 0)*fps))
        eF = int(s.get("endF",   s.get("end_sec",   0)*fps))
        if eF > sF: out.append((sF, eF, s.get("is_silence", False)))
    return out

def process_compound_clips(resolve_obj, tl, mp, proj, fps, hosts):
    """One processed timeline audio track per speaker; batched append with handle refresh.

    Does **not** create compound clips or Media Pool ``*_Gated`` items — segments stay
    as editable timeline clips on new ``[Processed] …`` tracks.
    """
    from collections import Counter

    print(f">>> Processing clips for {len(hosts)} host(s) (one processed track per speaker)...")

    hosts_sorted = sorted(hosts, key=lambda h: (h["track"], h.get("start_f", 0)))

    label_counts = Counter(h["compound_label"] for h in hosts_sorted)
    for h in hosts_sorted:
        if label_counts[h["compound_label"]] > 1:
            h["compound_name"] = f"{h['compound_label']} A{h['track']}"
        else:
            h["compound_name"] = h["compound_label"]

    current_track_count = tl.GetTrackCount("audio")
    needed_tracks = current_track_count + len(hosts_sorted)
    while tl.GetTrackCount("audio") < needed_tracks:
        tl.AddTrack("audio")

    all_infos = []
    meta = []
    host_segments = []

    for host_idx, host in enumerate(hosts_sorted):
        dst_idx = current_track_count + host_idx + 1
        json_path = f"{OUTDIR}/{host['name']}.json"
        if not os.path.exists(json_path):
            print(f">>> No JSON file found for {host['name']}: {json_path}")
            host_segments.append([])
            continue

        segs = load_segments(json_path, fps)
        if not segs:
            print(f">>> No segments found for {host['name']}")
            host_segments.append([])
            continue

        matching_item = host["item"]
        if not matching_item:
            print(f">>> WARNING: No original item for {host['name']}")
            host_segments.append([])
            continue

        mpi = matching_item.GetMediaPoolItem()
        if not mpi:
            print(f">>> ERROR: No Media Pool Item for {host['name']}")
            host_segments.append([])
            continue

        timeline_start = int(matching_item.GetStart())
        timeline_end = int(matching_item.GetEnd())
        timeline_duration = timeline_end - timeline_start

        segment_infos = []
        for sF, eF, isSil in segs:
            sF = max(0, min(sF, timeline_duration - 1))
            eF = max(0, min(eF, timeline_duration))
            if eF <= sF:
                continue
            record_frame = timeline_start + sF
            clip_info = {
                "mediaPoolItem": mpi,
                "startFrame": sF,
                "endFrame": eF,
                "recordFrame": record_frame,
                "trackIndex": dst_idx,
                "mediaType": 2,
                "trackType": "audio",
            }
            segment_infos.append(clip_info)
            all_infos.append(clip_info)
            meta.append({"is_silence": isSil})

        host_segments.append(segment_infos)
        print(
            f">>> Host {host_idx + 1}/{len(hosts_sorted)} "
            f"\"{host['compound_name']}\" → audio track {dst_idx} ({len(segment_infos)} segments)"
        )

    if not all_infos:
        print(">>> No segment clip infos to append — aborting")
        return

    added = append_in_chunks(all_infos, mp, resolve=resolve_obj)
    if len(added) != len(all_infos):
        print(
            f">>> WARNING: expected {len(all_infos)} new timeline items, got {len(added)} "
            "(per-host segment grouping may be misaligned)"
        )

    proj, tl, mp = refresh_handles(resolve_obj)

    fade_f = max(1, int(0.02 * fps))
    disabled_count = 0
    for clip, m in zip(added, meta):
        try:
            clip.SetProperty("AudioFadeIn", fade_f)
            clip.SetProperty("AudioFadeOut", fade_f)
            if m.get("is_silence", False):
                if disable_timeline_clip(clip):
                    disabled_count += 1
        except Exception:
            pass
    print(f">>> Disabled {disabled_count} silence clip(s) on processed tracks")

    offset = 0
    for host, infos in zip(hosts_sorted, host_segments):
        n = len(infos)
        if n == 0:
            continue
        chunk = added[offset : offset + n]
        offset += n
        if not chunk:
            print(f">>> WARNING: no timeline items for host {host['compound_name']}")
            continue
        if len(chunk) != n:
            print(
                f">>> WARNING: host {host['compound_name']}: "
                f"appended {len(chunk)}/{n} items"
            )

    for host_idx, host in enumerate(hosts_sorted):
        dst_idx = current_track_count + host_idx + 1
        try:
            tl.SetTrackName("audio", dst_idx, f"[Processed] {host['compound_name']}")
        except Exception:
            pass

    n_with_segments = sum(1 for seg in host_segments if seg)
    print(
        f">>> Done: {n_with_segments} speaker track(s) with gated segments on timeline "
        f"(audio tracks {current_track_count + 1}–{current_track_count + len(hosts_sorted)}). "
        "No compound clips created."
    )


def create_compound_clip_from_items(tl, mp, items, compound_name):
    """Create a compound clip from a specific list of timeline items."""
    if not items:
        print(f">>> ERROR: No items provided for compound clip '{compound_name}'")
        return None
    
    # Create compound clip from the specific items
    try:
        # Try with just the items list (simplest approach)
        compound_clip = tl.CreateCompoundClip(items)
        
        if compound_clip:
            _ensure_compound_media_pool_name(compound_clip, compound_name)
            return compound_clip
        
        # Try with clipName parameter
        compound_clip_info = {"clipName": compound_name}
        compound_clip = tl.CreateCompoundClip(items, compound_clip_info)
        
        if compound_clip:
            _ensure_compound_media_pool_name(compound_clip, compound_name)
            return compound_clip
            
        # Try with selection-based approach
        tl.SetSelection([])
        tl.SetSelection(items)
        compound_clip = tl.CreateCompoundClip(items)
        
        if compound_clip:
            _ensure_compound_media_pool_name(compound_clip, compound_name)
            return compound_clip
            
        return None
            
    except Exception as e:
        print(f">>> ERROR: Exception creating compound clip '{compound_name}': {e}")
        return None

def create_compound_clip_from_track(tl, mp, track_index, compound_name, resolve_obj):
    """Create a compound clip from all items in a track."""
    print(f">>> Creating compound clip '{compound_name}' from track {track_index}")
    
    # Get all items from the track
    track_items = tl.GetItemListInTrack("audio", track_index) or []
    if not track_items:
        print(f">>> ERROR: No items found in track {track_index}")
        return None
    
    return create_compound_clip_from_items(tl, mp, track_items, compound_name)

def process_host(tl, mp, host, fps, assigned_track_index, resolve_obj, gap_frames=0):
    """Process a single host with butt-joined speech segments only"""
    
    # Load silence detection results
    json_path = os.path.join(OUTDIR, f"{host['name']}.json")
    
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    # Handle both list and dict formats
    if isinstance(data, list):
        segs = data
    else:
        segs = data['segments']
    print(f">>> {host['name']}: {len(segs)} segments, FPS: {fps}")
    
    # Use the original item from discover_hosts
    original_item = host["item"]
    if not original_item:
        print(f">>> ERROR: No original item for {host['name']}")
        return
    
    # Use the compound clip's Media Pool Item directly
    # The API limitation means only the first host will work, but let's try anyway
    mpi = original_item.GetMediaPoolItem()
    if not mpi:
        print(f">>> ERROR: Could not get media pool item for {host['name']}")
        return
    
    print(f">>> Using compound clip's Media Pool Item for {host['name']}")
    print(f">>> Compound clip duration: {original_item.GetEnd() - original_item.GetStart()} frames")
    
    # Duration clamping
    dur_frames = None
    try:
        frames_str = (mpi.GetClipProperty("Frames") or "").strip()
        if frames_str:
            dur_frames = int(float(frames_str))
    except:
        pass

    # Build all segments to maintain sync
    def clamp(v, lo, hi): 
        return max(lo, min(v, hi))
    
    orig_start_recF = original_item.GetStart()   # anchor processed track to match timeline start
    recF = orig_start_recF + gap_frames  # add gap between hosts

    all_clip_infos = []
    
    for i, seg in enumerate(segs):
        if "startF" in seg and "endF" in seg:
            sF, eF = int(seg["startF"]), int(seg["endF"])
        else:
            sF = int(seg.get("start_sec", 0) * fps)
            eF = int(seg.get("end_sec", 0) * fps)
        if dur_frames is not None:
            sF = clamp(sF, 0, dur_frames - 1)
            eF = clamp(eF, 0, dur_frames)
        if eF <= sF:
            continue

        clip_info = {
            "mediaPoolItem": mpi,
            "startFrame": sF,
            "endFrame": eF,
            "mediaType": 2,               # audio
            "recordFrame": recF,          # place immediately after previous segment
            "trackIndex": assigned_track_index,
            "is_silence": seg.get("is_silence", False)  # Store silence flag for later
        }
        all_clip_infos.append(clip_info)
        recF += (eF - sF)

    if not all_clip_infos:
        print(f">>> No segments for {host['name']}")
        return

    speech_count = len([c for c in all_clip_infos if not c.get("is_silence", False)])
    silence_count = len([c for c in all_clip_infos if c.get("is_silence", False)])
    print(f">>> Adding {len(all_clip_infos)} total clips ({speech_count} speech, {silence_count} silence) to track {assigned_track_index}...")
    
    # Ensure we're on Edit page and track is unlocked
    resolve_obj.OpenPage("edit")
    time.sleep(0.1)

    # Ensure track is accessible

    # Append all clips in chunks to avoid large batch failures
    items = append_in_chunks(all_clip_infos, mp)
    
    print(f">>> {host['name']}: appended {len(items)} total clips to track {assigned_track_index}")

    disabled_count = 0
    fade_s = CONFIG["crossfade_ms"] / 1000.0  # Convert ms to seconds
    fade_f = max(1, int(fade_s * fps))
    
    for i, item in enumerate(items):
        try:
            item.SetProperty("AudioFadeIn", fade_f)
            item.SetProperty("AudioFadeOut", fade_f)
            if i < len(all_clip_infos) and all_clip_infos[i].get("is_silence", False):
                if disable_timeline_clip(item):
                    disabled_count += 1
        except Exception:
            pass

    print(f">>> Created [Processed] {host['name']} with {len(items)} clips ({disabled_count} silence segments disabled)")
    
    # Final track count
    track_items = tl.GetItemListInTrack("audio", assigned_track_index) or []
    print(f">>> Track {assigned_track_index} now has {len(track_items)} items")
    
    # Create compound clip from the processed track
    print(f">>> Attempting to create compound clip for {host['name']}...")
    compound_name = f"{host['name']}_Gated"
    
    # Add a small delay to ensure all clips are properly placed
    time.sleep(0.5)
    
    # Verify items are still on the track before creating compound clip
    final_track_items = tl.GetItemListInTrack("audio", assigned_track_index) or []
    print(f">>> Final verification: Track {assigned_track_index} has {len(final_track_items)} items before compound creation")
    
    if final_track_items:
        # Create compound clip from just this speaker's clips (the items we just added)
        compound_clip = create_compound_clip_from_items(tl, mp, items, compound_name)
        
        if compound_clip:
            print(f">>> Successfully created compound clip '{compound_name}' for {host['name']}")
        else:
            print(f">>> WARNING: Could not create compound clip for {host['name']}")
            print(f">>> You may need to manually select the {len(items)} clips for {host['name']} and create a compound clip")
    else:
        print(f">>> ERROR: No items found on track {assigned_track_index} for compound clip creation")
        compound_clip = None
    
    return compound_clip

def main():
    """Main function."""
    if not resolve:
        print("ERROR: Could not connect to DaVinci Resolve")
        return
    
    # Get project and timeline
    proj = resolve.GetProjectManager().GetCurrentProject()
    if not proj:
        print("ERROR: No project loaded")
        return
    
    tl = proj.GetCurrentTimeline()
    if not tl:
        print("ERROR: No timeline loaded")
        return
    
    # Discover hosts
    try:
        hosts = discover_hosts(tl)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return
    
    # Render audio files
    print(">>> Switching to Deliver page")
    resolve.OpenPage("deliver")
    proj = resolve.GetProjectManager().GetCurrentProject()
    tl = proj.GetCurrentTimeline()
    mp = proj.GetMediaPool()
    
    # Load render preset
    render_preset = CONFIG["render_preset"]
    print(f">>> Exporting with render preset: {render_preset}")
    
    # Try to activate the preset
    preset_activated = False
    try:
        proj.LoadRenderPreset(render_preset)
        preset_activated = True
    except Exception as e:
        try:
            proj.SetCurrentRenderPreset(render_preset)
            preset_activated = True
        except Exception as e:
            print(f">>> WARNING: Could not load render preset '{render_preset}' - using current preset")
    
    # Set render mode and directory
    proj.SetCurrentRenderMode(0)
    try:
        proj.SetRenderSettings({"TargetDir": OUTDIR})
    except Exception as e:
        print(f">>> ERROR: Could not set target directory")
        return
    
    # Add render job and start rendering
    job_id = proj.AddRenderJob()
    if not job_id:
        print("ERROR: Could not create render job")
        return
    
    proj.StartRendering()
    
    # Wait for render to complete
    while proj.IsRenderingInProgress():
        time.sleep(1)
    
    proj.DeleteRenderJob(job_id)
    
    # Get media pool
    mp = proj.GetMediaPool()
    if not mp:
        print("ERROR: No media pool available")
        return
    
    # Process silence detection
    all_wav_files = glob.glob(os.path.join(OUTDIR, "*.wav"))
    
    # Collect individual WAV files for each host
    per_host_wavs = []
    for host in hosts:
        wav_file = None
        patterns_to_try = [
            f"{host['clip']}.wav",
            f"{host['clip']}00000000.wav",
            f"{host['clip']}_00000000.wav",
            f"{host['name']}.wav",
            f"{host['name']}00000000.wav",
            f"{host['name']}_00000000.wav"
        ]
        
        for pattern in patterns_to_try:
            candidate = os.path.join(OUTDIR, pattern)
            if os.path.exists(candidate):
                wav_file = candidate
                break
        
        if wav_file:
            per_host_wavs.append((host, wav_file))
    
    # Run silence detection (stdlib wave reader — no pydub/numpy; supports 24-bit WAV)
    if per_host_wavs:
        analysis_fps = float(proj.GetSetting("timelineFrameRate") or CONFIG["fps_hint"])
        successful = 0
        failed_hosts = []
        for host, wav_file in per_host_wavs:
            json_path = os.path.join(OUTDIR, f"{host['name']}.json")
            
            try:
                print(f">>> Analyzing: {host['name']} ({wav_file})")
                detect_silence(
                    wav_file,
                    min_sil_ms=CONFIG["min_silence_ms"],
                    pad_ms=CONFIG["padding_ms"],
                    out_json=json_path,
                    silence_thresh_db=CONFIG["silence_threshold_db"],
                    fps_hint=analysis_fps,
                    hold_ms=CONFIG.get("hold_ms", 500),
                )
                
                if os.path.exists(json_path):
                    successful += 1
                else:
                    failed_hosts.append(host["name"])
            except Exception as e:
                print(f">>> ERROR: Silence detection failed for {host['name']}: {e}")
                failed_hosts.append(host["name"])
        
        print(f">>> Silence detection complete: {successful}/{len(per_host_wavs)} successful")
        if failed_hosts:
            print(f">>> ERROR: No analysis JSON for: {', '.join(failed_hosts)}")
            print(">>> Aborting timeline rebuild — sync detect_silence.py and re-run.")
            return
    else:
        print(f">>> ERROR: No WAV files found for processing")
        return
    
    # Switch to edit page and process audio
    print(">>> Switching to Edit page")
    proj, tl, mp = refresh_handles(resolve)
    
    # Get FPS from timeline settings
    fps = float(proj.GetSetting("timelineFrameRate") or "29.97")
    
    # Process clips using grouped approach
    use_compound_processing = CONFIG.get("use_compound_processing", True)
    
    if use_compound_processing:
        process_compound_clips(resolve, tl, mp, proj, fps, hosts)
    else:
        # Individual processing approach (simplified)
        print(">>> Using individual track processing approach")
        # ... individual processing code would go here if needed
    
    print(">>> Processing complete (timeline processed tracks; no compounds).")

if __name__ == "__main__":
    try:
        main()
    finally:
        # Clean up temp directory
        if os.path.exists(OUTDIR):
            shutil.rmtree(OUTDIR, ignore_errors=True)
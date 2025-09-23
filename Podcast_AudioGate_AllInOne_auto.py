#!/usr/bin/env python3
"""
Podcast Audio Gate - All-in-One Auto Script
Processes audio tracks by detecting silence and creating segmented versions.
Uses timeline manipulation instead of AppendToTimeline to avoid API issues.
"""

import os
import sys
import json
import time
import glob
import shutil
import subprocess
import gc
import tempfile
import re
from concurrent.futures import ThreadPoolExecutor

# Add the detect_silence script to path
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

for path in possible_paths:
    if os.path.exists(os.path.join(path, "detect_silence.py")):
        sys.path.insert(0, path)
        print(f">>> Found detect_silence.py in: {path}")
        break
else:
    print("ERROR: Could not find detect_silence.py in any of these locations:")
    for path in possible_paths:
        print(f"  - {path}")
    sys.exit(1)

try:
    from detect_silence import detect_silence
    print(">>> Successfully imported detect_silence")
except ImportError as e:
    print(f"ERROR: Could not import detect_silence.py: {e}")
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
    from config import *
    CONFIG = {
        "render_preset": RENDER_PRESET,
        "output_format": OUTPUT_FORMAT,
        "audio_codec": AUDIO_CODEC,
        "audio_bit_depth": AUDIO_BIT_DEPTH,
        "audio_sample_rate": AUDIO_SAMPLE_RATE,
        "silence_threshold_db": SILENCE_THRESHOLD_DB,
        "min_silence_ms": MIN_SILENCE_MS,
        "padding_ms": PADDING_MS,
        "hold_ms": HOLD_MS,
        "crossfade_ms": CROSSFADE_MS,
        "batch_size": BATCH_SIZE,
        "fps_hint": FPS_HINT,
        "script_dir": SCRIPT_DIR,
        "temp_dir": TEMP_DIR,
        "track_name_normalize": True,
        "use_compound_processing": True,  # Set to True to use compound processing approach
    }
    print(">>> Loaded configuration from config.py")
except ImportError:
    # Fallback configuration if config.py is not found
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
        "use_compound_processing": True,  # Set to True to use compound processing approach
    }
    print(">>> Using default configuration (config.py not found)")

def which(x): 
    """Check if executable exists in PATH."""
    return any(os.path.exists(os.path.join(p, x)) for p in os.getenv("PATH","").split(os.pathsep))

# Suppress pydub's ffmpeg warning since it's just informational
import warnings
warnings.filterwarnings("ignore", message="Couldn't find ffmpeg or avconv")

# Since pydub is already working (as evidenced by the warning), we'll skip the strict check
# and let pydub handle ffmpeg detection. The warning just means it had to guess the path.
print(">>> Using pydub's ffmpeg detection (may show warning but will work)")

# Use temporary directory for safer handling
if CONFIG["temp_dir"]:
    OUTDIR = CONFIG["temp_dir"]
    os.makedirs(OUTDIR, exist_ok=True)
else:
    OUTDIR = tempfile.mkdtemp(prefix="_temp_gate_")
print(f">>> Using temporary directory: {OUTDIR}")

def s2f(seconds, fps):
    """Convert seconds to frames."""
    return int(seconds * fps)

def f2s(frames, fps):
    """Convert frames to seconds."""
    return frames / fps

def refresh_handles(resolve_obj):
    """Refresh object handles to stabilize the API layer."""
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

def normalize_name(raw):
    base = raw.strip()
    return base.title()  # Always use title case for consistency

def json_fresh(host_name, src_mtime, max_age=86400):
    """Check if JSON file is fresh compared to source media."""
    jp = os.path.join(OUTDIR, f"{host_name}.json")
    return os.path.exists(jp) and os.path.getmtime(jp) >= src_mtime and (time.time()-os.path.getmtime(jp)) < max_age

def append_in_chunks(infos, mp, size=None):
    """Append timeline items in chunks to avoid large batch failures."""
    if size is None:
        size = CONFIG["batch_size"]
    out = []
    for i in range(0, len(infos), size):
        chunk = infos[i:i+size]
        print(f">>> DEBUG: Appending chunk {i//size + 1}/{(len(infos) + size - 1)//size} with {len(chunk)} items")
        result = mp.AppendToTimeline(chunk) or []
        print(f">>> DEBUG: AppendToTimeline returned {len(result)} items for this chunk")
        out.extend(result)
        print(f">>> Appended chunk {i//size + 1}/{(len(infos) + size - 1)//size} ({len(chunk)} items)")
    return out

def discover_hosts(tl):
    """Find all audio tracks with clips in the timeline."""
    hosts = []
    seen = set()
    
    for i in range(1, tl.GetTrackCount("audio") + 1):
        items = tl.GetItemListInTrack("audio", i) or []
        for item in items:
            try:
                name = item.GetName()
                # Accept any non-empty track name
                if name and name.strip():
                    # Use the original name as the host name, or normalize if configured
                    if CONFIG.get("track_name_normalize", True):
                        host_name = normalize_name(name.strip())
                    else:
                        host_name = name.strip()
                    
                    # Skip if we've already seen this name
                    if host_name not in seen:
                        hosts.append({
                            "name": host_name,
                            "clip": name,
                            "track": i,
                            "item": item
                        })
                        seen.add(host_name)
                        print(f">>> Found track: '{name}' -> '{host_name}'")
            except:
                continue
    
    if not hosts:
        raise RuntimeError("No audio tracks with clips found. Please ensure your timeline has audio tracks with named clips.")
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

def append_from_compound(item, dst_idx, segs, fps, mp):
    """Append segments from compound clip to destination track."""
    mpi = item.GetMediaPoolItem()
    if not mpi: 
        print(">>> ERROR: No Media Pool Item found for compound clip")
        return []
    
    # Get compound clip duration
    try: 
        durF = int(float(mpi.GetClipProperty("Frames") or 0))
        print(f">>> DEBUG: Compound clip duration: {durF} frames")
    except: 
        durF = 0
        print(f">>> DEBUG: Could not get compound clip duration, using 0")
    
    anchor = int(item.GetStart())  # compound's current timeline start
    recF = anchor
    infos = []
    
    print(f">>> DEBUG: Processing {len(segs)} segments for track {dst_idx}")
    
    for i, (sF, eF, isSil) in enumerate(segs):
        if durF:  # clamp if we know length
            sF = max(0, min(sF, durF-1))
            eF = max(0, min(eF, durF))
            if eF <= sF: 
                print(f">>> DEBUG: Skipping segment {i} (clamped to zero length)")
                continue
        
        clip_info = {
            "mediaPoolItem": mpi,
            "startFrame": sF,
            "endFrame": eF,
            "recordFrame": recF,
            "trackIndex": dst_idx,
            "mediaType": 2,
            "trackType": "audio",
            "is_silence": isSil
        }
        infos.append(clip_info)
        recF += (eF - sF)  # butt-join
        
        # Debug first few segments
        if i < 3:
            print(f">>> DEBUG: Segment {i+1}: {sF}-{eF} frames, record at {recF-(eF-sF)}, silence={isSil}")
    
    if not infos: 
        print(">>> ERROR: No valid segments to append")
        return []
    
    print(f">>> DEBUG: Created {len(infos)} clip infos for AppendToTimeline")
    print(f">>> DEBUG: Sample payload: {infos[0] if infos else 'None'}")
    
    # Try to append to timeline
    try:
        result = mp.AppendToTimeline(infos)
        if result is None:
            print(">>> ERROR: AppendToTimeline returned None")
            return []
        print(f">>> DEBUG: AppendToTimeline returned {len(result)} items")
        return result
    except Exception as e:
        print(f">>> ERROR: AppendToTimeline failed: {e}")
        return []

def process_compound_clips(tl, mp, proj, fps, hosts):
    """Process compound clips from source tracks to new processed tracks, grouping by source track."""
    print(f">>> Processing compound clips for {len(hosts)} hosts...")
    
    # Group hosts by source track
    hosts_by_track = {}
    for host in hosts:
        src_idx = host["track"]
        if src_idx not in hosts_by_track:
            hosts_by_track[src_idx] = []
        hosts_by_track[src_idx].append(host)
    
    print(f">>> Found hosts on {len(hosts_by_track)} source tracks: {list(hosts_by_track.keys())}")
    
    # Get current track count
    current_track_count = tl.GetTrackCount("audio")
    print(f">>> Current audio track count: {current_track_count}")
    
    # Ensure we have enough destination tracks (one per source track)
    needed_tracks = current_track_count + len(hosts_by_track)
    while tl.GetTrackCount("audio") < needed_tracks:
        tl.AddTrack("audio")
    print(f">>> Ensured {needed_tracks} total audio tracks exist")
    
    # Process each source track
    for i, (src_idx, track_hosts) in enumerate(hosts_by_track.items()):
        dst_idx = current_track_count + i + 1
        
        print(f">>> Processing {len(track_hosts)} hosts from track {src_idx} to track {dst_idx}")
        print(f">>> Hosts: {[h['name'] for h in track_hosts]}")
        
        # Unlock destination track
        try:
            if tl.GetTrackLockState("audio", dst_idx):
                tl.SetTrackLockState("audio", dst_idx, False)
                print(f">>> Unlocked track {dst_idx}")
        except Exception as e:
            print(f">>> Could not manage track lock for {dst_idx}: {e}")
        
        # Get all compound clips from source track
        items = tl.GetItemListInTrack("audio", src_idx) or []
        if not items: 
            print(f">>> Track {src_idx} empty, skipping")
            continue
        
        print(f">>> Found {len(items)} compound clips on track {src_idx}")
        
        # Process all hosts on this track together
        all_clip_infos = []
        total_segments = 0
        
        for host in track_hosts:
            # Load segments for this host
            json_path = f"{OUTDIR}/{host['name']}.json"
            if not os.path.exists(json_path):
                print(f">>> No JSON file found for {host['name']}: {json_path}")
                continue
                
            segs = load_segments(json_path, fps)
            if not segs:
                print(f">>> No segments found for {host['name']}")
                continue
                
            print(f">>> Loaded {len(segs)} segments for {host['name']}")
            total_segments += len(segs)
            
            # Find the matching compound clip for this host
            matching_item = None
            for item in items:
                if item.GetName() == host['clip']:
                    matching_item = item
                    break
            
            if not matching_item:
                print(f">>> WARNING: Could not find compound clip '{host['clip']}' for {host['name']}")
                continue
            
            # Create clip infos for this host's segments
            mpi = matching_item.GetMediaPoolItem()
            if not mpi:
                print(f">>> ERROR: No Media Pool Item for {host['name']}")
                continue
            
            # Get compound clip duration and anchor position
            try: 
                durF = int(float(mpi.GetClipProperty("Frames") or 0))
            except: 
                durF = 0
            
            anchor = int(matching_item.GetStart())
            recF = anchor
            
            # Add any gap between compound clips
            if all_clip_infos:
                # Add small gap between different hosts
                recF = all_clip_infos[-1]["recordFrame"] + (all_clip_infos[-1]["endFrame"] - all_clip_infos[-1]["startFrame"]) + int(0.1 * fps)  # 100ms gap
            
            for sF, eF, isSil in segs:
                if durF:  # clamp if we know length
                    sF = max(0, min(sF, durF-1))
                    eF = max(0, min(eF, durF))
                    if eF <= sF: continue
                
                clip_info = {
                    "mediaPoolItem": mpi,
                    "startFrame": sF,
                    "endFrame": eF,
                    "recordFrame": recF,
                    "trackIndex": dst_idx,
                    "mediaType": 2,
                    "trackType": "audio",
                    "is_silence": isSil,
                    "host_name": host['name']  # Store host name for later processing
                }
                all_clip_infos.append(clip_info)
                recF += (eF - sF)  # butt-join
        
        if not all_clip_infos:
            print(f">>> No valid segments found for track {src_idx}")
            continue
        
        print(f">>> Created {len(all_clip_infos)} total clip infos for track {dst_idx}")
        
        # Debug: Check track state before append
        track_items_before = tl.GetItemListInTrack("audio", dst_idx) or []
        print(f">>> DEBUG: Track {dst_idx} has {len(track_items_before)} items before append")
        
        # Append all segments in one call
        print(f">>> Appending {len(all_clip_infos)} clips to track {dst_idx}...")
        added = append_in_chunks(all_clip_infos, mp)
        print(f">>> AppendToTimeline returned {len(added)} items for track {dst_idx}")
        
        # Debug: Check track state after append
        track_items_after = tl.GetItemListInTrack("audio", dst_idx) or []
        print(f">>> DEBUG: Track {dst_idx} has {len(track_items_after)} items after append")
        print(f">>> DEBUG: Net change: +{len(track_items_after) - len(track_items_before)} items")
        
        if not added:
            print(f">>> WARNING: No items were appended for track {src_idx} - this may indicate an API issue")
            continue
        
        # Set track name (use first host's name or generic name)
        track_name = f"[Processed] {track_hosts[0]['name']}"
        if len(track_hosts) > 1:
            track_name += f" +{len(track_hosts)-1} more"
        
        try:
            tl.SetTrackName("audio", dst_idx, track_name)
            print(f">>> Set track name: {track_name}")
        except Exception as e:
            print(f">>> Could not set track name: {e}")
        
        # Add fades and disable silence segments
        fade_f = max(1, int(0.02*fps))
        disabled_count = 0
        for j, (clip, clip_info) in enumerate(zip(added, all_clip_infos)):
            try:
                clip.SetProperty("AudioFadeIn",  fade_f)
                clip.SetProperty("AudioFadeOut", fade_f)
                if clip_info.get("is_silence", False): 
                    try: 
                        clip.SetClipEnabled(False)
                        disabled_count += 1
                    except: 
                        try:
                            clip.SetProperty("Enabled", False)
                            disabled_count += 1
                        except:
                            pass
            except Exception as e:
                print(f">>> Could not process clip {j}: {e}")
        
        print(f">>> Track {dst_idx}: Disabled {disabled_count} silence segments from {len(track_hosts)} hosts")

def process_host_new_timeline(tl, mp, host, fps, track_index, resolve_obj):
    """Process a single host and append to new timeline with silence gating."""
    
    print(f">>> {host['name']}: Processing for new timeline on track {track_index}")
    
    # Force the target timeline (critical fix)
    resolve_obj.OpenPage("edit")
    # Get fresh project reference
    proj = resolve_obj.GetProjectManager().GetCurrentProject()
    if not proj:
        print(f">>> ERROR: Could not get current project for {host['name']}")
        return
    proj.SetCurrentTimeline(tl)
    
    # Create a new track for this host instead of managing existing ones
    try:
        print(f">>> DEBUG: Creating new track for {host['name']}")
        
        # Get current track count before adding
        current_track_count = tl.GetTrackCount("audio")
        print(f">>> DEBUG: Current track count: {current_track_count}")
        
        success = tl.AddTrack("audio")
        if not success:
            print(f">>> ERROR: Could not create new track for {host['name']}")
            return
        
        # The new track will be at current_track_count + 1
        new_track_index = current_track_count + 1
        print(f">>> DEBUG: Created track {new_track_index} for {host['name']}")
        
        # Try to set track name (may fail but that's ok)
        try:
            tl.SetTrackName("audio", new_track_index, f"[Processed] {host['name']}")
            print(f">>> DEBUG: Set track name for {host['name']}")
        except Exception as e:
            print(f">>> WARNING: Could not set track name for {host['name']}: {e}")
        
        # Try to ensure track is unlocked (may fail but that's ok)
        try:
            if tl.GetTrackLockState("audio", new_track_index):
                tl.SetTrackLockState("audio", new_track_index, False)
                print(f">>> DEBUG: Unlocked track for {host['name']}")
        except Exception as e:
            print(f">>> WARNING: Could not manage track lock for {host['name']}: {e}")
            
        # Update track_index to use the new track
        track_index = new_track_index
        print(f">>> DEBUG: Using track {track_index} for {host['name']}")
        
    except Exception as e:
        print(f">>> ERROR: Could not create track for {host['name']}: {e}")
        return
    
    # Load JSON data
    json_path = os.path.join(OUTDIR, f"{host['name']}.json")
    if not os.path.exists(json_path):
        print(f">>> ERROR: {host['name']}.json not found")
        return
    
    try:
        with open(json_path, 'r') as f:
            segments = json.load(f)
        print(f">>> {host['name']}: Loaded {len(segments)} segments from JSON")
        if segments:
            print(f">>> {host['name']}: First segment keys: {list(segments[0].keys())}")
    except Exception as e:
        print(f">>> ERROR: Could not load {host['name']}.json: {e}")
        return
    
    # Get the original Media Pool Item
    original_item = host["item"]
    mpi = original_item.GetMediaPoolItem()
    if not mpi:
        print(f">>> ERROR: Could not get media pool item for {host['name']}")
        return
    
    # Anchor placement to the compound's position
    anchor = int(original_item.GetStart())
    recF = anchor
    
    # Create clip infos for all segments
    all_clip_infos = []
    for i, seg in enumerate(segments):
        # Handle different JSON formats
        if "start_sec" in seg and "end_sec" in seg:
            # detect_silence format
            sF = int(seg["start_sec"] * fps)
            eF = int(seg["end_sec"] * fps)
            is_silence = seg.get("is_silence", False)
        elif "start" in seg and "end" in seg:
            # Alternative format
            sF = int(seg["start"] * fps)
            eF = int(seg["end"] * fps)
            is_silence = seg.get("is_silence", False)
        elif "start_time" in seg and "end_time" in seg:
            # Another alternative format
            sF = int(seg["start_time"] * fps)
            eF = int(seg["end_time"] * fps)
            is_silence = seg.get("is_silence", False)
        else:
            print(f">>> WARNING: Unknown segment format: {seg}")
            continue
            
        if eF <= sF:
            continue
            
        clip_info = {
            "mediaPoolItem": mpi,
            "startFrame": sF,
            "endFrame": eF,
            "recordFrame": recF,
            "trackIndex": track_index,
            "mediaType": 2,  # Audio
            "trackType": "audio",  # Critical fix - tells Resolve it's audio
            "is_silence": is_silence
        }
        all_clip_infos.append(clip_info)
        recF += (eF - sF)  # butt-join
    
    print(f">>> {host['name']}: Created {len(all_clip_infos)} clip infos for track {track_index}")
    
    # Debug: Check track state before append
    track_items_before = tl.GetItemListInTrack("audio", track_index) or []
    print(f">>> DEBUG: Track {track_index} has {len(track_items_before)} items before append")
    
    # Append all clips to the new timeline
    print(f">>> {host['name']}: Appending {len(all_clip_infos)} clips to new timeline...")
    items = append_in_chunks(all_clip_infos, mp)
    
    # Debug: Check track state after append
    track_items_after = tl.GetItemListInTrack("audio", track_index) or []
    print(f">>> DEBUG: Track {track_index} has {len(track_items_after)} items after append")
    print(f">>> DEBUG: Net change: +{len(track_items_after) - len(track_items_before)} items")
    
    if not items:
        print(f">>> ERROR: No items were appended for {host['name']}")
        print(f">>> DEBUG: AppendToTimeline returned empty list - this may indicate API issues")
        return
    
    print(f">>> {host['name']}: Successfully appended {len(items)} clips to track {track_index}")
    
    # Disable silence segments
    disabled_count = 0
    for i, item in enumerate(items):
        try:
            if i < len(all_clip_infos) and all_clip_infos[i].get("is_silence", False):
                item.SetClipEnabled(False)
                disabled_count += 1
        except Exception as e:
            print(f">>> WARNING: Could not disable silence segment {i}: {e}")
    
    print(f">>> {host['name']}: Disabled {disabled_count} silence segments on track {track_index}")

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
    print(f">>> DEBUG: Processing {len(segs)} segments for {host['name']}")
    
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
        
        # Debug: Print first few and last few clips
        if i < 3 or i >= len(segs) - 3:
            print(f">>> DEBUG: Clip {i+1}: {sF}-{eF} frames, record at {recF}, silence={seg.get('is_silence', False)}")
        
        recF += (eF - sF)
    
    print(f">>> DEBUG: Created {len(all_clip_infos)} clip infos for {host['name']}")
    print(f">>> DEBUG: First clip: {all_clip_infos[0] if all_clip_infos else 'None'}")
    print(f">>> DEBUG: Last clip: {all_clip_infos[-1] if all_clip_infos else 'None'}")

    if not all_clip_infos:
        print(f">>> No segments for {host['name']}")
        return

    speech_count = len([c for c in all_clip_infos if not c.get("is_silence", False)])
    silence_count = len([c for c in all_clip_infos if c.get("is_silence", False)])
    print(f">>> Adding {len(all_clip_infos)} total clips ({speech_count} speech, {silence_count} silence) to track {assigned_track_index}...")
    
    # Debug: Print sample clip data
    print(f">>> DEBUG: Sample clip data for {host['name']}:")
    for i, clip in enumerate(all_clip_infos[:3]):
        print(f">>>   Clip {i+1}: start={clip['startFrame']}, end={clip['endFrame']}, record={clip['recordFrame']}, track={clip['trackIndex']}")
    
    # Ensure we're on Edit page and track is unlocked
    resolve_obj.OpenPage("edit")
    time.sleep(0.1)

    # Debug: Check track state before append
    track_items_before = tl.GetItemListInTrack("audio", assigned_track_index) or []
    print(f">>> DEBUG: Track {assigned_track_index} has {len(track_items_before)} items before append")

    # Unlock the destination track
    try:
        if tl.GetTrackLockState("audio", assigned_track_index):
            print(f">>> WARNING: Track {assigned_track_index} is locked! Unlocking…")
            tl.SetTrackLockState("audio", assigned_track_index, False)
    except Exception as e:
        print(f">>> Could not check/unlock track {assigned_track_index}: {e}")

    # Append all clips in chunks to avoid large batch failures
    print(f">>> DEBUG: About to append {len(all_clip_infos)} clips to timeline")
    print(f">>> DEBUG: First few clip infos:")
    for i, clip in enumerate(all_clip_infos[:3]):
        print(f">>>   Clip {i+1}: start={clip['startFrame']}, end={clip['endFrame']}, record={clip['recordFrame']}, track={clip['trackIndex']}")
    
    items = append_in_chunks(all_clip_infos, mp)
    
    print(f">>> DEBUG: AppendToTimeline returned {len(items)} items")
    
    # Debug: Check track state after append
    track_items_after = tl.GetItemListInTrack("audio", assigned_track_index) or []
    print(f">>> DEBUG: Track {assigned_track_index} has {len(track_items_after)} items after append")
    print(f">>> DEBUG: Net change: +{len(track_items_after) - len(track_items_before)} items")
    
    print(f">>> {host['name']}: appended {len(items)} total clips to track {assigned_track_index}")

    # Disable silence segments and add crossfades
    disabled_count = 0
    fade_s = CONFIG["crossfade_ms"] / 1000.0  # Convert ms to seconds
    fade_f = max(1, int(fade_s * fps))
    
    for i, item in enumerate(items):
        try:
            # Add crossfades to all clips
            item.SetProperty("AudioFadeIn", fade_f)
            item.SetProperty("AudioFadeOut", fade_f)
            
            # Disable silence segments
            if i < len(all_clip_infos) and all_clip_infos[i].get("is_silence", False):
                try:
                    item.SetClipEnabled(False)
                    disabled_count += 1
                except:
                    # Fallback method
                    try:
                        item.SetProperty("Enabled", False)
                        disabled_count += 1
                    except:
                        pass
        except: 
            pass

    print(f">>> Created [Processed] {host['name']} with {len(items)} clips ({disabled_count} silence segments disabled)")
    
    # Final track count
    track_items = tl.GetItemListInTrack("audio", assigned_track_index) or []
    print(f">>> Track {assigned_track_index} now has {len(track_items)} items")

def create_segmented_tracks(tl, mp, hosts, fps, track_assignments=None):
    """Create new tracks and duplicate original audio for timeline manipulation."""
    
    print(f">>> Creating tracks and duplicating original audio...")
    
    for i, h in enumerate(hosts):
        print(f">>> Creating track for {h['name']}...")
        
        # Get the original track item to use as source
        track_index = h["track"]
        original_items = tl.GetItemListInTrack("audio", track_index) or []
        
        if not original_items:
            print(f">>> No items found in track {track_index} for {h['name']}")
            continue
            
        original_item = original_items[0]
        print(f">>> Using original item as source: {original_item.GetStart()}-{original_item.GetEnd()}")
        
        # Get assigned track index
        assigned_track_index = track_assignments[h['name']] if track_assignments else i + 4
        
        # Create new track
        new_track_index = tl.AddTrack("audio")
        tl.SetTrackName("audio", new_track_index, f"[Processed] {h['name']}")
        print(f">>> Created new track at index {new_track_index}")
        
        # Duplicate the original item to the new track
        # This is a simplified approach - in practice, you might need to use Resolve's copy/paste API
        print(f">>> WARNING: Track duplication not fully implemented - need to copy original item to track {new_track_index}")
        print(f">>> For now, the track is created but empty - timeline manipulation approach needs completion")
        
        # Update track assignments
        if track_assignments:
            track_assignments[h['name']] = new_track_index

def main():
    """Main function."""
    print(">>> Connecting to Resolve…")
    
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
        print(f">>> Hosts: {', '.join([h['clip'] for h in hosts])}")
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return
    
    
    # Check if all JSONs are fresh before rendering
    print(f">>> Checking JSON freshness...")
    all_fresh = True
    source_mtimes = {}
    
    for host in hosts:
        # Get source media modification time
        try:
            mpi = host["item"].GetMediaPoolItem()
            if mpi:
                # Try to get file path from media pool item
                file_path = mpi.GetClipProperty("File Path")
                if file_path and os.path.exists(file_path):
                    src_mtime = os.path.getmtime(file_path)
                    source_mtimes[host['name']] = src_mtime
                    fresh = json_fresh(host['name'], src_mtime)
                    print(f">>> {host['name']}: JSON fresh = {fresh} (source: {src_mtime}, max_age: 86400s)")
                    if not fresh:
                        all_fresh = False
                else:
                    print(f">>> {host['name']}: Could not get source file path, will re-render")
                    all_fresh = False
            else:
                print(f">>> {host['name']}: Could not get media pool item, will re-render")
                all_fresh = False
        except Exception as e:
            print(f">>> {host['name']}: Error checking freshness: {e}, will re-render")
            all_fresh = False
    
    if all_fresh:
        print(f">>> All JSONs are fresh! Skipping render phase...")
        skip_render = True
    else:
        print(f">>> Some JSONs are stale or missing. Proceeding with render...")
        skip_render = False
    
    if not skip_render:
        # Switch to deliver page
        resolve.OpenPage("deliver")
        print(f">>> Current page: {resolve.GetCurrentPage()}")
        
        # Re-fetch fresh handles after page change
        proj = resolve.GetProjectManager().GetCurrentProject()
        tl = proj.GetCurrentTimeline()
        mp = proj.GetMediaPool()
        
        # Load render preset
        render_preset = CONFIG["render_preset"]
        print(f">>> Loading render preset: {render_preset}")
        
        proj.LoadRenderPreset(render_preset)
        
        # Set render mode
        proj.SetCurrentRenderMode(0)  # Individual clips
        print(f">>> Setting render mode...")
        
        # Set only the target directory - let the preset handle everything else
        try:
            proj.SetRenderSettings({"TargetDir": OUTDIR})
            print(f">>> Set target directory: {OUTDIR}")
        except Exception as e:
            print(f">>> WARNING: Could not set target directory: {e}")
            return
        
        
        # Add render job
        job_id = proj.AddRenderJob()
        if not job_id:
            print("ERROR: Could not create render job")
            return
        
        print(f">>> Render job created: {job_id}")
        
        # Start rendering
        print(f">>> Starting render...")
        proj.StartRendering()
        
        # Wait for render to complete
        print(f">>> Waiting for render to complete...")
        while proj.IsRenderingInProgress():
            time.sleep(1)
        
        print(f">>> Render complete -> {OUTDIR}")
        
        # Clear render job
        proj.DeleteRenderJob(job_id)
        print(f">>> Render job {job_id} cleared from queue")
    else:
        print(f">>> Skipped rendering - using existing JSON files")
    
    # Get media pool
    mp = proj.GetMediaPool()
    if not mp:
        print("ERROR: No media pool available")
        return
    
    # Detect silence for each host (only if we rendered new files)
    if not skip_render:
        print(f">>> Checking for exported WAV files in: {OUTDIR}")
        
        all_wav_files = glob.glob(os.path.join(OUTDIR, "*.wav"))
        print(f">>> Found {len(all_wav_files)} WAV files")
        
        # Collect individual WAV files for each host
        per_host_wavs = []
        for host in hosts:
            print(f">>> Processing {host['name']}...")
            
            wav_file = None
            patterns_to_try = [
                f"{host['clip']}.wav",                    # Expected: [TrackName].wav
                f"{host['clip']}00000000.wav",            # Actual: [TrackName]00000000.wav
                f"{host['name']}.wav",                    # Fallback: [NormalizedName].wav
                f"{host['name']}00000000.wav"             # Fallback: [NormalizedName]00000000.wav
            ]
            
            for pattern in patterns_to_try:
                candidate = os.path.join(OUTDIR, pattern)
                if os.path.exists(candidate):
                    wav_file = candidate
                    break
            
            if not wav_file:
                print(f">>> ERROR: No WAV file found for {host['name']}")
                print(f">>> Tried patterns: {patterns_to_try}")
                print(f">>> Available WAV files: {[os.path.basename(f) for f in all_wav_files]}")
                continue
            
            print(f">>> Found: {os.path.basename(wav_file)}")
            per_host_wavs.append((host, wav_file))
        
        # Process silence detection using exported WAV files
        if per_host_wavs:
            print(f">>> Running silence detection on {len(per_host_wavs)} files...")
            
            successful = 0
            for host, wav_file in per_host_wavs:
                json_path = os.path.join(OUTDIR, f"{host['name']}.json")
                
                try:
                    print(f">>> [{host['name']}] Analyzing: {os.path.basename(wav_file)}")
                    
                    # Process the WAV file
                    result = detect_silence(
                        wav_file, 
                        min_sil_ms=CONFIG["min_silence_ms"],
                        pad_ms=CONFIG["padding_ms"],
                        out_json=json_path,
                        silence_thresh_db=CONFIG["silence_threshold_db"],
                        fps_hint=CONFIG["fps_hint"]
                    )
                    print(f">>> [{host['name']}] Found {len(result) if result else 'None'} segments")
                    
                    if os.path.exists(json_path):
                        file_size = os.path.getsize(json_path)
                        print(f">>> [{host['name']}] SUCCESS: {os.path.basename(json_path)}")
                        
                        # Verify JSON content
                        try:
                            with open(json_path, 'r') as f:
                                json_data = json.load(f)
                            print(f">>> [{host['name']}] JSON contains {len(json_data)} segments")
                        except Exception as json_e:
                            print(f">>> [{host['name']}] WARNING: Could not read JSON content: {json_e}")
                        successful += 1
                    else:
                        print(f">>> [{host['name']}] ERROR: detect_silence did not create {os.path.basename(json_path)}")
                except Exception as e:
                    print(f">>> [{host['name']}] ERROR: silence detection failed: {e}")
                    import traceback
                    print(f">>> [{host['name']}] Traceback: {traceback.format_exc()}")
            
            print(f">>> Silence detection complete: {successful}/{len(per_host_wavs)} successful")
        else:
            print(f">>> No WAV files found for processing")
    else:
        print(f">>> Skipped silence detection - using existing JSON files")
    
    # Switch to edit page and refresh handles
    proj, tl, mp = refresh_handles(resolve)
    
    print(f">>> DEBUG: Initial timeline object: {type(tl)}")
    print(f">>> DEBUG: Initial timeline is None: {tl is None}")
    
    # Verify silence detection files
    for host in hosts:
        json_path = os.path.join(OUTDIR, f"{host['name']}.json")
        if not os.path.exists(json_path):
            print(f">>> WARNING: {host['name']}.json not found")
    
    # Get FPS from timeline settings
    fps = float(proj.GetSetting("timelineFrameRate") or "29.97")
    
    # Check if we should use compound processing (tracks A2/A3 -> A5/A6)
    use_compound_processing = CONFIG.get("use_compound_processing", True)  # Try compound approach by default
    
    if use_compound_processing:
        print(">>> Using compound processing approach")
        process_compound_clips(tl, mp, proj, fps, hosts)
    else:
        print(">>> Using individual track processing approach")
        
        # Process all hosts with a single AppendToTimeline call
        print(f">>> Processing all {len(hosts)} hosts with single AppendToTimeline call...")
        
        # Get fresh timeline reference
        try:
            fresh_proj, fresh_tl, fresh_mp = refresh_handles(resolve)
            print(f">>> DEBUG: Fresh timeline object: {type(fresh_tl)}")
            print(f">>> DEBUG: Fresh timeline is None: {fresh_tl is None}")
        except Exception as e:
            print(f">>> ERROR: Could not get fresh timeline: {e}")
            return
        
        # Create tracks for all hosts first
        track_assignments = {}
        for i, host in enumerate(hosts):
            try:
                current_track_count = fresh_tl.GetTrackCount("audio")
                success = fresh_tl.AddTrack("audio")
                if not success:
                    print(f">>> ERROR: Could not create track for {host['name']}")
                    continue
                
                new_track_index = current_track_count + 1
                fresh_tl.SetTrackName("audio", new_track_index, f"[Processed] {host['name']}")
                track_assignments[host['name']] = new_track_index
                print(f">>> Created track {new_track_index} for {host['name']}")
            except Exception as e:
                print(f">>> ERROR: Could not create track for {host['name']}: {e}")
                continue
        
        # Collect all clip infos from all hosts
        all_clip_infos = []
        for host in hosts:
            if host['name'] not in track_assignments:
                continue
                
            track_index = track_assignments[host['name']]
            print(f">>> Processing {host['name']} for track {track_index}")
            
            # Load JSON data
            json_path = os.path.join(OUTDIR, f"{host['name']}.json")
            if not os.path.exists(json_path):
                print(f">>> ERROR: {host['name']}.json not found")
                continue
            
            try:
                with open(json_path, 'r') as f:
                    segments = json.load(f)
                print(f">>> {host['name']}: Loaded {len(segments)} segments from JSON")
            except Exception as e:
                print(f">>> ERROR: Could not load {host['name']}.json: {e}")
                continue
            
            # Get the original Media Pool Item
            original_item = host["item"]
            mpi = original_item.GetMediaPoolItem()
            if not mpi:
                print(f">>> ERROR: Could not get media pool item for {host['name']}")
                continue
            
            # Anchor placement to the compound's position
            anchor = int(original_item.GetStart())
            recF = anchor
            
            # Create clip infos for all segments
            for seg in segments:
                # Handle different JSON formats
                if "start_sec" in seg and "end_sec" in seg:
                    sF = int(seg["start_sec"] * fps)
                    eF = int(seg["end_sec"] * fps)
                    is_silence = seg.get("is_silence", False)
                elif "start" in seg and "end" in seg:
                    sF = int(seg["start"] * fps)
                    eF = int(seg["end"] * fps)
                    is_silence = seg.get("is_silence", False)
                elif "start_time" in seg and "end_time" in seg:
                    sF = int(seg["start_time"] * fps)
                    eF = int(seg["end_time"] * fps)
                    is_silence = seg.get("is_silence", False)
                else:
                    continue
                    
                if eF <= sF:
                    continue
                    
                clip_info = {
                    "mediaPoolItem": mpi,
                    "startFrame": sF,
                    "endFrame": eF,
                    "recordFrame": recF,
                    "trackIndex": track_index,
                    "mediaType": 2,  # Audio
                    "trackType": "audio",  # Critical fix - tells Resolve it's audio
                    "is_silence": is_silence
                }
                all_clip_infos.append(clip_info)
                recF += (eF - sF)  # butt-join
            
            print(f">>> {host['name']}: Created {len([c for c in all_clip_infos if c['trackIndex'] == track_index])} clip infos for track {track_index}")
        
        # Single AppendToTimeline call for all hosts
        print(f">>> Appending {len(all_clip_infos)} total clips to timeline in single call...")
        
        # Force the target timeline
        resolve.OpenPage("edit")
        proj = resolve.GetProjectManager().GetCurrentProject()
        tl = proj.GetCurrentTimeline()
        mp = proj.GetMediaPool()
        proj.SetCurrentTimeline(fresh_tl)
        
        # Append all clips in one call
        items = append_in_chunks(all_clip_infos, mp)
        
        if not items:
            print(f">>> ERROR: No items were appended")
            return
        
        print(f">>> Successfully appended {len(items)} total clips to timeline")
        
        # Disable silence segments
        disabled_count = 0
        for i, item in enumerate(items):
            try:
                if i < len(all_clip_infos) and all_clip_infos[i].get("is_silence", False):
                    item.SetClipEnabled(False)
                    disabled_count += 1
            except Exception as e:
                print(f">>> WARNING: Could not disable silence segment {i}: {e}")
        
        print(f">>> Disabled {disabled_count} silence segments total")
    
    # Refresh handles before muting
    try:
        proj, tl, mp = refresh_handles(resolve)
        
        # Mute original tracks for A/B comparison
        for host in hosts:
            try:
                original_track = host["track"]
                tl.SetTrackMute("audio", original_track, True)
            except Exception as e:
                print(f">>> Could not mute track {host['track']}: {e}")
    except Exception as e:
        print(f">>> Could not refresh handles for track muting: {e}")
    
    # Final summary
    if use_compound_processing:
        print(f">>> Done. Applied silence gating using compound processing")
        print(f">>> Processed compound clips with silence segments disabled")
    else:
        print(f">>> Done. Applied silence gating to {len(hosts)} hosts")
        print(f">>> Each host has been processed on their own track with silence segments disabled")
    
    # Show track summary
    print(f">>> Timeline track summary:")
    for i in range(1, tl.GetTrackCount("audio") + 1):
        try:
            name = tl.GetTrackName("audio", i) or f"Track {i}"
            items = tl.GetItemListInTrack("audio", i) or []
            print(f">>> Track {i}: '{name}' has {len(items)} items")
        except:
            pass

if __name__ == "__main__":
    try:
        main()
    finally:
        # Always clean up temp directory, even on exceptions
        print(f">>> Cleaning up temp directory...")
        if os.path.exists(OUTDIR):
            shutil.rmtree(OUTDIR, ignore_errors=True)
            print(f">>> Cleaned up: {OUTDIR}")
        else:
            print(f">>> Temp directory already cleaned up")
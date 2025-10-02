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
import tempfile

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
    print(">>> Loaded configuration from config.py")
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
    print(f">>> Using default configuration (config import failed: {e})")

def which(x): 
    """Check if executable exists in PATH."""
    return any(os.path.exists(os.path.join(p, x)) for p in os.getenv("PATH","").split(os.pathsep))

# Suppress pydub's ffmpeg warning since it's just informational
import warnings
warnings.filterwarnings("ignore", message="Couldn't find ffmpeg or avconv")
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pydub")

# Let pydub handle ffmpeg detection
print(">>> Using pydub's ffmpeg detection")

# Use temporary directory for safer handling
if CONFIG["temp_dir"]:
    OUTDIR = CONFIG["temp_dir"]
    os.makedirs(OUTDIR, exist_ok=True)
else:
    OUTDIR = tempfile.mkdtemp(prefix="_temp_gate_")
print(f">>> Using temporary directory")

def s2f(seconds, fps):
    """Convert seconds to frames."""
    return int(seconds * fps)

def f2s(frames, fps):
    """Convert frames to seconds."""
    return frames / fps

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

def normalize_name(raw):
    base = raw.strip()
    return base.title()

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
        result = mp.AppendToTimeline(chunk) or []
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
    except: 
        durF = 0
    
    anchor = int(item.GetStart())  # compound's current timeline start
    recF = anchor
    infos = []
    
    for i, (sF, eF, isSil) in enumerate(segs):
        if durF:  # clamp if we know length
            sF = max(0, min(sF, durF-1))
            eF = max(0, min(eF, durF))
            if eF <= sF: 
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
    
    if not infos: 
        print(">>> ERROR: No valid segments to append")
        return []
    
    try:
        result = mp.AppendToTimeline(infos)
        if result is None:
            print(">>> ERROR: AppendToTimeline returned None")
            return []
        return result
    except Exception as e:
        print(f">>> ERROR: AppendToTimeline failed: {e}")
        return []

def process_compound_clips(tl, mp, proj, fps, hosts):
    """Process clips from source tracks to new processed tracks, grouping by source track."""
    print(f">>> Processing clips for {len(hosts)} hosts...")
    
    # Group hosts by source track
    hosts_by_track = {}
    for host in hosts:
        src_idx = host["track"]
        if src_idx not in hosts_by_track:
            hosts_by_track[src_idx] = []
        hosts_by_track[src_idx].append(host)
    
    # Get current track count and ensure we have enough destination tracks
    current_track_count = tl.GetTrackCount("audio")
    needed_tracks = current_track_count + len(hosts_by_track)
    while tl.GetTrackCount("audio") < needed_tracks:
        tl.AddTrack("audio")
    
    # Process each source track
    for i, (src_idx, track_hosts) in enumerate(hosts_by_track.items()):
        dst_idx = current_track_count + i + 1
        
        print(f">>> Processing {len(track_hosts)} hosts to track {dst_idx}")
        
        # Ensure destination track is accessible
        
        # Get all clips from source track
        items = tl.GetItemListInTrack("audio", src_idx) or []
        if not items: 
            print(f">>> Track {src_idx} empty, skipping")
            continue
        
        print(f">>> Found {len(items)} clips on track {src_idx}")
        
        # Process each host individually to avoid Media Pool Item conflicts
        for host_idx, host in enumerate(track_hosts):
            print(f">>> Processing {host['name']} individually...")
            
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
            
            # Use the host's original item directly (from discover_hosts)
            matching_item = host['item']
            if not matching_item:
                print(f">>> WARNING: No original item for {host['name']}")
                continue
            
            # Use the host's original item directly (from discover_hosts)
            mpi = matching_item.GetMediaPoolItem()
            if not mpi:
                print(f">>> ERROR: No Media Pool Item for {host['name']}")
                continue
            
            host_clip_infos = []
            
            # Get timeline clip start time and duration
            timeline_start = int(matching_item.GetStart())
            timeline_end = int(matching_item.GetEnd())
            timeline_duration = timeline_end - timeline_start
            
            for sF, eF, isSil in segs:
                # Clamp segments to timeline clip duration
                sF = max(0, min(sF, timeline_duration-1))
                eF = max(0, min(eF, timeline_duration))
                if eF <= sF: continue
                
                record_frame = timeline_start + sF
                
                clip_info = {
                    "mediaPoolItem": mpi,
                    "startFrame": sF,
                    "endFrame": eF,
                    "recordFrame": record_frame,
                    "trackIndex": dst_idx,
                    "mediaType": 2,
                    "trackType": "audio",
                    "is_silence": isSil,
                    "host_name": host['name']
                }
                host_clip_infos.append(clip_info)
            
            # Process this host's segments immediately
            if not host_clip_infos:
                print(f">>> No valid segments found for {host['name']}")
                continue
            
            # Append this host's segments
            added = append_in_chunks(host_clip_infos, mp)
            
            if not added:
                print(f">>> WARNING: No items were appended for {host['name']}")
                continue
            
            # Add fades and disable silence segments
            fade_f = max(1, int(0.02*fps))
            disabled_count = 0
            for j, (clip, clip_info) in enumerate(zip(added, host_clip_infos)):
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
                except:
                    pass
        
        # Set track name
        track_name = f"[Processed] {track_hosts[0]['name']}"
        if len(track_hosts) > 1:
            track_name += f" +{len(track_hosts)-1} more"
        
        try:
            tl.SetTrackName("audio", dst_idx, track_name)
        except:
            pass
        

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
        # Get current track count before adding
        current_track_count = tl.GetTrackCount("audio")
        
        success = tl.AddTrack("audio")
        if not success:
            print(f">>> ERROR: Could not create new track for {host['name']}")
            return
        
        # The new track will be at current_track_count + 1
        new_track_index = current_track_count + 1
        
        # Try to set track name
        try:
            tl.SetTrackName("audio", new_track_index, f"[Processed] {host['name']}")
        except Exception as e:
            print(f">>> WARNING: Could not set track name for {host['name']}: {e}")
        
        # Track should be accessible by default
            
        # Update track_index to use the new track
        track_index = new_track_index
    
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
            "trackType": "audio",
            "is_silence": is_silence
        }
        all_clip_infos.append(clip_info)
        recF += (eF - sF)  # butt-join
    
    print(f">>> {host['name']}: Created {len(all_clip_infos)} clip infos for track {track_index}")
    
    # Append all clips to the new timeline
    print(f">>> {host['name']}: Appending {len(all_clip_infos)} clips to new timeline...")
    items = append_in_chunks(all_clip_infos, mp)
    
    if not items:
        print(f">>> ERROR: No items were appended for {host['name']}")
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
                    # Alternative method
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
        # Track duplication not implemented - placeholder for future enhancement
        
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
    
    
    # Always render fresh audio files
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
    
    # Try different methods to activate the preset
    preset_activated = False
    try:
        proj.LoadRenderPreset(render_preset)
        preset_activated = True
        print(f">>> Loaded render preset: {render_preset}")
    except Exception as e:
        print(f">>> WARNING: Could not load render preset '{render_preset}': {e}")
    
    # Try alternative method to set current preset
    if not preset_activated:
        try:
            # Some versions might use SetCurrentRenderPreset
            proj.SetCurrentRenderPreset(render_preset)
            preset_activated = True
            print(f">>> Set current render preset: {render_preset}")
        except Exception as e:
            print(f">>> WARNING: Could not set current render preset '{render_preset}': {e}")
    
    if not preset_activated:
        print(f">>> WARNING: Render preset activation failed - using current preset")
    
    # Set render mode to individual clips
    proj.SetCurrentRenderMode(0)
    print(f">>> Set render mode to individual clips")
    
    # Set only the target directory - let the preset handle everything else
    try:
        proj.SetRenderSettings({"TargetDir": OUTDIR})
    except Exception as e:
        print(f">>> WARNING: Could not set target directory")
        return
    
    
    # Add render job
    job_id = proj.AddRenderJob()
    if not job_id:
        print("ERROR: Could not create render job")
        return
    
    # Start rendering
    print(f">>> Starting render...")
    proj.StartRendering()
    
    # Wait for render to complete
    print(f">>> Waiting for render to complete...")
    while proj.IsRenderingInProgress():
        time.sleep(1)
    
    print(f">>> Render complete")
    
    # Clear render job
    proj.DeleteRenderJob(job_id)
    
    # Get media pool
    mp = proj.GetMediaPool()
    if not mp:
        print("ERROR: No media pool available")
        return
    
    # Detect silence for each host
    print(f">>> Checking for exported WAV files...")
    
    all_wav_files = glob.glob(os.path.join(OUTDIR, "*.wav"))
    print(f">>> Found {len(all_wav_files)} WAV files")
    
    # Collect individual WAV files for each host
    print(f">>> Processing {len(hosts)} hosts...")
    per_host_wavs = []
    for host in hosts:
        wav_file = None
        patterns_to_try = [
            f"{host['clip']}.wav",                    # Expected: [TrackName].wav
            f"{host['clip']}00000000.wav",            # Compound: [TrackName]00000000.wav
            f"{host['clip']}_00000000.wav",           # Regular: [TrackName]_00000000.wav
            f"{host['name']}.wav",                    # Alternative: [NormalizedName].wav
            f"{host['name']}00000000.wav",            # Alternative compound: [NormalizedName]00000000.wav
            f"{host['name']}_00000000.wav"            # Alternative regular: [NormalizedName]_00000000.wav
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
                
                if os.path.exists(json_path):
                    # Count segments
                    speech_count = len([s for s in result if not s.get("is_silence", False)])
                    silence_count = len([s for s in result if s.get("is_silence", False)])
                    print(f">>> [{host['name']}] SUCCESS: {speech_count} speech, {silence_count} silence segments")
                    successful += 1
                else:
                    print(f">>> [{host['name']}] ERROR: detect_silence did not create {os.path.basename(json_path)}")
            except Exception as e:
                print(f">>> [{host['name']}] ERROR: silence detection failed: {e}")
        
        print(f">>> Silence detection complete: {successful}/{len(per_host_wavs)} successful")
    else:
        print(f">>> No WAV files found for processing")
    
    # Switch to edit page and refresh handles
    proj, tl, mp = refresh_handles(resolve)
    
    # Verify silence detection files
    missing_files = [host['name'] for host in hosts if not os.path.exists(os.path.join(OUTDIR, f"{host['name']}.json"))]
    if missing_files:
        print(f">>> WARNING: Missing JSON files for: {', '.join(missing_files)}")
    
    # Get FPS from timeline settings
    fps = float(proj.GetSetting("timelineFrameRate") or "29.97")
    
    # Check if we should use grouped processing (tracks A2/A3 -> A5/A6)
    use_compound_processing = CONFIG.get("use_compound_processing", True)  # Try grouped approach by default
    
    if use_compound_processing:
        print(">>> Using grouped processing approach")
        process_compound_clips(tl, mp, proj, fps, hosts)
    else:
        print(">>> Using individual track processing approach")
        
        # Process all hosts with a single AppendToTimeline call
        print(f">>> Processing all {len(hosts)} hosts with single AppendToTimeline call...")
        
        # Get fresh timeline reference
        try:
            fresh_proj, fresh_tl, fresh_mp = refresh_handles(resolve)
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
                    "trackType": "audio",
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
        
        # Original tracks remain unmuted for comparison
    except Exception as e:
        print(f">>> Could not refresh handles for track muting: {e}")
    
    # Final summary
    if use_compound_processing:
        print(f">>> Done. Applied silence gating using grouped processing")
    else:
        print(f">>> Done. Applied silence gating to {len(hosts)} hosts")
    
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
        print(f">>> Cleanup complete")
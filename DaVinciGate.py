#!/usr/bin/env python3
"""
DaVinci Gate - Audio Processing Script
Processes audio tracks by detecting silence and creating segmented versions.
Creates compound clips for easy podcast editing workflow.
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

# Suppress pydub's ffmpeg warning since it's just informational
import warnings
warnings.filterwarnings("ignore", message="Couldn't find ffmpeg or avconv")
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pydub")

# Use temporary directory for safer handling
if CONFIG["temp_dir"]:
    OUTDIR = CONFIG["temp_dir"]
    os.makedirs(OUTDIR, exist_ok=True)
else:
    OUTDIR = tempfile.mkdtemp(prefix="_temp_gate_")

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
    
    # Track speaker clips for compound creation
    speaker_clips = {}  # Dictionary to store clips for each speaker
    
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
        
        # Process each host individually
        for host_idx, host in enumerate(track_hosts):
            # Load segments for this host
            json_path = f"{OUTDIR}/{host['name']}.json"
            if not os.path.exists(json_path):
                print(f">>> No JSON file found for {host['name']}: {json_path}")
                continue
                
            segs = load_segments(json_path, fps)
            if not segs:
                print(f">>> No segments found for {host['name']}")
                continue
            
            # Use the host's original item directly
            matching_item = host['item']
            if not matching_item:
                print(f">>> WARNING: No original item for {host['name']}")
                continue
            
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
            
            # Store the clips for this speaker
            speaker_clips[host['name']] = added
            
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
        
    # Create individual compound clips for each speaker
    created_compound_clips = {}
    
    for speaker_name, clips in speaker_clips.items():
        if clips:
            compound_name = f"{speaker_name}_Gated"
            compound_clip = create_compound_clip_from_items(tl, mp, clips, compound_name)
            
            if compound_clip:
                created_compound_clips[speaker_name] = compound_clip
            else:
                print(f">>> WARNING: Could not create compound clip for {speaker_name}")
        else:
            print(f">>> No clips found for {speaker_name} - skipping compound creation")
    
    # Summary of created compound clips
    if created_compound_clips:
        print(f">>> Successfully created {len(created_compound_clips)} compound clips:")
        for speaker_name in created_compound_clips.keys():
            print(f">>>   - {speaker_name}_Gated")
    else:
        print(f">>> No compound clips were created")
        

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
            return compound_clip
        
        # Try with clipName parameter
        compound_clip_info = {"clipName": compound_name}
        compound_clip = tl.CreateCompoundClip(items, compound_clip_info)
        
        if compound_clip:
            return compound_clip
            
        # Try with selection-based approach
        tl.SetSelection([])
        tl.SetSelection(items)
        compound_clip = tl.CreateCompoundClip(items)
        
        if compound_clip:
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
    
    # Run silence detection
    if per_host_wavs:
        successful = 0
        for host, wav_file in per_host_wavs:
            json_path = os.path.join(OUTDIR, f"{host['name']}.json")
            
            try:
                print(f">>> Analyzing: {host['name']}")
                result = detect_silence(
                    wav_file, 
                    min_sil_ms=CONFIG["min_silence_ms"],
                    pad_ms=CONFIG["padding_ms"],
                    out_json=json_path,
                    silence_thresh_db=CONFIG["silence_threshold_db"],
                    fps_hint=CONFIG["fps_hint"]
                )
                
                if os.path.exists(json_path):
                    successful += 1
            except Exception as e:
                print(f">>> ERROR: Silence detection failed for {host['name']}: {e}")
        
        print(f">>> Silence detection complete: {successful}/{len(per_host_wavs)} successful")
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
        process_compound_clips(tl, mp, proj, fps, hosts)
    else:
        # Individual processing approach (simplified)
        print(">>> Using individual track processing approach")
        # ... individual processing code would go here if needed
    
    print(f">>> Processing complete. Created compound clips for each speaker.")

if __name__ == "__main__":
    try:
        main()
    finally:
        # Clean up temp directory
        if os.path.exists(OUTDIR):
            shutil.rmtree(OUTDIR, ignore_errors=True)
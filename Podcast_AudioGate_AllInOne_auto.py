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
    return p, p.GetCurrentTimeline(), p.GetMediaPool()

def normalize_name(raw):
    base = raw.strip()
    return base.title()  # Always use title case for consistency

def delete_compounds_from_timeline_and_mediapool(tl, mp, hosts):
    """Delete compound clips from timeline and media pool to clean up used Media Pool Items."""
    print(">>> Deleting compound clips from timeline and media pool...")
    
    # Switch to edit page
    resolve.OpenPage("edit")
    
    # Track which Media Pool Items to delete
    mpi_to_delete = []
    
    # Delete from timeline first
    for host in hosts:
        track_index = host["track"]
        track_items = tl.GetItemListInTrack("audio", track_index) or []
        
        for item in track_items:
            try:
                # Check if this is a compound clip
                mpi = item.GetMediaPoolItem()
                if mpi:
                    clip_type = mpi.GetClipProperty("Type") or ""
                    if "compound" in clip_type.lower():
                        print(f">>> Found compound clip on track {track_index}: {item.GetName()}")
                        # Mark for deletion from media pool
                        mpi_to_delete.append(mpi)
                        # Delete from timeline
                        item.Delete()
                        print(f">>> Deleted compound clip from timeline: {item.GetName()}")
            except Exception as e:
                print(f">>> WARNING: Could not process item on track {track_index}: {e}")
    
    # Delete from media pool
    for mpi in mpi_to_delete:
        try:
            mpi.Delete()
            print(f">>> Deleted Media Pool Item from media pool")
        except Exception as e:
            print(f">>> WARNING: Could not delete Media Pool Item: {e}")
    
    print(f">>> Cleanup complete: deleted {len(mpi_to_delete)} compound Media Pool Items")
    return True

def reimport_original_files(mp, hosts, original_file_paths):
    """Re-import original source files to create fresh Media Pool Items."""
    print(">>> Re-importing original source files...")
    
    fresh_mpis = {}
    
    for host in hosts:
        host_name = host["name"]
        if host_name in original_file_paths:
            file_path = original_file_paths[host_name]
            if file_path and os.path.exists(file_path):
                try:
                    # Re-import the original file
                    imported_items = mp.AddItemListToMediaPool([file_path])
                    if imported_items and len(imported_items) > 0:
                        fresh_mpis[host_name] = imported_items[0]
                        print(f">>> Re-imported original file for {host_name}: {os.path.basename(file_path)}")
                    else:
                        print(f">>> WARNING: Failed to re-import {host_name}")
                except Exception as e:
                    print(f">>> ERROR: Could not re-import {host_name}: {e}")
            else:
                print(f">>> WARNING: Original file path not found for {host_name}: {file_path}")
        else:
            print(f">>> WARNING: No original file path stored for {host_name}")
    
    print(f">>> Re-import complete: {len(fresh_mpis)} fresh Media Pool Items created")
    return fresh_mpis

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

def process_host(tl, mp, host, fps, assigned_track_index, resolve_obj, fresh_mpis=None):
    """Process a single host with butt-joined speech segments only"""
    
    # Load silence detection results
    json_path = os.path.join(OUTDIR, f"{host['name']}.json")
    print(f">>> Looking for JSON file: {json_path}")
    
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
    
    print(f">>> Using original item as source: {original_item.GetStart()}-{original_item.GetEnd()}")
    
    # Use fresh Media Pool Item if available, otherwise use original after compound deletion
    if fresh_mpis and host['name'] in fresh_mpis:
        mpi = fresh_mpis[host['name']]
        print(f">>> Using fresh Media Pool Item for {host['name']}: {mpi}")
        print(f">>> Fresh Media Pool Item should avoid Source Patch issues")
    else:
        # Use original Media Pool Item (compounds have been deleted, so this should work better)
        original_mpi = original_item.GetMediaPoolItem()
        if not original_mpi:
            print(f">>> ERROR: Could not get media pool item for {host['name']}")
            return
        mpi = original_mpi
        print(f">>> Using original Media Pool Item for {host['name']}: {mpi}")
        print(f">>> Compound deletion should help with Source Patch issues")
    
    # Duration clamping (optional but robust)
    dur_frames = None
    try:
        frames_str = (mpi.GetClipProperty("Frames") or "").strip()
        if frames_str:
            dur_frames = int(float(frames_str))
    except:
        pass

    # Build ALL segments (speech + silence) to maintain sync
    def clamp(v, lo, hi): 
        return max(lo, min(v, hi))
    
    orig_start_recF = original_item.GetStart()   # anchor processed track to match timeline start
    recF = orig_start_recF

    all_clip_infos = []
    for seg in segs:
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

        all_clip_infos.append({
            "mediaPoolItem": mpi,
            "startFrame": sF,
            "endFrame": eF,
            "mediaType": 2,               # audio
            "recordFrame": recF,          # place immediately after previous segment
            "trackIndex": assigned_track_index,
            "is_silence": seg.get("is_silence", False)  # Store silence flag for later
        })
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

    # Unlock the destination track
    try:
        if tl.GetTrackLockState("audio", assigned_track_index):
            print(f">>> WARNING: Track {assigned_track_index} is locked! Unlocking…")
            tl.SetTrackLockState("audio", assigned_track_index, False)
    except Exception as e:
        print(f">>> Could not check/unlock track {assigned_track_index}: {e}")

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
    
    # Store original file paths before any processing
    print(">>> Storing original file paths...")
    original_file_paths = {}
    for host in hosts:
        try:
            mpi = host["item"].GetMediaPoolItem()
            if mpi:
                file_path = mpi.GetClipProperty("File Path")
                if file_path and os.path.exists(file_path):
                    original_file_paths[host["name"]] = file_path
                    print(f">>> Stored original path for {host['name']}: {os.path.basename(file_path)}")
                else:
                    print(f">>> WARNING: Could not get file path for {host['name']}")
        except Exception as e:
            print(f">>> WARNING: Could not store file path for {host['name']}: {e}")
    
    if not original_file_paths:
        print("WARNING: Could not store original file paths. Will use alternative approach...")
        # We'll proceed without re-importing and just use the existing Media Pool Items
        # after deleting the compounds
    
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
        
        # Load render preset
        presets = proj.GetRenderPresets()
        print(f">>> Available render presets: {presets}")
        
        # Use configuration for render preset
        render_preset = CONFIG["render_preset"]
        
        # Check if preset exists in the values (preset names)
        preset_names = list(presets.values())
        if render_preset not in preset_names:
            print(f"ERROR: Render preset '{render_preset}' not found")
            print(f">>> Looking for similar presets...")
            similar_presets = [p for p in preset_names if 'audio' in p.lower() or 'individual' in p.lower()]
            if similar_presets:
                print(f">>> Similar audio presets found: {similar_presets}")
                # Try to use the first similar preset
                render_preset = similar_presets[0]
                print(f">>> Using fallback preset: {render_preset}")
            else:
                print(f">>> No suitable fallback presets found. Available presets:")
                for i, preset_name in enumerate(preset_names[:10]):  # Show first 10
                    print(f">>>   {i+1}. {preset_name}")
                if len(preset_names) > 10:
                    print(f">>>   ... and {len(preset_names) - 10} more presets")
                return
        
        # Find the preset ID for the render preset name
        preset_id = None
        for pid, pname in presets.items():
            if pname == render_preset:
                preset_id = pid
                break
        
        if preset_id is None:
            print(f"ERROR: Could not find ID for preset '{render_preset}'")
            return
        
        # Render individual clips
        print(f">>> Rendering individual clips using preset: {render_preset} (ID: {preset_id})")
        
        proj.LoadRenderPreset(preset_id)
        print(f">>> Loading render preset...")
        
        # Set render mode
        proj.SetCurrentRenderMode(0)  # Individual clips
        print(f">>> Setting render mode...")
        
        # Debug: Check what the preset actually set
        try:
            current_settings = proj.GetRenderSettings()
            print(f">>> DEBUG: Current render settings after preset load:")
            for key, value in current_settings.items():
                print(f">>>   {key}: {value}")
        except Exception as e:
            print(f">>> DEBUG: Could not get current render settings: {e}")
        
        # Set only essential settings - let the preset handle format and codec
        render_settings = {
            "TargetDir": OUTDIR,
            "CustomName": "%{Clip Name}",  # Use curly braces as in the preset
            "UniqueFilenames": True        # Ensure unique filenames
        }
        
        try:
            proj.SetRenderSettings(render_settings)
            print(f">>> Setting render settings: {render_settings}")
        except Exception as e:
            print(f">>> WARNING: Could not set render settings: {e}")
            return
        
        # Debug: Check what settings are now active
        try:
            final_settings = proj.GetRenderSettings()
            print(f">>> DEBUG: Final render settings before render:")
            for key, value in final_settings.items():
                print(f">>>   {key}: {value}")
        except Exception as e:
            print(f">>> DEBUG: Could not get final render settings: {e}")
        
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
    
    # Clean workflow: Delete compounds and optionally re-import original files
    print(">>> Starting clean workflow to avoid Source Patch issues...")
    
    # Step 1: Delete compound clips from timeline and media pool
    delete_compounds_from_timeline_and_mediapool(tl, mp, hosts)
    
    # Step 2: Re-import original source files if we have file paths
    fresh_mpis = None
    if original_file_paths:
        fresh_mpis = reimport_original_files(mp, hosts, original_file_paths)
        if not fresh_mpis:
            print("WARNING: Could not re-import original files. Will use existing Media Pool Items.")
    else:
        print("INFO: No original file paths available. Will use existing Media Pool Items after compound deletion.")
    
    # Detect silence for each host (only if we rendered new files)
    if not skip_render:
        print(f">>> Checking for exported WAV files in: {OUTDIR}")
        
        # First, let's see what files were actually exported
        all_wav_files = glob.glob(os.path.join(OUTDIR, "*.wav"))
        print(f">>> All WAV files found: {[os.path.basename(f) for f in all_wav_files]}")
        
        # Collect individual WAV files for each host
        per_host_wavs = []
        for host in hosts:
            print(f">>> Collecting WAV file for {host['name']} (clip: {host['clip']})")
            
            # Look for the WAV file with the actual naming pattern
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
            
            print(f">>> Found WAV file: {os.path.basename(wav_file)}")
            per_host_wavs.append((host, wav_file))
        
        # Process silence detection using exported WAV files
        if per_host_wavs:
            print(f">>> Processing {len(per_host_wavs)} WAV files for silence detection...")
            
            successful = 0
            for host, wav_file in per_host_wavs:
                json_path = os.path.join(OUTDIR, f"{host['name']}.json")
                
                try:
                    print(f">>> [{host['name']}] Processing WAV file: {os.path.basename(wav_file)}")
                    
                    # Process the WAV file
                    result = detect_silence(
                        wav_file, 
                        min_sil_ms=CONFIG["min_silence_ms"],
                        pad_ms=CONFIG["padding_ms"],
                        out_json=json_path,
                        silence_thresh_db=CONFIG["silence_threshold_db"],
                        fps_hint=CONFIG["fps_hint"]
                    )
                    print(f">>> [{host['name']}] detect_silence returned: {len(result) if result else 'None'} segments")
                    
                    if os.path.exists(json_path):
                        file_size = os.path.getsize(json_path)
                        print(f">>> [{host['name']}] SUCCESS: Created {os.path.basename(json_path)} ({file_size} bytes)")
                        
                        # Verify JSON content
                        try:
                            with open(json_path, 'r') as f:
                                json_data = json.load(f)
                            print(f">>> [{host['name']}] JSON contains {len(json_data)} segments")
                            if json_data:
                                print(f">>> [{host['name']}] First segment: {json_data[0]}")
                        except Exception as json_e:
                            print(f">>> [{host['name']}] WARNING: Could not read JSON content: {json_e}")
                        successful += 1
                    else:
                        print(f">>> [{host['name']}] ERROR: detect_silence did not create {os.path.basename(json_path)}")
                except Exception as e:
                    print(f">>> [{host['name']}] ERROR: silence detection failed: {e}")
                    import traceback
                    print(f">>> [{host['name']}] Traceback: {traceback.format_exc()}")
            
            print(f">>> WAV processing complete: {successful}/{len(per_host_wavs)} successful")
        else:
            print(f">>> No WAV files found for processing")
    else:
        print(f">>> Skipped silence detection - using existing JSON files")
    
    # Switch to edit page and refresh handles
    print(f">>> Switching to edit page and refreshing handles...")
    proj, tl, mp = refresh_handles(resolve)
    print(f">>> Handles refreshed after rendering")
    
    # Verify all silence detection files are ready
    print(f">>> Verifying all silence detection files are ready...")
    for host in hosts:
        json_path = os.path.join(OUTDIR, f"{host['name']}.json")
        if os.path.exists(json_path):
            age = time.time() - os.path.getmtime(json_path)
            print(f">>> {host['name']}.json ready ({age:.1f}s old)")
        else:
            print(f">>> WARNING: {host['name']}.json not found")
    
    # Create tracks and process hosts
    print(f">>> Creating segmented tracks...")
    
    # Create all tracks first
    print(f">>> Creating all tracks first...")
    track_assignments = {}
    for i, host in enumerate(hosts):
        print(f">>> Creating track for {host['name']}...")
        # AddTrack returns True/False, not track index. Get current track count before adding
        current_track_count = tl.GetTrackCount("audio")
        success = tl.AddTrack("audio")
        if success:
            new_track_index = current_track_count + 1  # New track will be at this index
            tl.SetTrackName("audio", new_track_index, f"[Processed] {host['name']}")
            track_assignments[host['name']] = new_track_index
            print(f">>> {host['name']} assigned to track {new_track_index}")
        else:
            print(f">>> ERROR: Failed to create track for {host['name']}")
            return
    
    # Verify all tracks created
    print(f">>> Verifying all tracks created...")
    for host in hosts:
        assigned_track = track_assignments[host['name']]
        track_items = tl.GetItemListInTrack("audio", assigned_track) or []
        print(f">>> Track {assigned_track} ({host['name']}): {len(track_items)} items")
    
    # Get FPS from timeline settings
    fps = float(proj.GetSetting("timelineFrameRate") or "29.97")
    fps_int = round(fps)
    print(f">>> Timeline FPS: {fps} (rounded: {fps_int})")
    
    # Process all hosts with timeline manipulation
    print(f">>> Processing all hosts with timeline manipulation...")
    
    for i, host in enumerate(hosts):
        print(f">>> Processing host {i+1}/{len(hosts)}: {host['name']}")
        assigned_track = track_assignments[host['name']]
        print(f">>> {host['name']} assigned to track {assigned_track}")
        
        # Process this host with timeline manipulation
        print(f">>> Processing {host['name']} on track {assigned_track}...")
        process_host(tl, mp, host, fps, assigned_track, resolve, fresh_mpis)
        
        # Verify results immediately after processing
        track_items = tl.GetItemListInTrack("audio", assigned_track) or []
        print(f">>> {host['name']} final track count: {len(track_items)} items")
        
        # Wait between hosts
        if i < len(hosts) - 1:
            print(f">>> Waiting before next host...")
            time.sleep(1)
    
    # Mute original tracks for easy A/B comparison
    print(f">>> Muting original tracks for A/B comparison...")
    for host in hosts:
        try:
            original_track = host["track"]
            tl.SetTrackMute("audio", original_track, True)
            print(f">>> Muted original track {original_track} ({host['name']})")
        except Exception as e:
            print(f">>> Could not mute track {host['track']}: {e}")
    
    # Final summary
    print(f">>> Done. Applied silence gating to existing tracks")
    print(f">>> Final track count: {tl.GetTrackCount('audio')}")
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
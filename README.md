# DaVinci Gate

An automated DaVinci Resolve script that intelligently processes podcast audio by detecting silence segments and rebuilding each speaker onto its own gated audio track for a fast, non-destructive editing workflow.

<img width="2550" height="461" alt="Screenshot 2025-10-03 at 1 57 52 PM" src="https://github.com/user-attachments/assets/cb6f0845-79ea-4ebb-8765-214f1e67fab3" />

## Features

- **Smart Silence Detection**: Analyzes exported WAV audio to identify speech vs silence segments using a pure-Python stdlib implementation (no `pydub`, `ffmpeg`, or `numpy` required).
- **24-bit WAV Support**: Reads 16-bit and 24-bit PCM WAVs directly, matching Resolve's default individual-clip export.
- **Per-speaker Gated Tracks**: Creates one new `[Processed] Speaker` audio track per source clip with the silence segments disabled in place — no manual reorganization needed.
- **Perfect Sync Preservation**: Rebuilds segments at their original timeline positions so every speaker stays in sync with the rest of the edit.
- **Batched, Handle-safe Appends**: Refreshes MediaPool / timeline handles between batches to avoid the "second speaker" glitch on large timelines.
- **DaVinci Resolve Integration**: Runs entirely inside Resolve's embedded Python via Workspace → Scripts → Utility.
- **Non-destructive**: The original tracks are left untouched — the script only adds new `[Processed] …` tracks alongside them.

## How It Works

1. **Discovery**: Automatically finds every audio clip on every audio track in your timeline and treats each one as a "host" (speaker instance).
2. **Export**: Renders individual WAV files using the `AudioOnly_IndividualClips` render preset.
3. **Analysis**: Runs the stdlib silence detector (`detect_silence.py`) on each WAV to build a per-host list of speech/silence segments.
4. **Rebuild**: Appends new timeline clips onto fresh `[Processed] SpeakerName` audio tracks — one per host — placed at the original record frames so the timing matches the source.
5. **Gating**: Silence segments are muted in place (via `SetClipEnabled` / `Enabled` / `AudioEnabled` / `Volume` fallbacks) instead of being cut out, and small audio fades are applied to soften transitions.
6. **Done**: No compound clips are created and nothing on your original tracks is modified.

## Workflow

### Current Workflow (Recommended)

- **Setup**: Place your speakers on the timeline (recommended: convert each speaker's clip to a compound clip first for frame-accurate results — see the note below).
- **Processing**: Run DaVinci Gate — it renders, analyzes, and rebuilds one `[Processed] Speaker` track per source clip automatically.
- **Editing**: Edit against the new `[Processed] …` tracks. Delete or hide the originals if you don't need them.

### Why This Approach Works Best

- **Non-destructive**: Original clips and tracks are never modified, so you can always re-run or revert.
- **API-friendly**: Rebuilding via `AppendToTimeline` with per-batch handle refresh is the most reliable Resolve scripting pattern for large multi-speaker projects.
- **Frame-accurate**: Working from compound-clip sources keeps sync perfect even across mixed frame rates.
- **Clean results**: Each speaker gets a dedicated track with silence muted in place — ready to mix.

## Quick Start

1. **Clone or download** this repository
2. **Run the setup script**:
   ```bash
   python setup.py
   ```
   The setup script copies `DaVinciGate.py`, `detect_silence.py`, `config.py`, and the render preset into DaVinci Resolve's Fusion Utility folder for your OS. It does **not** need to install any Python packages — the script only uses the standard library.
3. **Open DaVinci Resolve** and load your podcast timeline
4. **Run DaVinci Gate** from Resolve's Scripts menu (Workspace > Scripts > Utility > DaVinciGate)

## Installation

### Quick Setup (Recommended)

1. **Run the setup script** (handles everything automatically):
   ```bash
   python setup.py
   ```

### Manual Setup

1. **No Python packages to install** — DaVinci Gate uses only the Python standard library (`wave`, `struct`, `math`, `json`, `tempfile`). This is intentional so it can run inside DaVinci Resolve 20+'s embedded Python 3.13 interpreter, where `pydub`, `pyaudioop`, and `numpy` are not cleanly available.

2. **Copy scripts to DaVinci Resolve**:
   - **macOS**: `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`
   - **Windows**: `~/AppData/Roaming/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`
   - **Linux**: `~/.local/share/DaVinciResolve/Fusion/Scripts/Utility/`

   Copy `DaVinciGate.py`, `detect_silence.py`, and `config.py` into that folder together — `DaVinciGate.py` imports `detect_silence` from the same directory.

3. **Import the render preset** (IMPORTANT - Required for script to work):

   **Option A: Automatic Import (Recommended)**
   - The `setup.py` script automatically copies the preset file to the correct location
   - If you used `python setup.py`, the preset should already be installed

   **Option B: Manual Import**
   - Copy `AudioOnly_IndividualClips.xml` to your DaVinci Resolve presets folder:
     - **macOS**: `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Support/Resolve Disk Database/Resolve Preferences/Export/`
     - **Windows**: `~/AppData/Roaming/Blackmagic Design/DaVinci Resolve/Support/Resolve Disk Database/Resolve Preferences/Export/`
     - **Linux**: `~/.local/share/DaVinciResolve/Support/Resolve Disk Database/Resolve Preferences/Export/`

   **Option C: Manual Creation**
   - If the preset file doesn't work, manually create a render preset with these settings:
     - Format: WAV
     - Audio Codec: LPCM
     - Audio Bit Depth: 24-bit
     - Sample Rate: 48kHz
     - Custom Name: `%{Clip Name}`
     - Name the preset: `AudioOnly_IndividualClips`

   **Verify Installation**: The preset should appear in DaVinci Resolve's render preset dropdown menu

## Usage

### ⚠️ IMPORTANT: Use Compound Clips for Frame Accuracy

**For best results and frame-accurate processing, wrap each speaker's audio in a compound clip before running the script:**

- **Regular clips** may experience sync issues, especially when the source frame rate differs from the timeline frame rate.
- **Compound clips** provide perfect frame accuracy and eliminate those sync problems.
- **How to create**: Right-click any clip → "Create Compound Clip".

### Recommended Workflow

1. **Prepare your timeline**:
   - Convert each speaker's clip to a compound clip first (right-click → "Create Compound Clip").
   - Place the compound clips (e.g., "John", "Aaron", "Koolaid Man") on your podcast timeline. They can live on a single track or on separate tracks — DaVinci Gate iterates over every audio track that has clips on it.
   - Give each clip a descriptive name; that name becomes the speaker's label on the resulting processed track.
<img width="1634" height="556" alt="Screenshot 2025-10-03 at 1 47 15 PM" src="https://github.com/user-attachments/assets/2a3d8728-64c5-482c-a923-9266b380dd82" />

2. **Run DaVinci Gate**:
   - Open DaVinci Resolve
   - Go to Workspace > Scripts > Utility
   - Run `DaVinciGate`
<img width="415" height="181" alt="Screenshot 2025-10-03 at 1 47 26 PM" src="https://github.com/user-attachments/assets/3613b340-4a84-499d-b855-37bd4429803a" />

3. **The script will automatically**:
   - Discover every audio clip on every audio track
   - Export individual WAV files using the `AudioOnly_IndividualClips` preset
   - Analyze silence patterns with the built-in stdlib detector (`detect_silence.py`)
   - Add new audio tracks named `[Processed] SpeakerName` and rebuild each speaker's clip on its own processed track
   - Mute the silence segments in place and apply short audio fades for smooth transitions
<img width="1630" height="543" alt="Screenshot 2025-10-03 at 1 48 36 PM" src="https://github.com/user-attachments/assets/0db7b52b-ea4b-4a00-b27c-3225a791916c" />

   > Note: earlier releases produced `*_Gated` compound clips in the Media Pool. The current version skips that step and instead builds the gated result directly on new `[Processed] …` timeline tracks so nothing has to be dragged around or decomposed afterward.

4. **After the script completes**:
   - Every speaker now lives on its own `[Processed] Speaker` track with silence already gated.
   - Original tracks are untouched — mute, disable, or delete them once you're happy with the processed tracks.
<img width="1635" height="547" alt="Screenshot 2025-10-03 at 1 49 03 PM" src="https://github.com/user-attachments/assets/b0c3f8f8-ae3f-4c17-b796-776b79b82dbb" />

   - If you'd rather work with an even flatter result, you can still select all the segments on a `[Processed]` track and use "Create Compound Clip" yourself.
<img width="1632" height="550" alt="Screenshot 2025-10-03 at 1 49 22 PM" src="https://github.com/user-attachments/assets/671545f2-2470-4b80-a2a5-d3ac9a422947" />

### Advanced Usage

- **Multiple speakers on the same track**: DaVinci Gate handles this fine — each clip becomes its own host and gets its own `[Processed]` track. The processed track name is disambiguated with the source track index if two clips share a name.
- **Re-running the script**: Because originals are never modified, you can safely re-run DaVinci Gate. It will simply add another set of `[Processed] …` tracks alongside the previous run.
- **Flattening to a compound clip**: If you prefer the old workflow, select the segments on a processed track and choose "Create Compound Clip". Use "Decompose Using Clips" later if you need the individual segments back.

## Requirements

- DaVinci Resolve 20+ (tested with the embedded Python 3.13 interpreter that ships with recent Resolve builds)
- Python 3.x (only the standard library — no `pydub`, no `ffmpeg`, no `numpy`)

## File Structure

- `detect_silence.py`: Core silence detection algorithm (Python standard library only — reads 16-bit and 24-bit PCM WAVs directly via `wave`).
- `DaVinciGate.py`: Main DaVinci Resolve automation script (render → analyze → rebuild processed tracks).
- `config.py`: Configuration file with customizable settings.
- `setup.py`: Automated setup script — copies the scripts and render preset into DaVinci Resolve's folders for your OS.
- `verify_installation.py`: Installation verification script.
- `AudioOnly_IndividualClips.xml`: DaVinci Resolve render preset for individual WAV export.
- `requirements.txt`: Placeholder — DaVinci Gate has no external Python dependencies.

## Configuration

The script includes a comprehensive configuration system. Edit `config.py` to customize behavior:

### Audio Processing Settings
- **Silence Threshold**: `-50.0 dB` - Threshold for detecting silence
- **Minimum Silence Duration**: `600ms` - Minimum length to consider as silence
- **Padding**: `120ms` - Padding around speech segments
- **Hold Time**: `500ms` - Extra hold time at end of speech segments

### Render Settings
- **Output Format**: `wav` - Audio output format
- **Audio Codec**: `lpcm` - Audio codec for rendering
- **Bit Depth**: `24` - Audio bit depth
- **Sample Rate**: `48000` - Audio sample rate

### Processing Settings
- **Crossfades**: `20ms` - Crossfade duration for smooth transitions
- **Batch Size**: `250` - Number of segments to process in each batch
- **FPS Hint**: `30` - FPS for frame-based calculations

### Advanced Settings
- **Max JSON Age**: `86400` seconds (24 hours) - How long to keep cached results
- **Merge Tolerance**: `100ms` - Tolerance for merging nearby segments
- **Min Silence Gap**: `1` frame - Minimum silence gap to preserve

### Platform-Specific Paths
The script automatically detects DaVinci Resolve installation paths, but you can override them in `config.py` if needed.

## Output

The script creates:
- **`[Processed] SpeakerName` audio tracks** — one per source clip, with silence segments muted in place using Resolve's clip-enable / audio-enable / volume APIs.
- **Perfect sync preservation** — every rebuilt segment is placed at its original record frame so the mix stays aligned.
- **Automatic audio fades** — short fade-ins/outs (default 20 ms) smooth the boundary between kept and muted segments.
- **Untouched originals** — your original audio clips and tracks are never modified.

After processing, you can:
- Edit the `[Processed] …` tracks directly.
- Mute, hide, or delete the original source tracks once you're happy with the result.
- Optionally wrap a processed track into your own compound clip if you prefer a single-clip container.

## Known Limitations

- **Frame Rate Issues with Regular Clips**: Regular clips whose source frame rate differs from the timeline may experience sync issues. **Always use compound clips for frame-accurate results.**
- **Handle Refresh Overhead**: Very large timelines are processed in batches with MediaPool / timeline handle refreshes between batches. This is intentional (it prevents Resolve from silently dropping items on the next append) but does add a small amount of overhead.
- **Render Preset Dependency**: The script relies on the `AudioOnly_IndividualClips` render preset being installed. If it's missing, `setup.py` or Option B / C in the install section above will get you there.

## Troubleshooting

- **Installation Issues**: Run `python verify_installation.py` to check your setup.
- **Nothing happens / import error inside Resolve**: Make sure `DaVinciGate.py`, `detect_silence.py`, and `config.py` are all in the same `Fusion/Scripts/Utility/` folder — `DaVinciGate.py` imports `detect_silence` from its own directory.
- **`AudioOnly_IndividualClips` preset not found**: Import the XML via `setup.py`, or create it manually with the settings in Option C above.
- **Second speaker looks wrong / fewer items than expected**: This is exactly the failure mode the batched append + handle-refresh logic is designed to fix. Check the log output for `WARNING: AppendToTimeline returned X/Y items`.
- **Memory Management**: The script writes WAVs and JSON sidecars to a temporary directory and cleans them up automatically when it exits.

## License

This project is provided as-is for educational and professional use with DaVinci Resolve.

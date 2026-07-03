# DaVinci Gate

An automated DaVinci Resolve script that intelligently processes podcast audio by detecting silence per speaker and rebuilding each one onto its own gated audio track — non-destructively, from a single side panel.

<img width="2550" height="461" alt="Screenshot 2025-10-03 at 1 57 52 PM" src="https://github.com/user-attachments/assets/cb6f0845-79ea-4ebb-8765-214f1e67fab3" />

## Features

- **Preview-first UI**: A Fusion side panel lets you **Analyze** (render + detect silence, no timeline changes) then **Apply** (rebuild the gated tracks) with a per-host preview table in between.
- **Adaptive, per-host thresholds**: Auto-calibrates a silence threshold for each speaker from that clip's own measured noise floor and speech level, with a single "strictness" slider and optional manual per-host overrides.
- **WAV + stats cache**: First analyze renders each host once; subsequent Analyzes reuse the WAVs and measured audio stats so iterating on settings is fast. A **Clear cache** button forces a fresh render when you need one.
- **Pure-stdlib silence detection**: Reads 16-bit and 24-bit PCM WAVs directly (`wave`, `struct`, `math`, `json`, `tempfile`) — no `pydub`, no `ffmpeg`, no `numpy`. Safe for Resolve 20+'s embedded Python 3.13.
- **Per-speaker gated tracks**: Creates one new `[Processed] Speaker` audio track per source clip with silence segments muted in place — no manual reorganization needed.
- **Perfect sync preservation**: Rebuilds every segment at its original record frame so all speakers stay in sync with the rest of the edit.
- **Batched, handle-safe appends**: Refreshes MediaPool / timeline handles between batches to avoid the "second speaker" glitch on large timelines.
- **Non-destructive**: Your original tracks are never modified — the script only adds new `[Processed] …` tracks alongside them.
- **Headless-friendly**: The same pipeline is exposed as `gate_core.run_headless(...)` for CLI / scripted runs without the UI.

## How It Works

1. **Discovery**: On launch (and on **Refresh**), the panel scans every audio clip on every audio track and treats each one as a "host" (speaker instance).
2. **Analyze**:
   - Renders individual WAV files using the `AudioOnly_IndividualClips` render preset (skipping any host whose WAV is already cached).
   - Measures each host's noise floor and speech level and computes an adaptive threshold: `noise_floor + strictness * (speech_level - noise_floor)`. Per-host overrides win.
   - Runs the stdlib silence detector (`detect_silence.py`) to produce a per-host plan of speech / silence segments, and fills the preview table.
3. **Apply**: Adds new `[Processed] SpeakerName` audio tracks — one per host — appends the segments at their original record frames, and mutes silence segments in place (via `SetClipEnabled` / `Enabled` / `AudioEnabled` / `Volume` fallbacks). Small audio fades soften the transitions.
4. **Done**: No compound clips are created and nothing on your original tracks is modified. Re-Analyze to try different settings on the same cached renders.

## Quick Start

1. **Clone or download** this repository.
2. **Run the setup script**:

   ```bash
   python setup.py
   ```

   The setup script copies `DaVinciGate.py`, `gate_core.py`, `detect_silence.py`, `config.py`, and the render preset into DaVinci Resolve's Fusion Utility folder for your OS. It does **not** install any Python packages — the tool only uses the standard library.
3. **Open DaVinci Resolve** and load your podcast timeline.
4. **Run DaVinci Gate** from Resolve's Scripts menu (Workspace > Scripts > Utility > DaVinciGate). The panel opens; click **Analyze**, review, then **Apply**.

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

   Copy `DaVinciGate.py`, `gate_core.py`, `detect_silence.py`, and `config.py` into that folder together — `DaVinciGate.py` imports `gate_core` and `detect_silence` from the same directory.

3. **Import the render preset** (IMPORTANT — required for the render step to work):

   **Option A: Automatic Import (Recommended)**
   - The `setup.py` script automatically copies the preset file to the correct location.
   - If you used `python setup.py`, the preset should already be installed.

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

   **Verify Installation**: The preset should appear in DaVinci Resolve's render preset dropdown menu.

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

2. **Launch DaVinci Gate**:
   - Open DaVinci Resolve.
   - Go to **Workspace > Scripts > Utility > DaVinciGate**.

<img width="415" height="181" alt="Screenshot 2025-10-03 at 1 47 26 PM" src="https://github.com/user-attachments/assets/3613b340-4a84-499d-b855-37bd4429803a" />

3. **Analyze**: In the panel, click **Analyze**. DaVinci Gate will:
   - Discover every audio clip on every audio track.
   - Render individual WAV files using the `AudioOnly_IndividualClips` preset (cached per host — later runs reuse them).
   - Measure each host's noise floor + speech level and compute an adaptive silence threshold (or use your per-host overrides).
   - Fill the preview table with each host's silence count, total gated time, and effective threshold — **nothing on your timeline changes yet**.

   Tune **Strictness (%)** or set per-host **override (dB)** values, click **Analyze** again (fast — WAV + stats cache hits), and iterate until the preview looks right.

4. **Apply**: When the preview looks good, click **Apply**. DaVinci Gate rebuilds one `[Processed] SpeakerName` track per host, appends the segments at their original record frames, and mutes silence segments in place with short audio fades.

<img width="1630" height="543" alt="Screenshot 2025-10-03 at 1 48 36 PM" src="https://github.com/user-attachments/assets/0db7b52b-ea4b-4a00-b27c-3225a791916c" />

   > Note: earlier releases produced `*_Gated` compound clips in the Media Pool. The current version skips that step and instead builds the gated result directly on new `[Processed] …` timeline tracks so nothing has to be dragged around or decomposed afterward.

5. **After Apply completes**:
   - Every speaker now lives on its own `[Processed] Speaker` track with silence already gated.
   - Original tracks are untouched — mute, disable, or delete them once you're happy with the processed tracks.

<img width="1635" height="547" alt="Screenshot 2025-10-03 at 1 49 03 PM" src="https://github.com/user-attachments/assets/b0c3f8f8-ae3f-4c17-b796-776b79b82dbb" />

   - If you'd rather work with an even flatter result, you can still select all the segments on a `[Processed]` track and use "Create Compound Clip" yourself.

<img width="1632" height="550" alt="Screenshot 2025-10-03 at 1 49 22 PM" src="https://github.com/user-attachments/assets/671545f2-2470-4b80-a2a5-d3ac9a422947" />

### Panel controls at a glance

- **Auto-calibrate threshold per host** — leave on (recommended). Uses each host's own noise floor + speech level to pick a threshold. Turn off to use a single manual **Threshold (dB)** for every host.
- **Strictness (%)** — how far above the noise floor speech has to be to count. Higher = more aggressive silence gating, lower = keep more of the borderline audio. Applies when auto-calibrate is on.
- **Per-host threshold overrides (dB)** — leave blank to let auto pick, or type a dB value to pin a specific host (e.g. someone with a hot mic or a lot of room tone).
- **Analyze** — render (or reuse cache), measure, detect silence, fill the preview table. Never touches the timeline.
- **Apply** — commit the current previewed plan to the timeline as `[Processed] …` tracks. Enabled only after a successful Analyze.
- **Refresh** — rescan the timeline for hosts (use after adding/removing clips).
- **Clear cache** — throw away cached WAVs + stats and force a fresh render on the next Analyze.

### Advanced Usage

- **Multiple speakers on the same track**: DaVinci Gate handles this fine — each clip becomes its own host and gets its own `[Processed]` track. The processed track name is disambiguated with the source track index if two clips share a name.
- **Re-running the script**: Because originals are never modified, you can safely re-run DaVinci Gate. It will simply add another set of `[Processed] …` tracks alongside the previous run.
- **Flattening to a compound clip**: If you prefer the old workflow, select the segments on a processed track and choose "Create Compound Clip". Use "Decompose Using Clips" later if you need the individual segments back.
- **Headless mode**: The pipeline is exposed as `gate_core.run_headless(settings=..., hosts=...)` for CLI / batch use. See the docstrings in `gate_core.py` for the settings dataclass.

## Requirements

- DaVinci Resolve 20+ (tested with the embedded Python 3.13 interpreter that ships with recent Resolve builds).
- Python 3.x (only the standard library — no `pydub`, no `ffmpeg`, no `numpy`).

## File Structure

- `DaVinciGate.py`: Entry point + Fusion UIManager panel (Analyze / Apply flow, host list, preview table).
- `gate_core.py`: Pipeline used by the panel — discovery, render, WAV/stats cache, adaptive per-host threshold, silence detection, timeline rebuild, and `run_headless` for scripted runs.
- `detect_silence.py`: Core silence detection algorithm (Python standard library only — reads 16-bit and 24-bit PCM WAVs directly via `wave`).
- `config.py`: Default settings used as the initial values in the panel (and by `run_headless` when no settings are passed).
- `setup.py`: Automated setup script — copies the scripts and render preset into DaVinci Resolve's folders for your OS.
- `verify_installation.py`: Installation verification script.
- `AudioOnly_IndividualClips.xml`: DaVinci Resolve render preset for individual WAV export.
- `requirements.txt`: Placeholder — DaVinci Gate has no external Python dependencies.

## Configuration

The panel reads its initial values from `config.py`, so edits there change the defaults you see when the panel opens. Anything you change in the panel for a given session overrides the file until you close the window.

### Audio Processing Settings

- **Silence Threshold**: `-50.0 dB` — Manual threshold, used only when auto-calibrate is off.
- **Strictness**: `0.35` (35%) — How far above the noise floor speech has to be when auto-calibrate is on.
- **Minimum Silence Duration**: `1000 ms` — Minimum length to consider as silence.
- **Padding**: `400 ms` — Padding around speech segments.
- **Hold Time**: `100 ms` — Extra hold time at the end of a speech segment.

### Render Settings

- **Output Format**: `wav`
- **Audio Codec**: `lpcm`
- **Bit Depth**: `24`
- **Sample Rate**: `48000`

### Processing Settings

- **Crossfades**: `20 ms` — Fade applied at each kept/muted boundary.
- **Batch Size**: `250` — Number of segments appended per batch before the handle refresh.
- **FPS Hint**: `30` — Used for frame-based fallbacks when the timeline frame rate can't be read.

### Cache Settings

- **Cache directory**: `<temp>/davinci-gate-cache` by default — WAVs and stats live here.
- **Max JSON Age**: `86400` seconds (24 hours) — how long to keep cached detection results.
- Use the **Clear cache** button in the panel (or delete the folder) to force a fresh render.

### Platform-Specific Paths

The scripts and `setup.py` automatically detect DaVinci Resolve installation paths, but you can override them in `config.py` if needed.

## Output

DaVinci Gate creates:

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
- **Render Preset Dependency**: The tool relies on the `AudioOnly_IndividualClips` render preset being installed. If it's missing, `setup.py` or Option B / C in the install section above will get you there.
- **Long Analyze on first run**: The first Analyze renders every host, which can take a while on large projects. Subsequent Analyzes reuse the cached WAVs and are much faster; use **Clear cache** only when the source audio actually changed.

## Troubleshooting

- **Installation Issues**: Run `python verify_installation.py` to check your setup.
- **Nothing happens / import error inside Resolve**: Make sure `DaVinciGate.py`, `gate_core.py`, `detect_silence.py`, and `config.py` are all in the same `Fusion/Scripts/Utility/` folder — `DaVinciGate.py` imports `gate_core` and `detect_silence` from its own directory.
- **`AudioOnly_IndividualClips` preset not found**: Import the XML via `setup.py`, or create it manually with the settings in Option C above.
- **Preview looks off**: Adjust **Strictness** or set a per-host override in dB, then click **Analyze** again — it's fast because the WAVs are cached. Only click **Apply** once the preview looks right.
- **Second speaker looks wrong / fewer items than expected**: This is exactly the failure mode the batched append + handle-refresh logic is designed to fix. Check the log output for `WARNING: AppendToTimeline returned X/Y items`.
- **Stale cache after re-editing source audio**: Click **Clear cache** in the panel (or delete `<temp>/davinci-gate-cache`) and Analyze again.

## License

This project is provided as-is for educational and professional use with DaVinci Resolve.

# DaVinci Gate

An automated DaVinci Resolve script that intelligently processes podcast audio by detecting silence segments and creating gated compound clips for easy editing workflow.

## Features

- **Smart Silence Detection**: Uses `pydub` to analyze audio and identify speech vs silence segments
- **Compound Clip Creation**: Creates individual compound clips for each speaker with silence automatically gated
- **Perfect Sync Preservation**: Maintains original timing by disabling silence segments rather than removing them
- **Batch Processing**: Processes multiple speakers simultaneously with parallel silence detection
- **DaVinci Resolve Integration**: Seamlessly works within Resolve's scripting environment
- **Flexible Workflow**: Creates compound clips that can be easily moved and organized on individual tracks

## How It Works

1. **Discovery**: Automatically finds all audio tracks with clips in your timeline
2. **Export**: Renders individual WAV files using the "AudioOnly_IndividualClips" preset
3. **Analysis**: Processes each WAV file to detect speech and silence segments
4. **Processing**: Creates segmented audio with silence segments disabled
5. **Compound Creation**: Automatically creates compound clips for each speaker (e.g., "1Scott_Gated", "2Wes_Gated")
6. **Manual Organization**: You drag compound clips to individual tracks and can decompose them if needed

## Workflow

### Current Workflow (Recommended)
- **Setup**: Place all compound clips (speakers) on a single audio track (Track 1)
- **Processing**: Script creates gated compound clips for each speaker
- **Organization**: Manually drag each compound clip to its own track
- **Decomposition**: Use "Decompose Using Clips" if you need individual segments

### Why This Approach Works Best
- **API Limitations**: DaVinci Resolve's API works best when all source clips are on one track
- **Flexibility**: Compound clips can be easily moved and organized after processing
- **Editing Options**: Keep as compound clips for easy movement, or decompose for detailed editing
- **Clean Results**: Each speaker gets their own gated compound clip with silence automatically handled

## Quick Start

1. **Clone or download** this repository
2. **Run the setup script**:
   ```bash
   python setup.py
   ```
3. **Open DaVinci Resolve** and load your podcast timeline
4. **Run DaVinci Gate** from Resolve's Scripts menu (Workspace > Scripts > Utility > DaVinciGate)

## Installation

### Quick Setup (Recommended)

1. **Run the setup script** (handles everything automatically):
   ```bash
   python setup.py
   ```

### Manual Setup

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Install ffmpeg** (required for audio processing):
   - **macOS**: `brew install ffmpeg`
   - **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/download.html)
   - **Linux**: `sudo apt install ffmpeg` (Ubuntu/Debian)

3. **Copy files to DaVinci Resolve**:
   - **macOS**: `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`
   - **Windows**: `~/AppData/Roaming/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`
   - **Linux**: `~/.local/share/DaVinciResolve/Fusion/Scripts/Utility/`

4. **Import the render preset** (IMPORTANT - Required for script to work):
   
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

**For best results and frame-accurate processing, always use compound clips:**

- **Regular clips** may experience sync issues, especially with different frame rates
- **Compound clips** provide perfect frame accuracy and eliminate sync problems
- **How to create**: Right-click any clip → "Create Compound Clip"

### Recommended Workflow

1. **Prepare your timeline**:
   - **Convert each clip to a compound clip first** (right-click → "Create Compound Clip")
   - Place all compound clips (e.g., "John", "Aaron", "Koolaid Man") on a single audio track (Track 1)
   - Ensure clips have descriptive names that will become the speaker names
<img width="1634" height="556" alt="Screenshot 2025-10-03 at 1 47 15 PM" src="https://github.com/user-attachments/assets/2a3d8728-64c5-482c-a923-9266b380dd82" />

2. **Run DaVinci Gate**:
   - Open DaVinci Resolve
   - Go to Workspace > Scripts > Utility
   - Run `DaVinciGate`
<img width="415" height="181" alt="Screenshot 2025-10-03 at 1 47 26 PM" src="https://github.com/user-attachments/assets/3613b340-4a84-499d-b855-37bd4429803a" />

3. **The script will automatically**:
   - Detect all compound clips on the source track
   - Export individual WAV files using the AudioOnly_IndividualClips preset
   - Analyze silence patterns using pydub
   - Create processed tracks with segmented audio (silence disabled)
   - Create individual compound clips for each speaker (e.g., "1Scott_Gated", "2Wes_Gated", "3CJ_Gated")
<img width="1630" height="543" alt="Screenshot 2025-10-03 at 1 48 36 PM" src="https://github.com/user-attachments/assets/0db7b52b-ea4b-4a00-b27c-3225a791916c" />

4. **Manual organization** (after script completes):
   - Drag each speaker's compound clip ("SpeakerName_Gated") to its own audio track
<img width="1635" height="547" alt="Screenshot 2025-10-03 at 1 49 03 PM" src="https://github.com/user-attachments/assets/b0c3f8f8-ae3f-4c17-b796-776b79b82dbb" />
   - Optional: Right-click compound clip → "Decompose Using Clips" if you need individual segments
<img width="1632" height="550" alt="Screenshot 2025-10-03 at 1 49 22 PM" src="https://github.com/user-attachments/assets/671545f2-2470-4b80-a2a5-d3ac9a422947" />

   - Each compound clip contains perfectly gated audio with silence automatically handled

### Advanced Usage

If you need to work with individual segments instead of compound clips:
- After moving compound clips to individual tracks
- Right-click the compound clip → "Decompose Using Clips"
- This gives you access to all the individual speech segments with silence already gated

## Requirements

- DaVinci Resolve (tested with recent versions)
- Python 3.x
- `pydub` library
- `ffmpeg` (for audio processing)

## File Structure

- `detect_silence.py`: Core silence detection algorithm using `pydub`
- `DaVinciGate.py`: Main DaVinci Resolve automation script
- `config.py`: Configuration file with customizable settings
- `setup.py`: Automated setup script for easy installation
- `verify_installation.py`: Installation verification script
- `AudioOnly_IndividualClips.xml`: DaVinci Resolve render preset for individual WAV export
- `requirements.txt`: Python dependencies

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
- **Processed tracks** with segmented audio where silence segments are disabled
- **Individual compound clips** for each speaker (e.g., "1Scott_Gated", "2Wes_Gated")
- **Perfect sync preservation** - all original timing maintained
- **Automatic crossfades** for smooth transitions between segments

After processing, you can:
- **Move compound clips** to individual tracks for organization
- **Keep as compound clips** for easy editing and movement
- **Decompose using clips** to access individual speech segments if needed

## Known Limitations

- **Frame Rate Issues with Regular Clips**: Regular clips with different frame rates may experience sync issues. **Always use compound clips for frame-accurate results.**

- **Manual Organization Required**: After script completion, you need to manually drag compound clips to individual tracks. This provides flexibility but requires a manual step.

- **API Limitations**: DaVinci Resolve's API works best when all source clips are on a single track during processing. The script is designed around this limitation.

- **Current Workflow**:
  - ✅ **Recommended**: All compound clips on Track 1 → Script creates gated compound clips → Manually organize
  - ❌ **Not supported**: Direct multi-track processing with automatic organization

## Troubleshooting

- **ffmpeg Warning**: The `pydub` warning about ffmpeg is normal and doesn't affect functionality
- **Installation Issues**: Run `python verify_installation.py` to check your setup
- **Compound Clip Creation**: If compound clips aren't created automatically, check that your clips have unique names
- **Manual Steps**: Remember to manually drag compound clips to individual tracks after processing
- **Memory Management**: The script automatically cleans up temporary files

## License

This project is provided as-is for educational and professional use with DaVinci Resolve.

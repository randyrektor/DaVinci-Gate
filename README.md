# DaVinci Resolve Podcast Audio Gate

An automated DaVinci Resolve script that intelligently processes podcast audio by detecting silence segments and creating gated versions that maintain perfect sync while removing unwanted silence.

## Features

- **Smart Silence Detection**: Uses `pydub` to analyze audio and identify speech vs silence segments
- **Perfect Sync Preservation**: Appends ALL segments (speech + silence) but disables silence segments to maintain original timing
- **Batch Processing**: Processes multiple hosts simultaneously with parallel silence detection
- **DaVinci Resolve Integration**: Seamlessly works within Resolve's scripting environment
- **Automatic Track Management**: Creates processed tracks and mutes originals for easy A/B comparison
- **Robust Error Handling**: Handles large segment counts with chunked processing and comprehensive error recovery

## How It Works

1. **Discovery**: Automatically finds all audio tracks with clips in your timeline (accepts any track name)
2. **Export**: Renders individual WAV files using the "AudioOnly_IndividualClips" preset
3. **Analysis**: Processes each WAV file to detect speech and silence segments
4. **Timeline Manipulation**: Creates new tracks with segmented audio where silence is disabled
5. **Sync Maintenance**: Preserves original timing by keeping all segments but muting silence

<img width="2542" height="414" alt="Screenshot 2025-09-22 at 10 29 45 AM" src="https://github.com/user-attachments/assets/3d82eda1-75fa-406c-9d0f-743fe7bda250" />

## Quick Start

1. **Clone or download** this repository
2. **Run the setup script**:
   ```bash
   python setup.py
   ```
3. **Open DaVinci Resolve** and load your podcast timeline
4. **Run the script** from Resolve's Scripts menu

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

4. **Import the render preset**:
   - Copy `AudioOnly_IndividualClips.xml` to your DaVinci Resolve presets folder
   - **macOS**: `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Support/Resolve Disk Database/Resolve Preferences/Export/`
   - **Windows**: `~/AppData/Roaming/Blackmagic Design/DaVinci Resolve/Support/Resolve Disk Database/Resolve Preferences/Export/`
   - **Linux**: `~/.local/share/DaVinciResolve/Support/Resolve Disk Database/Resolve Preferences/Export/`
   - Or manually create a render preset with these settings:
     - Format: WAV
     - Audio Codec: LPCM
     - Audio Bit Depth: 24-bit
     - Sample Rate: 48kHz
     - Custom Name: `%{Clip Name}`

## Usage

1. Open DaVinci Resolve
2. Load your podcast timeline with audio tracks containing named clips
3. Run `Podcast_AudioGate_AllInOne_auto.py` from Resolve's Scripts menu
4. The script will automatically:
   - Detect all audio tracks with clips (accepts any track name)
   - Export individual WAV files using the AudioOnly_IndividualClips preset
   - Analyze silence patterns using pydub
   - Create processed tracks with gated audio
   - Apply crossfades and disable silence segments
   - Mute original tracks for easy A/B comparison

## Requirements

- DaVinci Resolve (tested with recent versions)
- Python 3.x
- `pydub` library
- `ffmpeg` (for audio processing)

## File Structure

- `detect_silence.py`: Core silence detection algorithm using `pydub`
- `Podcast_AudioGate_AllInOne_auto.py`: Main DaVinci Resolve automation script
- `config.py`: Configuration file with customizable settings
- `setup.py`: Automated setup script for easy installation
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

The script creates new tracks named `[Processed] [HostName]` with:
- All original segments preserved for sync
- Silence segments disabled (muted)
- Speech segments playing normally
- Small crossfades for smooth transitions

## Known Limitations

- **Source Patch Limitation**: Due to DaVinci Resolve's internal Source Patch mechanism, only the first host's clips will appear on their assigned track. Subsequent hosts will show 0 clips on their tracks, even though the API reports successful append operations. This is a known limitation of the DaVinci Resolve API when using the same Media Pool Item across multiple tracks.

- **Current Status**: The script includes compound deletion and cleanup logic to help mitigate this issue, but the Source Patch limitation may still affect multiple hosts in some cases.

## Troubleshooting

- **ffmpeg Warning**: The `pydub` warning about ffmpeg is normal and doesn't affect functionality
- **Track Lock Issues**: The script automatically unlocks tracks before processing
- **Large Projects**: Uses chunked processing to handle projects with many segments
- **Memory Management**: Automatically cleans up temporary files

## License

This project is provided as-is for educational and professional use with DaVinci Resolve.

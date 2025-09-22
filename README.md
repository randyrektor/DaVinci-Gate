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

1. **Discovery**: Automatically finds hosts in your timeline (supports patterns like "1Scott", "2Wes", "3CJ")
2. **Export**: Renders individual WAV files using the "AudioOnly_IndividualClips" preset
3. **Analysis**: Processes each WAV file to detect speech and silence segments
4. **Timeline Manipulation**: Creates new tracks with segmented audio where silence is disabled
5. **Sync Maintenance**: Preserves original timing by keeping all segments but muting silence

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
   - Copy `AudioOnly_IndividualClips_Render.xml` to your DaVinci Resolve presets folder
   - Or manually create a render preset with these settings:
     - Format: WAV
     - Audio Codec: LPCM
     - Audio Bit Depth: 24-bit
     - Sample Rate: 48kHz
     - Custom Name: `%{Clip Name}`

## Usage

1. Open DaVinci Resolve
2. Load your podcast timeline with named audio tracks
3. Run the script from Resolve's Scripts menu
4. The script will automatically:
   - Detect hosts from track names
   - Export individual WAV files
   - Analyze silence patterns
   - Create processed tracks with gated audio
   - Mute original tracks for comparison

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
- `AudioOnly_IndividualClips_Render.xml`: DaVinci Resolve render preset for individual WAV export
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

## Troubleshooting

- **ffmpeg Warning**: The `pydub` warning about ffmpeg is normal and doesn't affect functionality
- **Track Lock Issues**: The script automatically unlocks tracks before processing
- **Large Projects**: Uses chunked processing to handle projects with many segments
- **Memory Management**: Automatically cleans up temporary files

## License

This project is provided as-is for educational and professional use with DaVinci Resolve.

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

1. Copy both Python files to your DaVinci Resolve Scripts directory:
   ```
   /Users/[username]/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/
   ```

2. Install required Python dependencies:
   ```bash
   pip install pydub
   ```

3. Ensure `ffmpeg` is installed and available in your PATH

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

## Configuration

The script includes several configurable parameters:

- **Silence Threshold**: `-50.0 dB` (adjustable in `detect_silence.py`)
- **Minimum Silence Duration**: `600ms` 
- **Padding**: `120ms` around speech segments
- **Crossfades**: `20ms` for smooth transitions
- **Batch Size**: `250` segments per append operation

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

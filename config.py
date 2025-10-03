#!/usr/bin/env python3
"""
Configuration file for DaVinci Resolve Podcast Audio Gate
Modify these settings to customize the behavior of the audio gating script.
"""

# Render settings
RENDER_PRESET = "AudioOnly_IndividualClips"
OUTPUT_FORMAT = "wav"
AUDIO_CODEC = "lpcm"
AUDIO_BIT_DEPTH = "24"
AUDIO_SAMPLE_RATE = "48000"

# Silence detection settings
SILENCE_THRESHOLD_DB = -50.0  # dB threshold for silence detection
MIN_SILENCE_MS = 1000          # Minimum silence duration in milliseconds
PADDING_MS = 400              # Padding around speech segments in milliseconds
HOLD_MS = 100                 # Extra hold time at end of speech segments

# Processing settings
CROSSFADE_MS = 20             # Crossfade duration in milliseconds
BATCH_SIZE = 250              # Number of segments to process in each batch
FPS_HINT = 30                 # FPS hint for frame-based calculations

# Path settings (leave as None for auto-detection)
SCRIPT_DIR = None             # Path to script directory (auto-detected if None)
TEMP_DIR = None               # Path to temporary directory (auto-detected if None)

# Advanced settings
MAX_JSON_AGE = 86400          # Maximum age of JSON files in seconds (24 hours)
MERGE_TOLERANCE_MS = 100      # Tolerance for merging nearby segments
MIN_SIL_GAP_MS = 1            # Minimum silence gap to preserve (in frames)

# Platform-specific settings
# These will be used if the auto-detection fails
PLATFORM_PATHS = {
    "macos": {
        "resolve_script_api": [
            "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Resources/Developer/Scripting/Modules",
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
            "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
        ],
        "script_locations": [
            "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility",
        ]
    },
    "windows": {
        "resolve_script_api": [
            "~/AppData/Roaming/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting/Modules",
            "C:/Program Files/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
            "C:/Program Files (x86)/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
        ],
        "script_locations": [
            "~/AppData/Roaming/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility",
        ]
    },
    "linux": {
        "resolve_script_api": [
            "~/.local/share/DaVinciResolve/Developer/Scripting/Modules",
            "/opt/resolve/Developer/Scripting/Modules",
            "/usr/local/DaVinciResolve/Developer/Scripting/Modules",
        ],
        "script_locations": [
            "~/.local/share/DaVinciResolve/Fusion/Scripts/Utility",
        ]
    }
}

#!/usr/bin/env python3
"""
Portability test script for DaVinci Resolve Podcast Audio Gate
This script tests the configuration loading and path detection without requiring DaVinci Resolve.
"""

import os
import sys
import platform
from pathlib import Path

def test_config_loading():
    """Test that the configuration loads correctly."""
    print("Testing configuration loading...")
    
    try:
        import config
        print("✓ config.py loaded successfully")
        
        # Test that all required variables are defined
        required_vars = [
            'RENDER_PRESET', 'OUTPUT_FORMAT', 'AUDIO_CODEC', 'AUDIO_BIT_DEPTH',
            'AUDIO_SAMPLE_RATE', 'SILENCE_THRESHOLD_DB', 'MIN_SILENCE_MS',
            'PADDING_MS', 'HOLD_MS', 'CROSSFADE_MS', 'BATCH_SIZE', 'FPS_HINT'
        ]
        
        for var in required_vars:
            if not hasattr(config, var):
                print(f"✗ Missing variable: {var}")
                return False
        
        print("✓ All required configuration variables found")
        return True
        
    except ImportError as e:
        print(f"✗ Failed to import config.py: {e}")
        return False

def test_detect_silence_import():
    """Test that detect_silence can be imported."""
    print("Testing detect_silence import...")
    
    try:
        from detect_silence import detect_silence
        print("✓ detect_silence imported successfully")
        return True
    except ImportError as e:
        print(f"✗ Failed to import detect_silence: {e}")
        return False

def test_platform_detection():
    """Test platform detection."""
    print("Testing platform detection...")
    
    system = platform.system().lower()
    print(f"Detected platform: {system}")
    
    if system in ['darwin', 'windows', 'linux']:
        print("✓ Platform supported")
        return True
    else:
        print(f"✗ Unsupported platform: {system}")
        return False

def test_path_expansion():
    """Test path expansion functionality."""
    print("Testing path expansion...")
    
    test_paths = [
        "~/test/path",
        "~/.local/share/test",
        "~/Library/Application Support/test"
    ]
    
    for path in test_paths:
        expanded = os.path.expanduser(path)
        if expanded != path:
            print(f"✓ Path expansion works: {path} -> {expanded}")
        else:
            print(f"✗ Path expansion failed: {path}")
            return False
    
    return True

def test_dependencies():
    """Test that required dependencies are available."""
    print("Testing dependencies...")
    
    try:
        import pydub
        print("✓ pydub available")
    except ImportError:
        print("✗ pydub not available - run: pip install pydub")
        return False
    
    try:
        import json
        print("✓ json available")
    except ImportError:
        print("✗ json not available")
        return False
    
    try:
        import tempfile
        print("✓ tempfile available")
    except ImportError:
        print("✗ tempfile not available")
        return False
    
    return True

def test_ffmpeg_detection():
    """Test ffmpeg detection."""
    print("Testing ffmpeg detection...")
    
    try:
        import subprocess
        result = subprocess.run(['ffmpeg', '-version'], 
                              capture_output=True, 
                              timeout=5)
        if result.returncode == 0:
            print("✓ ffmpeg available")
            return True
        else:
            print("✗ ffmpeg not working properly")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("✗ ffmpeg not found - install ffmpeg for audio processing")
        return False
    except Exception as e:
        print(f"✗ Error testing ffmpeg: {e}")
        return False

def main():
    """Run all portability tests."""
    print("DaVinci Resolve Podcast Audio Gate - Portability Test")
    print("=" * 60)
    
    tests = [
        test_platform_detection,
        test_path_expansion,
        test_dependencies,
        test_config_loading,
        test_detect_silence_import,
        test_ffmpeg_detection,
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        print()
        if test():
            passed += 1
        else:
            print("Test failed!")
    
    print("\n" + "=" * 60)
    print(f"Portability Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("✓ All tests passed! The system is ready for use.")
        return True
    else:
        print("✗ Some tests failed. Please address the issues above.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

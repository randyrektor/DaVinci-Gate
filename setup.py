#!/usr/bin/env python3
"""
Setup script for DaVinci Gate
This script helps users install dependencies and configure the system.
"""

import os
import sys
import platform
import subprocess
import shutil
from pathlib import Path

def get_platform_info():
    """Get platform-specific information."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    elif system == "windows":
        return "windows"
    elif system.startswith("linux"):
        return "linux"
    else:
        return "unknown"

def find_davinci_resolve_paths():
    """Find DaVinci Resolve installation paths."""
    platform_name = get_platform_info()
    
    if platform_name == "macos":
        possible_paths = [
            "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Resources/Developer/Scripting/Modules",
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
            os.path.expanduser("~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"),
        ]
    elif platform_name == "windows":
        possible_paths = [
            os.path.expanduser("~/AppData/Roaming/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting/Modules"),
            "C:/Program Files/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
            "C:/Program Files (x86)/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
        ]
    elif platform_name == "linux":
        possible_paths = [
            os.path.expanduser("~/.local/share/DaVinciResolve/Developer/Scripting/Modules"),
            "/opt/resolve/Developer/Scripting/Modules",
            "/usr/local/DaVinciResolve/Developer/Scripting/Modules",
        ]
    else:
        return []
    
    found_paths = []
    for path in possible_paths:
        expanded_path = os.path.expanduser(path)
        if os.path.exists(expanded_path):
            found_paths.append(expanded_path)
    
    return found_paths

def find_script_directory():
    """Find the DaVinci Resolve script directory."""
    platform_name = get_platform_info()
    
    if platform_name == "macos":
        script_paths = [
            os.path.expanduser("~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"),
        ]
    elif platform_name == "windows":
        script_paths = [
            os.path.expanduser("~/AppData/Roaming/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"),
        ]
    elif platform_name == "linux":
        script_paths = [
            os.path.expanduser("~/.local/share/DaVinciResolve/Fusion/Scripts/Utility"),
        ]
    else:
        return None
    
    for path in script_paths:
        if os.path.exists(path):
            return path
    
    # If directory doesn't exist, return the first expected path
    return script_paths[0] if script_paths else None

def install_dependencies():
    """Install Python dependencies."""
    print("Installing Python dependencies...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("✓ Dependencies installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to install dependencies: {e}")
        return False

def check_ffmpeg():
    """Check if ffmpeg is available."""
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        print("✓ ffmpeg is available")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("⚠ ffmpeg not found - pydub will attempt to use system audio libraries")
        print("  For best performance, install ffmpeg:")
        print("  macOS: brew install ffmpeg")
        print("  Windows: Download from https://ffmpeg.org/download.html")
        print("  Linux: sudo apt install ffmpeg (Ubuntu/Debian)")
        print("  The script will still work without ffmpeg, but may show warnings")
        return True  # Don't fail setup if ffmpeg is missing

def find_preset_directory():
    """Find the DaVinci Resolve preset directory."""
    platform_name = get_platform_info()
    
    if platform_name == "macos":
        preset_paths = [
            os.path.expanduser("~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Support/Resolve Disk Database/Resolve Preferences/Export/"),
        ]
    elif platform_name == "windows":
        preset_paths = [
            os.path.expanduser("~/AppData/Roaming/Blackmagic Design/DaVinci Resolve/Support/Resolve Disk Database/Resolve Preferences/Export/"),
        ]
    elif platform_name == "linux":
        preset_paths = [
            os.path.expanduser("~/.local/share/DaVinciResolve/Support/Resolve Disk Database/Resolve Preferences/Export/"),
        ]
    else:
        return None
    
    for path in preset_paths:
        if os.path.exists(path):
            return path
    
    # If directory doesn't exist, return the first expected path
    return preset_paths[0] if preset_paths else None

def copy_scripts(script_dir):
    """Copy scripts to the DaVinci Resolve script directory."""
    if not script_dir:
        print("✗ Could not determine script directory")
        return False
    
    # Create directory if it doesn't exist
    os.makedirs(script_dir, exist_ok=True)
    
    files_to_copy = [
        "detect_silence.py",
        "DaVinciGate.py",
        "config.py"
    ]
    
    success = True
    for filename in files_to_copy:
        if os.path.exists(filename):
            dest_path = os.path.join(script_dir, filename)
            try:
                shutil.copy2(filename, dest_path)
                print(f"✓ Copied {filename} to {dest_path}")
            except Exception as e:
                print(f"✗ Failed to copy {filename}: {e}")
                success = False
        else:
            print(f"✗ {filename} not found in current directory")
            success = False
    
    return success

def copy_preset(preset_dir):
    """Copy render preset to the DaVinci Resolve preset directory."""
    if not preset_dir:
        print("✗ Could not determine preset directory")
        return False
    
    # Create directory if it doesn't exist
    os.makedirs(preset_dir, exist_ok=True)
    
    preset_file = "AudioOnly_IndividualClips.xml"
    if os.path.exists(preset_file):
        dest_path = os.path.join(preset_dir, preset_file)
        try:
            shutil.copy2(preset_file, dest_path)
            print(f"✓ Copied {preset_file} to {dest_path}")
            return True
        except Exception as e:
            print(f"✗ Failed to copy {preset_file}: {e}")
            return False
    else:
        print(f"✗ {preset_file} not found in current directory")
        return False

def main():
    """Main setup function."""
    print("DaVinci Gate Setup")
    print("=" * 30)
    
    # Check platform
    platform_name = get_platform_info()
    print(f"Platform: {platform_name}")
    
    # Install dependencies
    if not install_dependencies():
        print("Setup failed - could not install dependencies")
        return False
    
    # Check ffmpeg (optional)
    check_ffmpeg()
    
    # Find script directory
    script_dir = find_script_directory()
    if not script_dir:
        print("✗ Could not find DaVinci Resolve script directory")
        print("Please manually copy the files to your DaVinci Resolve script directory")
        return False
    
    print(f"Script directory: {script_dir}")
    
    # Copy scripts
    if not copy_scripts(script_dir):
        print("Setup failed - could not copy scripts")
        return False
    
    # Find and copy render preset
    preset_dir = find_preset_directory()
    if preset_dir:
        print(f"Preset directory: {preset_dir}")
        if not copy_preset(preset_dir):
            print("⚠ Warning: Could not copy render preset - you may need to install it manually")
    else:
        print("⚠ Warning: Could not find preset directory - you may need to install the preset manually")
    
    print("\n✓ Setup completed successfully!")
    print("\nNext steps:")
    print("1. Open DaVinci Resolve")
    print("2. Go to Workspace > Scripts > Utility")
    print("3. Run 'DaVinciGate'")
    print("\nIMPORTANT USAGE NOTES:")
    print("• Place your audio tracks with compound clips in DaVinci Resolve")
    print("• The script automatically detects tracks and processes them")
    print("• Creates compound clips for each speaker with silence gated")
    print("• See README.md for detailed usage instructions")
    print("\nTo verify installation:")
    print("• Run: python verify_installation.py")
    print("\nTo customize settings:")
    print("• Edit the config.py file in the script directory")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
    
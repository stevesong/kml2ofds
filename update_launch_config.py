#!/usr/bin/env python3
"""
Script to automatically update .vscode/launch.json with launch configurations
for all .profile files in the project directory.
"""

import json
import os
import glob
import re
from pathlib import Path


def strip_json_comments(text):
    """Remove comments from JSON text (handles // style comments)."""
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        # Remove // comments (but preserve // in strings by checking if it's inside quotes)
        # Simple approach: remove // and everything after it, unless it's in a string
        in_string = False
        escape_next = False
        cleaned_line = []
        i = 0
        while i < len(line):
            char = line[i]
            if escape_next:
                cleaned_line.append(char)
                escape_next = False
            elif char == '\\':
                cleaned_line.append(char)
                escape_next = True
            elif char == '"' and (i == 0 or line[i-1] != '\\'):
                in_string = not in_string
                cleaned_line.append(char)
            elif not in_string and char == '/' and i + 1 < len(line) and line[i+1] == '/':
                # Found // comment outside of string
                break
            else:
                cleaned_line.append(char)
            i += 1
        cleaned_lines.append(''.join(cleaned_line))
    return '\n'.join(cleaned_lines)


def format_profile_name(profile_filename):
    """Convert profile filename to a human-readable name for the launch config."""
    # Remove .profile extension
    name = profile_filename.replace(".profile", "")
    # Replace underscores and hyphens with spaces
    name = name.replace("_", " ").replace("-", " ")
    # Title case each word
    name = " ".join(word.capitalize() for word in name.split())
    return name


def get_profile_files():
    """Get all .profile files, excluding default.profile."""
    profile_files = []
    for profile_path in glob.glob("*.profile"):
        if profile_path != "default.profile":
            profile_files.append(profile_path)
    return sorted(profile_files)


def get_existing_profiles(launch_config):
    """Extract profile filenames from existing launch configurations."""
    existing_profiles = set()
    for config in launch_config.get("configurations", []):
        args = config.get("args", [])
        # Look for --network-profile argument
        if "--network-profile" in args:
            idx = args.index("--network-profile")
            if idx + 1 < len(args):
                existing_profiles.add(args[idx + 1])
    return existing_profiles


def create_launch_config(profile_filename):
    """Create a launch configuration for a profile file."""
    profile_name = format_profile_name(profile_filename)
    return {
        "name": f"Python Debugger: {profile_name}",
        "type": "debugpy",
        "request": "launch",
        "program": "${file}",
        "console": "integratedTerminal",
        "args": [
            "--network-profile",
            profile_filename
        ]
    }


def update_launch_json():
    """Update launch.json with missing profile configurations."""
    launch_json_path = Path(".vscode/launch.json")
    
    # Read existing launch.json
    if launch_json_path.exists():
        with open(launch_json_path, "r") as f:
            content = f.read()
            # Strip comments before parsing
            content = strip_json_comments(content)
            launch_config = json.loads(content)
    else:
        # Create basic structure if it doesn't exist
        launch_config = {
            "version": "0.2.0",
            "configurations": []
        }
    
    # Get all profile files and existing profiles
    all_profiles = get_profile_files()
    existing_profiles = get_existing_profiles(launch_config)
    
    # Find missing profiles
    missing_profiles = [p for p in all_profiles if p not in existing_profiles]
    
    if not missing_profiles:
        print("All profiles are already in launch.json. No updates needed.")
        return
    
    # Separate the "Current File with Arguments" config from profile configs
    configurations = launch_config.get("configurations", [])
    current_file_config = None
    profile_configs = []
    
    for config in configurations:
        if config.get("name") == "Python Debugger: Current File with Arguments":
            current_file_config = config
        elif "--network-profile" in config.get("args", []):
            profile_configs.append(config)
        else:
            # Keep other configs that aren't profile-related
            profile_configs.append(config)
    
    # Add new profile configurations
    for profile_file in missing_profiles:
        new_config = create_launch_config(profile_file)
        profile_configs.append(new_config)
        print(f"Added launch configuration for: {profile_file}")
    
    # Sort profile configs by name
    profile_configs.sort(key=lambda x: x.get("name", ""))
    
    # Rebuild configurations list
    new_configurations = []
    if current_file_config:
        new_configurations.append(current_file_config)
    new_configurations.extend(profile_configs)
    
    launch_config["configurations"] = new_configurations
    
    # Write updated launch.json
    launch_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(launch_json_path, "w") as f:
        json.dump(launch_config, f, indent=2)
    
    print(f"\nUpdated {launch_json_path} with {len(missing_profiles)} new profile(s).")


if __name__ == "__main__":
    update_launch_json()


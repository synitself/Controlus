#!/bin/bash
# Controlus RGB - Apply saved color at startup with syncing
# Called by systemd user service
# Features: Restore last color, sync colors if mismatched

CONFIG="$HOME/.config/controlus/config.json"
HELPER="/usr/local/bin/controlus-helper"

if [ ! -x "$HELPER" ]; then
    echo "Helper not found at $HELPER"
    exit 1
fi

# Give system time to initialize hardware
sleep 1

# Get current colors for both devices
COLORS=$("$HELPER" get-current-colors 2>/dev/null)

if [ -z "$COLORS" ]; then
    echo "Could not detect device colors, using defaults"
    R=0
    G=214
    B=255
else
    # Parse JSON response
    R=$(echo "$COLORS" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['keyboard']['r'])" 2>/dev/null)
    G=$(echo "$COLORS" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['keyboard']['g'])" 2>/dev/null)
    B=$(echo "$COLORS" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['keyboard']['b'])" 2>/dev/null)
    
    # Fallback to defaults if parsing failed
    R=${R:-0}
    G=${G:-214}
    B=${B:-255}
fi

echo "Current keyboard color: R=$R G=$G B=$B"

# Parse config and get last saved color
if [ -f "$CONFIG" ]; then
    SAVED_COLOR=$(python3 << 'EOF'
import json
import os

config_path = os.path.expanduser("~/.config/controlus/config.json")
try:
    with open(config_path) as f:
        d = json.load(f)
    c = d.get("last_color", {"r": 0, "g": 214, "b": 255})
    print(f"{c.get('r', 0)} {c.get('g', 214)} {c.get('b', 255)}")
except Exception as e:
    print("0 214 255")  # Default cyan
EOF
)
    read SR SG SB <<< "$SAVED_COLOR"
    
    # If saved color exists and differs from current, apply it
    if [ "$SR" != "0" ] || [ "$SG" != "0" ] || [ "$SB" != "0" ]; then
        echo "Applying saved color: R=$SR G=$SG B=$SB"
        "$HELPER" set-color "$SR" "$SG" "$SB" 2>/dev/null
    fi
fi

# Sync colors - ensure mouse matches keyboard
# If colors were mismatched, mouse will be updated to keyboard color
echo "Syncing mouse to keyboard color..."
"$HELPER" sync-colors 2>/dev/null

echo "RGB initialization complete!"

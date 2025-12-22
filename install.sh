#!/bin/bash
#
# Controlus - Unified Installer
# Installs standalone application and/or GNOME Shell extension
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTENSION_UUID="controlus@io.github.controlus"
EXTENSION_DIR="$HOME/.local/share/gnome-shell/extensions/$EXTENSION_UUID"

echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║          Controlus - Unified Installer     ║${NC}"
echo -e "${BLUE}║   RGB Control for Gigabyte & Logitech      ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}"
echo

# --- UDEV RULES ---
install_udev() {
    echo -e "${BLUE}Installing udev rules (requires sudo)...${NC}"
    if [ -f "$SCRIPT_DIR/99-controlus.rules" ]; then
        sudo cp "$SCRIPT_DIR/99-controlus.rules" /etc/udev/rules.d/
        sudo udevadm control --reload-rules
        sudo udevadm trigger
        echo -e "${GREEN}✓${NC} udev rules installed"
    else
        echo -e "${RED}✗ udev rules file not found!${NC}"
    fi
}

# --- STANDALONE APP ---
install_app() {
    echo -e "\n${BLUE}Installing Standalone Application...${NC}"
    
    # 1. Backend module
    sudo mkdir -p /usr/local/lib/controlus
    sudo cp "$SCRIPT_DIR/app/backend.py" /usr/local/lib/controlus/
    sudo cp "$SCRIPT_DIR/app/__init__.py" /usr/local/lib/controlus/ 2>/dev/null || sudo touch /usr/local/lib/controlus/__init__.py
    
    # Python path
    PYTHON_SITE=$(python3 -c "import site; print(site.getsitepackages()[0])" 2>/dev/null || echo "/usr/lib/python3/dist-packages")
    sudo ln -sf /usr/local/lib/controlus "$PYTHON_SITE/controlus" 2>/dev/null || true
    
    # 2. Executables
    sudo cp "$SCRIPT_DIR/app/controlus-gui.py" /usr/local/bin/controlus
    sudo chmod +x /usr/local/bin/controlus
    
    sudo cp "$SCRIPT_DIR/app/controlus-autostart.py" /usr/local/bin/controlus-autostart
    sudo chmod +x /usr/local/bin/controlus-autostart
    
    # 3. Desktop entry
    sudo cp "$SCRIPT_DIR/app/io.github.controlus.desktop" /usr/share/applications/
    sudo update-desktop-database /usr/share/applications/ 2>/dev/null || true
    
    # 4. Service
    sudo cp "$SCRIPT_DIR/app/controlus-autostart.service" /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable controlus-autostart.service
    
    echo -e "${GREEN}✓${NC} Standalone app installed"
}

# --- GNOME EXTENSION ---
install_extension() {
    echo -e "\n${BLUE}Installing GNOME Shell Extension...${NC}"
    
    # 1. Check GNOME version
    if command -v gnome-shell &> /dev/null; then
        GNOME_VERSION=$(gnome-shell --version | grep -oP '\d+' | head -1)
        echo -e "${GREEN}✓${NC} GNOME Shell version: $GNOME_VERSION"
    fi
    
    # 2. Extension files
    mkdir -p "$EXTENSION_DIR"
    mkdir -p "$EXTENSION_DIR/schemas"
    mkdir -p "$EXTENSION_DIR/icons"
    
    cp "$SCRIPT_DIR/extension/metadata.json" "$EXTENSION_DIR/"
    cp "$SCRIPT_DIR/extension/extension.js" "$EXTENSION_DIR/"
    cp "$SCRIPT_DIR/extension/prefs.js" "$EXTENSION_DIR/"
    cp "$SCRIPT_DIR/extension/stylesheet.css" "$EXTENSION_DIR/"
    cp "$SCRIPT_DIR/extension/controlus-helper.py" "$EXTENSION_DIR/"
    
    if [ -d "$SCRIPT_DIR/extension/icons" ]; then
        cp -r "$SCRIPT_DIR/extension/icons/"* "$EXTENSION_DIR/icons/"
    fi
    cp "$SCRIPT_DIR/extension/schemas/"*.xml "$EXTENSION_DIR/schemas/"
    
    chmod +x "$EXTENSION_DIR/controlus-helper.py"
    
    # 3. Compile schemas
    if command -v glib-compile-schemas &> /dev/null; then
        glib-compile-schemas "$EXTENSION_DIR/schemas/"
        echo -e "${GREEN}✓${NC} Schemas compiled"
    fi
    
    # 4. System helper (Polkit)
    echo -e "${BLUE}Installing extension system helper (optional, requires sudo)...${NC}"
    sudo cp "$SCRIPT_DIR/extension/controlus-helper.py" /usr/local/bin/controlus-helper
    sudo chmod +x /usr/local/bin/controlus-helper
    sudo cp "$SCRIPT_DIR/extension/controlus-startup.sh" /usr/local/bin/controlus-startup
    sudo chmod +x /usr/local/bin/controlus-startup
    sudo cp "$SCRIPT_DIR/extension/io.github.controlus.policy" /usr/share/polkit-1/actions/ 2>/dev/null || true
    
    # 5. User service
    mkdir -p "$HOME/.config/systemd/user"
    cp "$SCRIPT_DIR/extension/controlus-autostart.service" "$HOME/.config/systemd/user/"
    systemctl --user daemon-reload
    systemctl --user enable controlus-autostart.service
    
    echo -e "${GREEN}✓${NC} Extension installed"
}

# --- UNINSTALL ---
uninstall() {
    echo -e "${YELLOW}Uninstalling Controlus...${NC}"
    
    # Standalone cleanup
    sudo rm -f /usr/local/bin/controlus
    sudo rm -f /usr/local/bin/controlus-autostart
    sudo rm -rf /usr/local/lib/controlus
    sudo rm -f /usr/share/applications/io.github.controlus.desktop
    sudo systemctl disable controlus-autostart.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/controlus-autostart.service
    
    # Extension cleanup
    gnome-extensions disable "$EXTENSION_UUID" 2>/dev/null || true
    rm -rf "$EXTENSION_DIR"
    sudo rm -f /usr/local/bin/controlus-helper
    sudo rm -f /usr/local/bin/controlus-startup
    sudo rm -f /usr/share/polkit-1/actions/io.github.controlus.policy
    systemctl --user disable controlus-autostart.service 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/controlus-autostart.service"
    
    # Rules
    sudo rm -f /etc/udev/rules.d/99-controlus.rules
    sudo udevadm control --reload-rules
    
    echo -e "${GREEN}✓${NC} Uninstallation complete"
}

# --- MAIN ---
usage() {
    echo "Usage: $0 [all|app|extension|uninstall]"
    exit 1
}

case "${1:-}" in
    all)
        install_udev
        install_app
        install_extension
        ;;
    app)
        install_udev
        install_app
        ;;
    extension)
        install_udev
        install_extension
        ;;
    uninstall)
        uninstall
        ;;
    *)
        echo "Choose what to install:"
        echo "1) All components (Recommended)"
        echo "2) Standalone Application only"
        echo "3) GNOME Shell Extension only"
        echo "4) Uninstall"
        read -p "Selection [1-4]: " Choice
        case "$Choice" in
            1) install_udev; install_app; install_extension ;;
            2) install_udev; install_app ;;
            3) install_udev; install_extension ;;
            4) uninstall ;;
            *) usage ;;
        esac
        ;;
esac

echo -e "\n${GREEN}Done!${NC}"
echo "Note: You may need to replug your devices or reboot for changes to take effect."

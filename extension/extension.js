/**
 * Controlus RGB - GNOME 49 Quick Settings Extension
 * Control AORUS/Gigabyte keyboard RGB lighting
 */

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Cairo from 'gi://cairo';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as Slider from 'resource:///org/gnome/shell/ui/slider.js';

import { Extension, gettext as _ } from 'resource:///org/gnome/shell/extensions/extension.js';

// HID device identifiers for AORUS/Gigabyte keyboard
const VENDOR_ID = 0x0414;
const PRODUCT_ID = 0x7A44;

/**
 * Convert HSV to RGB
 * @param {number} h - Hue (0-360)
 * @param {number} s - Saturation (0-1)
 * @param {number} v - Value (0-1)
 * @returns {object} {r, g, b} (0-255)
 */
function hsvToRgb(h, s, v) {
    let r, g, b;
    const i = Math.floor(h / 60) % 6;
    const f = h / 60 - Math.floor(h / 60);
    const p = v * (1 - s);
    const q = v * (1 - f * s);
    const t = v * (1 - (1 - f) * s);
    
    switch (i) {
        case 0: r = v; g = t; b = p; break;
        case 1: r = q; g = v; b = p; break;
        case 2: r = p; g = v; b = t; break;
        case 3: r = p; g = q; b = v; break;
        case 4: r = t; g = p; b = v; break;
        case 5: r = v; g = p; b = q; break;
    }
    
    return {
        r: Math.round(r * 255),
        g: Math.round(g * 255),
        b: Math.round(b * 255)
    };
}

/**
 * RGB Backend - Communicates with keyboard via helper script
 */
class RGBBackend {
    constructor(extensionPath) {
        this._extensionPath = extensionPath;
        this._helperPath = GLib.build_filenamev([extensionPath, 'controlus-helper.py']);
    }

    /**
     * Set keyboard color using helper script
     * @param {number} r - Red (0-255)
     * @param {number} g - Green (0-255)
     * @param {number} b - Blue (0-255)
     * @param {number} brightness - Brightness (0-100)
     * @returns {Promise<boolean>}
     */
    async setColor(r, g, b, brightness = 100) {
        return new Promise((resolve) => {
            try {
                const subprocess = Gio.Subprocess.new(
                    [this._helperPath, 'set-color',
                     r.toString(), g.toString(), b.toString(), brightness.toString()],
                    Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
                );
                
                subprocess.communicate_utf8_async(null, null, (proc, res) => {
                    try {
                        const [, stdout, stderr] = proc.communicate_utf8_finish(res);
                        const success = proc.get_successful();
                        if (!success) {
                            log(`[Controlus] Error: ${stderr}`);
                        }
                        resolve(success);
                    } catch (e) {
                        log(`[Controlus] Exception: ${e.message}`);
                        resolve(false);
                    }
                });
            } catch (e) {
                log(`[Controlus] Failed to start helper: ${e.message}`);
                resolve(false);
            }
        });
    }

    /**
     * Turn off keyboard backlight
     */
    async turnOff() {
        return this.setColor(0, 0, 0, 0);
    }

    /**
     * Check if keyboard device is available
     */
    isDeviceAvailable() {
        const hidrawDir = Gio.File.new_for_path('/sys/class/hidraw');
        try {
            const enumerator = hidrawDir.enumerate_children(
                'standard::name',
                Gio.FileQueryInfoFlags.NONE,
                null
            );

            let fileInfo;
            while ((fileInfo = enumerator.next_file(null)) !== null) {
                const name = fileInfo.get_name();
                const devicePath = `/sys/class/hidraw/${name}/device/uevent`;
                
                try {
                    const [ok, contents] = GLib.file_get_contents(devicePath);
                    if (ok) {
                        const text = new TextDecoder().decode(contents).toUpperCase();
                        if (text.includes(VENDOR_ID.toString(16).toUpperCase()) && 
                            text.includes(PRODUCT_ID.toString(16).toUpperCase())) {
                            return true;
                        }
                    }
                } catch (e) {
                    continue;
                }
            }
        } catch (e) {
            log(`[Controlus] Device check error: ${e.message}`);
        }
        return false;
    }
}

/**
 * Interactive RGB Spectrum Bar Widget
 */
const RGBSpectrumBar = GObject.registerClass({
    Signals: {
        'color-selected': { param_types: [GObject.TYPE_INT, GObject.TYPE_INT, GObject.TYPE_INT] },
    },
}, class RGBSpectrumBar extends St.DrawingArea {
    _init() {
        super._init({
            style_class: 'controlus-spectrum-bar',
            reactive: true,
            can_focus: true,
            x_expand: true,
            track_hover: true,
        });
        
        this.set_height(32);
        this._selectedHue = 0;
        this._isDragging = false;
        
        this.connect('repaint', this._onRepaint.bind(this));
        
        // Use button-press-event and motion for interaction
        this.connect('button-press-event', this._onButtonPress.bind(this));
        this.connect('button-release-event', this._onButtonRelease.bind(this));
        this.connect('motion-event', this._onMotion.bind(this));
    }
    
    _onButtonPress(actor, event) {
        this._isDragging = true;
        const [x, y] = event.get_coords();
        this._selectColorAtPosition(x);
        return Clutter.EVENT_STOP;
    }
    
    _onButtonRelease(actor, event) {
        this._isDragging = false;
        return Clutter.EVENT_STOP;
    }
    
    _onMotion(actor, event) {
        if (this._isDragging) {
            const [x, y] = event.get_coords();
            this._selectColorAtPosition(x);
        }
        return Clutter.EVENT_PROPAGATE;
    }
    
    _onRepaint(area) {
        const cr = area.get_context();
        const [width, height] = area.get_surface_size();
        
        if (width <= 0 || height <= 0) return;
        
        // Draw rounded rectangle clip
        const radius = 8;
        cr.newSubPath();
        cr.arc(width - radius, radius, radius, -Math.PI / 2, 0);
        cr.arc(width - radius, height - radius, radius, 0, Math.PI / 2);
        cr.arc(radius, height - radius, radius, Math.PI / 2, Math.PI);
        cr.arc(radius, radius, radius, Math.PI, 3 * Math.PI / 2);
        cr.closePath();
        cr.clip();
        
        // Draw spectrum gradient
        const gradient = new Cairo.LinearGradient(0, 0, width, 0);
        
        // Add color stops for full spectrum
        const stops = [
            [0.0, 1, 0, 0],      // Red
            [0.17, 1, 1, 0],     // Yellow
            [0.33, 0, 1, 0],     // Green
            [0.5, 0, 1, 1],      // Cyan
            [0.67, 0, 0, 1],     // Blue
            [0.83, 1, 0, 1],     // Magenta
            [1.0, 1, 0, 0],      // Red (wrap)
        ];
        
        stops.forEach(([offset, r, g, b]) => {
            gradient.addColorStopRGB(offset, r, g, b);
        });
        
        cr.setSource(gradient);
        cr.paint();
        
        // Draw selection indicator
        const indicatorX = (this._selectedHue / 360) * width;
        cr.setSourceRGBA(1, 1, 1, 0.9);
        cr.setLineWidth(3);
        cr.moveTo(indicatorX, 0);
        cr.lineTo(indicatorX, height);
        cr.stroke();
        
        cr.setSourceRGBA(0, 0, 0, 0.5);
        cr.setLineWidth(1);
        cr.moveTo(indicatorX, 0);
        cr.lineTo(indicatorX, height);
        cr.stroke();
        
        cr.$dispose();
    }
    
    _selectColorAtPosition(globalX) {
        const [actorX, actorY] = this.get_transformed_position();
        const localX = globalX - actorX;
        const width = this.get_width();
        
        if (width <= 0) return;
        
        // Calculate hue from position (0-360)
        const ratio = Math.max(0, Math.min(1, localX / width));
        this._selectedHue = ratio * 360;
        
        // Convert to RGB
        const rgb = hsvToRgb(this._selectedHue, 1, 1);
        
        // Emit signal
        this.emit('color-selected', rgb.r, rgb.g, rgb.b);
        
        // Redraw to update indicator
        this.queue_repaint();
    }
    
    setHue(hue) {
        this._selectedHue = hue;
        this.queue_repaint();
    }
});

/**
 * Quick Settings Menu for Controlus
 */
const ControlusMenuToggle = GObject.registerClass(
class ControlusMenuToggle extends QuickSettings.QuickMenuToggle {
    _init(backend, settings) {
        super._init({
            title: _('Controlus'),
            subtitle: _('Подсветка'),
            iconName: 'preferences-color-symbolic',
            toggleMode: true,
        });

        this._backend = backend;
        this._settings = settings;
        this._brightness = settings.get_int('brightness');
        this._currentColor = null;
        
        // Check if toggle is enabled from settings
        this.checked = settings.get_boolean('enabled');
        
        // Build the menu
        this._buildMenu();
        
        // Load saved color into UI (without applying to devices)
        this._loadSavedColorToUI();
        
        // Connect toggle
        this.connect('clicked', () => {
            this._onToggleClicked();
        });

        // Connect to settings changes
        this._settingsChangedId = settings.connect('changed', (settings, key) => {
            if (key === 'brightness') {
                this._brightness = settings.get_int('brightness');
                this._brightnessSlider.value = this._brightness / 100;
            } else if (key === 'enabled') {
                this.checked = settings.get_boolean('enabled');
            }
        });
    }
    
    /**
     * Load saved color from gsettings into UI elements (spectrum bar position, preview)
     * Does NOT apply color to devices
     */
    _loadSavedColorToUI() {
        const r = this._settings.get_int('last-color-r');
        const g = this._settings.get_int('last-color-g');
        const b = this._settings.get_int('last-color-b');
        
        // Store current color
        this._currentColor = { r, g, b, name: `RGB(${r}, ${g}, ${b})` };
        
        // Calculate hue from RGB to position spectrum bar
        const hue = this._rgbToHue(r, g, b);
        if (this._spectrumBar) {
            this._spectrumBar.setHue(hue);
        }
        
        // Update preview swatch and label
        if (this._colorSwatch) {
            this._colorSwatch.style = `background-color: rgb(${r}, ${g}, ${b}); border-radius: 4px; min-width: 24px; min-height: 24px;`;
        }
        if (this._colorLabel) {
            this._colorLabel.text = `RGB(${r}, ${g}, ${b})`;
        }
        
        // Update subtitle if enabled
        if (this.checked) {
            this.subtitle = `RGB(${r}, ${g}, ${b})`;
        }
    }
    
    /**
     * Convert RGB to Hue (0-360)
     */
    _rgbToHue(r, g, b) {
        r /= 255;
        g /= 255;
        b /= 255;
        
        const max = Math.max(r, g, b);
        const min = Math.min(r, g, b);
        const diff = max - min;
        
        if (diff === 0) return 0;
        
        let hue;
        if (max === r) {
            hue = ((g - b) / diff) % 6;
        } else if (max === g) {
            hue = (b - r) / diff + 2;
        } else {
            hue = (r - g) / diff + 4;
        }
        
        hue *= 60;
        if (hue < 0) hue += 360;
        
        return hue;
    }

    _buildMenu() {
        // Header section
        this.menu.setHeader('preferences-color-symbolic', _('Controlus'));
        
        // RGB Spectrum bar section
        const colorSection = new PopupMenu.PopupMenuSection();
        
        const spectrumContainer = new St.BoxLayout({
            vertical: true,
            style: 'padding: 12px;',
            x_expand: true,
        });
        
        // Label
        const colorLabel = new St.Label({
            text: _('Цвет'),
            style: 'font-weight: bold; margin-bottom: 8px;',
        });
        spectrumContainer.add_child(colorLabel);
        
        // Spectrum bar
        this._spectrumBar = new RGBSpectrumBar();
        this._spectrumBar.connect('color-selected', (bar, r, g, b) => {
            this._onSpectrumColorSelected(r, g, b);
        });
        spectrumContainer.add_child(this._spectrumBar);
        
        // Current color preview
        this._colorPreview = new St.BoxLayout({
            style: 'margin-top: 8px;',
            x_expand: true,
        });
        
        this._colorSwatch = new St.Widget({
            style: 'background-color: rgb(255, 0, 0); border-radius: 4px; min-width: 24px; min-height: 24px;',
        });
        
        this._colorLabel = new St.Label({
            text: 'RGB(255, 0, 0)',
            style: 'margin-left: 8px; font-family: monospace;',
            y_align: Clutter.ActorAlign.CENTER,
        });
        
        this._colorPreview.add_child(this._colorSwatch);
        this._colorPreview.add_child(this._colorLabel);
        spectrumContainer.add_child(this._colorPreview);
        
        const colorItem = new PopupMenu.PopupBaseMenuItem({
            reactive: false,
            can_focus: false,
        });
        colorItem.add_child(spectrumContainer);
        colorSection.addMenuItem(colorItem);

        this.menu.addMenuItem(colorSection);
        
        // Separator
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        
        // Brightness slider section
        const brightnessSection = new PopupMenu.PopupMenuSection();
        
        const brightnessItem = new PopupMenu.PopupBaseMenuItem({
            reactive: false,
            can_focus: false,
        });

        const brightnessBox = new St.BoxLayout({
            style_class: 'controlus-brightness-box',
            vertical: false,
            x_expand: true,
            style: 'spacing: 12px; padding: 4px 8px;',
        });

        const brightnessIcon = new St.Icon({
            icon_name: 'display-brightness-symbolic',
            style_class: 'popup-menu-icon',
        });

        this._brightnessSlider = new Slider.Slider(this._brightness / 100);
        this._brightnessSlider.x_expand = true;
        this._brightnessSlider.connect('notify::value', () => {
            this._onBrightnessChanged();
        });

        this._brightnessLabel = new St.Label({
            text: `${this._brightness}%`,
            style: 'min-width: 40px; text-align: right;',
        });

        brightnessBox.add_child(brightnessIcon);
        brightnessBox.add_child(this._brightnessSlider);
        brightnessBox.add_child(this._brightnessLabel);

        brightnessItem.add_child(brightnessBox);
        brightnessSection.addMenuItem(brightnessItem);

        this.menu.addMenuItem(brightnessSection);

        // Separator before settings
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // Settings button with icon
        const settingsItem = new PopupMenu.PopupImageMenuItem(_('Настройки'), 'emblem-system-symbolic');
        settingsItem.connect('activate', () => {
            this._openSettings();
        });
        this.menu.addMenuItem(settingsItem);
    }

    async _onToggleClicked() {
        const enabled = this.checked;
        this._settings.set_boolean('enabled', enabled);
        
        if (enabled) {
            // Restore last color or use default
            const lastR = this._settings.get_int('last-color-r');
            const lastG = this._settings.get_int('last-color-g');
            const lastB = this._settings.get_int('last-color-b');
            
            if (lastR || lastG || lastB) {
                await this._backend.setColor(lastR, lastG, lastB, this._brightness);
            } else {
                // Default to red
                await this._backend.setColor(255, 0, 0, this._brightness);
            }
        } else {
            await this._backend.turnOff();
        }
    }

    async _onColorSelected(color) {
        this._currentColor = color;
        this.checked = true;
        this._settings.set_boolean('enabled', true);
        
        // Save selected color
        this._settings.set_int('last-color-r', color.r);
        this._settings.set_int('last-color-g', color.g);
        this._settings.set_int('last-color-b', color.b);
        
        // Apply color
        await this._backend.setColor(color.r, color.g, color.b, this._brightness);
        
        // Update subtitle
        this.subtitle = color.name;
    }

    async _onSpectrumColorSelected(r, g, b) {
        this._currentColor = { r, g, b, name: `RGB(${r}, ${g}, ${b})` };
        this.checked = true;
        this._settings.set_boolean('enabled', true);
        
        // Update preview
        if (this._colorSwatch) {
            this._colorSwatch.style = `background-color: rgb(${r}, ${g}, ${b}); border-radius: 4px; min-width: 24px; min-height: 24px;`;
        }
        if (this._colorLabel) {
            this._colorLabel.text = `RGB(${r}, ${g}, ${b})`;
        }
        
        // Save selected color
        this._settings.set_int('last-color-r', r);
        this._settings.set_int('last-color-g', g);
        this._settings.set_int('last-color-b', b);
        
        // Apply color
        await this._backend.setColor(r, g, b, this._brightness);
        
        // Update subtitle
        this.subtitle = `RGB(${r}, ${g}, ${b})`;
    }

    async _onBrightnessChanged() {
        this._brightness = Math.round(this._brightnessSlider.value * 100);
        this._brightnessLabel.text = `${this._brightness}%`;
        this._settings.set_int('brightness', this._brightness);
        
        // Apply brightness if enabled
        if (this.checked && this._currentColor) {
            await this._backend.setColor(
                this._currentColor.r,
                this._currentColor.g,
                this._currentColor.b,
                this._brightness
            );
        } else if (this.checked) {
            const lastR = this._settings.get_int('last-color-r');
            const lastG = this._settings.get_int('last-color-g');
            const lastB = this._settings.get_int('last-color-b');
            if (lastR || lastG || lastB) {
                await this._backend.setColor(lastR, lastG, lastB, this._brightness);
            }
        }
    }

    async _turnOff() {
        this.checked = false;
        this._settings.set_boolean('enabled', false);
        this.subtitle = _('Выкл');
        await this._backend.turnOff();
    }

    _openSettings() {
        // Open extension preferences
        const extensionObject = Extension.lookupByUUID('controlus@io.github.controlus');
        if (extensionObject) {
            extensionObject.openPreferences();
        }
    }

    destroy() {
        if (this._settingsChangedId) {
            this._settings.disconnect(this._settingsChangedId);
            this._settingsChangedId = null;
        }
        super.destroy();
    }
});

/**
 * Quick Settings Indicator
 */
const ControlusIndicator = GObject.registerClass(
class ControlusIndicator extends QuickSettings.SystemIndicator {
    _init(backend, settings) {
        super._init();

        this._backend = backend;
        this._settings = settings;

        // Create indicator icon (shows when RGB is active)
        this._indicator = this._addIndicator();
        this._indicator.icon_name = 'preferences-color-symbolic';
        this._indicator.visible = settings.get_boolean('show-indicator') && settings.get_boolean('enabled');

        // Create toggle menu
        this._toggle = new ControlusMenuToggle(backend, settings);
        this.quickSettingsItems.push(this._toggle);

        // Connect to settings
        this._settingsChangedId = settings.connect('changed', (settings, key) => {
            if (key === 'enabled' || key === 'show-indicator') {
                this._indicator.visible = settings.get_boolean('show-indicator') && 
                                         settings.get_boolean('enabled');
            }
        });
    }

    destroy() {
        if (this._settingsChangedId) {
            this._settings.disconnect(this._settingsChangedId);
            this._settingsChangedId = null;
        }
        this._toggle.destroy();
        super.destroy();
    }
});

/**
 * Main Extension Class
 */
export default class ControlusExtension extends Extension {
    enable() {
        log('[Controlus] Extension enabled');
        
        this._settings = this.getSettings();
        this._backend = new RGBBackend(this.path);
        
        // Check device availability
        if (!this._backend.isDeviceAvailable()) {
            log('[Controlus] Warning: AORUS/Gigabyte keyboard not detected');
        }
        
        // Create indicator and add to Quick Settings
        this._indicator = new ControlusIndicator(this._backend, this._settings);
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);

        // Apply stylesheet
        this._loadStylesheet();

        // NOTE: We do NOT auto-apply colors on startup anymore.
        // User manages device colors manually, extension only stores/displays the selection.
        // Colors are applied only when user interacts with the spectrum bar or toggle.
    }

    disable() {
        log('[Controlus] Extension disabled');
        
        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
        
        this._backend = null;
        this._settings = null;
    }

    _loadStylesheet() {
        // IMPORTANT: Do not load stylesheet globally to avoid affecting GNOME Shell theme
        // Only apply minimal styling to custom widgets
        // The stylesheet.css should only contain .controlus-* class definitions
        
        const stylesheetPath = GLib.build_filenamev([this.path, 'stylesheet.css']);
        const stylesheetFile = Gio.File.new_for_path(stylesheetPath);
        
        if (stylesheetFile.query_exists(null)) {
            const themeContext = St.ThemeContext.get_for_stage(global.stage);
            const theme = themeContext.get_theme();
            // Load stylesheet but ensure it doesn't override core GNOME Shell styles
            theme.load_stylesheet(stylesheetFile);
        }
    }
}

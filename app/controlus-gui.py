#!/usr/bin/env python3
"""Controlus - Keyboard & Mouse RGB Control

Supports:
  - Gigabyte/AORUS keyboards
  - Logitech G Pro Wireless mouse
  - Any OpenRGB-supported device
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gdk, Gio, GLib
import subprocess, json, math, os, struct, fcntl, glob
from pathlib import Path
try:
    from controlus.backend import set_color as backend_set_color
except Exception:
    backend_set_color = None

CONFIG_DIR = Path.home() / ".config" / "controlus"
CONFIG_FILE = CONFIG_DIR / "config.json"

# HID constants for Gigabyte keyboard
HIDIOCSFEATURE = 0xC0094806
VENDOR_ID = 0x0414
PRODUCT_ID = 0x7A44


def find_hidraw_device():
    """Find the correct hidraw device for the keyboard"""
    for hidraw in glob.glob('/dev/hidraw*'):
        try:
            device_path = f'/sys/class/hidraw/{os.path.basename(hidraw)}/device'
            
            # Check modalias for VID:PID
            modalias_path = f'{device_path}/modalias'
            if os.path.exists(modalias_path):
                with open(modalias_path, 'r') as f:
                    modalias = f.read().lower()
                    vid = f'{VENDOR_ID:04X}'.lower()
                    pid = f'{PRODUCT_ID:04X}'.lower()
                    if vid in modalias and pid in modalias:
                        return hidraw
            
            # Alternative: check uevent
            uevent_path = f'{device_path}/uevent'
            if os.path.exists(uevent_path):
                with open(uevent_path, 'r') as f:
                    content = f.read().upper()
                    if f'{VENDOR_ID:04X}' in content and f'{PRODUCT_ID:04X}' in content:
                        return hidraw
        except (IOError, PermissionError):
            continue
    return None


def set_keyboard_color(r, g, b, brightness=100):
    """Set keyboard color directly via HID"""
    device = find_hidraw_device()
    if not device:
        return False, "Keyboard device not found"
    
    try:
        # Apply brightness
        factor = brightness / 100
        r = int(r * factor)
        g = int(g * factor)
        b = int(b * factor)
        
        with open(device, 'r+b', buffering=0) as fd:
            # Set all zones (0-9 and 0xFF for all)
            for zone in list(range(10)) + [0xFF]:
                # Build packet: [ReportID, Cmd, Zone, R, G, B, Brightness, 0, Checksum]
                cmd = 0x08  # SetZoneColors
                packet = [0x00, cmd, zone, r, g, b, 100, 0x00]
                checksum = (255 - sum(packet[1:7])) & 0xFF
                packet.append(checksum)
                
                data = bytes(packet)
                buf = bytearray(9)
                buf[0:len(data)] = data
                
                fcntl.ioctl(fd.fileno(), HIDIOCSFEATURE, bytes(buf))
        
        return True, f"RGB({r}, {g}, {b})"
    except PermissionError:
        return False, "Permission denied - udev rule not installed"
    except Exception as e:
        return False, str(e)


class ColorWheelWidget(Gtk.DrawingArea):
    """Custom color wheel widget"""
    
    def __init__(self):
        super().__init__()
        self.set_size_request(280, 280)
        self.set_draw_func(self.draw)
        
        self.hue = 0.0
        self.saturation = 1.0
        self.value = 1.0
        
        self.click_gesture = Gtk.GestureClick.new()
        self.click_gesture.connect("pressed", self.on_click)
        self.add_controller(self.click_gesture)
        
        self.drag_gesture = Gtk.GestureDrag.new()
        self.drag_gesture.connect("drag-update", self.on_drag)
        self.drag_gesture.connect("drag-begin", self.on_drag_begin)
        self.add_controller(self.drag_gesture)
        
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.on_color_changed = None
    
    def draw(self, area, cr, width, height):
        center_x = width / 2
        center_y = height / 2
        radius = min(width, height) / 2 - 10
        
        for angle in range(360):
            rad = math.radians(angle)
            for r in range(int(radius)):
                sat = r / radius
                h = angle / 360.0
                rgb = self.hsv_to_rgb(h, sat, self.value)
                cr.set_source_rgb(*rgb)
                x = center_x + r * math.cos(rad)
                y = center_y + r * math.sin(rad)
                cr.rectangle(x, y, 2, 2)
                cr.fill()
        
        sel_angle = self.hue * 2 * math.pi
        sel_radius = self.saturation * radius
        sel_x = center_x + sel_radius * math.cos(sel_angle)
        sel_y = center_y + sel_radius * math.sin(sel_angle)
        
        cr.set_source_rgb(1, 1, 1)
        cr.arc(sel_x, sel_y, 12, 0, 2 * math.pi)
        cr.stroke()
        
        cr.set_source_rgb(0, 0, 0)
        cr.arc(sel_x, sel_y, 10, 0, 2 * math.pi)
        cr.stroke()
        
        rgb = self.hsv_to_rgb(self.hue, self.saturation, self.value)
        cr.set_source_rgb(*rgb)
        cr.arc(sel_x, sel_y, 8, 0, 2 * math.pi)
        cr.fill()
    
    def hsv_to_rgb(self, h, s, v):
        if s == 0:
            return (v, v, v)
        i = int(h * 6)
        f = (h * 6) - i
        p = v * (1 - s)
        q = v * (1 - s * f)
        t = v * (1 - s * (1 - f))
        i %= 6
        if i == 0: return (v, t, p)
        if i == 1: return (q, v, p)
        if i == 2: return (p, v, t)
        if i == 3: return (p, q, v)
        if i == 4: return (t, p, v)
        if i == 5: return (v, p, q)
        return (v, v, v)
    
    def get_rgb(self):
        rgb = self.hsv_to_rgb(self.hue, self.saturation, self.value)
        return tuple(int(c * 255) for c in rgb)
    
    def set_rgb(self, r, g, b):
        r, g, b = r / 255, g / 255, b / 255
        max_c = max(r, g, b)
        min_c = min(r, g, b)
        diff = max_c - min_c
        
        self.value = max_c
        self.saturation = 0 if max_c == 0 else diff / max_c
        
        if diff == 0:
            self.hue = 0
        elif max_c == r:
            self.hue = ((g - b) / diff) % 6 / 6
        elif max_c == g:
            self.hue = ((b - r) / diff + 2) / 6
        else:
            self.hue = ((r - g) / diff + 4) / 6
        
        if self.hue < 0:
            self.hue += 1
        self.queue_draw()
    
    def update_from_coords(self, x, y):
        width = self.get_width()
        height = self.get_height()
        center_x = width / 2
        center_y = height / 2
        radius = min(width, height) / 2 - 10
        
        dx = x - center_x
        dy = y - center_y
        
        angle = math.atan2(dy, dx)
        self.hue = (angle / (2 * math.pi)) % 1.0
        
        dist = math.sqrt(dx * dx + dy * dy)
        self.saturation = min(1.0, dist / radius)
        
        self.queue_draw()
        if self.on_color_changed:
            self.on_color_changed(self.get_rgb())
    
    def on_click(self, gesture, n_press, x, y):
        self.update_from_coords(x, y)
    
    def on_drag_begin(self, gesture, x, y):
        self.drag_start_x = x
        self.drag_start_y = y
    
    def on_drag(self, gesture, offset_x, offset_y):
        x = self.drag_start_x + offset_x
        y = self.drag_start_y + offset_y
        self.update_from_coords(x, y)


class FavoriteColorButton(Gtk.Button):
    def __init__(self, r, g, b, name=""):
        super().__init__()
        self.r, self.g, self.b = r, g, b
        self.color_name = name
        
        self.set_size_request(48, 48)
        self.add_css_class("flat")
        
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_size_request(40, 40)
        self.drawing_area.set_draw_func(self.draw_color)
        self.set_child(self.drawing_area)
        
        tooltip = f"{name}\n" if name else ""
        self.set_tooltip_text(f"{tooltip}RGB({r}, {g}, {b})\nRight-click to remove")
    
    def draw_color(self, area, cr, width, height):
        cr.set_source_rgb(self.r / 255, self.g / 255, self.b / 255)
        radius = 8
        cr.new_sub_path()
        cr.arc(width - radius, radius, radius, -math.pi/2, 0)
        cr.arc(width - radius, height - radius, radius, 0, math.pi/2)
        cr.arc(radius, height - radius, radius, math.pi/2, math.pi)
        cr.arc(radius, radius, radius, math.pi, 3*math.pi/2)
        cr.close_path()
        cr.fill()
        
        cr.set_source_rgba(0, 0, 0, 0.3)
        cr.set_line_width(1)
        cr.new_sub_path()
        cr.arc(width - radius, radius, radius, -math.pi/2, 0)
        cr.arc(width - radius, height - radius, radius, 0, math.pi/2)
        cr.arc(radius, height - radius, radius, math.pi/2, math.pi)
        cr.arc(radius, radius, radius, math.pi, 3*math.pi/2)
        cr.close_path()
        cr.stroke()


class ControlusWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.set_title("Controlus")
        self.set_default_size(400, 600)
        
        self.config = self.load_config()
        
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)
        
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(self.main_box)
        
        # Header
        header = Adw.HeaderBar()
        title_widget = Adw.WindowTitle(title="Controlus", subtitle="RGB Control")
        header.set_title_widget(title_widget)
        self.main_box.append(header)
        
        # Content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        self.main_box.append(scrolled)
        
        clamp = Adw.Clamp()
        clamp.set_maximum_size(500)
        scrolled.set_child(clamp)
        
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(12)
        content.set_margin_end(12)
        clamp.set_child(content)
        
        # === Color Wheel ===
        wheel_group = Adw.PreferencesGroup()
        wheel_group.set_title("Color")
        content.append(wheel_group)
        
        wheel_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        wheel_box.set_halign(Gtk.Align.CENTER)
        
        self.color_wheel = ColorWheelWidget()
        self.color_wheel.on_color_changed = self.on_wheel_color_changed
        wheel_box.append(self.color_wheel)
        
        # Preview
        preview_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        preview_box.set_halign(Gtk.Align.CENTER)
        
        self.color_preview = Gtk.DrawingArea()
        self.color_preview.set_size_request(60, 40)
        self.color_preview.set_draw_func(self.draw_preview)
        preview_box.append(self.color_preview)
        
        self.color_label = Gtk.Label(label="RGB(255, 0, 0)")
        self.color_label.add_css_class("monospace")
        preview_box.append(self.color_label)
        
        wheel_box.append(preview_box)
        
        # Brightness
        brightness_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        brightness_label = Gtk.Label(label="Brightness")
        brightness_box.append(brightness_label)
        
        self.brightness_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.brightness_scale.set_value(self.config.get('brightness', 100))
        self.brightness_scale.set_hexpand(True)
        self.brightness_scale.connect("value-changed", self.on_brightness_changed)
        brightness_box.append(self.brightness_scale)
        
        wheel_box.append(brightness_box)
        
        wheel_row = Adw.ActionRow()
        wheel_row.set_child(wheel_box)
        wheel_group.add(wheel_row)
        
        # === Apply Button ===
        apply_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        apply_box.set_halign(Gtk.Align.CENTER)
        apply_box.set_margin_top(12)
        
        self.apply_button = Gtk.Button(label="Apply")
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.add_css_class("pill")
        self.apply_button.set_size_request(120, -1)
        self.apply_button.connect("clicked", self.on_apply_clicked)
        apply_box.append(self.apply_button)
        
        self.add_fav_button = Gtk.Button(label="♥ Save")
        self.add_fav_button.add_css_class("pill")
        self.add_fav_button.connect("clicked", self.on_add_favorite_clicked)
        apply_box.append(self.add_fav_button)
        
        # Turn off button
        self.off_button = Gtk.Button(label="Off")
        self.off_button.add_css_class("pill")
        self.off_button.add_css_class("destructive-action")
        self.off_button.connect("clicked", self.on_off_clicked)
        apply_box.append(self.off_button)
        
        content.append(apply_box)
        
        # === Favorites ===
        self.fav_group = Adw.PreferencesGroup()
        self.fav_group.set_title("Favorites")
        self.fav_group.set_description("Right-click to remove")
        content.append(self.fav_group)
        
        self.rebuild_favorites_ui()
        
        # Load color: try saved, then detect from device, then default cyan
        self.current_rgb = self._get_initial_color()
        self.color_wheel.set_rgb(*self.current_rgb)
        self.update_color_display()
    
    def draw_preview(self, area, cr, width, height):
        r, g, b = self.current_rgb
        cr.set_source_rgb(r / 255, g / 255, b / 255)
        radius = 8
        cr.new_sub_path()
        cr.arc(width - radius, radius, radius, -math.pi/2, 0)
        cr.arc(width - radius, height - radius, radius, 0, math.pi/2)
        cr.arc(radius, height - radius, radius, math.pi/2, math.pi)
        cr.arc(radius, radius, radius, math.pi, 3*math.pi/2)
        cr.close_path()
        cr.fill()
    
    def on_wheel_color_changed(self, rgb):
        self.current_rgb = rgb
        self.update_color_display()
    
    def on_brightness_changed(self, scale):
        self.color_wheel.value = scale.get_value() / 100
        self.color_wheel.queue_draw()
        self.current_rgb = self.color_wheel.get_rgb()
        self.update_color_display()
    
    def update_color_display(self):
        r, g, b = self.current_rgb
        self.color_label.set_text(f"RGB({r}, {g}, {b})")
        self.color_preview.queue_draw()
    
    def on_apply_clicked(self, button):
        r, g, b = self.current_rgb
        brightness = int(self.brightness_scale.get_value())
        self.apply_color(r, g, b, brightness)
    
    def on_off_clicked(self, button):
        self.apply_color(0, 0, 0, 0)
        self.show_toast("Keyboard backlight off")
    
    def apply_color(self, r, g, b, brightness=100):
        success, msg = (False, "backend not available")
        if backend_set_color:
            try:
                success, msg = backend_set_color(r, g, b, brightness)
            except Exception as e:
                success, msg = False, str(e)
        else:
            # Fallback to direct HID if backend import failed
            success, msg = set_keyboard_color(r, g, b, brightness)
        
        if success:
            # Save last color
            self.config['last_color'] = {'r': r, 'g': g, 'b': b}
            self.config['brightness'] = brightness
            self.save_config()
            self.show_toast(f"Applied {msg}")
        else:
            self.show_toast(f"Error: {msg}")
    
    def show_toast(self, message):
        toast = Adw.Toast.new(message)
        toast.set_timeout(2)
        self.toast_overlay.add_toast(toast)
    
    def on_add_favorite_clicked(self, button):
        r, g, b = self.current_rgb
        
        # Check if already exists
        for fav in self.config.get('favorites', []):
            if fav['r'] == r and fav['g'] == g and fav['b'] == b:
                self.show_toast("Color already in favorites")
                return
        
        if 'favorites' not in self.config:
            self.config['favorites'] = []
        
        self.config['favorites'].append({'r': r, 'g': g, 'b': b})
        self.save_config()
        self.rebuild_favorites_ui()
        self.show_toast("Added to favorites")
    
    def load_config(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {'favorites': [], 'last_color': None, 'brightness': 100}
    
    def _get_initial_color(self):
        """Get initial color from config or default cyan."""
        last = self.config.get('last_color')
        if last and last.get('r') is not None:
            return (last['r'], last['g'], last['b'])
        # Default cyan - matches typical setup
        return (0, 255, 255)
    
    def save_config(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=2)
    
    def rebuild_favorites_ui(self):
        # Clear old
        child = self.fav_group.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            if isinstance(child, Adw.ActionRow):
                self.fav_group.remove(child)
            child = next_child
        
        favorites = self.config.get('favorites', [])
        
        if not favorites:
            row = Adw.ActionRow()
            row.set_title("No favorites yet")
            row.set_subtitle("Click ♥ Save to add current color")
            self.fav_group.add(row)
            return
        
        fav_flow = Gtk.FlowBox()
        fav_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        fav_flow.set_max_children_per_line(8)
        fav_flow.set_min_children_per_line(4)
        fav_flow.set_homogeneous(True)
        fav_flow.set_row_spacing(8)
        fav_flow.set_column_spacing(8)
        
        for i, fav in enumerate(favorites):
            r, g, b = fav['r'], fav['g'], fav['b']
            
            btn = FavoriteColorButton(r, g, b)
            btn.connect("clicked", self.on_favorite_clicked, r, g, b)
            
            # Right-click to delete
            right_click = Gtk.GestureClick.new()
            right_click.set_button(3)  # Right mouse button
            right_click.connect("pressed", self.on_favorite_right_click, i)
            btn.add_controller(right_click)
            
            fav_flow.append(btn)
        
        fav_row = Adw.ActionRow()
        fav_row.set_child(fav_flow)
        self.fav_group.add(fav_row)
    
    def on_favorite_clicked(self, button, r, g, b):
        self.current_rgb = (r, g, b)
        self.color_wheel.set_rgb(r, g, b)
        self.update_color_display()
        brightness = int(self.brightness_scale.get_value())
        self.apply_color(r, g, b, brightness)
    
    def on_favorite_right_click(self, gesture, n_press, x, y, index):
        self.remove_favorite(index)
    
    def remove_favorite(self, index):
        favorites = self.config.get('favorites', [])
        if 0 <= index < len(favorites):
            removed = favorites.pop(index)
            self.save_config()
            self.rebuild_favorites_ui()
            self.show_toast(f"Removed RGB({removed['r']}, {removed['g']}, {removed['b']})")


class ControlusApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="io.github.controlus",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
    
    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = ControlusWindow(application=self)
        win.present()


def main():
    app = ControlusApp()
    app.run(None)


if __name__ == "__main__":
    main()

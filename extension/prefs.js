/**
 * Controlus RGB - Preferences
 * Settings dialog for the extension
 */

import Gio from 'gi://Gio';
import Gtk from 'gi://Gtk';
import Adw from 'gi://Adw';
import Gdk from 'gi://Gdk';
import GLib from 'gi://GLib';

import { ExtensionPreferences, gettext as _ } from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class ControlusPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();

        // Main page
        const page = new Adw.PreferencesPage({
            title: _('General'),
            icon_name: 'preferences-system-symbolic',
        });
        window.add(page);

        // General settings group
        const generalGroup = new Adw.PreferencesGroup({
            title: _('General Settings'),
            description: _('Configure keyboard RGB behavior'),
        });
        page.add(generalGroup);

        // Show indicator toggle
        const indicatorRow = new Adw.SwitchRow({
            title: _('Show Panel Indicator'),
            subtitle: _('Show icon in top panel when RGB is active'),
        });
        settings.bind('show-indicator', indicatorRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        generalGroup.add(indicatorRow);

        // Restore on login toggle
        const restoreRow = new Adw.SwitchRow({
            title: _('Restore on Login'),
            subtitle: _('Restore last color when you log in'),
        });
        settings.bind('restore-on-login', restoreRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        generalGroup.add(restoreRow);

        // Color settings group
        const colorGroup = new Adw.PreferencesGroup({
            title: _('Custom Color'),
            description: _('Set a custom RGB color'),
        });
        page.add(colorGroup);

        // Color chooser row
        const colorRow = new Adw.ActionRow({
            title: _('Custom Color'),
            subtitle: _('Click to choose a color'),
        });

        // Color preview button
        const colorButton = new Gtk.ColorDialogButton({
            valign: Gtk.Align.CENTER,
        });

        // Set initial color from settings
        const r = settings.get_int('custom-color-r');
        const g = settings.get_int('custom-color-g');
        const b = settings.get_int('custom-color-b');
        const rgba = new Gdk.RGBA();
        rgba.red = r / 255;
        rgba.green = g / 255;
        rgba.blue = b / 255;
        rgba.alpha = 1.0;
        colorButton.set_rgba(rgba);

        const colorDialog = new Gtk.ColorDialog({
            title: _('Choose Custom Color'),
            with_alpha: false,
        });
        colorButton.set_dialog(colorDialog);

        colorButton.connect('notify::rgba', () => {
            const newRgba = colorButton.get_rgba();
            settings.set_int('custom-color-r', Math.round(newRgba.red * 255));
            settings.set_int('custom-color-g', Math.round(newRgba.green * 255));
            settings.set_int('custom-color-b', Math.round(newRgba.blue * 255));
        });

        colorRow.add_suffix(colorButton);
        colorRow.set_activatable_widget(colorButton);
        colorGroup.add(colorRow);

        // Apply custom color button
        const applyColorRow = new Adw.ActionRow({
            title: _('Apply Custom Color'),
            subtitle: _('Set the keyboard to your custom color'),
        });

        const applyButton = new Gtk.Button({
            label: _('Apply'),
            valign: Gtk.Align.CENTER,
            css_classes: ['suggested-action'],
        });

        applyButton.connect('clicked', () => {
            this._applyCustomColor(settings);
        });

        applyColorRow.add_suffix(applyButton);
        colorGroup.add(applyColorRow);

        // Brightness group
        const brightnessGroup = new Adw.PreferencesGroup({
            title: _('Brightness'),
        });
        page.add(brightnessGroup);

        // Brightness slider
        const brightnessRow = new Adw.ActionRow({
            title: _('Brightness'),
            subtitle: `${settings.get_int('brightness')}%`,
        });

        const brightnessScale = new Gtk.Scale({
            orientation: Gtk.Orientation.HORIZONTAL,
            adjustment: new Gtk.Adjustment({
                lower: 0,
                upper: 100,
                step_increment: 1,
                page_increment: 10,
                value: settings.get_int('brightness'),
            }),
            draw_value: true,
            digits: 0,
            hexpand: true,
            valign: Gtk.Align.CENTER,
            width_request: 200,
        });

        brightnessScale.connect('value-changed', () => {
            const value = Math.round(brightnessScale.get_value());
            settings.set_int('brightness', value);
            brightnessRow.set_subtitle(`${value}%`);
        });

        settings.connect('changed::brightness', () => {
            brightnessScale.set_value(settings.get_int('brightness'));
        });

        brightnessRow.add_suffix(brightnessScale);
        brightnessGroup.add(brightnessRow);

        // Favorites page
        const favoritesPage = new Adw.PreferencesPage({
            title: _('Favorites'),
            icon_name: 'starred-symbolic',
        });
        window.add(favoritesPage);

        // Favorites group
        const favoritesGroup = new Adw.PreferencesGroup({
            title: _('Saved Colors'),
            description: _('Your favorite colors'),
        });
        favoritesPage.add(favoritesGroup);

        // Load favorites
        this._rebuildFavorites(favoritesGroup, settings);

        // Add favorite button
        const addFavoriteRow = new Adw.ActionRow({
            title: _('Add Current Color'),
            subtitle: _('Save the current color to favorites'),
        });

        const addButton = new Gtk.Button({
            icon_name: 'list-add-symbolic',
            valign: Gtk.Align.CENTER,
            css_classes: ['flat'],
        });

        addButton.connect('clicked', () => {
            this._addCurrentToFavorites(settings, favoritesGroup);
        });

        addFavoriteRow.add_suffix(addButton);
        addFavoriteRow.set_activatable_widget(addButton);
        favoritesGroup.add(addFavoriteRow);

        // About page
        const aboutPage = new Adw.PreferencesPage({
            title: _('About'),
            icon_name: 'help-about-symbolic',
        });
        window.add(aboutPage);

        const aboutGroup = new Adw.PreferencesGroup();
        aboutPage.add(aboutGroup);

        const aboutRow = new Adw.ActionRow({
            title: _('Controlus RGB'),
            subtitle: _('Version 1.0\nKeyboard RGB control for AORUS/Gigabyte laptops'),
        });

        const logo = new Gtk.Image({
            icon_name: 'preferences-color-symbolic',
            pixel_size: 64,
            margin_end: 12,
        });
        aboutRow.add_prefix(logo);
        aboutGroup.add(aboutRow);

        const linksGroup = new Adw.PreferencesGroup({
            title: _('Links'),
        });
        aboutPage.add(linksGroup);

        const githubRow = new Adw.ActionRow({
            title: _('GitHub Repository'),
            subtitle: _('Report issues and contribute'),
            activatable: true,
        });
        githubRow.add_suffix(new Gtk.Image({ icon_name: 'go-next-symbolic' }));
        githubRow.connect('activated', () => {
            Gio.AppInfo.launch_default_for_uri('https://github.com/controlus/gnome-extension', null);
        });
        linksGroup.add(githubRow);
    }

    _applyCustomColor(settings) {
        const r = settings.get_int('custom-color-r');
        const g = settings.get_int('custom-color-g');
        const b = settings.get_int('custom-color-b');
        const brightness = settings.get_int('brightness');

        // Save as last color
        settings.set_int('last-color-r', r);
        settings.set_int('last-color-g', g);
        settings.set_int('last-color-b', b);
        settings.set_boolean('enabled', true);

        // Call helper to apply
        const helperPath = GLib.build_filenamev([this.path, 'controlus-helper.py']);
        
        try {
            GLib.spawn_command_line_async(
                `pkexec ${helperPath} set-color ${r} ${g} ${b} ${brightness}`
            );
        } catch (e) {
            log(`[Controlus] Failed to apply color: ${e.message}`);
        }
    }

    _rebuildFavorites(group, settings) {
        const favoritesStr = settings.get_string('favorites');
        let favorites = [];
        
        try {
            favorites = JSON.parse(favoritesStr);
        } catch (e) {
            favorites = [];
        }

        favorites.forEach((fav, index) => {
            const row = this._createFavoriteRow(fav, index, settings, group);
            group.add(row);
        });

        if (favorites.length === 0) {
            const emptyRow = new Adw.ActionRow({
                title: _('No favorites yet'),
                subtitle: _('Add colors from the Quick Settings menu'),
            });
            group.add(emptyRow);
        }
    }

    _createFavoriteRow(fav, index, settings, group) {
        const row = new Adw.ActionRow({
            title: fav.name || `RGB(${fav.r}, ${fav.g}, ${fav.b})`,
            subtitle: `R: ${fav.r}, G: ${fav.g}, B: ${fav.b}`,
        });

        // Color preview
        const colorPreview = new Gtk.DrawingArea({
            width_request: 32,
            height_request: 32,
            valign: Gtk.Align.CENTER,
        });

        colorPreview.set_draw_func((area, cr, width, height) => {
            cr.setSourceRGB(fav.r / 255, fav.g / 255, fav.b / 255);
            cr.arc(width / 2, height / 2, Math.min(width, height) / 2 - 2, 0, 2 * Math.PI);
            cr.fill();
        });

        row.add_prefix(colorPreview);

        // Apply button
        const applyBtn = new Gtk.Button({
            icon_name: 'media-playback-start-symbolic',
            valign: Gtk.Align.CENTER,
            css_classes: ['flat'],
            tooltip_text: _('Apply this color'),
        });

        applyBtn.connect('clicked', () => {
            settings.set_int('last-color-r', fav.r);
            settings.set_int('last-color-g', fav.g);
            settings.set_int('last-color-b', fav.b);
            settings.set_boolean('enabled', true);

            const brightness = settings.get_int('brightness');
            const helperPath = GLib.build_filenamev([this.path, 'controlus-helper.py']);
            
            try {
                GLib.spawn_command_line_async(
                    `pkexec ${helperPath} set-color ${fav.r} ${fav.g} ${fav.b} ${brightness}`
                );
            } catch (e) {
                log(`[Controlus] Failed to apply favorite: ${e.message}`);
            }
        });

        row.add_suffix(applyBtn);

        // Delete button
        const deleteBtn = new Gtk.Button({
            icon_name: 'user-trash-symbolic',
            valign: Gtk.Align.CENTER,
            css_classes: ['flat', 'destructive-action'],
            tooltip_text: _('Remove from favorites'),
        });

        deleteBtn.connect('clicked', () => {
            this._removeFavorite(index, settings, group);
        });

        row.add_suffix(deleteBtn);

        return row;
    }

    _addCurrentToFavorites(settings, group) {
        const r = settings.get_int('last-color-r');
        const g = settings.get_int('last-color-g');
        const b = settings.get_int('last-color-b');

        if (!r && !g && !b) {
            return;
        }

        const favoritesStr = settings.get_string('favorites');
        let favorites = [];
        
        try {
            favorites = JSON.parse(favoritesStr);
        } catch (e) {
            favorites = [];
        }

        // Check if already exists
        const exists = favorites.some(f => f.r === r && f.g === g && f.b === b);
        if (exists) {
            return;
        }

        favorites.push({ r, g, b });
        settings.set_string('favorites', JSON.stringify(favorites));

        // Rebuild UI - clear and rebuild
        let child = group.get_first_child();
        while (child) {
            const next = child.get_next_sibling();
            if (child instanceof Adw.ActionRow) {
                group.remove(child);
            }
            child = next;
        }

        this._rebuildFavorites(group, settings);
    }

    _removeFavorite(index, settings, group) {
        const favoritesStr = settings.get_string('favorites');
        let favorites = [];
        
        try {
            favorites = JSON.parse(favoritesStr);
        } catch (e) {
            favorites = [];
        }

        if (index >= 0 && index < favorites.length) {
            favorites.splice(index, 1);
            settings.set_string('favorites', JSON.stringify(favorites));

            // Rebuild UI
            let child = group.get_first_child();
            while (child) {
                const next = child.get_next_sibling();
                if (child instanceof Adw.ActionRow) {
                    group.remove(child);
                }
                child = next;
            }

            this._rebuildFavorites(group, settings);
        }
    }
}

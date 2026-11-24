import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

function quotaRing(letter, rgb) {
    const area = new St.DrawingArea({style_class: 'brimdeck-ring',
        y_align: Clutter.ActorAlign.CENTER});
    area.percent = -1;
    area.connect('repaint', () => {
        const cr = area.get_context();
        const [width, height] = area.get_surface_size();
        const size = Math.min(width, height);
        cr.translate(width / 2, height / 2);
        cr.scale(size / 20, size / 20);
        cr.setLineWidth(2.2);
        cr.setSourceRGBA(1, 1, 1, 0.22);
        cr.arc(0, 0, 8.4, 0, 2 * Math.PI);
        cr.stroke();
        if (area.percent > 0) {
            cr.setSourceRGB(...rgb);
            cr.arc(0, 0, 8.4, -Math.PI / 2, -Math.PI / 2 + area.percent * Math.PI / 50);
            cr.stroke();
        }
        cr.$dispose();
    });
    const label = new St.Label({text: letter, style_class: 'brimdeck-letter',
        x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER});
    label.set_style(`color: rgb(${rgb.map(x => Math.round(x * 255)).join(',')})`);
    const stack = new St.Widget({layout_manager: new Clutter.BinLayout(),
        y_align: Clutter.ActorAlign.CENTER});
    stack.add_child(area);
    stack.add_child(label);
    return {stack, area};
}

export default class BrimDeckExtension extends Extension {
    enable() {
        this._button = new PanelMenu.Button(0, 'BrimDeck', true);
        const row = new St.BoxLayout({style_class: 'brimdeck-row',
            y_align: Clutter.ActorAlign.CENTER});
        this._rings = {claude: quotaRing('C', [0.90, 0.64, 0.52]),
            codex: quotaRing('X', [0.61, 0.73, 1])};
        for (const ring of Object.values(this._rings))
            row.add_child(ring.stack);
        this._cpu = new St.Label({style_class: 'brimdeck-cpu',
            y_align: Clutter.ActorAlign.CENTER});
        row.add_child(this._cpu);
        this._button.add_child(row);
        this._button.track_hover = true;
        this._button.connect('notify::hover', () => this._sendHover());
        this._button.connect('button-press-event', (_button, event) => {
            const button = event.get_button();
            if (button === 1 || button === 3)
                this._actions?.activate_action(button === 1 ? 'panel' : 'settings', null);
            return Clutter.EVENT_STOP;
        });
        this._button.connect('key-press-event', (_button, event) => {
            if (![Clutter.KEY_space, Clutter.KEY_Return, Clutter.KEY_KP_Enter].includes(event.get_key_symbol()))
                return Clutter.EVENT_PROPAGATE;
            this._actions?.activate_action('panel', null);
            return Clutter.EVENT_STOP;
        });
        Main.panel.addToStatusArea('brimdeck', this._button, 0, 'center');
        this._button.container.hide();
        this._watch = Gio.bus_watch_name(Gio.BusType.SESSION, 'io.github.brimdeck.Linux',
            Gio.BusNameWatcherFlags.NONE, (connection, _name, owner) => {
                this._clearActions();
                this._actions = Gio.DBusActionGroup.get(connection, owner, '/io/github/brimdeck/Linux');
                this._ids = ['action-added::panel', 'action-removed::panel', 'action-state-changed::panel']
                    .map(signal => this._actions.connect(signal, () => this._refresh()));
                this._ids.push(this._actions.connect('action-added::panel-hover', () => this._sendHover()));
                this._refresh();
            }, () => this._clearActions());
        this._host = Gio.bus_own_name(Gio.BusType.SESSION, 'io.github.brimdeck.PanelHost',
            Gio.BusNameOwnerFlags.NONE, null, null, null);
    }

    _sendHover() {
        if (this._actions?.has_action('panel-hover'))
            this._actions.activate_action('panel-hover', new GLib.Variant('b', this._button.hover));
    }

    _refresh() {
        const state = this._actions.get_action_state('panel')?.deepUnpack();
        this._button.container.visible = Boolean(state?.[0]);
        if (!state?.[0])
            return;
        const [, cpu, quotas] = state;
        for (const [id, {stack, area}] of Object.entries(this._rings)) {
            stack.visible = id in quotas;
            if (area.percent !== quotas[id]) {
                area.percent = quotas[id];
                area.queue_repaint();
            }
        }
        this._cpu.text = cpu >= 0 ? `${cpu}%` : '–%';
    }

    _clearActions() {
        for (const id of this._ids ?? [])
            this._actions.disconnect(id);
        this._ids = [];
        this._actions = null;
        this._button.container.hide();
    }

    disable() {
        Gio.bus_unwatch_name(this._watch);
        Gio.bus_unown_name(this._host);
        this._clearActions();
        this._button.destroy();
        this._button = this._rings = this._cpu = null;
    }
}

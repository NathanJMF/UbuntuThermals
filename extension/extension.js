import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const APP = 'io.local.MacBookThermals';
const PATH = '/io/local/MacBookThermals/Monitor';
const ROLE = 'ubuntu-macbook-thermals';

function column(css, params = {}) {
    return new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, style_class: css, ...params});
}
function label(text, css) {
    return new St.Label({text, style_class: css, y_align: Clutter.ActorAlign.CENTER});
}

const cpuMetric = {title:'CPU package', history:'CPU temperature history', css:'mbth-cpu', color:[1,.31,.36], index:1};
const drawMetric = {title:'System draw', history:'System power draw history', css:'mbth-draw', color:[1,.58,.47], index:4};
const fanMetric = {title:'Fan speed', history:'Fan speed history', css:'mbth-fan', color:[.98,.44,.50], index:3};

function paint(area, state, metric) {
    const history = state?.history ?? [];
    const temperature = metric === cpuMetric;
    const power = metric === drawMetric;
    const percent = metric === fanMetric && state?.fan_unit === 'percent';
    const cr = area.get_context();
    try {
        const [w, h] = area.get_surface_size();
        const left = 3, right = w - 32, top = 8, bottom = h - 7;
        if (right <= left || bottom <= top) return;
        const low = temperature ? 20 : 0;
        const maxRpm = state?.fan_max;
        const hasMax = Number.isFinite(maxRpm) && maxRpm > 0;
        const high = temperature ? 100 : power ? (state?.watts_max ?? 40) : hasMax ? maxRpm : Math.max(7500, ...history.map(row => row[3] ?? 0));
        const y = v => bottom - (Math.min(high, Math.max(low, v)) - low) / (high - low) * (bottom - top);
        cr.setFontSize(9);
        const ticks = temperature ? [[100, '100°'], [60, '60°'], [20, '20°']] :
            [1, .5, 0].map(fraction => [fraction * high, percent ? (fraction * 100)+'%' : Math.round(fraction * high) + (power ? ' W' : '')]);
        for (const [value, name] of ticks) {
            cr.setSourceRGBA(.75,.53,.58,.26);
            cr.setLineWidth(.7);
            cr.moveTo(left, y(value)); cr.lineTo(right, y(value)); cr.stroke();
            cr.setSourceRGB(.76,.69,.71);
            cr.moveTo(right + 5, y(value) + 3); cr.showText(name);
        }
        for (let i = 0; i <= 4; i++) {
            const x = left + i * (right-left)/4;
            cr.setSourceRGBA(.65,.51,.54,.13);
            cr.moveTo(x,top); cr.lineTo(x,bottom); cr.stroke();
        }
        if (!history.length || (!temperature && percent && !hasMax)) return;
        const now = history.at(-1)[0];
        const index = metric.index;
        cr.setLineWidth(1.9);
        cr.setSourceRGB(...metric.color);
        let previous = null;
        let end = null;
        for (const row of history) {
            if (row[0] < now - 120 || !Number.isFinite(row[index])) {
                previous = null; end = null; continue;
            }
            const x = right - (now-row[0])/120 * (right-left);
            const py = y(row[index]);
            if (previous === null || row[0] - previous > 5) cr.moveTo(x, py);
            else cr.lineTo(x, py);
            previous = row[0];
            end = [x, py];
        }
        cr.stroke();
        if (end) {
            cr.arc(...end, 2.6, 0, Math.PI*2);
            cr.fill();
        }
    } finally {
        cr.$dispose();
    }
}

export default class ThermalPanel extends Extension {
    enable() {
        this.generation = (this.generation ?? 0) + 1;
        this.bus = Gio.DBus.session;
        this.watch = Gio.bus_watch_name_on_connection(this.bus, APP, Gio.BusNameWatcherFlags.NONE,
            (_bus, _name, owner) => this.attach(owner), () => this.clearPeer());
    }

    async call(method, params = null, resultType = null) {
        return this.bus.call(this.owner, PATH, APP, method, params, resultType,
            Gio.DBusCallFlags.NO_AUTO_START, 3000, null);
    }

    async attach(owner) {
        this.clearPeer();
        this.owner = owner;
        const generation = this.generation;
        this.signalId = this.bus.signal_subscribe(owner, APP, null, PATH, null,
            Gio.DBusSignalFlags.NONE, (_bus, _sender, _path, _iface, name, params) => {
                if (!this.button) return;
                if (name === 'Changed') this.update(JSON.parse(params.get_child_value(0).get_string()[0]));
                if (name === 'Present' && this.button.mapped && this.button.has_allocation()) this.button.menu.open();
            });
        try {
            const result = await this.call('Attach', null, new GLib.VariantType('(s)'));
            if (!this.watch || generation !== this.generation || this.owner !== owner) return;
            const state = JSON.parse(result.get_child_value(0).get_string()[0]);
            this.build(state);
            this.update(state);
        } catch (error) {
            if (generation === this.generation && this.watch && this.owner === owner) {
                console.error('MacBook Thermals panel attachment failed', error);
                this.call('Detach').catch(() => {});
                this.clearPeer();
            }
        }
    }

    clearPeer() {
        if (this.signalId) { this.bus.signal_unsubscribe(this.signalId); this.signalId = 0; }
        this.owner = null;
        if (this.button) {
            this.button.menu.close(0);
            const actor = this.button.menu.actor;
            actor.get_parent()?.remove_child(actor);
            this.button.destroy();
        }
        this.button = null;
        this.history = [];
        this.openSelector = null;
    }

    disable() {
        const owner = this.owner;
        if (this.watch) Gio.bus_unwatch_name(this.watch);
        this.watch = 0;
        this.generation++;
        if (owner && this.button) {
            const method = Main.sessionMode.isLocked ? 'PauseView' : 'Detach';
            this.call(method).catch(() => {});
        }
        this.clearPeer();
    }

    build(state) {
        this.button = new PanelMenu.Button(.5, 'MacBook thermals');
        this.button.add_child(new St.Icon({gicon: new Gio.FileIcon({file:this.dir.get_child('thermometer.png')}),
            icon_size: 20, style_class:'mbth-icon'}));
        const outer = new PopupMenu.PopupBaseMenuItem({reactive:false,can_focus:false,style_class:'mbth-outer'});
        const frame = column('mbth-frame');
        outer.add_child(frame);
        this.button.menu.addMenuItem(outer);
        const selectorGroup = column('mbth-selector-group');
        frame.add_child(selectorGroup);
        const bar = new St.BoxLayout({style_class:'mbth-selectors'});
        selectorGroup.add_child(bar);
        const stack = new St.Widget({layout_manager:new Clutter.BinLayout(), x_expand:true});
        selectorGroup.add_child(stack);
        stack.add_child(this.card(cpuMetric));
        this.powerMenu = this.selector(bar,stack,'Power profile',state.choices.map(([key])=>key));
        this.fanMenu = this.selector(bar,stack,'Fan profile',state.fan_choices.map(([key])=>key));
        frame.add_child(this.card(drawMetric));
        frame.add_child(this.card(fanMetric));
        this.footerLabel = label('', 'mbth-error');
        this.footerLabel.visible = false;
        frame.add_child(this.footerLabel);
        this.button.menu.connect('open-state-changed', (_menu, open) => {
            this.setExpanded(null);
            if (open) this.redraw();
        });
        this.button.menu.actor.connect('captured-event', (_actor,event) => {
            if (event.type() === Clutter.EventType.KEY_PRESS && event.get_key_symbol()===Clutter.KEY_Escape && this.openSelector) {
                this.openSelector.button.grab_key_focus();
                this.setExpanded(null);
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        Main.panel.addToStatusArea(ROLE,this.button,0,'right');
    }

    card(metric) {
        const card = column('mbth-card',{x_expand:true, x_align:Clutter.ActorAlign.FILL});
        const meter = column('mbth-readings');
        meter.add_child(label(metric.title,'mbth-metric'));
        const reading = label('—', 'mbth-value ' + metric.css);
        meter.add_child(reading);
        card.add_child(meter);
        const graph = new St.DrawingArea({style_class:'mbth-graph', x_expand:true,
            accessible_name:metric.history});
        graph.connect('repaint', area => paint(area,this.state,metric));
        if (metric === fanMetric) {
            this.fan = reading;
            this.fgraph = graph;
            this.fanButton = new St.Button({child:graph, x_expand:true,
                reactive:true, can_focus:true, style_class:'mbth-fan-button'});
            this.fanButton.connect('clicked', () => this.call('ToggleFanUnit').catch(e => this.showFooter(e.message)));
            card.add_child(this.fanButton);
        } else {
            if (metric === cpuMetric) {
                this.cpu = reading;
                this.tgraph = graph;
            } else {
                this.draw = reading;
                this.wgraph = graph;
            }
            card.add_child(graph);
        }
        return card;
    }

    selector(bar,stack,name,keys) {
        const button = new St.Button({style_class:'mbth-selector', reactive:true,
            can_focus:true, x_expand:true, accessible_name:name});
        bar.add_child(button);
        const list = column('mbth-options',{visible:false,reactive:true,
            y_expand:true,y_align:Clutter.ActorAlign.START,x_expand:true,x_align:Clutter.ActorAlign.FILL});
        stack.add_child(list);
        const menu = {button,list,options:new Map()};
        button.connect('clicked', () => this.setExpanded(this.openSelector === menu ? null : menu));
        for (const key of keys) {
            const item = new St.Button({reactive:true,can_focus:true,style_class:'mbth-option'});
            item.connect('clicked', () => {
                this.setExpanded(null);
                if (!this.state?.busy && key !== this.state?.key && key !== this.state?.fan_key)
                    this.call('Select',new GLib.Variant('(s)',[key])).catch(e=>this.showFooter(e.message));
            });
            list.add_child(item);
            menu.options.set(key,item);
        }
        return menu;
    }

    setExpanded(active) {
        this.openSelector = active;
        for (const [menu,name] of [[this.powerMenu,this.state?.name],[this.fanMenu,this.state?.fan_name]]) {
            menu.list.visible = active === menu;
            if (name) menu.button.label = name+(active===menu?'   ▴':'   ▾');
        }
    }

    showFooter(message) {
        this.footerLabel.text = message || '';
        this.footerLabel.visible = Boolean(message);
    }

    redraw() {
        this.tgraph.queue_repaint();
        this.wgraph.queue_repaint();
        this.fgraph.queue_repaint();
    }

    update(s) {
        this.state = s;
        this.history = s.history;
        const latest = this.history.at(-1) ?? [];
        this.cpu.text = Number.isFinite(latest[1]) ? Math.round(latest[1])+' °C' : '—';
        this.draw.text = Number.isFinite(latest[4]) ? latest[4].toFixed(1)+' W' : '—';
        const rpm = latest[3];
        const percent = s.fan_unit === 'percent';
        const validScale = Number.isFinite(s.fan_max) && s.fan_max > 0;
        let fan = '—';
        if (Number.isFinite(rpm)) {
            if (!percent) fan = Math.round(rpm).toLocaleString()+' rpm';
            else if (validScale) fan = Math.round(Math.max(0,Math.min(100,100*rpm/s.fan_max)))+'%';
        }
        this.fan.text = fan;
        this.fanButton.accessible_name = percent ? 'Show fan speed in RPM' : 'Show fan speed as a percentage';
        this.powerMenu.button.reactive = this.fanMenu.button.reactive = !s.busy;
        this.setExpanded(s.busy ? null : this.openSelector);
        for (const [key,name,limits] of s.choices) {
            const item = this.powerMenu.options.get(key);
            if (item) item.label = (s.key===key?'✓   ':'     ')+name+(limits?'     '+limits:'');
        }
        for (const [key,name] of s.fan_choices ?? []) {
            const item = this.fanMenu.options.get(key);
            if (item) item.label = (s.fan_key===key?'✓   ':'     ')+name;
        }
        this.showFooter(s.message);
        if (this.button.menu.isOpen) this.redraw();
    }
}

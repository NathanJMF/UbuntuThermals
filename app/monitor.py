#!/usr/bin/python3 -E
"""Session thermal monitor with GNOME panel and system tray views."""
from collections import deque
import json
import math
import sys

from gi.repository import Gio, GLib
from tray import Tray
from monitors import Sensors, PRESETS, FAN_PRESETS, power_state, selected_power, fan_state, controls_available

ID = 'io.local.MacBookThermals'
UUID = 'macbook-thermals@ubuntu-thermals.local'
PATH = '/io/local/MacBookThermals/Monitor'
WINDOW = 120
XML = f"""<node><interface name="{ID}">
  <method name="Attach"><arg type="s" direction="out"/></method>
  <method name="Select"><arg type="s" direction="in"/></method>
  <method name="ToggleFanUnit"/>
  <method name="PauseView"/>
  <method name="Detach"/>
  <method name="Stop"/>
  <signal name="Changed"><arg type="s"/></signal>
  <signal name="Present"/>
</interface></node>"""


class Monitor(Gio.Application):
    def __init__(self):
        super().__init__(application_id=ID)
        self.history = deque(maxlen=62)
        self.sensors = Sensors()
        self.fan_unit = 'percent'
        self.peer = None
        self.panel_owner = None
        self.pending = None
        self.error = ''
        self.tray = None
        self.fallback = 0
        self.extension_request = None

    def do_dbus_register(self, connection, object_path):
        if not Gio.Application.do_dbus_register(self, connection, object_path):
            return False
        self.bus = connection
        self.info = Gio.DBusNodeInfo.new_for_xml(XML)
        self.registration = connection.register_object(PATH, self.info.interfaces[0], self.method, None, None)
        return True

    def do_dbus_unregister(self, connection, object_path):
        if getattr(self, 'registration', 0):
            connection.unregister_object(self.registration)
            self.registration = 0
        Gio.Application.do_dbus_unregister(self, connection, object_path)

    def do_startup(self):
        Gio.Application.do_startup(self)
        self.hold()
        quit_action = Gio.SimpleAction.new('quit', None)
        quit_action.connect('activate', lambda *_: self.quit())
        self.add_action(quit_action)
        self.watch = Gio.bus_watch_name_on_connection(self.bus, 'org.gnome.Shell',
            Gio.BusNameWatcherFlags.NONE, self.shell_online, self.shell_offline)
        self.tick()
        self.timer = GLib.timeout_add_seconds(2, self.tick)

    def shell_online(self, _bus, _name, owner):
        self.panel_owner = owner
        self.ensure_view()

    def shell_offline(self, *_args):
        self.panel_owner = None
        self.peer = None

    def do_activate(self):
        if self.peer:
            self.bus.emit_signal(self.peer, PATH, ID, 'Present', None)
        else:
            self.ensure_view()

    def ensure_view(self):
        if self.peer or not self.panel_owner:
            return
        # The standard tray can host the menu before the first new login.
        if not self.extension_request:
            settings = Gio.Settings.new('org.gnome.shell')
            if not settings.get_boolean('disable-user-extensions'):
                self.extension_request = Gio.Subprocess.new(
                    ['/usr/bin/gnome-extensions', 'enable', UUID],
                    Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
                self.extension_request.wait_async(None, self.extension_done)
        self.schedule_tray()

    def schedule_tray(self):
        if not self.fallback:
            self.fallback = GLib.timeout_add(650, self.open_tray)

    def extension_done(self, proc, result):
        try:
            proc.wait_finish(result)
        finally:
            self.extension_request = None

    def open_tray(self):
        self.fallback = 0
        if not self.peer:
            if not self.tray:
                self.tray = Tray(self)
            self.tray.show(True)
        return GLib.SOURCE_REMOVE

    def snapshot(self):
        inferred, pair = power_state()
        selected, target = selected_power()
        key = selected or inferred
        name = PRESETS[key][0] if key else ('Custom' if None not in pair else 'Unavailable')
        message = self.error
        drifted = selected is not None and selected != 'restore' and target != pair
        if not message and drifted:
            if None in pair:
                message = 'CPU limits unavailable.'
            else:
                message = f'Live limits: {pair[0]:g} / {pair[1]:g} W. Select {name} to reapply.'

        fan_key = fan_state()
        peak_watts = max((row[4] for row in self.history if row[4] is not None), default=0)
        watts = self.sensors.system_watts
        draw = f'{watts:.1f} W' if watts is not None else '—'
        footer = f'System draw    {draw}'
        if message:
            footer += '\n' + message
        # Cached panels use key to disable re-selecting the active choice.
        return dict(key=None if drifted else key, name=name, fan_key=fan_key, fan_name=FAN_PRESETS.get(fan_key,'Fan unavailable'),
                    # A panel cached by the current GNOME login reads its footer from "error".
                    fan_choices=list(FAN_PRESETS.items()) if controls_available() else [], busy=self.pending is not None, footer=footer, error=footer,
                    message=message, watts_max=max(40, 20 * math.ceil(peak_watts / 20)),
                    history=list(self.history), fan_unit=self.fan_unit, fan_max=self.sensors.fan_max,
                    choices=[[k, n, f'{p[0]} / {p[1]} W' if p else ''] for k,(n,p) in PRESETS.items()] if controls_available() else [])

    def method(self, _conn, sender, _path, _iface, method, args, invocation):
        # An enabled panel can attach before our asynchronous name-watch callback.
        if self.panel_owner is None:
            try:
                owner = self.bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                    'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', ('org.gnome.Shell',)),
                    GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NO_AUTO_START, 2000, None)
                self.panel_owner = owner.unpack()[0]
            except GLib.Error:
                pass
        if sender != self.panel_owner:
            invocation.return_dbus_error(ID+'.WrongPeer', 'Only the GNOME panel may control this view.')
            return
        if method == 'Attach':
            self.peer = sender
            if self.tray:
                self.tray.show(False)
            invocation.return_value(GLib.Variant('(s)', (json.dumps(self.snapshot()),)))
        elif method == 'ToggleFanUnit':
            self.toggle_fan_unit()
            invocation.return_value(None)
        elif method == 'PauseView':
            self.peer = None
            if self.tray:
                self.tray.show(False)
            if self.fallback:
                GLib.source_remove(self.fallback)
                self.fallback = 0
            invocation.return_value(None)
        elif method in ('Detach', 'Stop'):
            # Older panel code sends Stop during an ordinary Shell theme reload.
            self.peer = None
            invocation.return_value(None)
            self.schedule_tray()
        elif method == 'Select':
            key, = args.unpack()
            if key not in PRESETS and key not in FAN_PRESETS:
                invocation.return_dbus_error(ID+'.UnknownProfile', 'Unknown profile')
                return
            self.select(key)
            invocation.return_value(None)

    def tick(self):
        sample = self.sensors.sample()
        self.history.append((*sample, self.sensors.system_watts))
        while sample[0] - self.history[0][0] > WINDOW:
            self.history.popleft()
        self.refresh()
        return GLib.SOURCE_CONTINUE

    def toggle_fan_unit(self):
        self.fan_unit = 'rpm' if self.fan_unit == 'percent' else 'percent'
        self.refresh()

    def select(self, key):
        if not controls_available() or self.pending or (key not in PRESETS and key not in FAN_PRESETS):
            return
        if key in FAN_PRESETS and key == fan_state():
            return
        self.error = ''
        try:
            self.pending = Gio.Subprocess.new(['/usr/bin/sudo','-n','--','/usr/local/bin/macbook-power',key],
                Gio.SubprocessFlags.STDERR_PIPE | Gio.SubprocessFlags.STDOUT_SILENCE)
            self.pending.communicate_utf8_async(None, None, self.finished)
        except GLib.Error as err:
            self.error = str(err)
        self.refresh()

    def finished(self, proc, result):
        try:
            _, _, err = proc.communicate_utf8_finish(result)
            if not proc.get_successful():
                self.error = ((err or '').strip() or 'Could not apply the profile.').splitlines()[-1][:150]
        except GLib.Error as err:
            self.error = str(err)
        finally:
            self.pending = None
            self.refresh()

    def refresh(self):
        if self.peer:
            payload = json.dumps(self.snapshot())
            self.bus.emit_signal(self.peer, PATH, ID, 'Changed', GLib.Variant('(s)',(payload,)))
        elif self.tray and self.tray.visible:
            self.tray.refresh(self.snapshot())

    def do_shutdown(self):
        GLib.source_remove(self.timer)
        if self.fallback:
            GLib.source_remove(self.fallback)
        if self.tray:
            self.tray.close()
        Gio.bus_unwatch_name(self.watch)
        Gio.Application.do_shutdown(self)


if __name__ == '__main__':
    sys.exit(Monitor().run(sys.argv))

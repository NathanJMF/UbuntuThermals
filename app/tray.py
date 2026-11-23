"""A menu-only StatusNotifierItem for logins where the Shell panel is unavailable."""
from pathlib import Path

import gi
gi.require_version('Dbusmenu', '0.4')
from gi.repository import Dbusmenu, Gio, GLib

WATCHER = 'org.kde.StatusNotifierWatcher'
ITEM = 'org.kde.StatusNotifierItem'
PATH = '/io/local/MacBookThermals/Tray'
MENU = PATH + '/Menu'
FIELDS = {'Category':'s', 'Id':'s', 'Title':'s', 'Status':'s', 'IconName':'s',
          'Menu':'o', 'ItemIsMenu':'b', 'WindowId':'i'}
XML = '<node><interface name="' + ITEM + '">' + ''.join(
    f'<property name="{k}" type="{v}" access="read"/>' for k,v in FIELDS.items()
) + '<signal name="NewStatus"><arg type="s"/></signal></interface></node>'


def trace(samples, column, low, high):
    """Draw two minutes in a 60-by-8 dot grid, keeping gaps across sleep."""
    cells = [[0] * 30 for _ in range(2)]
    dots = ((1, 2, 4, 64), (8, 16, 32, 128))
    if samples:
        now = samples[-1][0]
        previous = None
        for row in samples:
            x = 59 - int((now - row[0]) // 2)
            if x < 0 or row[column] is None:
                previous = None
                continue
            y = 7 - min(7, max(0, round(7 * (row[column] - low) / (high-low))))
            if previous and row[0] - previous[2] <= 5:
                px, py, _ = previous
                for step in range(px + 1, x):
                    height = round(py + (y-py) * (step-px) / (x-px))
                    cells[height//4][step//2] |= dots[step%2][height%4]
            cells[y//4][x//2] |= dots[x%2][y%4]
            previous = (x, y, row[0])
    return '\n'.join(''.join(chr(0x2800 | mask) for mask in line) for line in cells)


class Tray:
    def __init__(self, app):
        self.app = app
        self.visible = True
        self.ever_registered = False
        self.owner = None
        self.values = dict(Category='Hardware', Id='macbook-thermals', Title='MacBook Thermals',
                           IconName=str(Path(__file__).with_name('thermometer.png').resolve()),
                           Menu=MENU, ItemIsMenu=True, WindowId=0)
        self.root = Dbusmenu.Menuitem.new()
        state = app.snapshot()
        self.selector,self.options = self.selector_menu(state['name'],state['choices'])
        self.fan_selector,self.fan_options = self.selector_menu(state['fan_name'],state['fan_choices'])
        self.open_menus = set()
        self.temperatures = [self.add(self.root,'',False) for _ in range(2)]
        self.draw = self.add(self.root,'',False)
        self.draw_trace = self.add(self.root,'',False)
        self.fan = self.add(self.root,'',False)
        self.fan_trace = self.add(self.root,'')
        self.fan_trace.property_set('accessible-desc','Switch fan speed between percentage and RPM')
        self.fan_trace.connect('item-activated',lambda *_: app.toggle_fan_unit())
        self.footer = self.add(self.root,'',False)
        self.root.connect('event',self.menu_event)
        self.server = Dbusmenu.Server.new(MENU)
        self.server.set_root(self.root)
        self.info = Gio.DBusNodeInfo.new_for_xml(XML)
        self.registration = app.bus.register_object(PATH,self.info.interfaces[0],None,self.property,None)
        self.watch = Gio.bus_watch_name_on_connection(app.bus,WATCHER,Gio.BusNameWatcherFlags.NONE,
                                                      self.host_online,self.host_offline)
        self.refresh(app.snapshot())

    @staticmethod
    def add(parent, label, enabled=True):
        item = Dbusmenu.Menuitem.new()
        item.property_set('label',label)
        item.property_set_bool('enabled',enabled)
        parent.child_append(item)
        return item

    def selector_menu(self, name, choices):
        selector = self.add(self.root,name)
        selector.property_set('children-display','submenu')
        options = {}
        for key, label, *details in choices:
            text = '   '.join(part for part in [label,*details] if part)
            item = self.add(selector,text)
            item.property_set('toggle-type','radio')
            item.connect('item-activated',lambda _item,_time,k=key: self.app.select(k))
            options[key] = item
        selector.connect('event',self.menu_event)
        return selector,options

    def menu_event(self, item, event, _data, _time):
        if event not in ('closed','opened'):
            return False
        if item == self.root:
            if event == 'closed':
                self.open_menus.clear()
        elif event == 'opened':
            self.open_menus.add(item.get_id())
        else:
            self.open_menus.discard(item.get_id())
        # Make room for the fifth power option in standard tray hosts.
        self.fan_selector.property_set_bool('visible', self.selector.get_id() not in self.open_menus)
        for row in [*self.temperatures,self.draw,self.draw_trace,self.fan,self.fan_trace]:
            row.property_set_bool('visible',not self.open_menus)
        return False

    def property(self, _conn, _sender, _path, _iface, key):
        value = ('Active' if self.visible else 'Passive') if key=='Status' else self.values[key]
        return GLib.Variant(FIELDS[key],value)

    def host_online(self, conn, _name, owner):
        self.owner = owner
        conn.call(owner,'/StatusNotifierWatcher',WATCHER,'RegisterStatusNotifierItem',
                  GLib.Variant('(s)',(PATH,)),None,Gio.DBusCallFlags.NO_AUTO_START,4000,None,
                  self.registered,owner)

    def registered(self, conn, result, owner):
        try:
            conn.call_finish(result)
        except GLib.Error as error:
            if owner == self.owner:
                print(f'Tray registration failed: {error}',flush=True)
        else:
            if owner == self.owner:
                self.ever_registered = True

    def host_offline(self, *_args):
        self.owner = None

    def show(self, visible):
        if self.visible == visible:
            return
        self.visible = visible
        state = 'Active' if visible else 'Passive'
        self.app.bus.emit_signal(None,PATH,ITEM,'NewStatus',GLib.Variant('(s)',(state,)))
        self.app.bus.emit_signal(None,PATH,'org.freedesktop.DBus.Properties','PropertiesChanged',
                                 GLib.Variant('(sa{sv}as)',(ITEM,{'Status':GLib.Variant('s',state)},[])))

    def refresh(self, s):
        for selector,options,key,name in [
            (self.selector,self.options,s['key'],s['name']),
            (self.fan_selector,self.fan_options,s['fan_key'],s['fan_name']),
        ]:
            selector.property_set('label',name)
            selector.property_set_bool('enabled',not s['busy'])
            for choice,item in options.items():
                item.property_set_int('toggle-state',int(choice==key))
        samples=s['history']
        _,cpu,_,rpm,watts = samples[-1]
        cpu_reading = '—' if cpu is None else f'{cpu:.0f} °C'
        self.temperatures[0].property_set('label','CPU package    '+cpu_reading)
        self.temperatures[1].property_set('label',trace(samples,1,20,100))
        draw = '—' if watts is None else f'{watts:.1f} W'
        self.draw.property_set('label','System draw    '+draw)
        self.draw_trace.property_set('label',trace(samples,4,0,s['watts_max']))
        maximum = s['fan_max']
        valid_max = maximum is not None and maximum > 0
        percent = s['fan_unit'] == 'percent'
        reading = '—'
        if rpm is not None:
            if not percent:
                reading = f'{rpm:,.0f} rpm'
            elif valid_max:
                reading = f'{min(100,max(0,100*rpm/maximum)):.0f}%'
        self.fan.property_set('label','Fan speed    '+reading)
        scale = maximum if valid_max else max(7500, *(row[3] or 0 for row in samples))
        plot = trace([] if percent and not valid_max else samples,3,0,scale)
        self.fan_trace.property_set('label',plot)
        self.footer.property_set('label',s['message'])
        self.footer.property_set_bool('visible',bool(s['message']))

    def close(self):
        Gio.bus_unwatch_name(self.watch)
        self.app.bus.unregister_object(self.registration)

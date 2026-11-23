"""Privileged writes are replaced only inside this private compositor test."""
import os, sys
from pathlib import Path
from unittest.mock import patch
from gi.repository import Gio, GLib
base=Path(os.environ['THERMAL_TEST_SOURCE'])
sys.path.insert(0,str(base/'app'))
import monitor
testdir=Path(os.environ['THERMAL_TEST_DIR'])
real=Gio.Subprocess.new
chosen=None
fan_choice='fan-auto'
def create(args,flags):
    global chosen,fan_choice
    if args[:4]==['/usr/bin/sudo','-n','--','/usr/local/bin/macbook-power']:
        if args[4] in monitor.FAN_PRESETS:
            fan_choice=args[4]
            return real(['/usr/bin/true'],flags)
        assert args[4] in monitor.PRESETS
        chosen=args[4]
        (testdir/'profile-click').write_text(chosen)
        return real(['/usr/bin/true'],flags)
    if args[0] in ('/usr/bin/sudo','/usr/bin/pkexec','/usr/local/bin/macbook-power'):
        raise AssertionError('Unmocked hardware action in private shell')
    return real(args,flags)
import math
import time
class DemoSensors:
    fan_max = 7200
    def sample(self):
        t = time.clock_gettime(time.CLOCK_BOOTTIME)
        self.system_watts = 9 + 2 * math.sin(t / 5)
        return (t, 60 + 6 * math.sin(t / 7), None, round(2500 + 550 * math.cos(t / 9)))
def initial_history(app):
    now = time.clock_gettime(time.CLOCK_BOOTTIME)
    for i in range(60):
        t = now-120+2*i
        app.history.append((t, 60+6*math.sin(t/7), None, round(2500+550*math.cos(t/9)), 9+2*math.sin(t/5)))
actual_state=lambda: ('mild',(16,20))
def power():
    if chosen=='restore':
        return 'restore',(95,125)
    if chosen:
        return chosen,monitor.PRESETS[chosen][1]
    return actual_state()
with patch.object(monitor,'Sensors',DemoSensors), patch.object(monitor,'controls_available',return_value=True), patch.object(monitor,'selected_power',return_value=(None,None)), patch.object(Gio.Subprocess,'new',side_effect=create), patch.object(monitor,'power_state',side_effect=power), patch.object(monitor,'fan_state',side_effect=lambda:fan_choice):
    app = monitor.Monitor()
    initial_history(app)
    sys.exit(app.run(sys.argv))

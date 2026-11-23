"""Read MacBook thermal and fan sensors without changing hardware settings."""
from pathlib import Path
import time
import json

HWMON = Path('/sys/class/hwmon')
RAPL = Path('/sys/class/powercap/intel-rapl:0')
PRESETS = {
    'cool': ('Cool', (14, 18)),
    'mild': ('Mild', (16, 20)),
    'everyday': ('Everyday', (18, 22)),
    'fast': ('Fast', (22, 26)),
    'restore': ('Stock', None),
}

FAN_PRESETS = {'fan-auto': 'Stock', 'fan-smooth': 'Smooth', 'fan-smooth-plus': 'Smooth+', 'fan-early': 'Aggressive'}


def controls_available():
    try:
        return Path('/sys/class/dmi/id/product_name').read_text().strip() == 'MacBookPro15,4' and Path('/usr/local/bin/macbook-power').is_file()
    except OSError:
        return False


def fan_state():
    state = Path('/run/macbook-fan-profile')
    for mode in ('smooth', 'smooth-plus', 'early'):
        if (state/mode).is_file():
            return 'fan-' + mode
    try:
        if not (state/'override').exists() and Path('/sys/module/macsmc_hwmon/parameters/fan_control').read_text().strip() == 'N':
            return 'fan-auto'
    except OSError:
        pass
    return None


def number(path, divisor=1):
    try:
        return int(path.read_text()) / divisor
    except (OSError, ValueError):
        return None


def chip(name):
    for path in HWMON.glob('hwmon*'):
        try:
            if (path / 'name').read_text().strip() == name:
                return path
        except OSError:
            continue
    return None


class Sensors:
    def __init__(self):
        self.cpu = self.fan = self.fan_max = None
        self.system_watts = None
        self.refresh()

    def refresh(self):
        core = chip('coretemp')
        self.cpu = None
        if core:
            for label in core.glob('temp*_label'):
                try:
                    if label.read_text().strip().startswith('Package id'):
                        self.cpu = label.with_name(label.name.replace('_label', '_input'))
                        break
                except OSError:
                    continue
        smc = chip('macsmc_hwmon')
        self.fan = smc / 'fan1_input' if smc else None
        self.system_power = None
        if smc:
            for label in smc.glob('power*_label'):
                try:
                    if label.read_text().strip().endswith('(PSTR)'):
                        self.system_power = label.with_name(label.name.replace('_label', '_input'))
                        break
                except OSError:
                    continue

    def sample(self):
        cpu = number(self.cpu, 1000) if self.cpu else None
        fan = number(self.fan) if self.fan else None
        watts = number(self.system_power, 1_000_000) if self.system_power else None
        self.system_watts = watts if watts is not None and watts > 0 else None
        if cpu is None or fan is None or (self.system_power and watts is None):
            self.refresh()
        self.fan_max = number(self.fan.with_name('fan1_max')) if self.fan else None
        # Keep the old wire slot empty for panel code cached in an existing login.
        return (time.clock_gettime(time.CLOCK_BOOTTIME), cpu, None, fan)


def power_state():
    pair = tuple(number(RAPL / f'constraint_{i}_power_limit_uw', 1_000_000) for i in (0, 1))
    profile = next((key for key, (_, limits) in PRESETS.items() if limits == pair), None)
    if profile is None and None not in pair:
        try:
            raw = json.loads(Path('/run/macbook-stock-power.json').read_text())
            if list(pair) == [value/1_000_000 for value in raw]:
                profile = 'restore'
        except FileNotFoundError:
            # No preset has captured the machine's original limits this boot.
            profile = 'restore'
        except (OSError, TypeError, ValueError):
            pass
    return profile, pair

def selected_power():
    """Read the last confirmed request for this boot and its capped targets."""
    try:
        record = json.loads(Path('/run/macbook-selected-power.json').read_text())
        key, limits = record['profile'], record['limits_uw']
        if (isinstance(key, str) and key in PRESETS and isinstance(limits, list)
                and len(limits) == 2 and all(type(v) is int and v >= 0 for v in limits)):
            return key, tuple(v / 1_000_000 for v in limits)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None, None

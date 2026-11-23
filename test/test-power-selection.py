"""Selection, drift, failed writes and Stock, without touching hardware."""
from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
base = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(base / 'app'))
import monitor
import monitors

with TemporaryDirectory() as tmp:
    root = Path(tmp)
    stock = root/'stock'
    record = root/'selected'
    limits = [root/'constraint_0_power_limit_uw', root/'constraint_1_power_limit_uw']
    for path, value in zip(limits, (100000000, 125000000)):
        path.write_text(str(value))
    select = runpy.run_path(str(base/'hardware/macbook-power'))['select']
    settings = dict(STATE=root/'state', STOCK=stock, SELECTION=record, LIMITS=limits)
    view = SimpleNamespace(history=[], sensors=SimpleNamespace(system_watts=9.5, fan_max=7200),
                           fan_unit='percent', error='', pending=None)
    def mapped(path):
        return {'/run/macbook-selected-power.json': record, '/run/macbook-stock-power.json': stock}.get(str(path), Path(path))
    with patch.dict(select.__globals__, settings), patch.object(monitors, 'RAPL', root), patch.object(monitors, 'Path', side_effect=mapped):
        assert monitor.Monitor.snapshot(view)['name'] == 'Stock'
        select('fast')
        good = monitor.Monitor.snapshot(view)
        assert (good['key'], good['name'], good['message']) == ('fast', 'Fast', '')
        limits[0].write_text('100000000')
        drift = monitor.Monitor.snapshot(view)
        assert drift['name'] == 'Fast' and drift['key'] is None and '100 / 26 W' in drift['message']
        select('fast')
        assert monitor.Monitor.snapshot(view)['key'] == 'fast'
        with patch.dict(select.__globals__, {'publish_record': lambda path, value: (_ for _ in ()).throw(OSError()) if path == record else None}):
            try:
                select('mild')
            except OSError:
                pass
            else:
                raise AssertionError('Failed state write should fail the operation')
        assert monitor.Monitor.snapshot(view)['key'] == 'fast'
        select('restore')
        assert monitor.Monitor.snapshot(view)['name'] == 'Stock'
        record.unlink()
        assert monitor.Monitor.snapshot(view)['name'] == 'Stock'
        record.write_text('invalid')
        assert monitors.selected_power() == (None, None)
        limits[0].write_text('14000000')
        limits[1].write_text('18000000')
        assert monitor.Monitor.snapshot(view)['name'] == 'Cool'
print('PASS: applied profile, drift, reapply, failed write rollback, Stock and legacy reads')

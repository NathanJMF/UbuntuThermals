"""Exercise the root controller against a temporary hardware tree, without privileges."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from unittest.mock import patch

file=Path(__file__).resolve().parents[1]/'hardware/macbook-thermal-fan'
loader=importlib.machinery.SourceFileLoader('controller',str(file))
spec=importlib.util.spec_from_loader(loader.name,loader)
c=importlib.util.module_from_spec(spec)
loader.exec_module(c)

with TemporaryDirectory() as directory:
    base=Path(directory)
    c.ROOT=base/'run'
    c.ROOT.mkdir()
    c.OWNED=c.ROOT/'override'
    c.READY=c.ROOT/'early'
    c.GATE=base/'fan_control'
    c.HWMON=base/'hw'
    fan=c.HWMON/'hwmon1'
    fan.mkdir(parents=True)
    core=c.HWMON/'hwmon0'
    core.mkdir()
    for path,value in ((core/'name','coretemp'),(core/'temp1_label','Package id 0'),
                       (core/'temp1_input','60000'),(fan/'name','macsmc_hwmon'),
                       (fan/'fan1_min','1200'),(fan/'fan1_max','7200'),(fan/'fan1_input','1500'),
                       (fan/'fan1_target','1500'),(c.GATE,'N')):
        path.write_text(value)
    real_write=c.write
    log=[]
    def record(path,value):
        log.append((path.name,str(value)))
        real_write(path,value)
    class Steps:
        def __init__(self,steps):
            self.steps=iter(steps)
            self.stopped=False
        def is_set(self):
            return self.stopped
        def wait(self,_):
            step=next(self.steps,None)
            if step is None:
                self.stopped=True
            else:
                (core/'temp1_input').write_text(str(step))
    with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as notify:
        path=str(base/'notify')
        notify.bind(path)
        with patch.dict(os.environ,NOTIFY_SOCKET=path),patch.object(c,'write',side_effect=record):
            with patch.object(c,'Event',return_value=Steps([73000,69000,65000,64000])):
                c.run()
            assert notify.recv(16)==b'READY=1'
            sequence=[('fan_control','Y'),('fan1_target','7200'),('fan_control','Y'),
                      ('fan1_target','0'),('fan_control','N')]
            assert log==sequence and not c.READY.exists() and not c.OWNED.exists(),log
            log.clear()
            with patch.object(c,'Event',return_value=Steps([74000,-1])):
                try: c.run()
                except RuntimeError as error: assert 'telemetry' in str(error)
                else: raise AssertionError('Invalid sensor was accepted')
            assert log==sequence and c.GATE.read_text().strip()=='N'
            c.GATE.write_text('Y')
            log.clear()
            try: c.run()
            except RuntimeError as error: assert 'Another program' in str(error)
            else: raise AssertionError('Took an unowned fan')
            assert log==[] and c.GATE.read_text()=='Y'
            c.OWNED.touch()
            c.release()
            assert not c.OWNED.exists() and c.GATE.read_text().strip()=='N'
            for i,name in enumerate(('Synthetic Bottom Skin Temperature (Ts0S)',
                                     'Synthetic Top Skin Temperature (Ts1S)','Battery Temperature (TB0T)')):
                (fan/f'temp{i}_label').write_text(name)
                (fan/f'temp{i}_input').write_text('35000')
            (c.ROOT/'requested').write_text('smooth')
            def simulate(temperatures, initial_target=1400, hot_skin_at=None, fail_feedback=False, spinup_at=None):
                steps=iter(temperatures)
                now=[0.0]
                targets=[]
                (core/'temp1_input').write_text(str(next(steps)))
                (fan/'temp0_input').write_text('35000')
                (fan/'fan1_target').write_text(str(initial_target))
                def apply(path,value):
                    if path.name=='fan1_target':
                        targets.append((now[0],value))
                        if value and fail_feedback:
                            return
                    real_write(path,value)
                class Clock:
                    stopped=False
                    def is_set(self): return self.stopped
                    def wait(self,delay):
                        now[0]+=delay
                        if hot_skin_at is not None and now[0]>=hot_skin_at:
                            (fan/'temp0_input').write_text('51000')
                        if spinup_at is not None and now[0]>=spinup_at:
                            (fan/'fan1_input').write_text('1500')
                        temp=next(steps,None)
                        if temp is None: self.stopped=True
                        else: (core/'temp1_input').write_text(str(temp))
                with patch.object(c,'Event',return_value=Clock()), patch.object(c.time,'monotonic',side_effect=lambda:now[0]), patch.object(c,'write',side_effect=apply):
                    c.run()
                assert not c.OWNED.exists() and not (c.ROOT/(c.ROOT/'requested').read_text()).exists() and c.GATE.read_text().strip()=='N'
                return targets
            targets=simulate([60000]*3+[70000]*100+[80000]*100+[50000]*180)
            assert targets[-1][1]==0 and targets[-2][1]<=1320, targets[-3:]
            assert len({v for _,v in targets if 1800<v<5400})>20
            assert any(3340<=v<=3460 for t,v in targets if 10<t<50)
            assert any(5340<=v<=5460 for t,v in targets if 60<t<100)
            for (t0,v0),(t1,v1) in zip(targets,targets[1:]):
                if v1:
                    assert -120*(t1-t0)-1<=v1-v0<=350*(t1-t0)+1, (t0,v0,t1,v1)
            assert simulate([65000]*15,initial_target=5000)==[]  # Keep firmware's stronger request.
            urgent=simulate([60000]*3+[90000]*5)
            assert (1.5,7200) in urgent
            released=simulate([70000]*15,hot_skin_at=2)
            assert released[-1]==(2,0)
            (c.ROOT/'requested').write_text('smooth-plus')
            # A trace 3°C cooler must give exactly the same smooth progression.
            assert simulate([57000]*3+[67000]*100+[77000]*100+[47000]*180)==targets
            plateau=simulate([70000]*120)
            assert any(3940 <= v <= 4060 for _,v in plateau)
            assert (1.5,7200) in simulate([57000]*3+[84000]*5)
            assert simulate([70000]*15,hot_skin_at=2)[-1]==(2,0)
            (fan/'fan1_input').write_text('0')
            assert simulate([49000]*16,initial_target=0)==[]  # Firmware intentionally parked the fan.
            assert any(v>0 for _,v in simulate([70000]*20,initial_target=0,spinup_at=2))
            (fan/'fan1_input').write_text('0')
            try: simulate([70000]*20,initial_target=0)
            except RuntimeError as error: assert 'zero RPM' in str(error)
            else: raise AssertionError('A commanded but stationary fan was accepted')
            assert c.GATE.read_text().strip()=='N' and not c.OWNED.exists()
            assert int((fan/'fan1_target').read_text())==0
            (fan/'fan1_input').write_text('1500')
            try: simulate([70000]*15,fail_feedback=True)
            except RuntimeError as error: assert 'confirm' in str(error)
            else: raise AssertionError('Unconfirmed target was accepted')
            assert not c.OWNED.exists() and not (c.ROOT/'smooth').exists()
print('PASS: original threshold behavior; smooth intermediate targets, rate limits, emergency maximum, sensor/feedback cleanup and firmware handoff.')

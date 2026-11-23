"""Install the shared thermal helpers and authorize this desktop user's presets."""
import argparse
from base64 import b64decode, b64encode
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
STATE = Path('/var/lib/ubuntu-thermals')
RECORD = STATE/'files.json'
FILES = {
    '/usr/local/bin/macbook-power': ('macbook-power',0o755),
    '/usr/local/libexec/macbook-thermal-fan': ('macbook-thermal-fan',0o755),
    '/usr/local/libexec/macbook-fan-resume': ('macbook-fan-resume',0o755),
    '/etc/systemd/system/macbook-fan-early.service': ('macbook-fan-early.service',0o644),
    '/etc/systemd/system/macbook-fan-resume.service': ('macbook-fan-resume.service',0o644),
}
WAKE = 'macbook-fan-resume.service'


def atomic(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as out:
        temp = Path(out.name)
        try:
            out.write(data)
            out.flush()
            os.fchmod(out.fileno(),mode)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def save(record):
    atomic(RECORD,json.dumps(record).encode(),0o600)


def deploy(record, target, data, mode):
    entry = record['files'].get(str(target))
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise RuntimeError(f'Unexpected file type at {target}')
    before = target.read_bytes() if target.exists() else None
    if entry is None:
        entry = {'previous':b64encode(before).decode() if before is not None else None,
                 'mode':target.stat().st_mode & 0o777 if before is not None else mode}
        record['files'][str(target)] = entry
    elif before is not None and hashlib.sha256(before).hexdigest() != entry['digest'] and (
            entry['previous'] is None or before != b64decode(entry['previous'])):
        raise RuntimeError(f'Preserving edited file {target}')
    entry['digest'] = hashlib.sha256(data).hexdigest()
    save(record)
    atomic(target,data,mode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action',choices=['install','remove'])
    args = parser.parse_args()
    uid = int(os.environ.get('SUDO_UID','0'))
    if os.geteuid() or uid <= 0 or Path('/sys/class/dmi/id/product_name').read_text().strip() != 'MacBookPro15,4':
        parser.exit(1,'Use the desktop installer on a supported MacBook.\n')
    STATE.mkdir(mode=0o700,exist_ok=True)
    with (STATE/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if not RECORD.exists() and args.action == 'remove':
            return
        record = json.loads(RECORD.read_text()) if RECORD.exists() else {
            'files':{}, 'users':[], 'wake_enabled':subprocess.run(['systemctl','is-enabled','--quiet',WAKE]).returncode == 0}
        rules = Path(f'/etc/sudoers.d/ubuntu-thermals-{uid}')
        if args.action == 'install':
            for name,(source,mode) in FILES.items():
                deploy(record,Path(name),(ROOT/source).read_bytes(),mode)
            sudoers = (ROOT/'presets.sudoers').read_text().replace('@UID@',f'#{uid}').encode()
            with tempfile.NamedTemporaryFile() as pending:
                pending.write(sudoers)
                pending.flush()
                subprocess.run(['visudo','-cf',pending.name],check=True)
            if uid not in record['users']:
                record['users'].append(uid)
                save(record)
            deploy(record,rules,sudoers,0o440)
            subprocess.run(['systemctl','daemon-reload'],check=True)
            subprocess.run(['systemctl','enable',WAKE],check=True)
            return
        if uid not in record['users']:
            return
        last = record['users'] == [uid]
        names = [str(rules),*FILES] if last else [str(rules)]
        for name in names:
            path = Path(name)
            entry = record['files'].get(name)
            if entry and (path.is_symlink() or (path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != entry['digest'])):
                raise RuntimeError(f'Preserving edited file {path}')
        if last:
            for choice in ('fan-auto','restore'):
                subprocess.run(['/usr/local/bin/macbook-power',choice],check=True)
            if not record['wake_enabled']:
                subprocess.run(['systemctl','disable',WAKE],check=True)
        for name in names:
            entry = record['files'].get(name)
            if entry:
                if entry['previous'] is None:
                    Path(name).unlink(missing_ok=True)
                else:
                    atomic(Path(name),b64decode(entry['previous']),entry['mode'])
        record['users'].remove(uid)
        record['files'].pop(str(rules),None)
        if last:
            subprocess.run(['systemctl','daemon-reload'],check=True)
            RECORD.unlink()
        else:
            save(record)


if __name__ == '__main__':
    main()

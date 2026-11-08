"""Native local launcher. Requires Python, but no Bash, WSL, Docker or Node."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def port_number(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError('Port must be between 1 and 65535.')
    return port


def environment_paths(root=ROOT):
    folder = root / ('.venv-windows' if os.name == 'nt' else '.venv')
    python = folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    return folder, python


def prepare(root=ROOT):
    folder, python = environment_paths(root)
    if not python.is_file():
        if folder.exists():
            raise RuntimeError(f'{folder} is incomplete. Remove or rename that environment and retry. Keep instance/ intact.')
        print(f'Creating the Python environment in {folder}', flush=True)
        venv.EnvBuilder(with_pip=True).create(folder)
    requirements = root / 'requirements.txt'
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    stamp = folder / '.airisk-requirements.sha256'
    if not stamp.exists() or stamp.read_text(encoding='ascii').strip() != fingerprint:
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                        '-r', str(requirements)], cwd=root, check=True)
        stamp.write_text(fingerprint, encoding='ascii')
    subprocess.run([str(python), '-m', 'pip', 'check'], cwd=root, check=True)
    return python


def serve(port, open_browser, prepare_only=False):
    from airisk.runtime import instance_lock, local_app, migrate, serve_application

    instance = Path(os.environ.get('AIRISK_INSTANCE', ROOT / 'instance')).resolve()
    with instance_lock(instance):
        app = local_app(instance)
        migrate(app)
        if not prepare_only:
            serve_application(app, port, open_browser)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=port_number, default=5000)
    parser.add_argument('--no-browser', action='store_true', help='Do not open the default browser.')
    parser.add_argument('--prepare-only', action='store_true', help='Install dependencies and apply migrations, then exit.')
    parser.add_argument('--serve', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        parser.error('Python 3.11 or newer is required; Python 3.12 is recommended.')
    os.chdir(ROOT)
    os.environ.setdefault('PYTHONUTF8', '1')
    if os.environ.get('APP_ENV') == 'production':
        parser.error('This launcher is for a local pilot. Follow docs/OPERATIONS.md for a hosted deployment.')
    try:
        if args.serve:
            serve(args.port, not args.no_browser, args.prepare_only)
        else:
            python = prepare()
            command = [str(python), str(ROOT / 'run_local.py'), '--serve', '--port', str(args.port)]
            if args.no_browser:
                command.append('--no-browser')
            if args.prepare_only:
                command.append('--prepare-only')
            return subprocess.call(command, cwd=ROOT)
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'AIrisk could not start: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Build an unsigned IT review bundle on native Windows. Never installs or signs it."""
import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import zlib
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop import VERSION


def sha256(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def inventory(destination):
    """Record the build environment and redistribute dependency license files."""
    components = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = dist.metadata['Name']
        components.append({'type': 'library', 'name': name, 'version': dist.version,
                           'purl': f'pkg:pypi/{re.sub(r"[-_.]+", "-", name).lower()}@{dist.version}'})
        for file in dist.files or []:
            if any(word in file.name.lower() for word in ('license', 'copying', 'notice')):
                source = Path(dist.locate_file(file))
                if source.is_file() and not file.name.endswith(('.py', '.pyc')):
                    target = destination / 'licenses' / name / str(file).replace('../', '').replace('..\\', '')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
    (destination / 'build-sbom.cdx.json').write_text(json.dumps({
        'bomFormat': 'CycloneDX', 'specVersion': '1.5', 'version': 1,
        'metadata': {'component': {'type': 'application', 'name': 'AIrisk Windows build environment', 'version': VERSION}},
        'components': components}, indent=2), encoding='utf-8')
    # Includes CPython and notices for bundled components such as OpenSSL/SQLite.
    license_file = Path(sys.base_prefix) / 'LICENSE.txt'
    if not license_file.is_file():
        raise RuntimeError('The approved Python distribution must provide LICENSE.txt.')
    shutil.copyfile(license_file, destination / 'licenses' / 'PYTHON-LICENSE.txt')


def icon(path, size):
    """Small original geometric risk-grid icon; no external artwork dependency."""
    pixels = bytearray()
    for y in range(size):
        pixels.append(0)
        for x in range(size):
            gx, gy = x * 9 // size, y * 9 // size
            cell = 1 <= gx <= 7 and 1 <= gy <= 7 and gx not in (3, 6) and gy not in (3, 6)
            pixels.extend((40, 184, 164, 255) if cell else (17, 35, 53, 255))
    def chunk(name, body):
        return struct.pack('!I', len(body)) + name + body + struct.pack('!I', zlib.crc32(name + body))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', size, size, 8, 6, 0, 0, 0)) +
                     chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


def package_manifest(folder, publisher):
    assets = folder / 'Assets'
    assets.mkdir()
    for size in (44, 50, 150):
        icon(assets / f'Logo{size}.png', size)
    safe_publisher = escape(publisher, {'"': '&quot;'})
    (folder / 'AppxManifest.xml').write_text(f'''<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
 xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
 xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
 IgnorableNamespaces="uap rescap">
 <Identity Name="AIrisk.Workspace" Publisher="{safe_publisher}" Version="{VERSION}.0" ProcessorArchitecture="x64" />
 <Properties><DisplayName>AIrisk</DisplayName><PublisherDisplayName>AIrisk</PublisherDisplayName>
 <Description>AI adoption assessment and independent review</Description><Logo>Assets\\Logo50.png</Logo></Properties>
 <Resources><Resource Language="en-us" /></Resources>
 <Dependencies><TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" /></Dependencies>
 <Applications><Application Id="AIrisk" Executable="AIrisk.exe" EntryPoint="Windows.FullTrustApplication">
 <uap:VisualElements DisplayName="AIrisk" Description="AI adoption workspace" BackgroundColor="#112335"
 Square150x150Logo="Assets\\Logo150.png" Square44x44Logo="Assets\\Logo44.png" /></Application></Applications>
 <Capabilities><rescap:Capability Name="runFullTrust" /></Capabilities>
</Package>''', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--makeappx', type=Path, help='Approved Windows SDK MakeAppx.exe (optional).')
    parser.add_argument('--publisher', default='CN=AIrisk IT Review', help='Exact signing certificate subject; review placeholder by default.')
    args = parser.parse_args()
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12) or struct.calcsize('P') != 8:
        parser.error('Build using native CPython 3.12 x64 Windows.')
    for line in (ROOT / 'packaging/windows/requirements.lock').read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        name, version = line.split()[0].split('==')
        if metadata.version(name) != version:
            parser.error(f'{name} differs from the reviewed lock; rebuild the environment from pinned wheels.')
    out = ROOT / 'artifacts' / f'AIrisk-{VERSION}-windows-x64-unsigned'
    if out.exists():
        parser.error(f'Output already exists: {out}. Preserve it or remove it explicitly before rebuilding.')
    subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build' / 'pyinstaller'),
                    str(ROOT / 'packaging' / 'windows' / 'AIrisk.spec')], cwd=ROOT, check=True)
    out.mkdir(parents=True)
    app = out / 'AIrisk'
    shutil.copytree(ROOT / 'dist' / 'AIrisk', app)
    inventory(app)
    for name in ('SECURITY_REVIEW.md', 'WINDOWS_IT_REVIEW.md'):
        shutil.copyfile(ROOT / 'docs' / name, out / name)
    shutil.copyfile(ROOT / 'packaging/windows/requirements.lock', out / 'requirements.lock')
    shutil.copyfile(ROOT / 'packaging/windows/verify.py', out / 'verify.py')
    (out / 'build-info.json').write_text(json.dumps({
        'version': VERSION, 'status': 'UNSIGNED — for IT review, not approved for deployment',
        'python': sys.version, 'windows': platform.platform(), 'publisher': args.publisher,
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'source_tree': subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=ROOT, text=True).strip(),
        'source_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()),
        'lock_sha256': sha256(ROOT / 'packaging/windows/requirements.lock'),
    }, indent=2), encoding='utf-8')
    shutil.make_archive(str(out / f'AIrisk-{VERSION}-portable-unsigned'), 'zip', out, 'AIrisk')
    if args.makeappx:
        stage = ROOT / 'build' / 'msix' / VERSION
        if stage.exists():
            shutil.rmtree(stage)
        shutil.copytree(app, stage)
        package_manifest(stage, args.publisher)
        subprocess.run([str(args.makeappx.resolve()), 'pack', '/d', str(stage), '/p',
                        str(out / f'AIrisk-{VERSION}-unsigned.msix'), '/o'], check=True)
    entries = {p.relative_to(out).as_posix(): sha256(p) for p in sorted(out.rglob('*')) if p.is_file()}
    (out / 'SHA256SUMS.json').write_text(json.dumps(entries, indent=2), encoding='utf-8')
    handoff = Path(shutil.make_archive(str(ROOT / 'artifacts' / f'AIrisk-{VERSION}-IT-review-unsigned'),
                                      'zip', out.parent, out.name))
    handoff.with_suffix('.zip.sha256').write_text(sha256(handoff) + '  ' + handoff.name + '\n', encoding='ascii')
    print(f'Unsigned IT review bundle: {out}', flush=True)


if __name__ == '__main__':
    main()

# Build on CPython 3.12 x64 Windows using the hashed build lock.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

root = Path(SPECPATH).parents[1]
data = [(str(root / name), name) for name in ('templates', 'static')]
# Migration scripts load dynamically; do not ship local interpreter caches.
data += [(str(p), str(p.parent.relative_to(root))) for p in (root / 'migrations').rglob('*')
         if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py', '.ini', '.mako')]
for name in ('Flask', 'Flask-SQLAlchemy', 'Flask-WTF', 'Flask-Migrate', 'SQLAlchemy', 'alembic'):
    data += copy_metadata(name)
a = Analysis([str(root / 'desktop.py')], pathex=[str(root)], datas=data,
             hiddenimports=collect_submodules('alembic') + ['sqlalchemy.dialects.sqlite', 'logging.config'],
             excludes=['tkinter', 'unittest', 'pytest', 'pip', 'setuptools'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='AIrisk', debug=False,
          strip=False, upx=False, console=True, uac_admin=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='AIrisk')

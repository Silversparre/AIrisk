"""Verify an extracted review bundle. Checksums detect change, not publisher identity."""
import hashlib
import json
from pathlib import Path
import sys


def verify(folder):
    folder = Path(folder).resolve()
    entries = json.loads((folder / 'SHA256SUMS.json').read_text(encoding='utf-8'))
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if actual != set(entries) | {'SHA256SUMS.json'}:
        raise ValueError('Missing or unexpected files in the review bundle.')
    for name, expected in entries.items():
        path = (folder / name).resolve()
        if folder not in path.parents:
            raise ValueError('Invalid manifest path.')
        with path.open('rb') as handle:
            observed = hashlib.file_digest(handle, 'sha256').hexdigest()
        if observed != expected:
            raise ValueError(f'Checksum mismatch: {name}')
    print(f'Verified {len(entries)} files. This does not verify a publisher signature.')


if __name__ == '__main__':
    try:
        verify(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent)
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc))

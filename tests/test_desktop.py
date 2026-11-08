from contextlib import closing
import importlib.util
import json
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from desktop import backup, data_directory


class DesktopTests(unittest.TestCase):
    def test_backup_preserves_database_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            with closing(sqlite3.connect(folder / 'airisk.db')) as source:
                source.execute('CREATE TABLE example(value TEXT)')
                source.execute("INSERT INTO example VALUES('retained')")
                source.commit()
            target = folder / 'backup.db'
            backup(folder, target)
            with closing(sqlite3.connect(target)) as saved:
                self.assertEqual(saved.execute('SELECT value FROM example').fetchone()[0], 'retained')
            with self.assertRaises(FileExistsError):
                backup(folder, target)

    def test_frozen_data_must_remain_outside_program_folder(self):
        with tempfile.TemporaryDirectory() as temp, patch('sys.frozen', True, create=True), patch('sys.executable', str(Path(temp) / 'AIrisk.exe')):
            with self.assertRaises(RuntimeError):
                data_directory(Path(temp) / 'data')
            with patch.dict(os.environ, {'LOCALAPPDATA': str(Path(temp).parent / 'profile')}):
                self.assertEqual(data_directory().name, 'instance')

    def test_bundle_verification_rejects_tampering_and_extra_files(self):
        spec = importlib.util.spec_from_file_location('bundle_verify', Path(__file__).resolve().parents[1] / 'packaging/windows/verify.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / 'test.txt').write_bytes(b'original')
            (folder / 'SHA256SUMS.json').write_text(json.dumps({'test.txt': hashlib.sha256(b'original').hexdigest()}))
            module.verify(folder)
            (folder / 'test.txt').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                module.verify(folder)
            (folder / 'test.txt').write_bytes(b'original')
            (folder / 'unexpected.txt').write_text('extra')
            with self.assertRaises(ValueError):
                module.verify(folder)


if __name__ == '__main__':
    unittest.main()

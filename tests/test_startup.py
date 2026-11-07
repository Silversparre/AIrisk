import os
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flask_migrate import upgrade
from sqlalchemy import text
from airisk import create_app
from airisk.models import db


class StartupTests(unittest.TestCase):
    def test_production_requires_secret_and_uses_secure_cookies(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'APP_ENV':'production','AIRISK_INSTANCE':tmp,'SECRET_KEY':''}):
            with self.assertRaises(RuntimeError):
                create_app()
            with patch.dict(os.environ, {'SECRET_KEY':'short'}):
                with self.assertRaises(RuntimeError):
                    create_app()
            with patch.dict(os.environ, {'SECRET_KEY':'random-test-value-'*3}):
                app = create_app()
                self.assertTrue(app.config['SESSION_COOKIE_SECURE'])
                self.assertEqual(app.test_client().get('/setup').status_code, 404)
                self.assertEqual(app.test_client().get('/health').status_code, 503)
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()

    def test_populated_database_upgrade_and_backup_restore(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'AIRISK_INSTANCE':tmp}):
            app = create_app({'SECRET_KEY':'test-key-'*8})
            with app.app_context():
                upgrade(revision='bcfdfc486ed0')
                # Populate the first released schema before the later migration.
                db.session.execute(text("INSERT INTO user(id,email,name,password_hash,role,active,session_version) VALUES(1,'test@example.test','Test','unusable-test-hash','member',1,1)"))
                db.session.execute(text("INSERT INTO ai_system(id,name,purpose,owner_id,model_version,ai_type,lifecycle,data_sensitivity,audience,autonomy,uses_rag,eu_scope,operator_role,use_domain,prohibited,created_at) VALUES(1,'Pilot','Test migration',1,'1','predictive','pilot','internal','internal','advisory',0,'yes','deployer','general','none',CURRENT_TIMESTAMP)"))
                db.session.execute(text("INSERT INTO assessment(id,system_id,author_id,catalog_version,snapshot,state,created_at) VALUES(1,1,1,'test','{}','draft',CURRENT_TIMESTAMP)"))
                db.session.commit()
                upgrade()
                self.assertEqual(db.session.execute(text('SELECT revision FROM assessment WHERE id=1')).scalar(),1)
                self.assertEqual(app.test_client().get('/health').status_code,200)
                db.session.remove()
                db.engine.dispose()
            original = Path(tmp)/'airisk.db'
            backup = Path(tmp)/'restored.db'
            with closing(sqlite3.connect(original)) as source, closing(sqlite3.connect(backup)) as target:
                source.backup(target)
            restored = create_app({'SECRET_KEY':'test-key-'*8,'SQLALCHEMY_DATABASE_URI':'sqlite:///'+str(backup)})
            with restored.app_context():
                self.assertEqual(db.session.execute(text('SELECT name FROM ai_system WHERE id=1')).scalar(),'Pilot')
                self.assertEqual(db.session.execute(text('PRAGMA integrity_check')).scalar(),'ok')
                db.session.remove()
                db.engine.dispose()


if __name__ == '__main__':
    unittest.main()

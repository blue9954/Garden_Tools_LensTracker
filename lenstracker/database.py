import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from lenstracker.prices import apply_prices, reference_price
from lenstracker.lenses import lens_key, merge_lenses


SCHEMA = """
CREATE TABLE IF NOT EXISTS equipment (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('camera','lens')),
 model TEXT NOT NULL, name TEXT NOT NULL, new_price INTEGER,
 purchase_price INTEGER, note TEXT NOT NULL DEFAULT '',
 UNIQUE(kind, model), CHECK(new_price IS NULL OR new_price >= 0),
 CHECK(purchase_price IS NULL OR purchase_price >= 0)
);
CREATE TABLE IF NOT EXISTS photos (
 id INTEGER PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
 camera_id INTEGER REFERENCES equipment(id), lens_id INTEGER REFERENCES equipment(id),
 taken_at TEXT, focal REAL, aperture REAL, iso REAL, format TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
 path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime INTEGER NOT NULL,
 photo_id INTEGER NOT NULL REFERENCES photos(id)
);
CREATE TABLE IF NOT EXISTS folders (
 path_key TEXT PRIMARY KEY, path TEXT NOT NULL, last_opened_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS csv_imports (
 id INTEGER PRIMARY KEY, digest TEXT NOT NULL UNIQUE, filename TEXT NOT NULL,
 source_path TEXT NOT NULL, imported_at TEXT NOT NULL,
 total_photos INTEGER NOT NULL, row_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS csv_import_rows (
 id INTEGER PRIMARY KEY, import_id INTEGER NOT NULL REFERENCES csv_imports(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('camera','lens','combination')),
 name TEXT NOT NULL, count INTEGER NOT NULL, share_percent REAL NOT NULL,
 new_price INTEGER, cost REAL
);
CREATE INDEX IF NOT EXISTS photos_date ON photos(taken_at);
CREATE INDEX IF NOT EXISTS photos_camera ON photos(camera_id);
CREATE INDEX IF NOT EXISTS photos_lens ON photos(lens_id);
CREATE INDEX IF NOT EXISTS files_photo ON files(photo_id);
CREATE INDEX IF NOT EXISTS csv_rows_import ON csv_import_rows(import_id);
"""


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)
        merge_lenses(db)
        apply_prices(db)


def remember_folder(database, folder):
    path = str(Path(folder).expanduser().resolve())
    # Windows paths differing only in spelling or case refer to the same folder.
    key = os.path.normcase(path)
    opened_at = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    with connect(database) as db:
        db.execute('INSERT INTO folders(path_key,path,last_opened_at) VALUES(?,?,?) '
                   'ON CONFLICT(path_key) DO UPDATE SET path=excluded.path,last_opened_at=excluded.last_opened_at',
                   (key, path, opened_at))
    return path


def recent_folders(database):
    with connect(database) as db:
        return [dict(row) for row in db.execute(
            'SELECT path,last_opened_at FROM folders ORDER BY last_opened_at DESC,path_key ASC')]


def equipment_id(db, kind, model):
    if not model:
        return None
    model = ' '.join(str(model).replace('\x00', '').split())[:300]
    if not model:
        return None
    if kind == 'lens':
        key = lens_key(model)
        for row in db.execute("SELECT id,model FROM equipment WHERE kind='lens'"):
            if lens_key(row['model']) == key:
                return row['id']
    price = reference_price(model) if kind == 'lens' else None
    db.execute('INSERT OR IGNORE INTO equipment(kind,model,name,new_price,note) VALUES(?,?,?,?,?)',
               (kind, model, model, price[0] if price else None, price[1] if price else ''))
    return db.execute('SELECT id FROM equipment WHERE kind=? AND model=?',
                      (kind, model)).fetchone()['id']

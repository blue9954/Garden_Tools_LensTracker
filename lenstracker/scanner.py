import hashlib
import os
import threading
from datetime import datetime
from pathlib import Path

from .database import connect, equipment_id, remember_folder
from .metadata import EXTENSIONS, exiftool_path, read_metadata


class Scanner:
    def __init__(self, database):
        self.database = database
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.state = dict(status='idle', discovered=0, processed=0, added=0, duplicates=0,
                          skipped=0, failed=0, current='', errors=[], folder='')

    def snapshot(self):
        with self.lock:
            return {**self.state, 'errors': list(self.state['errors'])}

    def start(self, folder):
        path = Path(folder).expanduser().resolve()
        if not path.is_dir():
            raise ValueError('접근 가능한 폴더 경로를 입력해 주세요.')
        with self.lock:
            if self.state['status'] in ('scanning', 'cancelling'):
                raise RuntimeError('이미 가져오기가 진행 중입니다.')
            remember_folder(self.database, path)
            self.cancelled.clear()
            self.state = dict(status='scanning', discovered=0, processed=0, added=0,
                              duplicates=0, skipped=0, failed=0, current='', errors=[],
                              folder=str(path), started_at=datetime.now().isoformat())
        thread = threading.Thread(target=self.run, args=(path,), daemon=True)
        thread.start()

    def cancel(self):
        with self.lock:
            if self.state['status'] == 'scanning':
                self.state['status'] = 'cancelling'
                self.cancelled.set()

    def error(self, path, error):
        with self.lock:
            self.state['failed'] += 1
            self.state['errors'].append(dict(path=str(path), message=str(error)[:400]))
            self.state['errors'] = self.state['errors'][-100:]

    def import_file(self, path, executable):
        path = path.resolve()
        before = path.stat()
        with connect(self.database) as db:
            previous = db.execute('SELECT * FROM files WHERE path=?', (str(path),)).fetchone()
            if previous and previous['size'] == before.st_size and previous['mtime'] == before.st_mtime_ns:
                return 'skipped'
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                if self.cancelled.is_set():
                    return None
                digest.update(chunk)
        fingerprint = digest.hexdigest()
        with connect(self.database) as db:
            existing = db.execute('SELECT id FROM photos WHERE digest=?', (fingerprint,)).fetchone()
            if existing:
                photo_id = existing['id']
                outcome = 'duplicates'
            else:
                metadata = read_metadata(path, executable)
                camera = equipment_id(db, 'camera', metadata.pop('camera'))
                lens = equipment_id(db, 'lens', metadata.pop('lens'))
                photo_id = db.execute(
                    'INSERT INTO photos(digest,camera_id,lens_id,taken_at,focal,aperture,iso,format) '
                    'VALUES(?,?,?,?,?,?,?,?)',
                    (fingerprint, camera, lens, metadata['taken_at'], metadata['focal'],
                     metadata['aperture'], metadata['iso'], metadata['format'])).lastrowid
                outcome = 'added'
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('처리 중 파일이 변경되었습니다. 다시 가져와 주세요.')
            db.execute('INSERT INTO files(path,size,mtime,photo_id) VALUES(?,?,?,?) '
                       'ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,photo_id=excluded.photo_id',
                       (str(path), after.st_size, after.st_mtime_ns, photo_id))
            if previous and previous['photo_id'] != photo_id:
                db.execute('DELETE FROM photos WHERE id=? AND NOT EXISTS '
                           '(SELECT 1 FROM files WHERE photo_id=photos.id)', (previous['photo_id'],))
            return outcome

    def run(self, folder):
        executable = exiftool_path()
        fatal = False
        try:
            for root, dirs, files in os.walk(folder, followlinks=False,
                                            onerror=lambda e: self.error(e.filename, e)):
                dirs[:] = sorted(d for d in dirs if not Path(root, d).is_symlink())
                for filename in files:
                    if self.cancelled.is_set():
                        break
                    path = Path(root, filename)
                    if path.suffix.lower() not in EXTENSIONS or path.is_symlink():
                        continue
                    with self.lock:
                        self.state['discovered'] += 1
                        self.state['current'] = str(path)
                    try:
                        outcome = self.import_file(path, executable)
                        if outcome:
                            with self.lock:
                                self.state[outcome] += 1
                    except Exception as exc:
                        self.error(path, exc)
                    finally:
                        with self.lock:
                            self.state['processed'] += 1
                if self.cancelled.is_set():
                    break
        except Exception as exc:
            self.error(folder, exc)
            fatal = True
        finally:
            with self.lock:
                self.state['status'] = 'failed' if fatal else ('cancelled' if self.cancelled.is_set() else 'completed')
                self.state['current'] = ''
                self.state['finished_at'] = datetime.now().isoformat()

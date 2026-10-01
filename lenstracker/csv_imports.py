"""Import exported statistics as independent snapshots, separate from photo metadata."""
import csv
import hashlib
import io
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .database import connect


HEADERS = ['유형', '장비/조합', '촬영 수', '비율(%)', '신품 가격(KRW)', '사진당 비용(KRW)']
KINDS = {'카메라': 'camera', '렌즈': 'lens', '조합': 'combination'}
MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100_000

SUMMARY_SQL = '''SELECT i.id,i.filename,i.source_path,i.imported_at,i.total_photos,i.row_count,
    sum(CASE WHEN r.kind='camera' THEN 1 ELSE 0 END) AS camera_count,
    sum(CASE WHEN r.kind='lens' THEN 1 ELSE 0 END) AS lens_count,
    sum(CASE WHEN r.kind='combination' THEN 1 ELSE 0 END) AS combination_count
    FROM csv_imports i LEFT JOIN csv_import_rows r ON r.import_id=i.id'''


def _number(value, label, row_number, *, optional=False, integer=False, maximum=None):
    value = value.strip()
    if not value and optional:
        return None
    message = f'{row_number}행의 {label} 값이 올바르지 않습니다.'
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(message) from exc
    if not result.is_finite() or result < 0 or (maximum is not None and result > maximum):
        raise ValueError(message)
    if integer:
        if result != result.to_integral_value():
            raise ValueError(message)
        return int(result)
    number = float(result)
    if not math.isfinite(number):
        raise ValueError(message)
    return number


def read_stats_csv(source_path):
    """Read and validate every row before any database changes are made."""
    try:
        source = Path(source_path).expanduser().resolve()
        if source.suffix.lower() != '.csv' or not source.is_file():
            raise ValueError('접근 가능한 CSV 파일을 선택해 주세요.')
        before = source.stat()
        if before.st_size > MAX_BYTES:
            raise ValueError('CSV 파일은 10MB 이하여야 합니다.')
        with source.open('rb') as stream:
            content = stream.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError('CSV 파일은 10MB 이하여야 합니다.')
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('읽는 중 CSV 파일이 변경되었습니다. 다시 가져와 주세요.')
    except OSError as exc:
        raise ValueError('CSV 파일을 읽을 수 없습니다. 경로와 접근 권한을 확인해 주세요.') from exc
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            text = content.decode('cp949')
        except UnicodeDecodeError as exc:
            raise ValueError('CSV 파일의 문자 인코딩은 UTF-8 또는 CP949여야 합니다.') from exc

    rows, totals = [], {}
    try:
        reader = csv.reader(io.StringIO(text, newline=''), strict=True)
        if next(reader, None) != HEADERS:
            raise ValueError('LensTracker 통계 CSV의 열 이름과 순서가 일치해야 합니다.')
        for row in reader:
            row_number = reader.line_num
            if len(rows) >= MAX_ROWS:
                raise ValueError('CSV 데이터는 100,000행 이하여야 합니다.')
            if len(row) != len(HEADERS):
                raise ValueError(f'{row_number}행의 열 개수가 올바르지 않습니다.')
            kind = KINDS.get(row[0].strip())
            if kind is None:
                raise ValueError(f'{row_number}행의 유형은 카메라, 렌즈 또는 조합이어야 합니다.')
            name = row[1]
            if not name.strip() or len(name) > 1000 or '\x00' in name:
                raise ValueError(f'{row_number}행의 장비/조합 이름이 올바르지 않습니다.')
            count = _number(row[2], HEADERS[2], row_number, integer=True, maximum=1_000_000_000_000)
            share = _number(row[3], HEADERS[3], row_number, maximum=100)
            price = _number(row[4], HEADERS[4], row_number, optional=True, integer=True,
                            maximum=200_000_000_000 if kind == 'combination' else 100_000_000_000)
            cost = _number(row[5], HEADERS[5], row_number, optional=True)
            rows.append(dict(kind=kind, name=name, count=count, share_percent=share, new_price=price, cost=cost))
            totals[kind] = totals.get(kind, 0) + count
    except csv.Error as exc:
        raise ValueError('CSV 형식을 읽을 수 없습니다. 구분자와 따옴표를 확인해 주세요.') from exc
    if len(set(totals.values())) > 1:
        raise ValueError('카메라, 렌즈, 조합의 촬영 수 합계가 서로 일치하지 않습니다.')
    return dict(filename=source.name, source_path=str(source), digest=hashlib.sha256(content).hexdigest(),
                total_photos=next(iter(totals.values()), 0), rows=rows)


def _summary(row):
    result = dict(row)
    result['counts'] = {kind: result.pop(f'{kind}_count') for kind in KINDS.values()}
    return result


def list_csv_imports(database):
    with connect(database) as db:
        return [_summary(row) for row in db.execute(
            SUMMARY_SQL + ' GROUP BY i.id ORDER BY i.imported_at DESC,i.id DESC')]


def _detail(db, identifier):
    row = db.execute(SUMMARY_SQL + ' WHERE i.id=? GROUP BY i.id', (identifier,)).fetchone()
    if row is None:
        return None
    result = _summary(row)
    result['rows'] = [dict(row) for row in db.execute(
        '''SELECT kind,name,count,share_percent,new_price,cost FROM csv_import_rows WHERE import_id=?
           ORDER BY CASE kind WHEN 'camera' THEN 0 WHEN 'lens' THEN 1 ELSE 2 END,count DESC,name,id''',
        (identifier,))]
    return result


def get_csv_import(database, identifier):
    with connect(database) as db:
        return _detail(db, identifier)


def import_stats_csv(database, source_path):
    parsed = read_stats_csv(source_path)
    imported_at = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    with connect(database) as db:
        result = db.execute(
            '''INSERT INTO csv_imports(digest,filename,source_path,imported_at,total_photos,row_count)
               VALUES(?,?,?,?,?,?) ON CONFLICT(digest) DO NOTHING''',
            (parsed['digest'], parsed['filename'], parsed['source_path'], imported_at,
             parsed['total_photos'], len(parsed['rows'])))
        duplicate = result.rowcount == 0
        identifier = db.execute('SELECT id FROM csv_imports WHERE digest=?', (parsed['digest'],)).fetchone()[0]
        if not duplicate:
            db.executemany(
                '''INSERT INTO csv_import_rows(import_id,kind,name,count,share_percent,new_price,cost)
                   VALUES(?,?,?,?,?,?,?)''',
                [(identifier, row['kind'], row['name'], row['count'], row['share_percent'], row['new_price'], row['cost'])
                 for row in parsed['rows']])
        detail = _detail(db, identifier)
    return {'duplicate': duplicate, 'import': detail}

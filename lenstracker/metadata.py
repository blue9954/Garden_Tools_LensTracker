import json
import math
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image


BASIC_EXTENSIONS = {'.jpg', '.jpeg', '.tif', '.tiff', '.png', '.webp'}
RAW_EXTENSIONS = {'.heic', '.heif', '.dng', '.cr2', '.cr3', '.nef', '.nrw',
                  '.arw', '.raf', '.rw2', '.orf', '.pef', '.srw'}
EXTENSIONS = BASIC_EXTENSIONS | RAW_EXTENSIONS


def exiftool_path():
    custom = os.environ.get('EXIFTOOL_PATH')
    if custom and Path(custom).is_file():
        return custom
    roots = [Path(__file__).resolve().parent.parent]
    if getattr(sys, 'frozen', False):
        roots.insert(0, Path(sys.executable).resolve().parent)
    for root in roots:
        local = root / 'tools' / 'exiftool.exe'
        if local.is_file():
            return str(local)
    return shutil.which('exiftool')


def clean(value):
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    return ' '.join(str(value or '').replace('\x00', '').split())[:300]


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result > 0 else None
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def camera_name(make, model):
    make, model = clean(make), clean(model)
    if not model:
        return None
    return model if not make or model.casefold().startswith(make.casefold()) else f'{make} {model}'


def date_value(value):
    value = clean(value)
    if not value:
        return None
    for pattern in ('%Y:%m:%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(value[:19], pattern).isoformat(timespec='seconds')
        except ValueError:
            pass
    return None


def read_metadata(path, executable=None):
    if executable:
        result = subprocess.run(
            [executable, '-json', '-n', '-charset', 'filename=UTF8', '-Make', '-Model',
             '-LensModel', '-LensID', '-Lens', '-DateTimeOriginal', '-CreateDate',
             '-FocalLength', '-FNumber', '-ISO', str(path)],
            capture_output=True, timeout=45, encoding='utf-8', errors='replace',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            record = json.loads(result.stdout)[0]
        except (ValueError, IndexError, TypeError) as exc:
            raise ValueError('ExifTool이 메타데이터를 읽지 못했습니다.') from exc
        if record.get('Error') or result.returncode != 0:
            raise ValueError(record.get('Error') or result.stderr.strip()[:200])
        lens = record.get('LensModel') or record.get('Lens')
        # A numerical LensID is not a reliable human-readable model.
        if not lens and isinstance(record.get('LensID'), str):
            lens = record['LensID']
        return dict(camera=camera_name(record.get('Make'), record.get('Model')),
                    lens=clean(lens) or None,
                    taken_at=date_value(record.get('DateTimeOriginal') or record.get('CreateDate')),
                    focal=number(record.get('FocalLength')), aperture=number(record.get('FNumber')),
                    iso=number(record.get('ISO')), format=path.suffix.lstrip('.').upper())
    if path.suffix.lower() not in BASIC_EXTENSIONS:
        raise ValueError('RAW/HEIC 파일을 읽으려면 ExifTool을 설치해 주세요.')
    with Image.open(path) as image:
        exif = image.getexif()
        details = exif.get_ifd(34665) if 34665 in exif else {}
        tags = {**dict(exif), **details}
        return dict(camera=camera_name(tags.get(271), tags.get(272)),
                    lens=clean(tags.get(42036)) or None,
                    taken_at=date_value(tags.get(36867) or tags.get(36868)),
                    focal=number(tags.get(37386)), aperture=number(tags.get(33437)),
                    iso=number(tags.get(34855)), format=image.format or path.suffix[1:].upper())

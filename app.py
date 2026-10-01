import csv
import io
import secrets
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request, Response

from lenstracker.analytics import photo_page, report
from lenstracker.csv_imports import get_csv_import, import_stats_csv, list_csv_imports
from lenstracker.database import connect, initialize, recent_folders, remember_folder
from lenstracker.metadata import exiftool_path
from lenstracker.runtime import data_directory
from lenstracker.scanner import Scanner
from lenstracker.version import VERSION


ROOT = Path(__file__).resolve().parent


def csv_safe(value):
    text = str(value) if value is not None else ''
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text


def create_app(database=None, choose_folder=None, choose_csv=None):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 64 * 1024
    app.config['TRUSTED_HOSTS'] = ['127.0.0.1', 'localhost', '[::1]']
    app.json.ensure_ascii = False
    path = Path(database) if database is not None else data_directory() / 'lenstracker.sqlite3'
    initialize(path)
    scanner = Scanner(path)
    app.extensions['scanner'] = scanner
    token = secrets.token_urlsafe(32)

    @app.before_request
    def local_requests():
        if request.method in ('POST', 'PATCH', 'DELETE', 'PUT'):
            origin = request.headers.get('Origin')
            if origin and urlsplit(origin).netloc != request.host:
                return jsonify(error='외부 사이트의 요청은 허용하지 않습니다.'), 403
            if request.headers.get('X-LensTracker-Token') != token:
                return jsonify(error='페이지를 새로고침한 뒤 다시 시도해 주세요.'), 403

    @app.after_request
    def response_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(ValueError)
    def bad_input(error):
        return jsonify(error=str(error)), 400

    @app.get('/')
    def home():
        return render_template('index.html', token=token, desktop=choose_folder is not None, version=VERSION)

    @app.post('/api/desktop/folder')
    def desktop_folder():
        if choose_folder is None:
            return jsonify(error='독립 앱에서 사용할 수 있는 기능입니다.'), 404
        folder = choose_folder()
        if folder:
            if not isinstance(folder, str) or not Path(folder).expanduser().is_dir():
                raise ValueError('접근 가능한 폴더 경로를 선택해 주세요.')
            folder = remember_folder(path, folder)
        return jsonify(folder=folder)

    @app.post('/api/desktop/csv')
    def desktop_csv():
        if choose_csv is None:
            return jsonify(error='독립 앱에서 사용할 수 있는 기능입니다.'), 404
        return jsonify(path=choose_csv() or None)

    @app.get('/api/csv-imports')
    def csv_import_list():
        return jsonify(imports=list_csv_imports(path))

    @app.get('/api/csv-imports/<int:identifier>')
    def csv_import_detail(identifier):
        imported = get_csv_import(path, identifier)
        if imported is None:
            return jsonify(error='저장된 CSV 통계를 찾을 수 없습니다.'), 404
        return jsonify(imported)

    @app.post('/api/csv-imports')
    def csv_import():
        body = request.get_json(silent=True) or {}
        source = body.get('path') if isinstance(body, dict) else None
        if not isinstance(source, str) or not source.strip():
            raise ValueError('가져올 CSV 파일 경로를 입력해 주세요.')
        with scanner.lock:
            if scanner.state['status'] in ('scanning', 'cancelling'):
                return jsonify(error='사진 가져오기를 마친 뒤 CSV를 가져와 주세요.'), 409
            result = import_stats_csv(path, source.strip())
        return jsonify(result), 200 if result['duplicate'] else 201

    @app.get('/api/folders')
    def get_folders():
        folders = recent_folders(path)
        return jsonify(folders=folders, last_folder=folders[0]['path'] if folders else '',
                       data_directory=str(path.parent.resolve()))

    @app.get('/api/report')
    def get_report():
        return jsonify(report(path, request.args))

    @app.get('/api/photos')
    def get_photos():
        return jsonify(photo_page(path, request.args))

    @app.get('/api/status')
    def status():
        return jsonify(scan=scanner.snapshot(), exiftool=bool(exiftool_path()))

    @app.post('/api/scan')
    def scan():
        body = request.get_json(silent=True) or {}
        folder = body.get('folder') if isinstance(body, dict) else None
        if not isinstance(folder, str) or not folder.strip():
            raise ValueError('가져올 폴더 경로를 입력해 주세요.')
        try:
            scanner.start(folder.strip())
        except (OSError, RuntimeError) as exc:
            return jsonify(error=str(exc)), 409
        return jsonify(scanner.snapshot()), 202

    @app.post('/api/scan/cancel')
    def cancel_scan():
        scanner.cancel()
        return jsonify(scanner.snapshot())

    @app.patch('/api/equipment/<int:identifier>')
    def update_equipment(identifier):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError('장비 정보가 올바르지 않습니다.')
        fields = {}
        for key in ('new_price', 'purchase_price'):
            if key in body:
                value = body[key]
                if value is not None and (type(value) is not int or not 0 <= value <= 100_000_000_000):
                    raise ValueError('가격은 0~1000억원 사이의 원화 정수로 입력해 주세요.')
                fields[key] = value
        for key, limit in [('name', 300), ('note', 1000)]:
            if key in body:
                if not isinstance(body[key], str) or len(body[key].strip()) > limit:
                    raise ValueError('이름 또는 메모가 너무 길거나 형식이 올바르지 않습니다.')
                fields[key] = body[key].strip()
                if key == 'name' and not fields[key]:
                    raise ValueError('장비 이름을 입력해 주세요.')
        if not fields:
            raise ValueError('저장할 변경 사항이 없습니다.')
        with connect(path) as db:
            result = db.execute('UPDATE equipment SET ' + ','.join(f'{key}=?' for key in fields) + ' WHERE id=?',
                                (*fields.values(), identifier))
            if not result.rowcount:
                return jsonify(error='장비를 찾을 수 없습니다.'), 404
        return jsonify(ok=True)

    @app.get('/api/export')
    def export():
        data = report(path, request.args)
        buffer = io.StringIO(newline='')
        writer = csv.writer(buffer)
        writer.writerow(['유형', '장비/조합', '촬영 수', '비율(%)', '신품 가격(KRW)', '사진당 비용(KRW)'])
        for kind, label in [('camera', '카메라'), ('lens', '렌즈')]:
            for item in data['rankings'][kind]:
                writer.writerow([label, csv_safe(item['name']), item['count'], round(item['share']*100, 2),
                                 item['new_price'], round(item['cost'], 2) if item['cost'] is not None else ''])
        for item in data['combinations']:
            writer.writerow(['조합', csv_safe(item['name']), item['count'], round(item['share']*100, 2),
                             item['price'], round(item['cost'], 2) if item['cost'] is not None else ''])
        return Response('\ufeff' + buffer.getvalue(), mimetype='text/csv; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="lenstracker-stats.csv"'})

    return app


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='LensTracker local photo analytics')
    parser.add_argument('--version', action='version', version=f'LensTracker {VERSION}')
    parser.add_argument('--port', type=int, default=5273)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    application = create_app()
    if not args.no_browser:
        threading.Timer(1, lambda: webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    application.run(host='127.0.0.1', port=args.port, debug=False, threaded=True)

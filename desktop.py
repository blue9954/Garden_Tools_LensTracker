"""LensTracker Windows application with an embedded, bundled Qt rendering engine."""
import argparse
import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading

from PySide6.QtCore import QLockFile, QObject, QSettings, QTimer, QUrl, Signal, Slot, Qt
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineLoadingInfo
from PySide6.QtWebEngineWidgets import QWebEngineView
from werkzeug.serving import make_server, WSGIRequestHandler

from app import ROOT, create_app
from lenstracker.database import recent_folders, remember_folder
from lenstracker.runtime import data_directory, legacy_candidates, migrate_legacy
from lenstracker.version import VERSION


class QuietRequestHandler(WSGIRequestHandler):
    def log_request(self, code='-', size='-'):
        pass


class FolderPicker(QObject):
    requested = Signal(object)

    def __init__(self):
        super().__init__()
        self.window = None
        self.requested.connect(self.pick)

    def choose(self):
        job = {'done': threading.Event(), 'folder': None}
        self.requested.emit(job)
        job['done'].wait()
        return job['folder']

    def choose_csv(self):
        job = {'done': threading.Event(), 'kind': 'csv', 'path': None}
        self.requested.emit(job)
        job['done'].wait()
        return job['path']

    @Slot(object)
    def pick(self, job):
        try:
            if self.window and not self.window.closing:
                settings = self.window.settings
                if job.get('kind') == 'csv':
                    filename, _ = QFileDialog.getOpenFileName(
                        self.window, '가져올 통계 CSV 선택',
                        settings.value('csv_folder', str(Path.home())), 'CSV 파일 (*.csv)')
                    if filename:
                        settings.setValue('csv_folder', str(Path(filename).parent))
                        settings.sync()
                        job['path'] = filename
                    return
                folders = recent_folders(self.window.application.extensions['scanner'].database)
                folder = QFileDialog.getExistingDirectory(
                    self.window, '분석할 사진 폴더 선택',
                    folders[0]['path'] if folders else settings.value('last_folder', str(Path.home())))
                if folder:
                    settings.setValue('last_folder', folder)
                    settings.sync()
                    job['folder'] = folder
        finally:
            job['done'].set()


class LocalPage(QWebEnginePage):
    def __init__(self, profile, origin, parent):
        super().__init__(profile, parent)
        self.origin = QUrl(origin)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return (url.scheme(), url.host(), url.port()) == (
            self.origin.scheme(), self.origin.host(), self.origin.port())

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level != self.JavaScriptConsoleMessageLevel.InfoMessageLevel:
            logging.warning('UI %s:%s %s', source, line, message)


class MainWindow(QMainWindow):
    def __init__(self, application, origin, directory):
        super().__init__()
        self.application = application
        self.directory = directory
        self.closing = False
        self.settings = QSettings(str(directory / 'window.ini'), QSettings.Format.IniFormat)
        database = application.extensions['scanner'].database
        previous_folder = self.settings.value('last_folder', '')
        if previous_folder and not recent_folders(database):
            remember_folder(database, previous_folder)
        self.setWindowTitle(f'LensTracker {VERSION}')
        self.setWindowIcon(QIcon(str(ROOT / 'static' / 'app.ico')))
        self.resize(1360, 940)
        self.setMinimumSize(850, 620)
        geometry = self.settings.value('geometry')
        if geometry:
            self.restoreGeometry(geometry)
        self.view = QWebEngineView(self)
        self.profile = QWebEngineProfile(self.view)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.page = LocalPage(self.profile, origin, self.view)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self.view.setPage(self.page)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setCentralWidget(self.view)
        self.profile.downloadRequested.connect(self.save_download)
        self.page.loadingChanged.connect(self.loaded)
        self.statusBar().showMessage('라이브러리를 여는 중…')
        self.downloads = set()
        self.close_timer = QTimer(self)
        self.close_timer.setInterval(200)
        self.close_timer.timeout.connect(self.finish_close)
        self.create_menus()
        self.view.setUrl(QUrl(origin))

    def create_menus(self):
        file_menu = self.menuBar().addMenu('파일(&F)')
        choose = QAction('사진 폴더 가져오기…', self)
        choose.setShortcut(QKeySequence('Ctrl+O'))
        choose.triggered.connect(lambda: self.page.runJavaScript(
            "requestPhotoImport();"))
        file_menu.addAction(choose)
        import_csv = QAction('CSV 기록 가져오기…', self)
        import_csv.setShortcut(QKeySequence('Ctrl+I'))
        import_csv.triggered.connect(lambda: self.page.runJavaScript('requestCsvImport();'))
        file_menu.addAction(import_csv)
        data_action = QAction('데이터 폴더 열기', self)
        data_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.directory))))
        file_menu.addAction(data_action)
        file_menu.addSeparator()
        quit_action = QAction('종료', self)
        quit_action.setShortcut(QKeySequence('Ctrl+Q'))
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)
        view_menu = self.menuBar().addMenu('보기(&V)')
        refresh = QAction('새로고침', self)
        refresh.setShortcut(QKeySequence('F5'))
        refresh.triggered.connect(self.view.reload)
        view_menu.addAction(refresh)
        for label, shortcut, delta in [('확대', 'Ctrl+=', 0.1), ('축소', 'Ctrl+-', -0.1), ('기본 크기', 'Ctrl+0', 0)]:
            action = QAction(label, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda checked=False, d=delta: self.view.setZoomFactor(
                max(0.7, min(1.6, self.view.zoomFactor()+d)) if d else 1.0))
            view_menu.addAction(action)
        help_menu = self.menuBar().addMenu('도움말(&H)')
        about = QAction('LensTracker 정보', self)
        about.triggered.connect(lambda: QMessageBox.information(
            self, f'LensTracker {VERSION}', '사진 EXIF로 살펴보는 카메라와 렌즈 사용 기록\n\n'
            '독립 실행형 Windows 앱 · 사진 원본을 변경하지 않습니다.\n'
            'RAW/HEIC 분석에는 ExifTool이 필요합니다.\n\n'
            f'데이터 저장 위치:\n{self.directory}'))
        help_menu.addAction(about)

    def loaded(self, info):
        if info.isDownload():
            return
        if info.status() == QWebEngineLoadingInfo.LoadStatus.LoadSucceededStatus:
            self.statusBar().showMessage('내 PC에서 실행 중 · 사진 원본은 그대로 유지됩니다.', 5000)
        elif info.status() == QWebEngineLoadingInfo.LoadStatus.LoadFailedStatus:
            self.statusBar().showMessage('화면을 불러오지 못했습니다. F5로 다시 시도해 주세요.')
            logging.error('Desktop page failed to load')

    def save_download(self, download):
        folder = self.settings.value('export_folder', str(Path.home() / 'Documents'))
        filename, _ = QFileDialog.getSaveFileName(
            self, '통계 CSV 저장', str(Path(folder) / 'lenstracker-stats.csv'), 'CSV 파일 (*.csv)')
        if not filename:
            download.cancel()
            return
        target = Path(filename)
        if not target.suffix:
            target = target.with_suffix('.csv')
        self.settings.setValue('export_folder', str(target.parent))
        download.setDownloadDirectory(str(target.parent))
        download.setDownloadFileName(target.name)
        self.downloads.add(download)
        download.isFinishedChanged.connect(lambda: self.download_finished(download))
        download.accept()

    def download_finished(self, download):
        if not download.isFinished():
            return
        self.downloads.discard(download)
        if download.state() == download.DownloadState.DownloadCompleted:
            self.statusBar().showMessage('CSV 파일을 저장했습니다.', 7000)
        elif download.state() == download.DownloadState.DownloadInterrupted:
            QMessageBox.warning(self, '저장 실패', download.interruptReasonString())

    def closeEvent(self, event):
        scanner = self.application.extensions['scanner']
        active = scanner.snapshot()['status'] in ('scanning', 'cancelling')
        if active:
            if not self.closing:
                answer = QMessageBox.question(self, '가져오기 중',
                    '가져오기를 중단하고 종료할까요?\n이미 등록한 사진은 보존됩니다.',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
                self.closing = True
                scanner.cancel()
                self.view.setEnabled(False)
                self.menuBar().setEnabled(False)
                self.statusBar().showMessage('현재 파일 처리를 마치고 종료합니다…')
                self.close_timer.start()
            event.ignore()
            return
        for download in list(self.downloads):
            download.cancel()
        self.closing = True
        self.settings.setValue('geometry', self.saveGeometry())
        self.settings.sync()
        event.accept()

    def finish_close(self):
        if self.application.extensions['scanner'].snapshot()['status'] not in ('scanning', 'cancelling'):
            self.close_timer.stop()
            self.close()


def main():
    parser = argparse.ArgumentParser(description='LensTracker desktop')
    parser.add_argument('--version', action='version', version=f'LensTracker {VERSION}')
    parser.add_argument('--smoke-test', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_test:
        os.environ['LENSTRACKER_DATA_DIR'] = str(args.smoke_test.resolve() / 'library')
    # Keep console-less Qt/Python libraries from writing to missing streams.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')
    if sys.platform == 'win32':
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('LensTracker.Desktop')
    qt = QApplication([sys.argv[0]])
    qt.setWindowIcon(QIcon(str(ROOT / 'static' / 'app.ico')))
    qt.setApplicationName('LensTracker')
    qt.setApplicationVersion(VERSION)
    qt.setOrganizationName('LensTracker')
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / 'desktop.log', maxBytes=1_000_000, backupCount=2, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler], format='%(asctime)s %(levelname)s %(message)s')
    lock = QLockFile(str(directory / 'app.lock'))
    if not lock.tryLock(100):
        if not args.smoke_test:
            QMessageBox.information(None, 'LensTracker', '이 라이브러리를 사용하는 LensTracker가 이미 실행 중입니다.')
        return 1
    server = None
    try:
        database = directory / 'lenstracker.sqlite3'
        if not args.smoke_test and not os.environ.get('LENSTRACKER_DATA_DIR'):
            source = migrate_legacy(database, legacy_candidates())
            if source:
                logging.info('Copied previous library from %s', source)
        picker = FolderPicker()
        application = create_app(database, choose_folder=picker.choose, choose_csv=picker.choose_csv)
        server = make_server('127.0.0.1', 0, application, threaded=True, request_handler=QuietRequestHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        window = MainWindow(application, origin, directory)
        picker.window = window
        logging.info('Desktop started; frozen=%s; port=%s', bool(getattr(sys, 'frozen', False)), server.server_port)
        if args.smoke_test:
            from scripts.desktop_smoke import SmokeTest
            smoke = SmokeTest(window, args.smoke_test.resolve(), qt)
            smoke.start()
        else:
            window.show()
        result = qt.exec()
        # Delete the page before its profile to release the Chromium process cleanly.
        from shiboken6 import delete
        delete(window.page)
        delete(window.profile)
        return result
    except Exception:
        logging.exception('Desktop startup failed')
        if args.smoke_test:
            raise
        QMessageBox.critical(None, 'LensTracker 실행 오류',
                             f'앱을 시작하지 못했습니다.\n기록 파일: {directory / "desktop.log"}')
        return 1
    finally:
        if server:
            server.shutdown()
            server.server_close()
        lock.unlock()


if __name__ == '__main__':
    raise SystemExit(main())

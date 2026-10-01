"""Exercise the real Qt window and native bridges in an isolated library."""
import csv
import json
from pathlib import Path
import shutil
import sys
import time

from PIL import Image
from PySide6.QtCore import QTimer, QObject
from PySide6.QtWidgets import QFileDialog
from lenstracker.version import VERSION


class SmokeTest(QObject):
    def __init__(self, window, output, qt):
        super().__init__(window)
        self.window, self.output, self.qt = window, output, qt
        self.step = 0
        self.started = time.monotonic()
        self.pending = False
        self.folder_calls = 0
        self.save_called = False
        self.csv_open_calls = 0
        self.output.mkdir(parents=True, exist_ok=True)
        self.photos = output / 'photos'
        self.photos.mkdir(exist_ok=True)
        for i in range(3):
            exif = Image.Exif()
            exif[271], exif[272] = 'Test', 'Desktop Camera'
            exif[34665] = {42036: 'Desktop Lens', 36867: f'2026:09:0{i+1} 12:30:00'}
            nested = self.photos.joinpath(*['nested'] * i)
            nested.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (24,24), (i*50,100,40)).save(nested / f'image-{i}.jpg', exif=exif)
        shutil.copy2(self.photos / 'image-0.jpg', self.photos / 'duplicate.jpg')
        self.csv_path = output / 'native-export.csv'
        self.old_folder = QFileDialog.getExistingDirectory
        self.old_save = QFileDialog.getSaveFileName
        self.old_open = QFileDialog.getOpenFileName
        QFileDialog.getExistingDirectory = self.choose_folder
        QFileDialog.getSaveFileName = self.choose_save
        QFileDialog.getOpenFileName = self.choose_csv
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.tick)

    def choose_folder(self, *args, **kwargs):
        self.folder_calls += 1
        return '' if self.folder_calls == 1 else str(self.photos)

    def choose_save(self, *args, **kwargs):
        self.save_called = True
        return str(self.csv_path), 'CSV 파일 (*.csv)'

    def choose_csv(self, *args, **kwargs):
        self.csv_open_calls += 1
        return ('' if self.csv_open_calls == 1 else str(self.csv_path)), 'CSV 파일 (*.csv)'

    def start(self):
        # Render the actual window without stealing focus or showing a console.
        from PySide6.QtCore import Qt
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.window.show()
        self.timer.start()

    def script(self, code):
        self.window.page.runJavaScript(code)

    def tick(self):
        if time.monotonic()-self.started > 90:
            self.finish(False, f'Timeout at step {self.step}')
            return
        if self.pending:
            return
        self.pending = True
        self.window.page.runJavaScript("""JSON.stringify({
          ready:typeof data!=='undefined'&&!!data,
          total:typeof data!=='undefined'&&data?data.summary.total:-1,
          average:typeof data!=='undefined'&&data?data.summary.average_cost:null,
          folder:document.querySelector('#folder')?.value||'',
          saved:typeof savedFolders!=='undefined'?savedFolders.length:-1,
          csvCount:typeof csvImports!=='undefined'?csvImports.length:-1,
          csvRows:document.querySelectorAll('#csv-records-body tr').length,
          csvReady:!!document.querySelector('#csv-import-button')&&!document.querySelector('#csv-import-button').disabled,
          csvPath:document.querySelector('#csv-path')?.value||'',
          duplicate:window.__csvDuplicate||false,
          picker:!!document.querySelector('#choose-folder'),
          picking:typeof pickingFolder!=='undefined'&&pickingFolder,
          modal:document.querySelector('#equipment-dialog')?.open||false,
          desktop:document.querySelector('.local-status')?.textContent||''
        })""", self.advance)

    def advance(self, raw):
        self.pending = False
        try:
            state = json.loads(raw or '{}')
            if self.step == 0 and state.get('ready'):
                assert state['picker'] and f'Desktop {VERSION}' in state['desktop']
                assert self.window.windowTitle() == f'LensTracker {VERSION}'
                assert self.qt.applicationVersion() == VERSION
                self.script("document.querySelector('[data-import]').click();")
                self.step = 1
            elif self.step == 1 and self.folder_calls == 1 and not state.get('picking'):
                assert state.get('total') == 0
                assert self.window.application.extensions['scanner'].snapshot()['status'] == 'idle'
                self.script("document.querySelector('[data-import]').click();")
                self.step = 2
            elif self.step == 2 and state.get('total') == 3:
                assert self.folder_calls == 2 and state.get('folder') == str(self.photos)
                scan = self.window.application.extensions['scanner'].snapshot()
                assert scan['duplicates'] == 1 and scan['failed'] == 0
                self.script("showView('equipment');openEquipment(data.equipment.find(e=>e.kind==='camera').id);")
                self.step = 3
            elif self.step == 3 and state.get('modal'):
                self.script("document.querySelector('#new-price').value='2000000';document.querySelector('#equipment-form').requestSubmit(document.querySelector('#equipment-form button[type=submit]'));")
                self.step = 4
            elif self.step == 4 and not state.get('modal'):
                self.script("openEquipment(data.equipment.find(e=>e.kind==='lens').id);")
                self.step = 5
            elif self.step == 5 and state.get('modal'):
                self.script("document.querySelector('#new-price').value='1000000';document.querySelector('#equipment-form').requestSubmit(document.querySelector('#equipment-form button[type=submit]'));")
                self.step = 6
            elif self.step == 6 and state.get('average') == 1000000:
                self.script("showView('overview');document.querySelector('#export-button').click();")
                self.step = 7
            elif self.step == 7 and self.csv_path.exists() and not self.window.downloads:
                assert self.save_called
                rows = list(csv.reader(self.csv_path.read_text(encoding='utf-8-sig').splitlines()))
                assert rows[-1][2] == '3' and float(rows[-1][-1]) == 1000000
                self.window.grab().save(str(self.output / 'desktop.png'))
                assert state.get('saved') == 1
                self.first_scan = self.window.application.extensions['scanner'].snapshot()['started_at']
                self.window.view.reload()
                self.step = 8
            elif self.step == 8 and state.get('ready') and state.get('folder') == str(self.photos) and state.get('saved') == 1:
                assert state.get('total') == 3 and state.get('average') == 1000000
                assert self.window.application.extensions['scanner'].snapshot()['started_at'] == self.first_scan
                self.script("showView('import');document.querySelector('#saved-folders').selectedIndex=1;document.querySelector('#saved-folders').dispatchEvent(new Event('change'));document.querySelector('#import-form').requestSubmit();")
                self.step = 9
            elif self.step == 9:
                scan = self.window.application.extensions['scanner'].snapshot()
                if scan['status'] == 'completed' and scan['started_at'] != self.first_scan and state.get('csvReady'):
                    assert (scan['processed'], scan['skipped'], scan['added'], scan['failed']) == (4, 4, 0, 0)
                    assert state.get('total') == 3 and self.folder_calls == 2
                    self.window.grab().save(str(self.output / 'saved-folders.png'))
                    self.script('requestCsvImport();')
                    self.step = 10
            elif self.step == 10 and self.csv_open_calls == 1 and state.get('csvReady'):
                assert state.get('csvCount') == 0
                self.script('requestCsvImport();')
                self.step = 11
            elif self.step == 11 and state.get('csvCount') == 1 and state.get('csvRows') == 3 and state.get('csvReady'):
                assert self.csv_open_calls == 2 and state.get('total') == 3
                self.script("(async()=>{const result=await api('/api/csv-imports',{method:'POST',body:JSON.stringify({path:document.querySelector('#csv-path').value})});window.__csvDuplicate=result.duplicate;})();")
                self.step = 12
            elif self.step == 12 and state.get('duplicate'):
                self.window.view.reload()
                self.step = 13
            elif self.step == 13 and state.get('csvCount') == 1 and state.get('csvRows') == 3 and state.get('csvPath') == str(self.csv_path):
                assert state.get('total') == 3 and state.get('average') == 1000000
                self.script("showView('csv');")
                self.step = 14
            elif self.step == 14 and state.get('csvReady'):
                self.window.grab().save(str(self.output / 'csv-records.png'))
                self.finish(True, 'Native folder import/cancellation; recursive scan and duplicate exclusion; price editing and CSV export; saved folder restoration and unchanged-file skip; native CSV file picker/cancellation; CSV snapshot import, duplicate skip and reload persistence; existing photo totals/prices preserved; Qt rendering')
        except Exception as error:
            self.finish(False, f'{type(error).__name__}: {error}; step={self.step}')

    def finish(self, success, detail):
        self.timer.stop()
        QFileDialog.getExistingDirectory = self.old_folder
        QFileDialog.getSaveFileName = self.old_save
        QFileDialog.getOpenFileName = self.old_open
        (self.output / 'result.json').write_text(json.dumps(dict(
            success=success, detail=detail, frozen=bool(getattr(sys,'frozen',False)),
            version=VERSION,
            executable=sys.executable, elapsed_seconds=round(time.monotonic()-self.started,2)),
            ensure_ascii=False, indent=2), encoding='utf-8')
        self.window.application.extensions['scanner'].cancel()
        self.qt.exit(0 if success else 1)

# LensTracker 1.2.0 · Windows x64

릴리스: 2026-09-12. [변경 이력](CHANGELOG.md)에 버전별 추가·변경 사항을 기록합니다. 앱의 사이드바와 도움말, 실행 파일 속성에서 버전을 확인할 수 있습니다.

## 실행

프로젝트 폴더에서는 **`Garden_Tools_LensTracker.exe`를 더블클릭하세요.** 이 실행 파일은 `dist\LensTracker\LensTracker.exe`를 실행합니다. `dist` 폴더를 함께 유지해야 하며, 기존 데이터 위치는 `dist\LensTracker\data`입니다. `start.cmd`와 `start.ps1`도 이 실행 파일을 우선 사용합니다. 프로젝트의 `icon.png`는 빌드 시 실행 파일과 앱 창에 사용할 Windows 아이콘으로 변환됩니다.

**`LensTracker.exe`를 더블클릭하세요.** Python, 별도 브라우저, Node.js, WebView2를 설치할 필요가 없습니다. 앱 창과 화면 엔진, Python 실행 환경을 배포 폴더에 포함했습니다. Windows 10/11 64비트를 대상으로 빌드했습니다.

ZIP으로 받았다면 먼저 전체 압축을 풀고 실행합니다. `LensTracker.exe`와 `_internal` 폴더를 함께 유지하세요. 다른 PC로 옮길 때에도 **LensTracker 폴더 전체**를 복사해야 합니다.

## 사용

- **사진 가져오기**, **가져오기 → 폴더 찾아보기** 또는 **Ctrl+O**: Windows 폴더 선택 창을 엽니다. 폴더를 선택하면 즉시 해당 폴더와 모든 하위 폴더의 사진을 재귀 검색하고 등록합니다. 취소하면 검색하지 않습니다. 경로를 직접 입력할 때에는 분석 시작을 누릅니다.
- **장비와 가격**: 신품 가격과 실제 구입 가격을 입력합니다.
- **가져오기 → 열었던 폴더**: 저장된 폴더를 다시 선택합니다. 마지막 폴더는 앱을 다시 실행해도 입력란에 복원됩니다. **폴더 확인 시작**을 누르면 새 사진과 변경된 사진을 확인하고, 변경되지 않은 사진은 저장된 메타데이터를 재사용합니다.
- **CSV 내보내기**: Windows 저장 창에서 파일 위치를 선택합니다.
- **CSV 기록** 또는 **파일 → CSV 기록 가져오기… (Ctrl+I)**: 내보낸 통계 CSV를 선택해 앱의 `data` 폴더에 저장합니다. 저장된 파일 목록에서 카메라·렌즈·조합의 모든 항목을 다시 볼 수 있습니다. 동일한 내용의 파일은 한 번만 저장하며, 가져온 집계가 기존 사진 수를 늘리거나 장비 가격을 변경하지 않습니다. CSV에 없는 사진별 촬영일·EXIF는 복원하지 않습니다.
- **보기 메뉴**: 확대/축소, 기본 크기, 새로고침.
- **파일 → 데이터 폴더 열기**: DB와 실행 로그 위치를 엽니다.
- 종료 시 앱의 내부 서비스도 함께 종료됩니다. 가져오기 중에는 취소 여부를 확인하고 현재 파일 처리를 마친 뒤 종료합니다.

## 기존 데이터

독립 앱은 **실행 파일 옆 `data\lenstracker.sqlite3`**에 사진 메타데이터·장비 가격·열었던 폴더 목록을 저장합니다. 창 설정과 로그도 같은 `data` 폴더에 보관합니다. 앱을 다시 실행하면 원본 사진을 다시 읽지 않고 저장된 통계를 표시합니다. 같은 라이브러리의 중복 실행을 방지합니다.

새 데이터 파일이 없으면 이전 버전의 `%LOCALAPPDATA%\LensTracker\lenstracker.sqlite3`와 창 설정을 우선 복사합니다. 이 프로젝트의 `dist\LensTracker`에서 실행할 때는 프로젝트의 기존 `data\lenstracker.sqlite3`도 이전 후보로 확인합니다. **새 앱의 DB가 이미 있으면 덮어쓰지 않으며 이전 DB도 그대로 남겨둡니다.** 이전 창 설정의 마지막 폴더도 저장된 폴더 목록으로 옮깁니다. 전환 이후 이전 DB와 자동 동기화하지는 않습니다.

업데이트할 때는 `data` 폴더를 유지한 채 실행 파일과 `_internal`을 교체하세요. 제공된 빌드 스크립트도 기존 `data` 폴더를 보존하며 배포 ZIP에는 개인 기록을 넣지 않습니다. 새 PC로 기록까지 이전하려면 앱을 종료한 다음 `data` 폴더도 함께 복사하세요. 원본 사진은 복사하지 않으므로 경로가 달라진 사진은 새 폴더에서 다시 가져올 수 있습니다. 동일 내용 파일은 중복 집계하지 않습니다. 환경변수 `LENSTRACKER_DATA_DIR`를 지정하면 별도 라이브러리를 사용할 수 있으며 자동 이전은 생략됩니다.

## 파일 지원

JPEG/TIFF/PNG/WebP는 기본 지원합니다. RAW/HEIC는 기존과 같이 ExifTool이 필요합니다. ExifTool Windows 배포본의 실행 파일 및 동봉 지원 폴더를 `LensTracker.exe` 옆의 `tools` 폴더에 배치하거나 PATH/`EXIFTOOL_PATH`로 지정합니다. ExifTool은 이번 배포본에 포함하지 않았습니다.

## 소스 실행과 재빌드

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv\Scripts\python.exe desktop.py

powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1
```

결과: 프로젝트 폴더의 `Garden_Tools_LensTracker.exe`, `dist\LensTracker\LensTracker.exe`, `dist\LensTracker-1.2.0-Windows-x64.zip`, 최신 배포본 사본 `dist\LensTracker-Windows-x64.zip`.

버전 원본은 `lenstracker/version.py`입니다. 다음 릴리스에서는 버전·날짜와 `CHANGELOG.md`를 갱신한 뒤 빌드합니다. 앱 버전 표시와 Windows 실행 파일 버전, ZIP 파일명에 같은 버전이 적용됩니다.

독립 앱은 PySide6/Qt WebEngine을 사용하며 기존 HTML UI를 자체 창에 표시합니다. 내부 분석 API는 127.0.0.1의 자동 할당 포트에서 앱이 직접 시작·종료합니다. 사용자가 서버를 실행하거나 브라우저 주소를 입력할 필요가 없습니다. 기존 `app.py`는 개발용 웹 모드로도 사용할 수 있습니다.

배포된 구성 요소의 안내와 소스 링크는 `THIRD_PARTY_NOTICES.md`, 라이선스 파일은 `_internal\licenses`를 확인하세요.

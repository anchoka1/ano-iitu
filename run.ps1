# =====================================================================
#  Запуск ANO IITU в PowerShell.
#    .\run.ps1             — подготовить всё и запустить сервер (+ бот)
#    .\run.ps1 -SetupOnly  — только подготовить (venv, зависимости, .env, база)
#    .\run.ps1 -Test       — подготовить и прогнать тесты
#  Если PowerShell запрещает скрипты, запустите run.bat — он обходит запрет
#  только для этого запуска.
# =====================================================================
param(
    [switch]$SetupOnly,
    [switch]$Test
)

# Ошибки внешних программ (python, pip) проверяем сами через $LASTEXITCODE.
# Режим "Stop" не подходит: в Windows PowerShell 5.1 он считает ошибкой
# любое предупреждение, которое программа пишет в поток ошибок.
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot   # работаем из папки проекта, откуда бы ни запустили

# Кириллица в консоли и в Python
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Say($text) { Write-Host "[ANO IITU] $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "[ANO IITU] ОШИБКА: $text" -ForegroundColor Red; exit 1 }

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

# --- 1. Виртуальное окружение (.venv) --------------------------------
# venv — отдельная «копия» Python для проекта, чтобы библиотеки проекта
# не смешивались с системными.
if (-not (Test-Path $venvPython)) {
    Say "Создаю виртуальное окружение .venv ..."
    $created = $false
    # Пробуем версии по очереди: сначала проверенные, потом любую 3.11+.
    foreach ($ver in @("3.12", "3.11", "3.13")) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py "-$ver" -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) {
                & py "-$ver" -m venv .venv
                if ($LASTEXITCODE -eq 0) { $created = $true; Say "Использую Python $ver"; break }
            }
        }
    }
    if (-not $created -and (Get-Command python -ErrorAction SilentlyContinue)) {
        & python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
        if ($LASTEXITCODE -eq 0) { & python -m venv .venv; $created = ($LASTEXITCODE -eq 0) }
    }
    if (-not $created) {
        Fail "Не найден Python 3.11+. Установите его с https://www.python.org/downloads/ (галочка «Add python.exe to PATH») и запустите снова."
    }
}

# --- 2. Зависимости ---------------------------------------------------
# Ставим заново только если requirements.txt изменился (сравниваем хэш).
$reqHash = (Get-FileHash requirements.txt -Algorithm SHA256).Hash
$stamp = ".venv-installed.txt"
$oldHash = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { "" }
if ($reqHash -ne $oldHash) {
    Say "Устанавливаю зависимости (первый раз 1–3 минуты) ..."
    & $venvPython -m pip install --disable-pip-version-check -q --upgrade pip
    & $venvPython -m pip install --disable-pip-version-check -q -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Fail "Не удалось установить зависимости. Проверьте интернет и запустите снова." }
    Set-Content -Path $stamp -Value $reqHash -Encoding ascii
}

# --- 3. Файл настроек .env -------------------------------------------
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Say "Создан файл .env из .env.example (демо-режим, ключи не нужны)."
}

# --- 4. База данных ---------------------------------------------------
& $venvPython scripts\init_db.py
if ($LASTEXITCODE -ne 0) { Fail "Не удалось подготовить базу данных (см. сообщение выше)." }

if ($SetupOnly) { Say "Подготовка завершена."; exit 0 }

if ($Test) {
    & $venvPython -m pytest
    exit $LASTEXITCODE
}

# --- 5. Порт свободен? -------------------------------------------------
# Если «Вердикт» уже запущен (например, в другом окне или в фоне), второй
# экземпляр не сможет занять порт. Старый экземпляр останавливаем сами,
# чтобы запуск всегда работал с одного нажатия (как перезапуск).
$port = 8000
$portLine = Get-Content ".env" -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\s*PORT\s*=\s*(\d+)' } | Select-Object -First 1
if ($portLine -and $portLine -match '(\d+)') { $port = [int]$Matches[1] }

$busy = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $busy) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
    if ($proc -and $proc.CommandLine -match 'backend\.app\.main') {
        Say "ANO IITU уже запущен (процесс $procId) — останавливаю и запускаю заново ..."
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    } else {
        $name = if ($proc) { $proc.Name } else { "неизвестная программа" }
        Fail "Порт $port занят другой программой ($name, процесс $procId). Закройте её или поменяйте PORT в .env."
    }
}
if ($busy) {
    # ждём, пока Windows освободит порт
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
}

# --- 6. Запуск --------------------------------------------------------
Say "Запускаю сервер. Откройте в браузере: http://localhost:8000"
& $venvPython -m backend.app.main

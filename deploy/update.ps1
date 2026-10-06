<#
ATS v2 — обновление на Windows-сервере (аналог deploy/update.sh).

Запуск (PowerShell от администратора, из каталога с кодом):
    powershell -ExecutionPolicy Bypass -File .\deploy\update.ps1
    powershell -ExecutionPolicy Bypass -File .\deploy\update.ps1 -Branch arena/01a0cdee-ats -Fast
    powershell -ExecutionPolicy Bypass -File .\deploy\update.ps1 -Rollback

Что делает: стоп сервиса/задачи → бэкап ats.db (+ -wal) → git fetch + merge --ff-only →
(тесты, если не -Fast) → старт → проверка /api/v2/health → автооткат при недоступности.

Параметры окружения (по умолчанию как в проде):
    $env:ATS_DATA_DIR   (по умолчанию .\data_v2)
    $env:ATS_PORT       (по умолчанию 9124)
    $env:ATS_SERVICE    (имя службы Windows или Scheduled Task; пусто = запуск процессом)
#>
[CmdletBinding()]
param(
    [string]$Branch = "",
    [string]$Remote = "origin",
    [switch]$Fast,        # без прогона тестов
    [switch]$NoBuild,     # не пересобирать UI (frontend → app\ui)
    [switch]$Rollback
)

$ErrorActionPreference = "Stop"
$AppDir = Split-Path -Parent $PSScriptRoot
Set-Location $AppDir
$DataDir = if ($env:ATS_DATA_DIR) { $env:ATS_DATA_DIR } else { Join-Path $AppDir "data_v2" }
$Port    = if ($env:ATS_PORT) { $env:ATS_PORT } else { 9124 }
$Service = $env:ATS_SERVICE
$LogFile = Join-Path $env:TEMP "ats-update.log"

function Log($m)  { Write-Host "[ats-update] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[ats-update] $m" -ForegroundColor Yellow }

function Invoke-Git([string[]]$args_) {
    $out = & git @args_ 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git $($args_ -join ' ') → код $LASTEXITCODE : $out" }
    return $out
}

function Test-Health {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v2/health" -TimeoutSec 3
        return [bool]$r.ok
    } catch { return $false }
}

function Stop-App {
    if ($Service -and (Get-Service -Name $Service -ErrorAction SilentlyContinue)) {
        Log "Stop-Service $Service"; Stop-Service $Service -Force
    } else {
        $p = Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
             Where-Object { $_.CommandLine -match "app\.run" }
        if ($p) { Log "останавливаем PID $($p.ProcessId)"; $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force } }
        Start-Sleep -Seconds 2
    }
}

function Start-App {
    if ($Service -and (Get-Service -Name $Service -ErrorAction SilentlyContinue)) {
        Log "Start-Service $Service"; Start-Service $Service
    } else {
        Log "запуск python -m app.run (порт $Port)"
        Start-Process -FilePath "python" -ArgumentList "-m","app.run","--host","0.0.0.0","--port","$Port" `
                      -WorkingDirectory $AppDir -WindowStyle Hidden `
                      -RedirectStandardOutput (Join-Path $DataDir "ats.out.log") `
                      -RedirectStandardError  (Join-Path $DataDir "ats.err.log")
    }
    for ($i = 0; $i -lt 30; $i++) { if (Test-Health) { Log "health OK"; return $true }; Start-Sleep -Seconds 2 }
    Warn "health не ответил за 60 с"; return $false
}

if (-not (Test-Path (Join-Path $AppDir ".git"))) { throw "$AppDir — не git-репозиторий" }
if (-not $Branch) { $Branch = (Invoke-Git rev-parse --abbrev-ref HEAD).Trim() }
$Prev = (Invoke-Git rev-parse HEAD).Trim()
$LastFile = Join-Path $AppDir ".deploy-last"

if ($Rollback) {
    $target = if (Test-Path $LastFile) { (Get-Content $LastFile -First 1).Trim() } else { "" }
    if (-not $target) { throw "нет .deploy-last — откат нечем сделать (можно: git reset --hard <sha>)" }
    Stop-App
    Log "откат на $target"; Invoke-Git reset --hard $target
    if (-not (Start-App)) { throw "откатили, но сервис не отвечает — смотрите $DataDir\ats.err.log" }
    Log "готово: сервис на $target"; exit 0
}

Log "каталог: $AppDir | ветка: $Branch | remote: $Remote | данные: $DataDir"
Invoke-Git fetch $Remote --prune | Out-Null
$remoteRef = "$Remote/$Branch"
try { $remoteSha = (Invoke-Git rev-parse $remoteRef).Trim() } catch { throw "ветка $remoteRef не найдена" }
$behind = [int](Invoke-Git rev-list --count "HEAD..$remoteRef")
if ($behind -eq 0 -and $Prev -eq $remoteSha) { Log "уже актуально ($($Prev.Substring(0,[Math]::Min(8,$Prev.Length))))"; exit 0 }
Log "обновляем: $($Prev.Substring(0,8)) → $($remoteSha.Substring(0,8)) ($behind коммитов)"

# незакоммиченное — в stash, чтобы merge не упал
$dirty = Invoke-Git status --porcelain | Where-Object { $_ -notmatch '^\?\?' }
if ($dirty) { Warn "локальные правки → git stash"; Invoke-Git stash push -u -m "ats-update-$(Get-Date -Format yyyyMMdd-HHmmss)" | Out-Null }

# боевую БД из индекса git не пускаем в перезапись
git ls-files --error-unmatch "data_v2/ats.db" 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { Invoke-Git update-index --skip-worktree "data_v2/ats.db" | Out-Null; Log "data_v2\ats.db защищён (skip-worktree)" }

$bak = Join-Path $DataDir "backups"; New-Item -ItemType Directory -Force -Path $bak | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
if (Test-Path (Join-Path $DataDir "ats.db")) {
    Copy-Item (Join-Path $DataDir "ats.db*") $bak -Force
    Log "копия БД: $bak\ats.db ($stamp)"
}

Set-Content -Path $LastFile -Value $Prev
Invoke-Git checkout $Branch | Out-Null
try { Invoke-Git merge --ff-only $remoteRef | Out-Null }
catch { throw "ff-only merge не прошёл (на сервере свои коммиты). Разбор: git log --oneline $remoteRef..HEAD" }
Log "код: $((Invoke-Git rev-parse --short HEAD).Trim())"

if (-not $NoBuild -and (Test-Path (Join-Path $AppDir "frontend\package.json"))) {
    $feChanged = Invoke-Git diff --name-only "$Prev..HEAD" 2>$null | Select-String -Pattern '^frontend/'
    if ($feChanged -and (Get-Command npm -ErrorAction SilentlyContinue)) {
        Log "пересборка UI (npm ci && npm run build)"
        Push-Location (Join-Path $AppDir "frontend")
        try { & npm ci --no-audit --no-fund *>> $LogFile; & npm run build *>> $LogFile }
        finally { Pop-Location }
    } elseif ($feChanged) { Warn "npm не найден — оставляем app\ui из репозитория" }
}

if (-not $Fast) {
    Log "тесты: python -m unittest discover -s tests"
    & python -m unittest discover -s tests *>> $LogFile
    if ($LASTEXITCODE -ne 0) { throw "тесты упали — сервис не трогаем. Лог: $LogFile (откат: -Rollback)" }
    Log "тесты OK"
}

Stop-App
if (-not (Start-App)) {
    Warn "автооткат на $Prev"
    Invoke-Git reset --hard $Prev | Out-Null
    if (Start-App) { Log "откатились, сервис жив" } else { throw "и после отката не жив — $DataDir\ats.err.log" }
}

Log "готово. Было $((($Prev).Substring(0,8))), стало $((Invoke-Git rev-parse --short HEAD).Trim())"
Log "откат: powershell -File .\deploy\update.ps1 -Rollback"

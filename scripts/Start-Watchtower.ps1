<#
.SYNOPSIS
    Summon Cerberus Watchtower - starts the server if needed and opens the dashboard.

.DESCRIPTION
    Idempotent launcher: if the server is already listening on :8765 it just opens
    the browser; otherwise it starts the server hidden, waits for it to come up,
    then opens the browser.

.PARAMETER NoBrowser
    Start/verify the server without opening the dashboard.

.PARAMETER Stop
    Stop the running Watchtower server.

.EXAMPLE
    watchtower            # (via profile function) start if needed + open dashboard
    watchtower -Stop      # shut the server down
#>
param(
    [switch]$NoBrowser,
    [switch]$Stop
)

$Root = Split-Path $PSScriptRoot -Parent
$Url = 'http://127.0.0.1:8765'
$Python = Join-Path $Root '.venv\Scripts\python.exe'

function Test-Watchtower {
    try {
        $null = Invoke-WebRequest "$Url/api/repos" -UseBasicParsing -TimeoutSec 2
        return $true
    } catch {
        return $false
    }
}

if ($Stop) {
    $conn = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        Stop-Process -Id $conn.OwningProcess -Force -Confirm:$false
        Write-Host 'Watchtower stopped.' -ForegroundColor Yellow
    } else {
        Write-Host 'Watchtower is not running.' -ForegroundColor DarkGray
    }
    return
}

if (Test-Watchtower) {
    Write-Host "Watchtower already running at $Url" -ForegroundColor Green
} else {
    if (-not (Test-Path $Python)) {
        Write-Error "venv not found at $Python - run: py -3.12 -m venv .venv; pip install -r requirements.txt"
        return
    }
    if (-not (Test-Path (Join-Path $Root 'frontend\dist\index.html'))) {
        Write-Warning 'frontend/dist not built - dashboard UI will 404. Run: cd frontend; npm run build'
    }
    Write-Host 'Starting Watchtower...' -ForegroundColor Cyan
    Start-Process -FilePath $Python -ArgumentList '-m', 'watchtower' -WorkingDirectory $Root -WindowStyle Hidden

    $deadline = (Get-Date).AddSeconds(15)
    while (-not (Test-Watchtower)) {
        if ((Get-Date) -gt $deadline) {
            Write-Error 'Watchtower did not come up within 15s - run ".venv\Scripts\python.exe -m watchtower" in a console to see the error.'
            return
        }
        Start-Sleep -Milliseconds 400
    }
    Write-Host "Watchtower up at $Url" -ForegroundColor Green
}

if (-not $NoBrowser) {
    Start-Process $Url
}

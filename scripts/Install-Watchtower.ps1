<#
.SYNOPSIS
    Install Watchtower shell integration: icon shortcuts + startup task.

.DESCRIPTION
    Creates "Cerberus Watchtower" shortcuts (Desktop + Start Menu, both
    pinnable to the taskbar) that run the launcher hidden with the Cerberus
    icon, and registers a Task Scheduler logon task that starts the server
    in the background (no window) - same pattern as the content manager.

.PARAMETER NoStartupTask
    Skip the Task Scheduler logon task.

.PARAMETER Uninstall
    Remove the shortcuts and the startup task.
#>
param(
    [switch]$NoStartupTask,
    [switch]$Uninstall
)

$Root = Split-Path $PSScriptRoot -Parent
$Launcher = Join-Path $PSScriptRoot 'Start-Watchtower.ps1'
$Icon = Join-Path $Root 'assets\icon.ico'
$TaskName = 'CerberusWatchtower'
$Pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $Pwsh) { $Pwsh = (Get-Command powershell).Source }

$shortcutPaths = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Cerberus Watchtower.lnk'),
    (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\Cerberus Watchtower.lnk')
)

if ($Uninstall) {
    $shortcutPaths | Where-Object { Test-Path $_ } | Remove-Item -Force
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host 'Watchtower shortcuts and startup task removed.' -ForegroundColor Yellow
    return
}

# --- shortcuts ---
$shell = New-Object -ComObject WScript.Shell
foreach ($path in $shortcutPaths) {
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $Pwsh
    $shortcut.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Launcher`""
    $shortcut.WorkingDirectory = $Root
    $shortcut.IconLocation = "$Icon,0"
    $shortcut.Description = 'Cerberus Watchtower - engineering dashboard'
    $shortcut.Save()
    Write-Host "shortcut: $path" -ForegroundColor Green
}

# --- startup task: server only (no window) at logon ---
if (-not $NoStartupTask) {
    $action = New-ScheduledTaskAction -Execute $Pwsh `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Launcher`" -NoBrowser"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Force | Out-Null
    Write-Host "startup task: $TaskName (server starts hidden at logon)" -ForegroundColor Green
}

Write-Host "`nPin it: right-click the Desktop shortcut -> 'Pin to taskbar'," -ForegroundColor Cyan
Write-Host "or drag it onto the taskbar. Uninstall with -Uninstall." -ForegroundColor Cyan

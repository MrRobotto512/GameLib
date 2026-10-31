# Start Game Shop's tray app at logon (a Scheduled Task, no admin needed for the current user).
#   powershell -ExecutionPolicy Bypass -File tools\install-autostart.ps1            # uses dist\GameShop.exe if built, else the venv
#   powershell -ExecutionPolicy Bypass -File tools\install-autostart.ps1 -Remove
param([switch]$Remove)
$name = "Game Shop"
$root = Split-Path -Parent $PSScriptRoot
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed the '$name' logon task."
    exit
}
$exe = Join-Path $root "dist\GameShop.exe"
if (Test-Path $exe) {
    $action = New-ScheduledTaskAction -Execute $exe -WorkingDirectory $root
} else {
    $pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
    if (-not (Test-Path $pyw)) { Write-Error "Neither dist\GameShop.exe nor .venv\Scripts\pythonw.exe exists." }
    $action = New-ScheduledTaskAction -Execute $pyw -Argument "-m gameshop --tray" -WorkingDirectory $root
}
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Registered '$name' to start at logon for $env:USERNAME. Starting it now..."
Start-ScheduledTask -TaskName $name

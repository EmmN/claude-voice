# Registers a Windows Task Scheduler task that starts the claude-voice listener (and the Windows-side player) at logon.
# Usage (PowerShell): .\register-autostart.ps1 -Distro Ubuntu-24.04
param([Parameter(Mandatory=$true)][string]$Distro)
$action  = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d $Distro -- bash -lc '~/.local/bin/claude-voice start'"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0
Register-ScheduledTask -TaskName "claude-voice listener ($Distro)" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Registered: claude-voice listener ($Distro) at logon. Microphone: Settings > Privacy & security > Microphone > allow desktop apps."

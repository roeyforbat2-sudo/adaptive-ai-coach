# Registers two Windows scheduled tasks for the CURRENT USER (no admin rights needed):
#   "Adaptive Coach - Dashboard"      at log-on: starts the dashboard on localhost:8501
#   "Adaptive Coach - Weekly review"  Saturdays 20:05: posts the weekly review if due
#   "Adaptive Coach - Morning brief"  daily 05:25: posts today's brief (05:30) if due
# Both run the project's venv pythonw.exe (no console window); logs go to .\logs\.
# Re-running replaces the tasks. Remove them with scripts\uninstall_tasks.ps1.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "Not found: $pythonw (create the venv first)." }
$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

# 1. Dashboard at log-on (runs until you sign out; never a second copy).
$dashAction = New-ScheduledTaskAction -Execute $pythonw -Argument "-m coach.launch dashboard" -WorkingDirectory $root
$dashTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$dashSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Adaptive Coach - Dashboard" -Action $dashAction -Trigger $dashTrigger `
    -Settings $dashSettings -Principal $principal -Force `
    -Description "Starts the Adaptive Coach dashboard on http://localhost:8501 (local only)." | Out-Null

# 2. Weekly review every Saturday 20:05; if the PC was off, runs as soon as it is on again
#    (the app only posts until Monday 08:00, and only once per week).
$revAction = New-ScheduledTaskAction -Execute $pythonw -Argument "-m coach.launch weekly-review" -WorkingDirectory $root
$revTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At "20:05"
$revSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Adaptive Coach - Weekly review" -Action $revAction -Trigger $revTrigger `
    -Settings $revSettings -Principal $principal -Force `
    -Description "Posts the Adaptive Coach weekly review to Google Calendar (Saturday 20:05)." | Out-Null

# 3. Morning brief every day 05:25 (posts "Today's brief" for 05:30; also makes sure the
#    daily 17:30 check-in reminder exists). Wakes the PC from sleep for it; if the PC was
#    off, runs when it is on again (the app only posts the brief until 09:00).
$briefAction = New-ScheduledTaskAction -Execute $pythonw -Argument "-m coach.launch morning-brief" -WorkingDirectory $root
$briefTrigger = New-ScheduledTaskTrigger -Daily -At "05:25"
$briefSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Adaptive Coach - Morning brief" -Action $briefAction -Trigger $briefTrigger `
    -Settings $briefSettings -Principal $principal -Force `
    -Description "Posts today's workout brief to Google Calendar at 05:30." | Out-Null

Get-ScheduledTask -TaskName "Adaptive Coach*" | Select-Object TaskName, State

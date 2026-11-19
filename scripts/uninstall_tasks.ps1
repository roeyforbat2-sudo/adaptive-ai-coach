# Removes the two Adaptive Coach scheduled tasks (does not stop a dashboard that is running).
$ErrorActionPreference = "Stop"
foreach ($name in "Adaptive Coach - Dashboard", "Adaptive Coach - Weekly review", "Adaptive Coach - Morning brief") {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        "Removed: $name"
    } else {
        "Not installed: $name"
    }
}

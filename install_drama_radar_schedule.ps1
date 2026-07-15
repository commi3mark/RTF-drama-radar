param(
    [string]$RepositoryPath = "C:\AI\RTF-drama-radar",
    [string]$TaskName = "Drama Radar Workday Scans"
)

$ErrorActionPreference = "Stop"

$BatchFile = Join-Path $RepositoryPath "run_drama_radar_scheduled.bat"
if (-not (Test-Path $BatchFile)) {
    throw "Cannot find scheduled runner: $BatchFile"
}

$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$BatchFile`"" `
    -WorkingDirectory $RepositoryPath

$Triggers = @(
    New-ScheduledTaskTrigger -Daily -At 8:00AM
    New-ScheduledTaskTrigger -Daily -At 12:00PM
    New-ScheduledTaskTrigger -Daily -At 4:00PM
)

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10)

# InteractiveToken means it runs while your Windows account is logged in,
# without storing your password in this script.
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType InteractiveToken `
    -RunLevel Highest

$Task = New-ScheduledTask `
    -Action $Action `
    -Trigger $Triggers `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Collects new Drama Radar material, archives transcripts by publication month, rebuilds indexes, and pushes changes at 08:00, 12:00 and 16:00."

Register-ScheduledTask `
    -TaskName $TaskName `
    -InputObject $Task `
    -Force | Out-Null

Write-Host ""
Write-Host "Installed task: $TaskName"
Write-Host "Schedule: 08:00, 12:00 and 16:00 every day"
Write-Host "Repository: $RepositoryPath"
Write-Host ""
Write-Host "Test it now with:"
Write-Host "Start-ScheduledTask -TaskName `"$TaskName`""

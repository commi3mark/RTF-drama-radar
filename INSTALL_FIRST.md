# Drama Radar scheduled scans and monthly transcript archive

Copy these files into:

```text
C:\AI\RTF-drama-radar
```

They are designed to sit beside your existing:

- `run_drama_radar.py`
- `build_intelligence.py`
- `transcript-manifest.json`
- `transcripts\`

## Files

- `archive_transcripts.py`  
  Moves transcript JSON files into `transcripts\YYYY\MM\` using the video's
  publication date. It updates matching paths in `transcript-manifest.json`.

- `run_scheduled_pipeline.py`  
  Runs collection, monthly filing, intelligence building, then commits and
  pushes only when something changed.

- `run_drama_radar_scheduled.bat`  
  Windows launcher used by Task Scheduler.

- `install_drama_radar_schedule.ps1`  
  Installs three daily runs at 08:00, 12:00 and 16:00.

- `uninstall_drama_radar_schedule.ps1`  
  Removes the scheduled task.

## 1. Test the archive safely

Open Command Prompt in the repository:

```bat
cd C:\AI\RTF-drama-radar
python archive_transcripts.py
```

That is a dry run. Read the report created under `logs\`.

When the proposed moves look correct:

```bat
python archive_transcripts.py --apply
```

Files without a usable publication date are skipped rather than guessed.

## 2. Test the full pipeline

```bat
run_drama_radar_scheduled.bat
```

Check the newest file in `logs\`.

The pipeline expects the collector to be named `run_drama_radar.py`. If yours
has a different name, edit `COLLECTOR_CANDIDATES` near the top of
`run_scheduled_pipeline.py`.

## 3. Install the schedule

Open PowerShell **as Administrator**, then run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd C:\AI\RTF-drama-radar
.\install_drama_radar_schedule.ps1
```

The task will run at:

```text
08:00
12:00
16:00
```

## Important Windows behaviour

The supplied task runs while your Windows account remains logged in. Locking
the PC is fine. Signing out is not.

To run while completely signed out, open Task Scheduler, edit the task, choose
**Run whether user is logged on or not**, and enter your Windows credentials.

The computer must be powered on. `WakeToRun` can wake it from sleep, but not
from a full shutdown.

## Check or start the task

```powershell
Get-ScheduledTask -TaskName "Drama Radar Workday Scans"
Start-ScheduledTask -TaskName "Drama Radar Workday Scans"
```

## Remove it

```powershell
.\uninstall_drama_radar_schedule.ps1
```

## Safety details

- Overlapping runs are blocked by `.scheduled-pipeline.lock`.
- A lock older than three hours is treated as stale.
- Scheduled runs stop after two hours.
- New transcript files are grouped by publication month.
- Existing destination files are never overwritten when their contents differ.
- Files with unknown publication dates remain where they are.
- Git commits are made only when repository contents actually changed.

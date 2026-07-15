DRAMA RADAR — ONE-CLICK LAUNCHER

Copy these two files into:

C:\AI\DRAMA RADAR

Files:
- DRAMA RADAR.bat
- run_drama_radar_all.py

Then double-click:

DRAMA RADAR.bat

It runs, in order:

1. Source collection
2. Transcript download
3. Transcript index
4. Feed linking
5. Monthly archive
6. Validation

Transcript cooldowns are handled by download_transcripts.py. If YouTube is in cooldown, that stage skips cleanly and the rest of the Radar still completes.

OPTIONAL DESKTOP SHORTCUT

1. Right-click DRAMA RADAR.bat.
2. Choose "Send to" > "Desktop (create shortcut)".
3. Rename the desktop shortcut to "Drama Radar".
4. Optional: right-click shortcut > Properties > Run: Minimized or Normal window.

Do not delete the existing run_radar.bat or run_transcripts.bat yet. Keep them as diagnostic launchers.

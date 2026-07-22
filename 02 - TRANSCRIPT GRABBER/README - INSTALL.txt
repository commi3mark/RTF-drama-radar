RTF TRANSCRIPT GRABBER UPGRADE
================================

WHAT THIS ZIP DOES

1. Manual links in PRIORITY TRANSCRIPTS.txt always go first.
2. Direct Commi3 Mark / RTF Radar hits go next.
3. New Adventures of Piper videos and livestreams are permanent priorities.
4. YouTube videos linked by Drama Radar from unfamiliar channels receive a
   sleeper bonus.
5. Other current Radar candidates follow.
6. Remaining capacity slowly fills the Commi3 Mark livestream and video archive.
7. Rumble links are never sent to the transcript retriever.

INSTALLATION

Copy the contents of this folder over the existing
"02 - TRANSCRIPT GRABBER" folder and allow Windows to replace matching files.

The upgrade does not contain a transcripts folder or state folder, so it does
not overwrite transcripts already collected or the grabber's existing state.

The expected surrounding layout is:

  01 - DRAMA RADAR\
  02 - TRANSCRIPT GRABBER\
      GET TRANSCRIPTS.bat
      PRIORITY TRANSCRIPTS.txt
      app\
      config\
      state\                 (created automatically / preserved)
      transcripts\           (created automatically / preserved)

Python requirements already used by the existing grabber:

  pip install youtube-transcript-api yt-dlp

RUNNING IT

Double-click GET TRANSCRIPTS.bat.

The default run limit is 20 candidates. Edit config\transcript-priorities.json
to change it. Every manual priority is included even if that temporarily takes
the run above the configured limit.

PACING

The first pace is approximately 60 seconds between complete video attempts.
After three clean attempts it falls by five seconds, down to a 15-second floor.
A non-block failure such as missing captions does not trigger a cooldown.

If both transcript routes are IP-blocked, the run stops immediately. Repeated
dual blocks use these cooldowns:

  1st: 30 minutes
  2nd: 2 hours
  3rd: 8 hours
  4th: 24 hours
  5th and later: 72 hours

The pace and cooldown survive closing and reopening the BAT.

CHANNEL INVENTORY

Piper and Commi3 Mark channel inventories refresh at most once every 24 hours.
Both the livestream and regular-video tabs are inventoried, deduplicated, and
sorted newest first. Existing transcript IDs are removed before queue selection.

To change a channel handle, edit config\transcript-priorities.json.

QUEUE SHARE

After manual priorities, the normal target is 70% active work and 30% Commi3
archive work. If one side has no candidates, the other side consumes the unused
places.

When at least one outsider sleeper exists, one non-manual place is reserved as
a discovery wildcard so a busy priority week cannot completely bury sleepers.

FILES ADDED BY THIS UPGRADE

  app\adaptive_pacing.py
  app\candidate_queue.py
  app\channel_inventory.py
  config\transcript-priorities.json

FILES REPLACED BY THIS UPGRADE

  app\get_selected_transcripts.py
  app\run_priority_transcripts.py
  app\youtube_retrieval.py
  GET TRANSCRIPTS.bat

The other included app files preserve the index and GitHub publishing pieces
needed by the one-click run.

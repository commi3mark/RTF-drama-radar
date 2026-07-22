TRANSCRIPT NAME REPAIR
======================

Copy the contents of this folder over:

  02 - TRANSCRIPT GRABBER

Allow Windows to replace app\get_selected_transcripts.py and app\build_index.py.
Then double-click:

  FIX RADAR TRANSCRIPT NAMES.bat

The repair will:

* Find the ten placeholder Radar transcript pairs by YouTube video ID.
* Update the title and source stored inside each JSON file.
* Update the title and source header inside each readable transcript.
* Rename both files to the same useful title while preserving the video ID.
* Rebuild transcript-index.json and transcript-manifest.json.

The replacement get_selected_transcripts.py prevents recurrence. Unknown
Radar-linked candidates now resolve their real YouTube metadata before their
transcript filenames and payloads are created.

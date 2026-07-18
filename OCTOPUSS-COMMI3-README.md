# OCTOPUSS — Reiterative Commi3 Mark Profiler

Run `RUN OCTOPUSS - COMMI3 PROFILE.bat` from the repository root.

The profiler now performs up to six controlled passes:

1. Search with canonical aliases and already trusted knowledge.
2. Extract candidate transcription variants, associates, terms, appearances and comic-history passages.
3. Promote only clues that pass independent-video and independent-source thresholds.
4. Rescan with the newly promoted knowledge.
5. Stop when a pass adds neither a new mention nor new trusted knowledge.

Candidate clues never train the detector. Only promoted clues are reused. This prevents one weak match from causing a feedback cascade.

## Outputs

- `octopuss/entities/commi3-mark/profile.json`
- `octopuss/entities/commi3-mark/mention-index.json`
- `octopuss/entities/commi3-mark/candidates.json`
- `octopuss/entities/commi3-mark/evidence.json`
- `octopuss/entities/commi3-mark/trusted-knowledge.json`
- `octopuss/entities/commi3-mark/scan-history.json`
- `octopuss/output/commi3-mark-report.txt`

`trusted-knowledge.json` persists promoted knowledge between later runs. `scan-history.json` records what each pass added and why the run stopped.

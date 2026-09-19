# EARSHOT — 90-second demo

Run `bash scripts/run_demo.sh` from the project root, then open <http://127.0.0.1:8000/>. The launcher validates the local dataset, warms a real recorded hour and pauses at **2025-02-21 20:25:06**. Keep it paused until the proof beat. Replay runs at **15×** for this presentation; the normal configuration remains 600×.

Before the timer, inspect the rule ledger. Undo any earlier demonstration correction that already targets T04/code 20 or T04/code 25; otherwise those events may already be suppressed. Preserve unrelated site rules and the audit journal. Start with the application offline switch on if demonstrating zero application provider calls from the outset. No API key is needed for text or labeled fixture teaching.

The speech fixture buttons and keys 1–5 are **scripted fixture replay**. Their WAVs are locally generated synthetic voices, not human recordings, and their declared transcripts are not speech-recognition results. For a live microphone demonstration, grant permission and test that path before presenting. If it fails, visibly select text or the scripted fixture.

| Time | Presenter lines and actions |
| --- | --- |
| **0–20s — Pain** | “These are real records from 21 wind turbines. This recorded site hour contains 276 alarm entries. Across our three-month sample, ten codes account for 92 percent of the log. The log tells us what occurred; the operator supplies the operating context.” Point to the rate and source attribution. |
| **20–40s — Teach** | “For this demonstration, I’ll mark this generator cut-in pattern as routine on turbine four.” Say or type exactly **“ignore generator cut-in on turbine four”**. If needed, openly select **scripted fixture 1**. “Four matching buffered records change visibility. The model version advances, and the correction is saved locally and can be undone.” Point to the changed rows, version badge and ledger card. |
| **40–65s — Proof** | Press **Resume**, without seeking. “Here is the next recorded cut-in on turbine four. It follows the correction. This different stopping record is still visible.” The matching cut-in at **20:27:36** appears about **10 seconds** after Resume. The untouched **T21, code 3130, Pitch lubrication** stopping record at **20:30:02** follows about **19.7 seconds** after Resume. Pause after it appears. |
| **65–90s — Unplug** | Engage the offline switch, or leave it engaged if already on. “The application’s outbound providers are blocked. I can still teach it here.” Say/type exactly **“ignore fast cut-out of generator on turbine four”**, or openly select **scripted fixture 3**. Show the next model-version change and new ledger entry. “The correction, its examples and the learned state stay on this computer. This is a reversible monitoring prototype; it does not control the turbine.” |

The rate is **276 logged alarm records in (19:25:06,20:25:06]**, not 276 confirmed false alarms or verified operator annunciations. Four records in that hour match the first correction. The stopping flag comes from the supplied description lookup and does not prove that the example represents a hazardous incident. These presenter corrections are demonstration labels, not the dataset’s ground truth.

All timestamps and identities are recorded in [scenario.json](scenario.json), with the alarm-source checksum. Alarm timestamps retain the project’s existing naive UTC assumption. Use normal Resume for the timings above. A proof recovery jump seeks immediately before the event and intentionally changes that timing.

## RECOVERY LINES

**Speech fails:** “The room is noisy, so I’m switching to text. It applies the same local teaching pipeline.” Paste the exact line or visibly choose the labeled scripted fixture. Do not describe known fixture transcripts as live recognition.

**The network dies:** “Local replay and teaching still work. I’m leaving the application offline and using text or a local fixture.” The offline switch blocks new optional application provider calls; it does not disable Wi-Fi or recall a request already sent. Browser recognition may require a remote service, so it is not an offline guarantee.

**The UI freezes:** “I’ll reconnect the console; the correction is stored locally.” Refresh once, inspect the ledger and connection indicator, and resume. For a missed proof, use the proof jump at **2025-02-21T20:27:35**; its first event is immediate. For the untouched stopping example, seek to **2025-02-21T20:30:01**. If the backend itself stopped, use Ctrl+C in its launcher terminal and rerun `bash scripts/run_demo.sh`; only that launcher's server is stopped.

**A prior rule already suppresses the example:** “This correction survived the last run. I’ll undo that demo correction and teach it again.” Use its own Undo button, then return to the prepared beat. Do not delete the journal or unrelated rules.

**The offline correction needs another future example:** Seek to **2025-02-21T20:45:28**. T04’s real fast-cut-out event at **20:45:29** matches the new code 25 correction. The first event after a seek anchors replay immediately.

The source is the [Hill of Towie wind farm open dataset, Zenodo 22662930](https://zenodo.org/records/22662930), released by RES on behalf of TRIG under CC-BY-4.0. The selected local records cover January–March 2025. Measurements and baseline methods are in [baseline_stats.json](baseline_stats.json).

Before presenting: record a backup video while the system works, rehearse with a 90-second timer, check microphone permissions and room volume, disable notifications, and submit before the deadline. Live online speech and LLM calls need configured credentials and were not exercised against providers in this environment.

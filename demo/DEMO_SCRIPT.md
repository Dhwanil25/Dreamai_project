# EARSHOT — 90-second presentation

Start with `bash scripts/run_demo.sh`, then open <http://127.0.0.1:8000/> and [/evidence](http://127.0.0.1:8000/evidence). The launcher warms a real recorded interval and pauses at **2025-02-21 20:25:06**. Presentation speed is **15×**; ordinary replay defaults to 600×. Keep replay paused until the proof beat.

This presentation uses recorded source data and instructions you enter during the demonstration. No prerecorded operator input or automatic fixed-transcript teaching is served. The example lines below are optional live instructions, not hidden UI actions. Inspect existing rules first; undo only your previous matching demonstration corrections if you want an unchanged starting hour.

**Current voice verification:** Nebius parsing and both ElevenLabs website endpoints have succeeded. A real human reference WAV sent to `/listen` returned HTTP 200 and its transcript; `/speak` returned HTTP 200 with fresh MP3 output. The earlier permission failures have been resolved. This does not verify an industrial command captured through your browser microphone: allow microphone access, check the selected device and rehearse that interaction before presenting. Typed input remains available.

| Time | Presenter lines and actions |
| --- | --- |
| **0–20s — Recorded source** | “These are real records from 21 wind turbines, replayed from the supplied dataset. This recorded hour contains 276 alarm entries. The evidence view traces every alarm back to the original ZIP and checks a real sensor snapshot against its original CSV cells.” Show the source hashes and recorded-data label. |
| **20–40s — Live instruction** | Type or speak your own instruction. For this reproducible case, enter **“ignore generator cut-in on turbine four”**. “I supplied that operating-context label now. The response shows the rule, real training examples and the local classifier state before and after.” Inspect the accepted scope, state hashes, model revision and four changed buffered records. |
| **40–65s — Recorded recurrence** | Press **Resume**, without seeking. “The next recorded cut-in on turbine four follows that correction. This different stopping record remains visible.” The T04/code 20 event at **20:27:36** arrives about **10 seconds** after Resume. The untouched **T21/code 3130, Pitch lubrication**, stopping record at **20:30:02** follows at about **19.7 seconds**. Pause after it appears. |
| **65–90s — Local learning** | Engage the offline switch. Enter **“ignore fast cut-out of generator on turbine four”**. “Provider calls are now blocked. This instruction still produces a local rule and trains the local classifier. Here are its actual operation receipt and state change.” Show the local parser source and new rule. “The raw records remain intact, and the correction can be undone.” |

If the demonstration starts offline, keep it offline and explain that the separate Nebius receipt documents a prior successful online call. If it starts online, inspect the actual response to this instruction; a local fallback must be identified as local. A changed version badge alone is insufficient: compare the training-example count and actual classifier-state hashes in the operation receipt.

The selected hour contains **276 logged records in (19:25:06,20:25:06]**. Four match the first example scope. With no prior matching correction, its visible count becomes 272. The instruction is a presenter-supplied label; the source does not establish that these are false alarms. The stopping flag is supplied metadata, not proof of a hazardous incident. Immediate code matching is an explicit rule alongside local classifier training, not LLM fine-tuning.

[scenario.json](scenario.json) records the actual navigation timestamps and source checksum. Normal Resume gives the timings above. Entering a new timestamp in the replay navigator deliberately changes the timeline and anchors the first available event immediately; it is source navigation, not an injection of new events or instructions.

## Evidence to show

`/evidence` combines the source audit performed at this server startup, current classifier state and recent operation/provider records. Check its `verified_at` value; detected source-file changes invalidate the audit until a restart. The source audit compares all 68,891 alarm timestamp/station/code records and duplicate counts against the ZIP, checks description/stopping joins, and matches 185 sampled SCADA cells to the original tables. Its full 50,327,215 SCADA count comes from Parquet metadata; it does not claim to recheck every raw sensor cell. Use `./venv/bin/python scripts/audit_source_evidence.py` for an independent local rerun; restart the server after source changes.

The successful Nebius call used `Qwen/Qwen3-30B-A3B-Instruct-2507` at `api.tokenfactory.nebius.com` and took 3.652 seconds. After the ElevenLabs permissions were corrected, `/listen` returned HTTP 200 in **831.529 ms** for a real human reference WAV, transcribing “I have that curiosity beside me at this moment.” That non-industrial sentence returned `parsed: false` and left learning unchanged. `/speak` returned HTTP 200 in **780.125 ms** with a **79,038-byte MP3**; its provider request ID was `AShN6GPZSPhe1VkahNjB`. The local receipt is `data/processed/website_voice_verification.json`. See [current live verification](../docs/LIVE_VERIFICATION.md) and `/evidence` for recorded provider outcomes; these endpoint checks do not substitute for a microphone-to-teaching rehearsal.

## RECOVERY LINES

**Speech fails:** “The provider has not accepted this audio, so I’m typing the instruction. You can see which path actually handled it.” Enter the instruction yourself. There is no prerecorded substitute or fixed-transcript shortcut.

**Network or provider fails:** “This request fell back to the local parser,” only if its receipt says so. Otherwise say, “I’ll switch the application offline and enter the instruction locally.” The switch blocks new application provider calls; it does not disable the computer's Wi-Fi or recall a request already sent.

**UI disconnects:** “I’ll reconnect the console; accepted corrections are stored locally.” Refresh, inspect the connection state and ledger, and resume. If the backend stopped, Ctrl+C its launcher and rerun it. Preserve unrelated rules and the journal.

**You missed the recurrence:** Enter **2025-02-21T20:27:35** in the replay navigator. For the untouched stopping example, use **2025-02-21T20:30:01**. For a later T04/code 25 event after the offline correction, use **2025-02-21T20:45:28**. The next actual events occur at 20:27:36, 20:30:02 and 20:45:29 respectively. A seek resets recent observations while retaining teaching.

**A previous correction already matches:** “This saved correction survived the previous run. I’ll undo my demonstration rule and enter a new instruction.” Use that rule’s Undo button, then restart the prepared interval if needed. Never remove unrelated site knowledge to make the counter look better.

Source: [Hill of Towie wind farm open dataset, Zenodo 22662930](https://zenodo.org/records/22662930), RES on behalf of TRIG, CC-BY-4.0. Selected records cover January–March 2025. Alarm timestamps retain the existing unverified naive-UTC assumption.

Before presenting, rehearse the actual text/microphone path, confirm browser microphone permission and device capture, inspect the visible evidence, check projector readability, record a backup video of the real run and disable notifications.

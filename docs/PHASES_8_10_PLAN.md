# Remaining implementation plan

Requested on 2026-09-19: plan and execute the remaining phases. Phases 0–7 are already committed; this work covers Phases 8–10 in that order.

1. **Console:** one self-contained vanilla HTML/CSS/JavaScript page, authoritative alarm snapshots and WebSocket updates, bounded feed, workload chart, model revision, rule ledger/undo, text teaching, and offline/replay controls. Verify real source data and browser interactions.
2. **Voice:** optional ElevenLabs transcription and synthesis with bounded requests, a hard offline gate, local cached confirmations, verified on-device browser recognition where available, and explicitly labeled scripted recording fallbacks. Verify provider contracts with mocks; real microphone and paid-provider checks depend on local permissions and credentials.
3. **Demo and resilience:** select genuine timestamps for the four beats, prewarm without teaching or rewriting history, provide a single-command launcher, rehearse teach/recurrence/undo/offline behavior, and rewrite setup, provenance, presentation, and submission documentation. Run the full regression suite and browser checks before committing and pushing.

Acceptance: a fresh local demo opens within 60 seconds with existing processed data; the UI reports authoritative model revisions and corrected verdicts; scoped future recurrences are hidden while an unrelated stopping alarm remains visible; teaching and cached audio work offline; failures produce recoverable messages; and all changes are reviewable in Git.

Accuracy constraints: 12 alarms/hour is a workload reference, not a universal operator safety limit. Scripted recordings are not presented as live transcription. Offline browser recognition requires confirmed local processing; cloud-backed browser speech cannot silently bypass the switch. The actual local source and measured limitations govern the demo claims.

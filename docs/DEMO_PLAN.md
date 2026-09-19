# EARSHOT implementation and demo plan

## The central demonstration

An operator explains a nuisance event in ordinary language. EARSHOT turns that feedback into inspectable local state that changes its handling of a subsequent matching event. The knowledge survives an application restart and is usable with Wi-Fi off.

The uploaded dataset determines the asset, signal, time window, and event used in the demonstration. Do not assume it contains pressure readings, startup events, or confirmed failures.

## Proposed architecture

Keep the prototype on one computer: a browser interface served by a loopback-only Python application, local files for imported sensor data, and SQLite for operator corrections and audit history. Choose the detector and a local speech engine after inspecting the dataset and presentation hardware. This is a proposed architecture, not an installed stack.

The initial interface needs three connected views:

| View | Operator action | Evidence shown |
| --- | --- | --- |
| Monitor | Replay a dataset interval and select an event | Sensor values, timestamps, detector output, and data source |
| Teach | Speak or type an explanation and inspect the proposed correction | Transcript, affected asset and signal, operating conditions, and bounded scope |
| Site knowledge | Inspect, revoke, or replay a correction | Original event, explanation, saved change, version, and subsequent matches |

## Correction contract

Each correction records the site or dataset identity, asset and sensor where available, original event reference, transcript or typed explanation, matching conditions, creation time, and active or revoked status. Future matching decisions reference the correction that influenced them.

The interface ties feedback to a selected event to avoid guessing which alarm the operator means. Ambiguous instructions remain drafts until clarified. Reject blanket instructions to hide everything. An original event remains available for review even when its operator-facing notification is deprioritized.

Begin with a small, inspectable mechanism supported by the data. If the initial version uses rules, label those as local correction rules. Only claim model learning when feedback actually changes learned parameters or examples used in subsequent inference. A site-specific learned component should be saved and reloaded locally.

## Dataset-dependent decisions

- Determine whether the source contains a continuous time series, event records, or another data type.
- Establish which timestamps, identifiers, measurements, units, and labels are available.
- Find repeatable normal patterns and candidate unusual events without assuming every statistical outlier is a fault.
- Use confirmed labels when available. Otherwise describe events as detector candidates or operator-labeled examples.
- Reserve later events for the before-and-after replay. Avoid reporting performance on the same example used to teach the correction.
- If a needed event must be injected, label it as a synthetic test event in both the app and presentation.

## Demo sequence

1. Show the dataset source and selected replay interval.
2. Play an interval with a recurring nuisance candidate and a distinct anomaly candidate.
3. Select the nuisance event, record the explanation locally, and review the resulting correction.
4. Apply the correction and show the saved site knowledge entry.
5. Replay a later matching event and explain why its handling changes.
6. Replay the distinct anomaly and show that it still surfaces.
7. Restart the application with Wi-Fi disabled and repeat the relevant replay.
8. Revoke the correction and demonstrate that the prior behavior is restored.

## Acceptance criteria

- The application loads and replays the supplied dataset without contacting an external service.
- Every displayed event can be traced to source data or an explicitly marked synthetic test.
- Speech is transcribed using a locally installed engine and local model files. Typed feedback is a development fallback, not proof of offline voice support.
- Applying a correction changes a later matching decision and provides a visible reason.
- A nonmatching event and any configured critical-limit event remain visible.
- Saved knowledge survives a restart and can be revoked.
- Offline testing covers loading the UI, speech, inference, correction storage, restart, and replay. Inspect outbound network behavior rather than relying on an "offline" badge.
- Any claimed improvement comes from counted events with disclosed labels and evaluation boundaries.

## Relationship to Rain

Keep dataset adapters, event records, correction records, and detector interfaces separate so the prototype can later be evaluated for reuse by Rain. Specific integration points depend on Rain's architecture and are not yet established in this workspace.

# Phase 4 — baseline evidence

Phase 4 implements the six planned metric functions and a local report/JSON builder. It reads the real Phase 3 alarm Parquet and turbine metadata, preserving every source row in the primary results. It does not implement detection, replay, teaching or suppression.

## Measured results

| Measure | Result |
|---|---:|
| Source records | 68,891 |
| Observed interval | 2025-01-01 00:21:00 → 2025-03-31 21:39:54 |
| Observed hours | 2,157.315 |
| Site-wide logged records/hour | **31.933677** |
| Ratio to 12/hour operator-console reference | **2.661140×** |
| Mean per known turbine/hour | **1.520607** |
| Top ten code share | **92.022180%** |
| Codes needed to reach 80% | **2** |
| Distinct codes | 192 |
| Qualifying ten-minute bins | **2,263 / 12,944** |
| Consecutive flagged-bin runs | 849 |
| Longest run | **50 bins / 500 minutes** |
| Worst bin | **567 records**, March 31, 07:30–07:40 |
| Pairwise co-occurrence groups | **38**, including **21** with limited evidence |
| Stopping / non-stopping / unknown records | 639 / 55,695 / 12,557 |

All 21 metadata turbines are represented. Two station-91 records remain in site-wide totals; they are excluded from the known-turbine numerator and station 91 is not counted as a twenty-second turbine. Individual turbine rates and the turbine mean use the same observed site interval.

The top two codes are 25 (Fast cut-out of generator, 27,858 records) and 20 (Large generator Cut-in, 27,837 records). Their concentration does not establish that the events are false alarms or safe to suppress.

## Chosen methods

- Flood bins are fixed, clock-aligned, half-open ten-minute intervals. More than ten records qualifies; exactly ten does not. Consecutive flagged bins form runs. The longest run is March 16, 07:50–16:10, containing 1,684 records. These runs do not implement a standards-defined recovery threshold.
- Co-occurrence is measured on the same station within inclusive ±60 seconds. A record counts once per candidate code even if several candidate events are nearby. Both directional supports must reach 80%.
- Groups are maximal cliques, requiring every pair to qualify. All groups are reported; any member with fewer than 20 records flags limited evidence. Membership can overlap, so group counts and support are not independent.
- Primary counts retain all source duplicates. A separate comparison removes 348 repeated timestamp/station/code keys: 68,543 records, 31.772365/hour, 92.054623% top-ten share, and 2,251 qualifying bins. All 38 cluster memberships remain unchanged.
- The full report follows the phase prompt's required section order and includes all groups, interpretations, top twenty codes, stopping categories and five computed slide statements. JSON keeps full numeric precision and every qualifying window, run and pairwise-support numerator/denominator.

## Interpretations and limitations

Three groups have documented descriptions that support a limited interpretation:

| Codes | Evidence | Supported association |
|---|---|---|
| 29, 10105 | 76/76 events matched in each direction | Cable untwisting |
| 30, 8000 | 77/77 in each direction | Excessive-wind shutdown |
| 168, 3130 | 353/355 and 353/362 | Pitch lubrication |

The other members' meanings remain undocumented, and association is not proof of one physical cause. Those three documented anchor codes have stopping classifications; temporal association is not permission to suppress them. Every other group's physical meaning is explicitly unverified in the full report.

The anticipated icing family was not discovered in this period. Code 8230 (Ice detection: Low torque) has five records and no qualifying mutual-support group. No family or sample was invented to satisfy the prompt's expectation.

The peak ten-minute bin deserves inspection: 546 of its 567 records have unknown stopping classification; it spans all 21 known turbines plus station 91. It has no repeated timestamp/station/code keys, and its triggering condition remains unverified. Twenty-one rare groups also need caution: a perfect ratio from very few observations is weak evidence.

The **12/hour operator-console reference** is an approximate average workload benchmark. It is not a universal safety limit. The dataset does not specify annunciation, operator assignments or staffing, so these rates cannot establish actual operator overload or compliance. Per-turbine rates are asset diagnostics. [ISA reference](https://www.isa.org/intech-home/2016/may-june/features/getting-the-most-from-your-safety-alarms)

The source timezone is unverified; Phase 3's naive-UTC interpretation remains an assumption. The observation span runs between actual first/last alarm timestamps, rather than assuming the entire calendar quarter was observed.

## Outputs and reproduction

```bash
./venv/bin/python scripts/compute_baseline.py
cat data/processed/baseline_report.md
./venv/bin/python -m json.tool demo/baseline_stats.json
./venv/bin/pytest -q
```

The full local report is `data/processed/baseline_report.md`. Aggregate machine-readable results are tracked in [`demo/baseline_stats.json`](../demo/baseline_stats.json); raw data stays Git-ignored. JSON schema version 1 groups provenance, observed period, counts, rates, Pareto, top codes, clusters, floods, stopping split, duplicate sensitivity, checks, caveats and slide numbers.

The alarm input SHA-256 is `9b99821be20e5758765e206107c0bd0e4a4225d79ac860908937320fc192220e`. Repeated runs produced byte-identical report/JSON outputs and left the source unchanged. The build passed with socket connection attempts blocked and when launched from an unrelated working directory. Both outputs are fully prepared and validated before replacement; tests cover failures before publication, including failure while staging the second file.

All **78 tests passed in 3.78 seconds**: 27 metric checks, 14 baseline checks and 37 existing configuration, ingestion and server checks. One existing Starlette/AnyIO deprecation warning remains. Live checks confirmed HTTP 200 at `/`, `/health` and `/openapi.json`, and intentional HTTP 501 at `/stats`.

Missing or empty inputs, invalid domains, zero exposure, conflicting labels, non-finite JSON and implausible headline values stop publication. No fallback data or silent clipping exists. Meaningful tests use unchanged real records, including boundary cases, rare groups, station 91 and repeated events; data-dependent tests skip explicitly when local evidence is unavailable.

The backend's existing scaffold label now reports Phase 4. Home and health still return 200; the unfinished `/stats`, teaching and policy APIs retain their intentional 501 responses. Phase 5 has not started.

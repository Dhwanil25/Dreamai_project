# Research behind the EARSHOT demo

Reviewed September 19, 2026. The primary supplied source is **EARSHOT — Market Research Dossier & Model Rebuild.md**, dated September 19, 2026. Earlier DOM/RAIN material provides background. This note distinguishes publisher estimates, planning assumptions and demonstrated project measurements. The original documents remain unchanged and are not republished here; their instructions do not trigger a model or backend rebuild.

## Supplied material and provenance

Dates below are stated in the documents. An undated document is not assigned a publication date from its file modification time. SHA-256 identifies the exact local bytes reviewed; it does not certify the document's claims.

| Source filename and local origin | Stated date | SHA-256 |
| --- | --- | --- |
| **`EARSHOT — Market Research Dossier & Model Rebuild.md` — `Downloads/`** | **September 19, 2026** | `849399aeba3c48ee2b22ff5adf9ace34f2ed7d56fad41988f70ca9c4aee23622` |
| `DOM_problem_market_report.md` — `Downloads/` | Prepared August 23, 2026 | `b86bdcfc04c2510670317463ed30b8161cc70430a11649bf3afe5893b794fe82` |
| `use-case-research.md` — `Desktop/DOM/rainlab_portfolio/docs/` | Sources reviewed September 2026 | `1cc600840f3382a10067a589792045a1961964f0d534aacf18f00b483e243692` |
| `RAIN-investor-overview.txt` — `Desktop/DOM/doc/site/` | September 2026 | `9bc80c2d7586be439c21008e87e8da7cd038f02ccab916034f457b71fc23335f` |
| `REPORT.md`, “DOM and the AI infrastructure bottleneck” — `Desktop/DOM/doc/research/machine-age/` | Undated report; source-access notes include September 9, 2026 | `e333990d6b0c4fc1bbdf12420b4ffc994d935f85ac1e5d0cf10848b8a78d9988` |
| `rain-architecture-playbook.md` — `Downloads/` | Undated | `14365c6ed62e89eb95abc2f55e9b0f5c8438061b00d673434d75670eea8309c5` |
| [PITCH.md](PITCH.md) — this repository's `docs/` | Undated, original supplied pitch preserved verbatim | `6f86f1edf38a825b933715e635f65a475a1748adde9095dde3c7fa185160a688` |

The EARSHOT dossier proposes a wind beachhead, adjacent markets and a product expansion ladder. It distinguishes broader markets from the current product and labels pricing/site-capture assumptions, but some of its category and deployment-readiness conclusions need the qualifications below. The older DOM market report concerns owner-controlled operating records. RAIN's investor overview identifies simulator evidence and a future field pilot; the other background research treats buyers, savings and operational usefulness as questions requiring customer evidence.

## Publisher estimates checked for the market tab

The five public publisher pages below were opened and checked on September 19, 2026. These are the publishers' market estimates and forecasts, not official economic measurements, observed EARSHOT revenue or independent validation of the publishers' methods. Full paid reports were not purchased. TBRC labels 2025 its estimation base and 2026–2030 its forecast period; calling 2026 figures “measured actuals” would overstate the evidence.

| Publisher category | 2026 estimate | Forecast | Forecast CAGR | Checked publisher source |
| --- | --- | --- | --- | --- |
| Alarm Management System | $8.70B | $10.87B in 2030 | 5.7%, 2026–2030 | [The Business Research Company](https://www.thebusinessresearchcompany.com/report/alarm-management-system-global-market-report) |
| AIOps | $14.44B | $41.60B in 2030 | 30.3%, 2026–2030 | [The Business Research Company](https://www.thebusinessresearchcompany.com/report/aiops-global-market-report) |
| Clinical Alarm Management | $4.14B | $9.42B in 2030 | 22.8%, 2026–2030 | [The Business Research Company](https://www.thebusinessresearchcompany.com/report/clinical-alarm-management-global-market-report) |
| Aircraft Health Monitoring System | $5.80B | $7.73B in 2030 | 7.4%, 2026–2030 | [The Business Research Company](https://www.thebusinessresearchcompany.com/report/aircraft-health-monitoring-system-global-market-report) |
| Anti-Money Laundering Software | $4.27B | $10.74B in 2035 | 10.83%, 2026–2035 | [Precedence Research](https://www.precedenceresearch.com/anti-money-laundering-software-market) |

The first category is broader than the dossier's “industrial alarm management” shorthand. TBRC includes hardware, software and services across industrial, commercial and residential applications. The $8.70B is not an industrial alarm-software-only estimate. Its 5.7% is the stated 2026–2030 forecast CAGR; the page separately states 6.8% for its 2025–2026 growth discussion. [TBRC category definition, segmentation and forecast](https://www.thebusinessresearchcompany.com/report/alarm-management-system-global-market-report).

The arithmetic **$37.35B = $8.70B + $14.44B + $4.14B + $5.80B + $4.27B** is correct. Label it **“sum of five adjacent-market estimates; overlap not removed.”** Different definitions, potentially overlapping revenue and different publisher methods prevent treating it as a researched, non-overlapping EARSHOT TAM. Do not apply one category's CAGR to that sum.

The dossier's **$12.84B “PAM”** is the alarm-management plus clinical-alarm subtotal. It is not an established market that this prototype can serve today. Shared telemetry/alert structure does not make a recorded-wind demo ready for patient care. Clinical integration, independent safety evaluation and deployment suitability remain unestablished. Aircraft, IT operations and financial services likewise need their own data, workflows, evaluation and product work. The current demonstrated focus is recorded wind alarms and operator-scoped correction.

## Editable planning model: assumptions and arithmetic

Use the dossier's inputs as editable **planning defaults**, with their provenance and dates visible. Capacity/count inputs in this subsection were transcribed from the dossier; their original GWEC, USGS and WindEurope pages were not independently rechecked in this review. The approximate 100 MW average is a modeling assumption generalized from U.S. figures of different vintages, not an observed global mean.

| Input | Supplied default | Evidence status |
| --- | --- | --- |
| Global wind capacity | 1,299 GW, end 2025 | Dossier attributes to GWEC; a capacity denominator, not a site count. |
| Average capacity per project | 100 MW | Assumption; conversion to independently purchasable sites is not validated. |
| Annual price per site | $40,000 | Proposed Fleet tier; no demonstrated willingness to pay. |
| U.S. wind projects | Approximately 1,500, January 2022 | Dossier attributes to USGS; retain the old as-of date. |
| European onshore capacity | 265 GW, end 2025 | Dossier attributes to WindEurope; not an observed facility count. |
| Five-year share captured | 2% | Sales assumption; not an observed conversion rate or forecast commitment. |

```text
Global modeled projects = 1,299 GW × 1,000 MW/GW ÷ 100 MW = 12,990
Global annual opportunity scenario = 12,990 × $40,000 = $519,600,000

U.S. + Europe modeled projects = 1,500 + (265 × 1,000 ÷ 100) = 4,150
Modeled customers at 2% = 4,150 × 0.02 = 83
Modeled annual recurring revenue = 83 × $40,000 = $3,320,000
```

“SAM scenario” and “SOM scenario” can retain the dossier's terminology if the assumption label is equally prominent. These calculations do not filter for data access, addressable owners, integration feasibility, eligibility, procurement or sales capacity. They do not establish 12,990 reachable customers or 83 future contracts. Institution totals across hospitals, banks and factories also cannot become qualified sites solely by multiplication with a licence price.

The dossier's five-year sector ramp, 90% retention and 1.5×/2×/3× expansion-price multipliers are business assumptions. For example, a 3× multiplier on $3.32M gives $9.96M arithmetically; it does not prove customers will buy that scope or that expansion has no acquisition/support cost.

## From research themes to this project

| Supplied theme | EARSHOT implementation | Evidence boundary |
| --- | --- | --- |
| Operators know the local operating context. | An operator's typed instruction or actual transcript can become a validated, asset-scoped rule and local classifier training examples. | Operator corrections are labels; this dataset does not independently establish which alarms are false. Ambiguous or unsupported instructions can produce no rule. |
| Operating records should remain inspectable by the owner. | Original source files remain intact. The console exposes source reconciliation, accepted corrections, model-state hashes and operation receipts. Undo reconstructs learning from remaining batches. | Local records are not claimed to be immutable, a certified compliance record or proof against a malicious administrator. Receipt-integrity status reports known gaps. |
| Useful analysis should continue without a cloud dependency. | Recorded replay, local text parsing, detectors and learning operate locally. The offline switch blocks new application provider calls. | Online voice and parsing can send audio/text to ElevenLabs/Nebius. The switch does not disable Wi-Fi or recall requests already sent. |
| Renewable operators need actionable context, not merely more measurements. | The demo links an instruction to the exact affected records, a later recorded recurrence and an untouched stopping record from another scope. | No field maintenance outcome, avoided incident, downtime reduction or customer ROI has been established. |

Proposed first users are shift operators and reliability/O&M engineers. Proposed buyers are renewable asset owners or managers responsible for maintenance and asset performance. These are customer hypotheses from the supplied research, not existing EARSHOT customer relationships. A useful first pilot would test one recurring alarm class, operator agreement, investigation effort, missed-important-event risk and installation/support effort against the current workflow.

## Measured project evidence

The January–March 2025 Hill of Towie selection contains **68,891 alarm records**, **21 known turbines** and **50,327,215 long-form SCADA rows**. Its site log averages **31.933677 records/hour**; ten alarm codes account for **92.022180%** of records. These are dataset calculations, not an industry average or a count of verified nuisance alarms. Two records from unmapped station `91` remain in the site totals. See [baseline calculations](../demo/baseline_stats.json), [methodology](PHASE_4_REPORT.md) and [dataset attribution](../README.md#measured-dataset).

The source audit reconciles all alarm timestamp/station/code records and multiplicities with the original ZIP, verifies description/stopping joins and checks **185 selected SCADA cells** against their original CSV values. It does not reconcile every SCADA cell in the quarter. The prepared hour contains 276 logged alarms; the example T04/code 20 correction affects four buffered records when no prior matching correction exists. That demonstrates scoped behavior, not a general alarm-reduction percentage.

The running `/evidence` endpoint shows the startup source audit, its freshness status, current classifier state and actual operation receipts. Simple code rules change visibility explicitly while the local River classifier also trains. The language model is not fine-tuned. Live provider verification and its limits are documented separately in [LIVE_VERIFICATION.md](LIVE_VERIFICATION.md).

## Two checked primary-source anchors

**Wind operations and connectivity.** DOE's May 21, 2024 article describes an Idaho National Laboratory study of wind cybersecurity and reports attacks affecting wind organizations or facilities in at least seven countries over the preceding decade. This supports discussing connectivity and third-party access as operating concerns; it does not establish that EARSHOT prevents cyberattacks. [DOE — Protecting Wind Energy Systems From Cyberattacks](https://www.energy.gov/cmei/systems/articles/protecting-wind-energy-systems-cyberattacks), checked September 19, 2026.

**Historical renewable-sector scale.** EIA reports approximately **238 billion kWh of U.S. solar electricity generation in 2023**, with approximately **69% from utility-scale PV**. The page was updated July 12, 2024 and labels its 2023 U.S. data preliminary. These figures describe historical energy generation, not current installed capacity, addressable buyers or EARSHOT revenue. [EIA — Solar explained: Where solar is found and used](https://www.eia.gov/energyexplained/solar/where-solar-is-found.php), checked September 19, 2026.

Neither organization endorses EARSHOT or RAIN. No regulatory obligation is inferred from these sources.

## Relationship to RAIN

EARSHOT explores a correction interface and evidence trail that could inform RAIN's broader local-learning product: operators contribute contextual labels, decisions stay scoped, learning persists locally and changes can be inspected or undone. This is a proposed future contribution, not a shipped RAIN integration.

The supplied RAIN architecture playbook describes a broader OTel/gateway/TimescaleDB/Iceberg/Grafana/MLflow design. EARSHOT currently uses local Parquet replay, Python/FastAPI, River, local journals and a browser console. Its architecture diagram should show those implemented components. Equipment adapters, deployment boundaries, integration contracts, shared model lifecycle and field validation would require additional work before incorporation into RAIN.

## Claims excluded from the website's factual copy

- The older DOM report's **$30–60k per site**, **200+ new sites/year** and broad dollar opportunity are unverified estimates. The newer dossier is the primary research input; even its correctly sourced category totals do not establish EARSHOT's serviceable market.
- Regulatory obligations, deadlines, penalties, certification shortcuts and “local architecture is the compliance answer” conclusions were not verified in this review. They are not EARSHOT capabilities or universal buying requirements. The older report's “12 primary sources” description should not be repeated: many linked sources are secondary trade publications.
- Assertions that no competitor combines local processing and data custody, or that particular competitors cannot work offline, require separate product and contract verification.
- The pitch's universal alarm-rate/80–20 statements, consulting-price range and promised correction latency are not independently established by these documents. The demo instead presents its measured dataset and actual request outcomes.
- The dossier's “shipping today,” installation without safety review, uniquely available prediction capability and “strictly weaker” competitor characterizations are not demonstrated here. Its external ECG/AML statistics and PagerDuty comparisons were not rechecked in this bounded review. An operator's suppression label is not automatically a confirmed false alarm, and it does not prove future failure-prediction accuracy.
- No field deployment, secured customer, signed pilot partnership, certified safety benefit or proven economic return is claimed. The original “never phones home” wording is qualified by the implemented online/offline behavior above.

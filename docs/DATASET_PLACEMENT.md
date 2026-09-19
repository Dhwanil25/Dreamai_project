# Local dataset placement

Keep the supplied archives intact in the repository's `data/raw/` directory:

```text
data/raw/
  2024.zip
  2025.zip
  2026.zip
  Hill_of_Towie_ShutdownDuration.zip
  Hill_of_Towie_alarms_description.csv
  Hill_of_Towie_turbine_metadata.csv
  Hill_of_Towie_turbine_fields_description.csv
  Hill_of_Towie_tables_description.csv
```

The current configuration reads January–March from `2025.zip`. The other year archives and shutdown-duration archive are retained for later analysis; they are not silently combined with this selection. Discovery uses all four CSV companions. Nothing requires moving or deleting the original Downloads files.

Run the discovery/build commands in the README. They write `data/processed/alarms.parquet`, `scada.parquet`, schema/ingestion reports, and measured baseline/vocabulary artifacts. Data remains ignored by Git. The local `rules.jsonl` journal belongs to the site and must be preserved across restarts; the demo launcher does not delete it.

The raw source is the [Hill of Towie wind farm open dataset](https://zenodo.org/records/22662930), released by RES on behalf of TRIG under CC-BY-4.0. Repository demo JSON contains selected source records and aggregate measurements with attribution; it is not a replacement for the full dataset.

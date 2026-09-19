"""Phase 2 placeholder for building a local processed dataset."""


def main() -> int:
    """Build the configured dataset using validated source-column mappings.

    Inputs are the project configuration, discovered schema, and local raw
    files. A later phase will write processed data under the configured
    processed-data directory and return a zero exit status on success. This
    scaffold performs no ingestion, transformation, or filesystem writes.
    """
    raise NotImplementedError("Dataset construction is not implemented in Phase 2.")


if __name__ == "__main__":
    raise SystemExit(main())

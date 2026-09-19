"""Phase 2 placeholder for local dataset schema discovery."""


def main() -> int:
    """Inspect the configured raw dataset and report its observed schema.

    Inputs are the project configuration and files in its raw-data directory.
    A later phase will return a zero exit status after producing local schema
    findings; any saved mappings must come from the inspected dataset. This
    scaffold reads no dataset files and writes no reports or configuration.
    """
    raise NotImplementedError("Schema discovery is not implemented in Phase 2.")


if __name__ == "__main__":
    raise SystemExit(main())

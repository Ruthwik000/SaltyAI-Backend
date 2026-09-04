"""Phase 1 smoke test for the INCOIS ERDDAP data layer."""

from erddap_client import ERDDAPClient, ERDDAPError


def main() -> None:
    # The INCOIS endpoint currently presents a certificate chain unavailable
    # in some minimal environments. Production callers should keep the secure
    # default (verify_ssl=True); this smoke test opts out only to exercise the
    # public metadata endpoint in that environment.
    client = ERDDAPClient(verify_ssl=False)
    try:
        status = client.check_connection()
        print(f"Connected to {status['base_url']} ({status['dataset_count']} datasets)")
        datasets = client.list_datasets()
        if not datasets:
            raise ERDDAPError("ERDDAP returned no datasets")
        for dataset in datasets:
            print(f"{dataset['dataset_id']}: {dataset['title']}")
        selected = next(
            (dataset["dataset_id"] for dataset in datasets if dataset["dataset_id"] != "allDatasets"),
            datasets[0]["dataset_id"],
        )
        metadata = client.get_dataset_metadata(selected)
        print(f"\nMetadata for {selected}")
        print(f"Title: {metadata['title']}")
        print(f"Dimensions: {metadata['dimensions']}")
        print(f"Time coverage: {metadata['time_coverage']}")
        print("Variables:")
        for variable in metadata["variables"]:
            print(f"  {variable['name']} | dimensions={variable['dimensions']} | units={variable['units']}")
    except ERDDAPError as exc:
        print(f"ERDDAP test failed: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

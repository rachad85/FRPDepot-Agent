from gla_sync_target_monitor import run_monitor


if __name__ == "__main__":
    raise SystemExit(
        run_monitor(
            family="FRP Manway",
            target={"kind": "variation", "product_id": 1397, "variation_id": 1409},
            expected_sku="MWDN50005PSI411",
            state_stem="gla_sync_manway_1409",
        )
    )

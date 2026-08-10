from gla_sync_target_monitor import run_monitor


if __name__ == "__main__":
    raise SystemExit(
        run_monitor(
            family="FRP Manway Cover",
            target={"kind": "variation", "product_id": 1411, "variation_id": 1412},
            expected_sku="MWCDN50015PSI411",
            state_stem="gla_sync_manway_cover_1412",
        )
    )

"""
Create synthetic NRT QC output so the site can be built without real data.

The generator writes the same columns an ``aiqclib`` NRT QC run produces, with
enough structure for every part of the site to have something to show: clean
profiles, spikes only the computed flags catch, stretches only the input flags
call bad, and a few profiles where the two agree. No real observation is
involved, and the output is deterministic, so the demo site is reproducible.

Usage:

    python scripts/make_demo_data.py [--out demo-data] [--profiles 40]
"""

import argparse
import os
import random
from datetime import datetime, timedelta
from typing import Dict, List

import polars as pl

#: The datasets the shipped ``config/datasets.yaml`` expects.
DEMO_DATASETS: Dict[str, Dict[str, float]] = {
    "bal_cora_nrt": {"latitude": 58.9, "longitude": 20.3, "salinity": 7.0},
    "bal_cora_dm": {"latitude": 57.4, "longitude": 19.1, "salinity": 7.6},
    "arc_cora_nrt": {"latitude": 74.2, "longitude": 15.8, "salinity": 34.4},
}

#: The per-item flag columns the demo writes, mirroring an aiqclib run.
ITEM_COLUMNS: List[str] = [
    "qc_impossible_date",
    "qc_impossible_location",
    "qc_pressure_increasing",
    "temp_qc_global_range",
    "temp_qc_spike",
    "temp_qc_density_inversion",
    "psal_qc_global_range",
    "psal_qc_spike",
    "psal_qc_density_inversion",
]


def build_profile(
    platform: str, profile_no: int, base: Dict[str, float], rng: random.Random
) -> List[Dict[str, object]]:
    """
    Build one synthetic profile, seeded with its own anomalies.

    :param platform: The platform code.
    :param profile_no: The profile number within the platform.
    :param base: The region's baseline position and salinity.
    :param rng: The random source, already seeded.
    :return: One dictionary per observation.
    """
    depth_count = rng.randint(20, 60)
    timestamp = datetime(2021, 3, 1) + timedelta(hours=6 * profile_no)

    # Which kind of disagreement this profile demonstrates.
    kind = rng.choice(["clean", "spike", "input_only", "both", "clean"])
    spike_at = rng.randrange(3, depth_count - 3) if depth_count > 8 else 0

    rows: List[Dict[str, object]] = []
    for index in range(depth_count):
        pressure = round(2.0 + index * rng.uniform(1.8, 2.4), 1)
        temperature = 12.0 - 0.06 * pressure + rng.gauss(0.0, 0.05)
        salinity = base["salinity"] + 0.004 * pressure + rng.gauss(0.0, 0.01)

        temp_qc, psal_qc = 1, 1
        items = {name: 1 for name in ITEM_COLUMNS}

        if kind in ("spike", "both") and index == spike_at:
            temperature += rng.choice([-9.0, 9.0])
            items["temp_qc_spike"] = 4
            if kind == "both":
                temp_qc = 4
        if kind in ("input_only", "both") and index in (0, 1):
            temp_qc, psal_qc = 4, 4
        if kind == "spike" and index == spike_at + 1:
            items["psal_qc_density_inversion"] = 3

        temp_nrt = max(
            [
                items["temp_qc_global_range"],
                items["temp_qc_spike"],
                items["temp_qc_density_inversion"],
                items["qc_pressure_increasing"],
            ]
        )
        psal_nrt = max(
            [
                items["psal_qc_global_range"],
                items["psal_qc_spike"],
                items["psal_qc_density_inversion"],
                items["qc_pressure_increasing"],
            ]
        )

        rows.append(
            {
                "platform_code": platform,
                "profile_no": profile_no,
                "observation_no": index + 1,
                "profile_timestamp": timestamp,
                "longitude": base["longitude"] + rng.gauss(0.0, 0.2),
                "latitude": base["latitude"] + rng.gauss(0.0, 0.2),
                "pres": pressure,
                "temp": round(temperature, 3),
                "psal": round(salinity, 3),
                "temp_qc": temp_qc,
                "psal_qc": psal_qc,
                "temp_nrt_flag": temp_nrt,
                "psal_nrt_flag": psal_nrt,
                **items,
            }
        )
    return rows


def build_dataset(name: str, profiles: int, seed: int) -> pl.DataFrame:
    """
    Build one demo dataset.

    :param name: The dataset id, used as the platform prefix.
    :param profiles: How many profiles to generate.
    :param seed: The random seed, so the output is reproducible.
    :return: The dataset frame.
    """
    rng = random.Random(seed)
    base = DEMO_DATASETS[name]
    rows: List[Dict[str, object]] = []
    for index in range(profiles):
        platform = f"{name.upper()}{index % 5:02d}"
        rows += build_profile(platform, index + 1, base, rng)
    # An aiqclib run writes one contiguous block per platform, and the build
    # step requires that ordering rather than sorting hundreds of millions of
    # rows itself. Profiles are generated round robin over five platforms, so
    # sort here to make the demo input look like the real thing.
    return pl.DataFrame(rows).sort(["platform_code", "profile_no", "observation_no"])


def main() -> None:
    """Write one parquet per demo dataset."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="demo-data", help="Output directory.")
    parser.add_argument(
        "--profiles", type=int, default=40, help="Profiles per dataset."
    )
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    for seed, name in enumerate(DEMO_DATASETS):
        frame = build_dataset(name, args.profiles, seed=17 + seed)
        path = os.path.join(args.out, f"{name}.parquet")
        frame.write_parquet(path)
        print(f"wrote {path} ({frame.height} observations)")


if __name__ == "__main__":
    main()

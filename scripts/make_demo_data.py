"""
Create synthetic NRT QC output so the site can be built without real data.

The generator writes the same columns an ``aiqclib`` NRT QC run produces, with
enough structure for every part of the site to have something to show: clean
profiles, spikes only the computed flags catch, stretches only the input flags
call bad, and a few profiles where the two agree. No real observation is
involved, and the output is deterministic, so the demo site is reproducible.

Every QC check the About page lists can fire here, one per profile, because
the demo is what the published site runs on: a check the demo never writes is
a check nobody sees working until real data is hosted.

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

#: The checks that judge one measurement, written per variable as
#: ``{variable}_qc_{item}``.
VARIABLE_ITEMS: List[str] = [
    "global_range",
    "regional_range",
    "spike",
    "gradient",
    "digit_rollover",
    "stuck_value",
    "density_inversion",
]

#: The checks that judge the profile rather than a measurement, so they flag
#: every observation of it and feed both variables' roll-up.
PROFILE_ITEMS: List[str] = [
    "qc_impossible_date",
    "qc_impossible_location",
    "qc_position_on_land",
    "qc_pressure_increasing",
]

#: Every per-item flag column the demo writes: the item set of the `test_nrt`
#: batch plus `qc_position_on_land`. Nothing downstream is configured with
#: these names, the build and the site both discover them, so this list is
#: also what decides how wide that discovery is exercised.
ITEM_COLUMNS: List[str] = (
    PROFILE_ITEMS
    + [
        f"{variable}_qc_{item}"
        for variable in ("temp", "psal")
        for item in VARIABLE_ITEMS
    ]
    # Not a test: salinity inherits a bad temperature flag. Computed from the
    # finished temperature flag below, which is why it is not in the loop.
    + ["psal_qc_temp_to_psal"]
)

#: What a profile demonstrates, one check each. `clean` is repeated because
#: most of a real dataset is unremarkable and the trimming rule drops it,
#: which the demo should show as well.
KINDS: List[str] = [
    "clean",
    "clean",
    "spike",
    "both",
    "input_only",
    "gradient",
    "digit_rollover",
    "global_range",
    "regional_range",
    "stuck_value",
    "pressure_increasing",
    "impossible_date",
    "impossible_location",
    "position_on_land",
]

#: How far a rolled-over reading jumps, by variable. The magnitudes are the
#: `digit_rollover` thresholds of the real run, rounded up so the jump is
#: unambiguous; the demo writes the flags, the thresholds belong to aiqclib.
ROLLOVER: Dict[str, float] = {"temp": 12.0, "psal": 6.0}

#: How far a spiked reading departs from its neighbours, by variable. A
#: salinity cast spans a far narrower range than a temperature one, so the
#: same number would be a different kind of anomaly in each.
SPIKE: Dict[str, float] = {"temp": 9.0, "psal": 2.0}


def rollup(items: Dict[str, int], variable: str) -> int:
    """
    The flag ``aiqclib`` publishes for one variable: the worst item flag.

    An item judges either the whole profile (``qc_impossible_date``) or one
    measurement (``temp_qc_spike``), and both feed the roll-up. Reading the
    set off the column names rather than off a second list is what stops a
    new entry in :data:`ITEM_COLUMNS` from being written but never counted.

    :param items: The per-item flags of one observation.
    :param variable: The variable to roll up.
    :return: The most severe flag among the checks that apply to it.
    """
    prefix = f"{variable}_qc_"
    return max(
        value
        for name, value in items.items()
        if name.startswith(prefix) or name.startswith("qc_")
    )


def build_profile(
    platform: str, profile_no: int, base: Dict[str, float], rng: random.Random
) -> List[Dict[str, object]]:
    """
    Build one synthetic profile, seeded with its own anomalies.

    The profile demonstrates one thing, named by ``kind``. A check that judges
    a measurement hits one variable, chosen per profile so both temperature
    and salinity get their turn; a check that judges the profile flags all of
    it, which is what it does in a real run.

    :param platform: The platform code.
    :param profile_no: The profile number within the platform.
    :param base: The region's baseline position and salinity.
    :param rng: The random source, already seeded.
    :return: One dictionary per observation.
    """
    depth_count = rng.randint(20, 60)
    timestamp = datetime(2021, 3, 1) + timedelta(hours=6 * profile_no)

    kind = rng.choice(KINDS)
    # Which variable the measurement checks hit in this profile.
    target = rng.choice(["temp", "psal"])
    at = rng.randrange(3, depth_count - 3) if depth_count > 8 else 0
    stuck_level = 11.8 if target == "temp" else base["salinity"]

    latitude = base["latitude"]
    if kind == "impossible_location":
        # Past the pole, which is what the check looks for.
        latitude = 95.0
    if kind == "impossible_date":
        # Before 1950, so the profile time cannot be real.
        timestamp = datetime(1904, 6, 2) + timedelta(hours=6 * profile_no)

    rows: List[Dict[str, object]] = []
    previous_pressure = 0.0
    for index in range(depth_count):
        pressure = round(2.0 + index * rng.uniform(1.8, 2.4), 1)
        values = {
            "temp": 12.0 - 0.06 * pressure + rng.gauss(0.0, 0.05),
            "psal": base["salinity"] + 0.004 * pressure + rng.gauss(0.0, 0.01),
        }

        input_flags = {"temp": 1, "psal": 1}
        items = {name: 1 for name in ITEM_COLUMNS}

        if kind in ("spike", "both") and index == at:
            values[target] += rng.choice([-SPIKE[target], SPIKE[target]])
            items[f"{target}_qc_spike"] = 4
            if kind == "both":
                # The input flags the same observation, so the two sources
                # agree about it and the profile shows the "both" category.
                input_flags[target] = 4
        if kind in ("input_only", "both") and index in (0, 1):
            input_flags["temp"], input_flags["psal"] = 4, 4
        if kind == "spike" and index == at + 1:
            # Potential density falling with depth is a property of the pair,
            # so aiqclib flags temperature and salinity together.
            items["temp_qc_density_inversion"] = 3
            items["psal_qc_density_inversion"] = 3
        if kind == "gradient" and index == at:
            # Too steep a step for one pressure bin, but a plausible value.
            values[target] += 7.0
            items[f"{target}_qc_gradient"] = 4
        if kind == "digit_rollover" and index == at:
            # The jump a sensor makes when it wraps around its range.
            values[target] -= ROLLOVER[target]
            items[f"{target}_qc_digit_rollover"] = 4
        if kind == "global_range" and index == at:
            # Outside the gross worldwide range, and so outside the regional
            # one as well: a real run flags both.
            values[target] = 99.0
            items[f"{target}_qc_global_range"] = 4
            items[f"{target}_qc_regional_range"] = 4
        if kind == "regional_range" and index == at:
            # Inside the worldwide range, outside the one set for the region.
            values[target] += 20.0
            items[f"{target}_qc_regional_range"] = 4
        if kind == "stuck_value":
            # A stuck sensor repeats one reading down the whole cast, so this
            # check fires on every observation rather than on one.
            values[target] = stuck_level
            items[f"{target}_qc_stuck_value"] = 4
        if kind == "pressure_increasing" and index == at:
            # The same depth twice, which is what the check catches.
            pressure = previous_pressure
            items["qc_pressure_increasing"] = 4
        if kind == "impossible_date":
            items["qc_impossible_date"] = 4
        if kind == "impossible_location":
            items["qc_impossible_location"] = 4
        if kind == "position_on_land":
            items["qc_position_on_land"] = 4

        temp_nrt = rollup(items, "temp")
        if temp_nrt in (3, 4):
            # Salinity inherits a bad temperature flag at the same severity.
            items["psal_qc_temp_to_psal"] = temp_nrt
        psal_nrt = rollup(items, "psal")

        rows.append(
            {
                "platform_code": platform,
                "profile_no": profile_no,
                "observation_no": index + 1,
                "profile_timestamp": timestamp,
                "longitude": base["longitude"] + rng.gauss(0.0, 0.2),
                "latitude": latitude + rng.gauss(0.0, 0.2),
                "pres": pressure,
                "temp": round(values["temp"], 3),
                "psal": round(values["psal"], 3),
                "temp_qc": input_flags["temp"],
                "psal_qc": input_flags["psal"],
                "temp_nrt_flag": temp_nrt,
                "psal_nrt_flag": psal_nrt,
                **items,
            }
        )
        previous_pressure = pressure
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

# The NRT QC tests of the regional products

Which near real time QC tests the Arctic, Baltic and Mediterranean regional
centres say they run on CTD profiles, from their own replies. It explains
some of what the site shows: a test the input never ran cannot agree with
the same test in nrtqclib.

For the tests of `nc2parquet`, the earlier scripts whose NRT QC is used on
Arctic data, and how they differ from nrtqclib's, see
[`NC2PARQUET.md`](NC2PARQUET.md).

## The tests

| Test | nrtqclib item | Arctic | Baltic | Mediterranean |
| --- | --- | --- | --- | --- |
| Global range | `global_range` | yes | yes | yes |
| Regional range | `regional_range` | yes | yes | yes |
| Spike | `spike` | yes | yes | yes |
| Gradient | `gradient` | yes | no | yes |
| Stuck value (frozen value) | `stuck_value` | yes | no | yes |
| Frozen profile | none | yes | yes | yes |
| Density inversion | `density_inversion` | yes, 0.05 kg/m³ per dbar | yes, 0.05 kg/m³ per dbar | unclear (see below) |
| Metadata hydrological | none | no | no | no |

Each answer covers temperature and salinity alike. nrtqclib has no frozen
profile test; `nc2parquet` has one, described in
[`NC2PARQUET.md`](NC2PARQUET.md#only-in-nc2parquet-the-frozen-profile-test).

The data shows no sign of the centres' frozen profile test:

- ctddump's own parquet keeps every copy ctddump's duplicate step later
  removes, so repeats within a platform can be checked. Among copies of one
  platform with identical values taken an hour or more apart (19 groups in
  the Arctic, 1,571 in the Baltic, most of them exactly 12 hours apart,
  none in the Mediterranean), the input flags every copy or none, never
  only one (checked on the `test_nrt` batch on 2026-11-08, with throwaway
  scripts, not in `nrtqc-diff analyse`).
- After the duplicate step, `items_response` sets each NRT profile at sea
  against the same platform's previous one (the `frozen_profile` test, see
  [`ANALYSES.md`](ANALYSES.md#whether-the-input-runs-a-test)). Exact
  repeats are few, and the input flags none of the Arctic regional
  product's (38 for temperature, 41 for salinity), none of the Baltic's
  (10 or 11, too few to tell), and the Mediterranean regional product has
  none.

So the test cannot explain the whole profiles only the input flags. The
site says so on the Flag differences in NRT and Flagged only by the input
pages.

An exact repeat counts only on profiles inside the global range and not
one value throughout. Without that rule, three Arctic platforms whose
temperature reads one value outside the range at nearly every level
(TOP-9 and TOP-17 about -6.0 and -5.6 °C, TOP-1 a fill value of 999.9),
flagged by the range test on both sides, make about 2,000 flagged exact
repeats that say nothing about a frozen profile test.

The sources are beside this note: the table the centres filled in,
[`nrtqc_items.xlsx`](nrtqc_items.xlsx), and the replies that came with it
from the Baltic ([`nrtqc_item_bal.txt`](nrtqc_item_bal.txt)) and the
Mediterranean ([`nrtqc_item_med.txt`](nrtqc_item_med.txt)).

The site keeps one copy of this table, `REGIONAL_TESTS` in
`site/assets/js/regional.js`, which the Flag differences in NRT page draws
and the Flagged only by nrtqclib page reads per check. Change the two
together.

## Notes from the replies

- **Baltic.** The gradient test is not run because the stratification
  differs too much with depth; the centre may look at it again. Before the
  tests on each variable, the whole profile is checked for negative
  pressure (a measurement in air), for pressure increasing from the surface
  to the bottom, for spikes in pressure, and for a soaking depth left in.
- **Mediterranean.** The table ticks the density inversion test, but the
  reply says it is not run yet. Treat it as not run until confirmed.

## How this fits the data

`items_response` (see [`ANALYSES.md`](ANALYSES.md#whether-the-input-runs-a-test))
measures each failure of nrtqclib's version of a test and counts how many
the input flags, by how far past the threshold it is. In the analyses run
on 2026-11-09, the share of the clear failures the input flags:

- **Arctic regional:** global range and spike as claimed (97% to 100%);
  gradient mostly (79% in temperature, 85% in salinity). Density inversion
  only partly: 26% of inversions above 0.05 kg/m³, and 3% measured per
  dbar, the way the centre states its threshold. The reason is that it
  runs on ice-tethered profilers only, at 0.05 kg/m³ between levels (see
  [Density inversion in detail](#density-inversion-in-detail)). Too few
  stuck profiles to tell.
- **Baltic regional:** gradient and stuck value leave no sign (under 1%),
  as the centre says it runs neither. Spike and density inversion, which
  it says it runs, leave almost none either: 1.5% of clear salinity
  spikes, 2% of clear inversions; a density test shows on a few platforms
  only ([Density inversion in detail](#density-inversion-in-detail)).
  Salinity just under 2 is flagged three times in four,
  which fits a range test with a bound near it. Repeated pressures are not
  flagged, and no pressure goes shallower, so its pressure check cannot be
  seen.
- **Mediterranean regional:** too few clear failures for most tests. 16% of
  its few clear density inversions are flagged, although the reply says
  the test is not run yet.

ctddump sorts each profile by pressure when it merges the converted files,
so no profile of these products goes shallower in the data nrtqclib checks,
and every failure of its pressure increasing check is a repeated pressure.
The source files do hold profiles that go back up (ctddump's `checkpres`
reports count them, and the Data conversion page sums them up), so a
producer's pressure test on the stored order cannot be checked here.
The Mediterranean global product flags 59% of them, in the pressure flag.

### Density inversion in detail

The Arctic and Baltic centres give 0.05 kg/m³ per dbar; nrtqclib uses
0.03 kg/m³ between consecutive levels. nrtqclib's default stays at 0.03
(decided on 2026-11-10); this records what the flags show instead.

Checked first on the `test_nrt` batch on 2026-11-10 with throwaway
scripts; since nrtqc-diff 0.14.0 `nrtqc-diff analyse` writes the counts
as `density_threshold` and `density_platforms`
([`ANALYSES.md`](ANALYSES.md#whether-the-input-runs-a-test)), and the
Flag differences in NRT page shows them. Where the two differ, the tables
are right: the numbers below are theirs (EOS-80 density at zero pressure,
a flag on either variable), except the TEOS-10 comparisons, which only
the scripts made. The scripts Every consecutive pair of levels in the NRT
profiles compared for both variables, with both values inside the global
range, was taken with the input flags of both levels; "flagged" there
meant the input flags both temperature and salinity of the deeper level. To keep
the choice of platforms from making the step, the platforms were also
chosen on their even profiles (most inversions of 0.1 kg/m³ or more
flagged) and the step measured on their odd profiles; it is the same.

**Arctic regional.** A density test at **0.05 kg/m³ between consecutive
levels, on potential density, flagging the deeper level**, run on the
ice-tethered profilers only (platform codes `ITP-` and `TOP-`):

| Inversion between levels, potential density (kg/m³) | Flagged, ice-tethered profilers |
| --- | --- |
| Under 0.0495 | 0.4% or less |
| 0.0495 to 0.05 | 6% (2% to 6% in the scripts, by density) |
| 0.05 to 0.0505 | 97% |
| 0.0505 and above | 100% |

- **Not per dbar.** Their levels are 1 to 2 dbar apart (a median 2), so
  the test is 0.025 to 0.05 kg/m³ per dbar there; measured per dbar, the
  share flagged climbs from 32% at 0.02 to 0.03 to 80% to 86% up to 0.05,
  with no step.
- **Not in situ density.** With TEOS-10 in situ density (`gsw.rho` at each
  level's pressure), flagging starts at 0.04 to 0.045 kg/m³ and moves with
  the level spacing; with potential density it sits at 0.05 exactly.
- **The deeper level only.** The upper level is flagged too in a quarter
  to over half of the pairs (25% to 57%), rising with the size of the
  inversion, not always as a density test flagging both levels would.
- **The other platforms flag almost none**: 25 platforms with a clear
  inversion, with ship style codes (for example `LCLJ`, `3YAR`, `58JR`),
  at a median 0.3 to 5 dbar spacing, holding three quarters of the
  inversions above 0.05 kg/m³ (68,061 of 89,733). They flag 0.1% of those
  up to 0.2 kg/m³ and 13% of the larger ones, which other tests can
  account for; two (`58UO`, `58US`) flag nearly all of their 45 and 42.
  That is why only a quarter of the clear inversions are flagged in
  `items_response`.

The data cannot say whether the Arctic centre runs the test on these
platforms only or the flags come with the profiler data.
[`NC2PARQUET.md`](NC2PARQUET.md#density_inversion) has 0.05 kg/m³ too, but
on in situ density and flagging both levels, and off by default; the
Arctic flags match neither, so they come from another implementation with
the same threshold.

**Baltic regional.** No sign of the test on almost all of the data:

- 413 of the 521 platforms with an inversion above 0.05 kg/m³ flag none
  of them, and hold 90% of them (53,635 of 59,522). Over the whole
  product the share flagged goes from 0.3% or less under 0.045 kg/m³ to
  1% to 2.4% above 0.05.
- 61 platforms flag at least half of theirs and hold 1% (614, of which
  they flag 595), at 0.1 to 1 dbar spacing. Most have only a few each:
  seven have ten or more (`FYN8361`, `ARH230902`, `STR0101015`,
  `STR0101023`, `FYN0018532`, `FYN0018752`, `MSI17`) and flag nearly all.
  They flag 1.5% of their inversions up to 0.05 kg/m³, so they run a test
  between levels near 0.05, but there are too few pairs to place it. It
  is not 0.05 per dbar: at their spacing that would flag inversions from
  0.005 to 0.05 kg/m³ between levels, which they leave alone. The "about
  a dozen platforms" of the scripts were those with enough pairs to judge
  on half of their profiles.
- The remaining 47 flag some of theirs, 579 of 5,273 (11%), almost all
  on platforms with `SMHI` codes: a test on some of their profiles, or
  another test.

So the Baltic threshold cannot be read from its flags; ask the centre
which platforms the test runs on.

## The global products

The table describes the regional products only, not the Arctic and
Mediterranean subsets of the global product. A one-off comparison on the
current batch shows why:

- **Mediterranean:** the regional and global products share no cast.
- **Arctic:** about 18% of the global product's profiles are casts the
  regional product also holds, under other platform codes. The global copy
  is a different version of the cast: either a low resolution NRT copy
  under a WMO number, or the profiler team's processed copy in delayed
  mode. On the observations the two copies share, the input flags hardly
  overlap: of the temperature observations either product flags bad,
  under 1% are flagged by both.

So the global product's flags come from its own QC, which these replies do
not describe.

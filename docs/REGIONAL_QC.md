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

The data shows no sign of the centres' frozen profile test (checked on the
`test_nrt` batch on 2026-11-08, with throwaway scripts, not in
`nrtqc-diff analyse`):

- ctddump's own parquet keeps every copy ctddump's duplicate step later
  removes, so repeats within a platform can be checked. Among copies of one
  platform with identical values taken an hour or more apart (19 groups in
  the Arctic, 1,571 in the Baltic, most of them exactly 12 hours apart,
  none in the Mediterranean), the input flags every copy or none, never
  only one.
- After the duplicate step, an exact repeat of the same platform's previous
  profile an hour or more later is rare (22 in the Arctic regional product,
  9 in the Baltic, none in the Mediterranean) and the input flags almost
  none of them (4 in the Arctic).

So the test cannot explain the whole profiles only the input flags. The
site says so on the Flagged only by the input page, without the counts.

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
  dbar, the way the centre states its threshold. Too few stuck profiles to
  tell.
- **Baltic regional:** gradient and stuck value leave no sign (under 1%),
  as the centre says it runs neither. Spike and density inversion, which
  it says it runs, leave none either: 1.5% of clear salinity spikes, 2% of
  clear inversions. Salinity just under 2 is flagged three times in four,
  which fits a range test with a bound near it. Repeated pressures are not
  flagged, and no pressure goes shallower, so its pressure check cannot be
  seen.
- **Mediterranean regional:** too few clear failures for most tests. 16% of
  its few clear density inversions are flagged, although the reply says
  the test is not run yet.

No profile of these products goes shallower in recorded order, so every
failure of nrtqclib's pressure increasing check is a repeated pressure.
The Mediterranean global product flags 59% of them, in the pressure flag.

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

# The NRT QC tests of the regional products

Which near real time QC tests the Arctic, Baltic and Mediterranean regional
centres say they run on CTD profiles, from their own replies. It explains
some of what the site shows: a test the input never ran cannot agree with
the same test in nrtqclib.

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

Each answer covers temperature and salinity alike.

## Notes from the replies

- **Baltic.** The gradient test is not run because the stratification
  differs too much with depth; the centre may look at it again. Before the
  tests on each variable, the whole profile is checked for negative
  pressure (a measurement in air), for pressure increasing from the surface
  to the bottom, for spikes in pressure, and for a soaking depth left in.
- **Mediterranean.** The table ticks the density inversion test, but the
  reply says it is not run yet. Treat it as not run until confirmed.

## How this fits the data

The share of nrtqclib's failures of a test that the input flags too, from
`items_by_item`, is consistent with the table:

- The Baltic input flags almost none of what nrtqclib's gradient and stuck
  value tests fail (about 1%), as expected of tests it does not run.
- The Arctic regional input flags most of what nrtqclib's gradient test
  fails (about 70% in temperature, 90% in salinity).
- The Baltic spike test is listed as run, yet its input flags only about 2%
  of nrtqclib's spike failures, so the two spike tests must differ.

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

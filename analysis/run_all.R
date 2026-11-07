# Draw every figure of the Analyses pages from the analysis tables.
#
#   uv run nrtqc-diff analyse -c config/test_nrt.yaml   # the tables, in Python
#   Rscript analysis/run_all.R                          # the figures, in R
#
# Run from the repository root. Each page's script is run in a fresh
# environment, so one can be rerun alone the same way:
#   Rscript -e 'source("analysis/R/depth.R")'

scripts <- c("conversion", "depth", "data_mode", "flag_differences")
for (name in scripts) {
  source(file.path("analysis", "R", paste0(name, ".R")), local = new.env())
}

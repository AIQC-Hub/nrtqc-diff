# Shared setup for the figure scripts: packages, paths, labels, palettes,
# the theme, and the one function that writes a figure.
#
# The scripts read only the small CSV tables `nrtqc-diff analyse` writes
# (the largest is a few tens of thousands of rows). Nothing here touches the
# NRT QC output itself: anything that needs the observations is computed in
# Python and written as a table first. See docs/ANALYSES.md.

suppressPackageStartupMessages({
  library(dplyr)
  library(ggplot2)
  library(jsonlite)
  library(patchwork)
  library(readr)
  library(scales)
  library(tidyr)
})

# Where the tables are and where the figures go. Both are relative to the
# repository root, which run_all.R makes the working directory.
analyses_dir <- Sys.getenv("NRTQC_ANALYSES_DIR", "site/data/analyses")
tables_dir <- file.path(analyses_dir, "tables")
figures_dir <- file.path(analyses_dir, "figures")
dir.create(figures_dir, showWarnings = FALSE, recursive = TRUE)

meta_path <- file.path(analyses_dir, "meta.json")
if (!file.exists(meta_path)) {
  stop("No ", meta_path, ". Run `uv run nrtqc-diff analyse` first.")
}
meta <- fromJSON(meta_path, simplifyVector = TRUE)

# The figures are drawn for two widths: a journal's double column, and half
# of it. Heights are chosen per figure.
width_full <- 174
width_half <- 84

#' Read one analysis table, or NULL with a message when it was not made.
#'
#' Codes are read as text whatever they look like: a platform code such as
#' 6901234 is a name, not a number, and an empty data mode is a value.
read_table <- function(name) {
  path <- file.path(tables_dir, paste0(name, ".csv"))
  if (!file.exists(path)) {
    message("  skipped: no table ", name)
    return(NULL)
  }
  header <- names(read_csv(path, n_max = 0, show_col_types = FALSE))
  text <- intersect(c("platform_code", "data_mode", "original", "combination"), header)
  types <- do.call(cols, c(setNames(rep(list(col_character()), length(text)), text),
                           list(.default = col_guess())))
  table <- read_csv(path, show_col_types = FALSE, guess_max = 100000,
                    na = c("", "NA"), col_types = types)
  if (nrow(problems(table)) > 0) {
    warning("Parsing problems in ", name, ": ", format(problems(table)[1, ]))
  }
  table
}

#' Write a figure as SVG, for the site, and PDF, for the manuscript.
save_figure <- function(plot, name, width = width_full, height = 100) {
  svg_path <- file.path(figures_dir, paste0(name, ".svg"))
  pdf_path <- file.path(figures_dir, paste0(name, ".pdf"))
  ggsave(svg_path, plot, width = width, height = height, units = "mm",
         device = svglite::svglite, bg = "white")
  ggsave(pdf_path, plot, width = width, height = height, units = "mm",
         device = cairo_pdf, bg = "white")
  message("  wrote ", name)
  invisible(plot)
}

# ------------------------------------------------------------------ labels --

datasets <- as_tibble(meta$datasets) |>
  mutate(
    region_short = sub(" (Ocean|Sea)$", "", region),
    short = paste(region_short, product),
    order = row_number()
  )

region_levels <- unique(datasets$region)
product_levels <- unique(datasets$product)

#' Attach the dataset's short name and ordered factors to a table.
with_datasets <- function(df) {
  df |>
    select(-any_of(c("region", "product", "label"))) |>
    left_join(select(datasets, id, region, region_short, product, short, order),
              by = c("dataset_id" = "id")) |>
    mutate(
      region = factor(region, levels = region_levels),
      product = factor(product, levels = product_levels),
      short = factor(short, levels = datasets$short)
    )
}

statuses <- as_tibble(meta$statuses)
status_colours <- setNames(statuses$color, statuses$label)
status_label <- function(key) {
  factor(statuses$label[match(key, statuses$key)], levels = statuses$label)
}

groups <- as_tibble(meta$groups)
group_label <- function(key) {
  factor(groups$label[match(key, groups$key)], levels = groups$label)
}

variable_label <- function(name) {
  labels <- setNames(meta$variables$label, meta$variables$name)
  factor(unname(labels[name]), levels = meta$variables$label)
}

# The nrtqclib checks in words: the same list as QC_CHECKS in
# site/assets/js/labels.js and the table on the About page.
qc_checks <- tribble(
  ~item, ~test, ~check,
  "impossible_date", "RTQC2", "Impossible date",
  "impossible_location", "RTQC3", "Impossible location",
  "position_on_land", "RTQC4", "Position on land",
  "global_range", "RTQC6", "Global range",
  "regional_range", "RTQC7", "Regional range",
  "pressure_increasing", "RTQC8", "Pressure increasing",
  "spike", "RTQC9", "Spike",
  "gradient", "RTQC11", "Gradient",
  "digit_rollover", "RTQC12", "Digit rollover",
  "stuck_value", "RTQC13", "Stuck value",
  "density_inversion", "RTQC14", "Density inversion",
  "temp_to_psal", NA, "Temperature flag carried over"
)
check_label <- function(item) {
  known <- qc_checks$check[match(item, qc_checks$item)]
  factor(ifelse(is.na(known), item, known), levels = unique(c(qc_checks$check, item)))
}

# ---------------------------------------------------------------- palettes --
#
# Statuses use the site's own colours (from meta.json), so a figure and the
# dashboard say the same thing in the same colour. The rest are slots of a
# categorical palette checked for colour vision deficiency: products take
# three slots that stay apart in every pairing (they share scatter plots),
# and the analysis groups take four plus a neutral grey for "unknown".

product_colours <- setNames(c("#2a78d6", "#eb6834", "#1baf7a"), product_levels[1:3])

group_colours <- c(
  "On land" = "#008300",
  "Contains delayed mode" = "#6250d6",
  "Estimated delayed mode" = "#eb6834",
  "Data mode unknown" = "#9a9a9a",
  "NRT" = "#2a78d6"
)

data_mode_colours <- c(
  "R (real time)" = "#2a78d6",
  "A (adjusted)" = "#1baf7a",
  "D (delayed mode)" = "#6250d6",
  "Unknown" = "#9a9a9a"
)

# ------------------------------------------------------------------- theme --

theme_nrtqc <- function(base_size = 8) {
  theme_minimal(base_size = base_size) +
    theme(
      panel.grid.minor = element_blank(),
      panel.grid.major = element_line(colour = "#e6e6e3", linewidth = 0.3),
      axis.ticks = element_line(colour = "#9a9a96", linewidth = 0.3),
      axis.title = element_text(colour = "#52514e"),
      axis.text = element_text(colour = "#52514e"),
      strip.text = element_text(face = "bold", hjust = 0, colour = "#0b0b0b"),
      legend.position = "bottom",
      legend.title = element_blank(),
      legend.key.size = unit(3, "mm"),
      plot.title.position = "plot",
      panel.spacing = unit(4, "mm"),
      plot.margin = margin(2, 8, 2, 2)
    )
}
theme_set(theme_nrtqc())

comma0 <- label_comma(accuracy = 1)

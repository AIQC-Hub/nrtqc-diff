# Figures of the Delayed mode in NRT products page: the groups profiles are filed
# under, and the share of input-only flags that estimates delayed mode. The
# estimate is made per variable, so every figure has a panel per variable.

source("analysis/R/setup.R")
message("Delayed mode in NRT products")

rule <- meta$rules$estimated_delayed

# Profiles at sea only: the land ones are the Depth page's, and every
# analysis after it leaves them out.
grouped <- read_table("dm_groups")
if (!is.null(grouped)) {
  sea_colours <- group_colours[names(group_colours) != "On land"]
  counts <- grouped |>
    filter(group != "land") |>
    with_datasets() |>
    group_by(short, variable, group) |>
    summarise(n_profiles = sum(n_profiles), .groups = "drop") |>
    group_by(short) |>
    # Every variable covers the same profiles at sea; the first gives the count.
    mutate(row = paste0(short, " (", comma(sum(n_profiles[variable == variable[1]])), ")")) |>
    ungroup() |>
    arrange(short) |>
    mutate(row = factor(row, levels = rev(unique(row))),
           variable = variable_label(variable),
           group = factor(as.character(group_label(group)), levels = names(sea_colours)))
  plot <- ggplot(counts, aes(n_profiles, row, fill = group)) +
    geom_col(width = 0.7, colour = "white", linewidth = 0.3,
             position = position_fill(reverse = TRUE)) +
    facet_grid(. ~ variable) +
    scale_fill_manual(values = sea_colours) +
    scale_x_continuous(labels = label_percent(), breaks = c(0, 0.25, 0.5, 0.75),
                       expand = expansion(0)) +
    labs(x = "Profiles at sea", y = NULL) +
    theme(panel.spacing.x = unit(5, "mm"))
  save_figure(plot, "dm_groups", height = 50)
}

hist <- read_table("dm_share_hist")
if (!is.null(hist)) {
  mode_labels <- setNames(meta$data_modes$label, meta$data_modes$key)
  mode_colours <- setNames(
    unname(group_colours[c("NRT", "Contains delayed mode", "Data mode unknown")]),
    mode_labels[c("nrt", "delayed", "unknown")]
  )
  # Profiles with no input-only flag at all are most of every dataset and
  # would flatten everything else, so the histogram starts above zero; the
  # page gives their count in a table.
  shown <- hist |>
    with_datasets() |>
    filter(bin_hi > 0) |>
    mutate(
      variable = variable_label(variable),
      data_mode = factor(mode_labels[data_mode], levels = names(mode_colours)),
      centre = if_else(bin_lo == bin_hi, 1 + 0.025, bin_lo + (bin_hi - bin_lo) / 2)
    )
  plot <- ggplot(shown, aes(centre, n_profiles, fill = data_mode)) +
    geom_col(width = 0.045) +
    geom_vline(xintercept = rule$min_share, linetype = "22", linewidth = 0.3) +
    facet_grid(short ~ variable, scales = "free_y") +
    scale_fill_manual(values = mode_colours, drop = TRUE) +
    scale_x_continuous(labels = label_percent(), breaks = seq(0, 1, 0.25)) +
    scale_y_sqrt(labels = comma0) +
    labs(
      x = "Observations flagged by the input alone, share of those with a usable input flag (last bar: exactly 100%)",
      y = "Profiles (axis spaced by square root)"
    ) +
    theme(strip.text.y = element_text(angle = 0, hjust = 0))
  save_figure(plot, "dm_share", height = 130)
}

thresholds <- read_table("dm_threshold")
if (!is.null(thresholds)) {
  lines <- thresholds |>
    with_datasets() |>
    mutate(set = if_else(data_mode %in% rule$groups, "NRT or unknown: estimated",
                         "Delayed mode: would be caught")) |>
    group_by(short, region, product, variable, set, threshold) |>
    summarise(n_profiles = sum(n_profiles), .groups = "drop") |>
    filter(n_profiles > 0 | set == "NRT or unknown: estimated") |>
    mutate(variable = variable_label(variable))
  # Within a region every dataset is a different product, so the product's
  # colour tells the lines of a panel apart.
  plot <- ggplot(lines, aes(threshold, n_profiles, colour = product, linetype = set,
                            group = interaction(short, set))) +
    geom_line(linewidth = 0.6) +
    geom_point(size = 1.2) +
    geom_vline(xintercept = rule$min_share, linetype = "22", linewidth = 0.3) +
    facet_grid(variable ~ region, scales = "free_y") +
    scale_colour_manual(values = product_colours) +
    scale_x_continuous(labels = label_percent()) +
    scale_y_continuous(labels = comma0, limits = c(0, NA)) +
    labs(x = "Threshold", y = "Profiles at or above it") +
    guides(linetype = guide_legend(nrow = 2))
  save_figure(plot, "dm_threshold", height = 120)
}

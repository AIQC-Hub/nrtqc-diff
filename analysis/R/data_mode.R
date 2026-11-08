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
  # What the estimate would catch at each threshold: profiles of the groups
  # the rule applies to (marked NRT, or with no data mode), one panel per
  # product and variable. Each panel has its own scale but starts at zero,
  # so a line's fall is its real proportion: flat means the threshold hardly
  # matters there, falling means it decides how many are estimated. The
  # profiles that declare delayed mode are left to the table: drawn here as
  # well, they needed a second legend and were in the Arctic only.
  caught <- thresholds |>
    filter(data_mode %in% rule$groups) |>
    with_datasets() |>
    group_by(short, variable, threshold) |>
    summarise(n_profiles = sum(n_profiles), .groups = "drop") |>
    mutate(variable = variable_label(variable)) |>
    left_join(select(datasets, short, region_short, product, order),
              by = c("short" = "short")) |>
    arrange(variable, order) |>
    mutate(panel = paste0(region_short, "\n", product, "\n", variable),
           panel = factor(panel, levels = unique(panel)))
  chosen <- caught |> filter(abs(threshold - rule$min_share) < 1e-9)
  estimated <- group_colours[["Estimated delayed mode"]]
  plot <- ggplot(caught, aes(threshold, n_profiles)) +
    geom_vline(xintercept = rule$min_share, colour = "#9a9a96", linewidth = 0.3) +
    geom_line(colour = estimated, linewidth = 0.6) +
    geom_point(colour = estimated, size = 1) +
    geom_point(data = chosen, colour = estimated, size = 2.2) +
    geom_text(data = chosen, aes(label = comma(n_profiles)), colour = "#0b0b0b",
              hjust = -0.25, vjust = -0.6, size = 2.3) +
    facet_wrap(~panel, ncol = length(unique(caught$short)), scales = "free_y") +
    scale_x_continuous(labels = label_percent(), breaks = c(0.5, 0.75, 1),
                       expand = expansion(mult = 0.08)) +
    scale_y_continuous(labels = comma0, limits = c(0, NA),
                       expand = expansion(mult = c(0, 0.2))) +
    labs(x = "Threshold: share of the profile's observations flagged by the input alone",
         y = "Profiles estimated delayed mode") +
    theme(strip.text = element_text(size = 6.5))
  save_figure(plot, "dm_threshold", height = 95)
}

# Where the input's probably good (2) and probably bad (3) flags end up:
# every such observation at sea in the NRT products, by the group of its
# profile and, in an NRT profile, by whether nrtqclib flags it bad too.
probable <- read_table("dm_probable_obs")
if (!is.null(probable)) {
  probable_classes <- c(
    "Declared delayed mode profile" = group_colours[["Contains delayed mode"]],
    "Estimated delayed mode profile" = group_colours[["Estimated delayed mode"]],
    "NRT profile, nrtqclib flags it bad too" = group_colours[["NRT"]],
    "NRT profile, the input's flag alone" = "#b8c0c8"
  )
  # The products the page covers: those not filed whole under one data
  # mode, whether or not they use either flag.
  nrt_products <- as.character(datasets$short[is.na(datasets$data_mode)])
  shares <- probable |>
    with_datasets() |>
    mutate(
      class = case_when(
        group == "delayed" ~ names(probable_classes)[1],
        group == "estimated_delayed" ~ names(probable_classes)[2],
        nrtqclib_bad ~ names(probable_classes)[3],
        TRUE ~ names(probable_classes)[4]
      ),
      class = factor(class, levels = names(probable_classes)),
      panel = paste0(variable_label(variable), ", ",
                     c("2" = "2 (probably good)", "3" = "3 (probably bad)")[as.character(flag)]),
      panel = factor(panel, levels = as.vector(outer(
        levels(variable_label(meta$variables$name)),
        c("2 (probably good)", "3 (probably bad)"), paste, sep = ", ")))
    ) |>
    group_by(short, panel, class) |>
    summarise(n_obs = sum(n_obs), .groups = "drop") |>
    group_by(short, panel) |>
    mutate(share = n_obs / sum(n_obs)) |>
    ungroup() |>
    mutate(short = factor(as.character(short), levels = nrt_products))
  # A product with none of a flag gets a row that says so, rather than a
  # gap that reads like missing data.
  totals <- shares |>
    group_by(short, panel) |>
    summarise(n_obs = sum(n_obs), .groups = "drop") |>
    mutate(short = factor(short, levels = nrt_products)) |>
    complete(short, panel, fill = list(n_obs = 0L)) |>
    mutate(text = if_else(n_obs == 0, "none", comma(n_obs)),
           x = if_else(n_obs == 0, 0, 1))

  plot <- ggplot(shares, aes(share, forcats::fct_rev(short), fill = class)) +
    geom_col(width = 0.7, colour = "white", linewidth = 0.3,
             position = position_stack(reverse = TRUE)) +
    geom_text(data = totals, inherit.aes = FALSE,
              aes(x, forcats::fct_rev(short), label = text),
              hjust = -0.15, size = 2.3, colour = "#52514e") +
    facet_wrap(~panel, ncol = 2, drop = FALSE) +
    scale_fill_manual(values = probable_classes, drop = FALSE) +
    scale_y_discrete(limits = rev(nrt_products)) +
    scale_x_continuous(labels = label_percent(), breaks = c(0, 0.5, 1),
                       expand = expansion(mult = c(0, 0.22))) +
    labs(x = "Share of the observations with this input flag (number at the end)",
         y = NULL) +
    guides(fill = guide_legend(nrow = 2)) +
    theme(panel.grid.major.y = element_blank())
  save_figure(plot, "dm_probable", height = 105)
}

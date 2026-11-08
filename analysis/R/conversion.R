# Figures of the Data conversion page: what ctddump kept at each step, the
# data modes it found, the duplicates it removed, and how much of each NRT
# product CORA holds.

source("analysis/R/setup.R")
message("Data conversion")

# The steps in a word each, for the axis; the page's table gives them in
# full. The numbers follow the order ctddump runs them in.
step_short <- c(
  convert = "1 Converted", dropqc = "2 QC", dropna = "3 All missing",
  filter = "4 Region", dedup = "5 Duplicates"
)

steps <- read_table("conversion_steps")
if (!is.null(steps)) {
  steps <- steps |>
    with_datasets() |>
    group_by(dataset_id) |>
    mutate(share = n_profiles / n_profiles[step == "convert"]) |>
    ungroup() |>
    mutate(step_short = factor(step_short[step], levels = step_short))

  last <- steps |> group_by(dataset_id) |> filter(step_order == max(step_order))

  plot <- ggplot(steps, aes(step_short, share, colour = product, group = short)) +
    geom_line(linewidth = 0.6) +
    geom_point(size = 1.4) +
    ggrepel::geom_text_repel(
      data = last, aes(label = percent(share, accuracy = 1)),
      nudge_x = 0.45, direction = "y", hjust = 0, size = 2.4,
      segment.size = 0.2, min.segment.length = 0.3, show.legend = FALSE
    ) +
    facet_wrap(~region, nrow = 1) +
    scale_colour_manual(values = product_colours) +
    scale_y_continuous(labels = label_percent(), limits = c(0, 1.02),
                       expand = expansion(mult = c(0, 0.02))) +
    scale_x_discrete(expand = expansion(add = c(0.3, 0.9))) +
    labs(x = "ctddump step",
         y = "Profiles kept, share of converted") +
    theme(axis.text.x = element_text(angle = 40, hjust = 1))
  save_figure(plot, "conversion_steps", height = 82)
}

# The two ends of the pipeline, numbered as in the step figure.
mode_steps <- c(convert = "1 Converted", dedup = "5 Kept (after duplicates)")

modes <- read_table("conversion_data_mode")
if (!is.null(modes)) {
  modes <- modes |>
    with_datasets() |>
    filter(variable == "temp") |>
    mutate(
      data_mode = recode(coalesce(data_mode, ""),
                         "R" = "R (real time)", "A" = "A (adjusted)",
                         "D" = "D (delayed mode)", .default = "No data mode"),
      data_mode = factor(data_mode, levels = names(data_mode_colours)),
      step = factor(mode_steps[step], levels = mode_steps)
    ) |>
    group_by(short, step) |>
    mutate(share = n_obs / sum(n_obs)) |>
    ungroup()

  plot <- ggplot(modes, aes(share, forcats::fct_rev(short), fill = data_mode)) +
    geom_col(width = 0.7, colour = "white", linewidth = 0.3) +
    facet_wrap(~step, nrow = 1) +
    scale_fill_manual(values = data_mode_colours, drop = FALSE) +
    scale_x_continuous(labels = label_percent(), breaks = c(0, 0.5, 1),
                       expand = expansion(0)) +
    labs(x = "Temperature observations", y = NULL) +
    theme(panel.spacing.x = unit(8, "mm"))
  save_figure(plot, "conversion_data_mode", height = 70)
}

# Duplicates: the profiles step 5 removed, as a share of those it was given,
# split by the kind of duplicate group: on one platform, spread over several,
# or both at once.
kind_colours <- c("Within one platform" = "#2a78d6", "Across platforms" = "#eb6834",
                  "Both" = "#6250d6")
kind_label <- function(kind) {
  labels <- c(within = "Within one platform", across = "Across platforms", both = "Both")
  factor(unname(labels[kind]), levels = names(kind_colours))
}

kinds <- read_table("conversion_dup_kinds")
before <- read_table("conversion_duplicates")
if (!is.null(kinds) && !is.null(before)) {
  removed <- kinds |>
    left_join(select(before, dataset_id, n_before = n_profiles), by = "dataset_id") |>
    with_datasets() |>
    mutate(share = n_removed / n_before, kind = kind_label(kind))
  totals <- removed |>
    group_by(short) |>
    summarise(share = sum(share), n_removed = sum(n_removed), .groups = "drop")

  plot <- ggplot(removed, aes(share, forcats::fct_rev(short), fill = kind)) +
    geom_col(width = 0.7, colour = "white", linewidth = 0.3,
             position = position_stack(reverse = TRUE)) +
    geom_text(data = totals, inherit.aes = FALSE,
              aes(share, forcats::fct_rev(short),
                  label = paste0(percent(share, accuracy = 0.1), "  (",
                                 comma(n_removed), ")")),
              hjust = -0.08, size = 2.4) +
    scale_fill_manual(values = kind_colours) +
    scale_x_continuous(labels = label_percent(), expand = expansion(mult = c(0, 0.35))) +
    labs(x = "Profiles removed as duplicates, share of those in the region", y = NULL,
         fill = NULL)
  save_figure(plot, "conversion_duplicates", height = 70)
}

# Coverage: an NRT product carries real time QC and CORA delayed mode QC of
# the same casts, so CORA should hold every NRT profile it covers in time.
# Where each NRT profile was found, as a share of those in CORA's period.
coverage_colours <- c(
  "Same platform code" = "#2a78d6",
  "Another platform code" = "#9ec5f0",
  "Nearby, not paired" = "#6250d6",
  "Platform in CORA, cast not" = "#eb6834",
  "Platform not in CORA" = "#cc3355",
  "Outside CORA's period" = "#c8c8c8"
)
coverage_names <- setNames(names(coverage_colours), c(
  "same_platform", "other_platform", "nearby", "platform_only", "no_platform",
  "outside_period"
))

coverage <- read_table("conversion_coverage")
if (!is.null(coverage)) {
  inside <- names(coverage_names)[names(coverage_names) != "outside_period"]
  coverage <- coverage |>
    with_datasets() |>
    mutate(n_inside = n_profiles - n_outside_period,
           row = paste0(short, " (", comma(n_inside), ")"),
           row = factor(row, levels = rev(unique(row)))) |>
    tidyr::pivot_longer(all_of(paste0("n_", inside)), names_to = "class",
                        values_to = "n") |>
    mutate(class = factor(unname(coverage_names[sub("^n_", "", class)]),
                          levels = names(coverage_colours)),
           share = n / n_inside)
  found <- coverage |>
    filter(class %in% names(coverage_colours)[1:2]) |>
    group_by(row) |>
    summarise(share = sum(share), .groups = "drop")

  plot <- ggplot(coverage, aes(share, row, fill = class)) +
    geom_col(width = 0.7, colour = "white", linewidth = 0.3,
             position = position_stack(reverse = TRUE)) +
    geom_text(data = found, inherit.aes = FALSE,
              aes(share / 2, row, label = percent(share, accuracy = 0.1)),
              colour = "white", size = 2.4) +
    scale_fill_manual(values = coverage_colours[1:5], drop = FALSE) +
    scale_x_continuous(labels = label_percent(), breaks = seq(0, 1, 0.25),
                       expand = expansion(0)) +
    guides(fill = guide_legend(nrow = 2)) +
    labs(x = "NRT profiles in CORA's period, by where they were found in CORA",
         y = NULL, fill = NULL)
  save_figure(plot, "conversion_coverage", height = 62)
}

coverage_years <- read_table("conversion_coverage_years")
if (!is.null(coverage_years)) {
  coverage_years <- coverage_years |>
    with_datasets() |>
    mutate(panel = factor(short, levels = unique(short)),
           class = factor(unname(coverage_names[coverage]),
                          levels = rev(names(coverage_colours))))
  plot <- ggplot(coverage_years, aes(year, n_profiles, fill = class)) +
    geom_col(width = 1) +
    facet_wrap(~panel, ncol = 1, scales = "free_y", strip.position = "top") +
    scale_fill_manual(values = coverage_colours, breaks = names(coverage_colours),
                      drop = FALSE) +
    scale_x_continuous(breaks = seq(1950, 2030, 10), expand = expansion(0.01)) +
    scale_y_continuous(labels = comma0, expand = expansion(mult = c(0, 0.05))) +
    guides(fill = guide_legend(nrow = 2)) +
    labs(x = NULL, y = "NRT profiles per year", fill = NULL) +
    theme(strip.text = element_text(hjust = 0))
  save_figure(plot, "conversion_coverage_years", height = 170)
}

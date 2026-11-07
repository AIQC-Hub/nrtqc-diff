# Figures of the Data conversion page: what ctddump kept at each step, the
# data modes it found, the duplicates it removed, how much of each NRT product
# CORA holds, and how the casts they share compare.

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

# The pairs: one cast in two versions should hold the same observations.
# Split by whether the two products give the platform the same code.
matches <- read_table("conversion_match")
if (!is.null(matches)) {
  obs_colours <- c("Same number" = "#2a78d6", "NRT has more" = "#eb6834",
                   "CORA has more" = "#1baf7a")
  matches <- matches |>
    with_datasets() |>
    mutate(row = paste0(short, ", ", if_else(coverage == "same_platform",
                                             "same code", "another code"),
                        " (", comma(n_matched), ")"),
           row = factor(row, levels = rev(unique(row)))) |>
    transmute(row, n_matched,
              `Same number` = n_same_obs, `NRT has more` = n_more_in_dataset,
              `CORA has more` = n_more_in_compared) |>
    tidyr::pivot_longer(-c(row, n_matched), names_to = "obs", values_to = "n") |>
    mutate(obs = factor(obs, levels = names(obs_colours)), share = n / n_matched)
  same <- matches |> filter(obs == "Same number")

  plot <- ggplot(matches, aes(share, row, fill = obs)) +
    geom_col(width = 0.7, colour = "white", linewidth = 0.3,
             position = position_stack(reverse = TRUE)) +
    geom_text(data = same |> filter(share >= 0.12), inherit.aes = FALSE,
              aes(share / 2, row, label = percent(share, accuracy = 0.1)),
              colour = "white", size = 2.3) +
    scale_fill_manual(values = obs_colours) +
    scale_x_continuous(labels = label_percent(), breaks = seq(0, 1, 0.25),
                       expand = expansion(0)) +
    labs(x = "Pairs of the same cast, by their numbers of observations", y = NULL,
         fill = NULL)
  save_figure(plot, "conversion_match", height = 72)
}

# The mismatched pairs compared level by level: how the two versions of a
# cast relate, and what the levels found in one version only hold.
kinds <- read_table("conversion_match_kinds")
if (!is.null(kinds)) {
  short_of <- setNames(datasets$short, datasets$id)
  kind_colours <- c(
    "CORA lacks levels NRT has" = "#2a78d6",
    "NRT lacks levels CORA has" = "#1baf7a",
    "CORA holds every level twice" = "#6250d6",
    "NRT holds every level twice" = "#cc3355",
    "Each has levels the other lacks" = "#eb6834"
  )
  kind_names <- setNames(names(kind_colours), c("compared_removed", "dataset_removed",
                                                "compared_doubled", "dataset_doubled",
                                                "both_differ"))
  code_names <- c(same_platform = "Same platform code",
                  other_platform = "Another platform code")
  kinds <- kinds |>
    filter(kind %in% names(kind_names)) |>
    with_datasets() |>
    mutate(pair = factor(short, levels = rev(unique(short))),
           code = factor(unname(code_names[coverage]), levels = code_names),
           kind = factor(unname(kind_names[kind]), levels = names(kind_colours)))
  totals <- kinds |>
    group_by(code, pair) |>
    summarise(n = sum(n_pairs), .groups = "drop")
  plot <- ggplot(kinds, aes(n_pairs, pair, fill = kind)) +
    geom_col(width = 0.7, colour = "white", linewidth = 0.3,
             position = position_fill(reverse = TRUE)) +
    geom_text(data = totals, inherit.aes = FALSE, aes(1, pair, label = comma(n)),
              hjust = -0.15, size = 2.3) +
    facet_grid(. ~ code) +
    scale_fill_manual(values = kind_colours, drop = FALSE) +
    scale_x_continuous(labels = label_percent(), breaks = c(0, 0.5, 1),
                       expand = expansion(mult = c(0, 0.22))) +
    guides(fill = guide_legend(nrow = 2)) +
    theme(panel.spacing.x = unit(5, "mm")) +
    labs(x = "Mismatched pairs, by how the two versions' levels relate", y = NULL,
         fill = NULL)
  save_figure(plot, "conversion_match_kinds", height = 66)
}

levels_only <- read_table("conversion_match_levels")
if (!is.null(levels_only)) {
  short_of <- setNames(datasets$short, datasets$id)
  content_colours <- c("Good value" = "#2a78d6", "Flagged bad by the input" = "#cc3355",
                       "Repeat of a shared pressure" = "#6250d6", "No value" = "#9a9a9a")
  content_names <- setNames(names(content_colours),
                            c("good_value", "flagged_bad", "repeat", "no_value"))
  position_names <- c(top = "Above", inside = "Between", bottom = "Below",
                      no_common = "No level shared")
  # Only the pairs under one code: under two, the versions are mostly
  # sampled at different levels throughout, and the panels would say little.
  levels_only <- levels_only |>
    filter(coverage == "same_platform") |>
    with_datasets() |>
    mutate(
      # The comparison is always with the CORA of the region, so the row
      # names only the NRT product and the side.
      row = paste0(short, ", only in ", if_else(side == "dataset", "NRT", "CORA")),
      row = factor(row, levels = rev(unique(row))),
      content = factor(unname(content_names[content]), levels = names(content_colours)),
      position = factor(unname(position_names[position]), levels = position_names)
    ) |>
    group_by(row) |>
    mutate(share = n_levels / sum(n_levels),
           row = paste0(row, " (", comma(sum(n_levels)), ")")) |>
    ungroup() |>
    mutate(row = factor(row, levels = rev(unique(row))))
  plot <- ggplot(levels_only, aes(share, row, fill = content)) +
    geom_col(width = 0.7, colour = "white", linewidth = 0.2,
             position = position_stack(reverse = TRUE)) +
    facet_grid(. ~ position) +
    scale_fill_manual(values = content_colours, drop = FALSE) +
    scale_x_continuous(labels = label_percent(), limits = c(0, 1), breaks = c(0, 0.5),
                       expand = expansion(0)) +
    guides(fill = guide_legend(nrow = 2)) +
    labs(x = "Levels in one version only, share of the row, by where they sit against the shared levels",
         y = NULL, fill = NULL) +
    theme(panel.spacing.x = unit(4, "mm"))
  save_figure(plot, "conversion_match_levels", height = 85)
}

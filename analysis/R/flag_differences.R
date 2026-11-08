# Figures of the three Flag differences pages: over NRT profiles at sea, how
# the two sources agree, which nrtqclib checks account for what only
# nrtqclib flags, where in the water column each side flags alone, and
# where in its profile each one-sided flag sits.

source("analysis/R/setup.R")
message("Flag differences")

flagged <- statuses$key[statuses$flagged]

status <- read_table("items_status")
if (!is.null(status)) {
  rates <- status |>
    with_datasets() |>
    group_by(short, variable) |>
    mutate(rate = n_obs / sum(n_obs)) |>
    ungroup() |>
    filter(status %in% flagged) |>
    mutate(status = status_label(status), variable = variable_label(variable))
  plot <- ggplot(rates, aes(rate, forcats::fct_rev(short), fill = status)) +
    geom_col(width = 0.7, position = position_dodge2(width = 0.75, reverse = TRUE)) +
    facet_wrap(~variable, nrow = 1) +
    scale_fill_manual(values = status_colours[levels(droplevels(rates$status))]) +
    scale_x_continuous(labels = label_percent(), expand = expansion(mult = c(0, 0.05))) +
    labs(x = "Share of the observations", y = NULL)
  save_figure(plot, "items_status", height = 75)
}

by_item <- read_table("items_by_item")
if (!is.null(by_item)) {
  attributed <- by_item |>
    with_datasets() |>
    left_join(status |> group_by(dataset_id, variable) |>
                summarise(total = sum(n_obs), .groups = "drop"),
              by = c("dataset_id", "variable")) |>
    transmute(
      short, variable = variable_label(variable), check = check_label(item),
      `The only check that failed` = n_alone_nrtqclib_only / total,
      `Failed with other checks` = (n_fail_nrtqclib_only - n_alone_nrtqclib_only) / total
    ) |>
    pivot_longer(c(`The only check that failed`, `Failed with other checks`),
                 names_to = "how", values_to = "rate") |>
    group_by(variable, check) |>
    filter(sum(rate) > 0) |>
    ungroup() |>
    mutate(how = factor(how, levels = c("The only check that failed",
                                        "Failed with other checks")),
           check = droplevels(check))
  plot <- ggplot(attributed, aes(rate, forcats::fct_rev(check), fill = how)) +
    geom_col(width = 0.7, position = position_stack(reverse = TRUE)) +
    facet_grid(variable ~ short, scales = "free", space = "free_y",
               labeller = labeller(short = label_wrap_gen(14))) +
    scale_fill_manual(values = c("The only check that failed" = "#cc3355",
                                 "Failed with other checks" = "#eba0b0")) +
    scale_x_continuous(labels = label_percent(drop0trailing = TRUE),
                       expand = expansion(mult = c(0, 0.12)), n.breaks = 3) +
    labs(x = "Observations only nrtqclib flags, share of all observations", y = NULL)
  save_figure(plot, "items_attribution", height = 120)
}

combos <- read_table("items_combinations")
if (!is.null(combos)) {
  # An UpSet-style view: the commonest sets of failing checks behind the
  # observations only nrtqclib flags, pooled over the NRT datasets.
  upset <- function(var) {
    top <- combos |>
      filter(variable == var, status == "nrtqclib_only") |>
      group_by(combination) |>
      summarise(n_obs = sum(n_obs), .groups = "drop") |>
      slice_max(n_obs, n = 10, with_ties = FALSE) |>
      mutate(combination = factor(combination, levels = combination))
    if (nrow(top) == 0) return(NULL)
    members <- top |>
      mutate(item = strsplit(as.character(combination), "+", fixed = TRUE)) |>
      unnest(item) |>
      mutate(check = check_label(item))
    checks <- levels(droplevels(members$check))
    grid <- expand_grid(combination = top$combination,
                        check = factor(checks, levels = checks)) |>
      left_join(mutate(members, member = TRUE) |> select(combination, check, member),
                by = c("combination", "check")) |>
      mutate(member = coalesce(member, FALSE))
    bars <- ggplot(top, aes(combination, n_obs)) +
      geom_col(width = 0.65, fill = "#cc3355") +
      scale_y_continuous(labels = label_number(scale_cut = cut_short_scale()),
                         expand = expansion(mult = c(0, 0.05))) +
      labs(title = as.character(variable_label(var)), x = NULL,
           y = "Observations") +
      theme(axis.text.x = element_blank(), panel.grid.major.x = element_blank(),
            plot.title = element_text(face = "bold", size = 8))
    dots <- ggplot(grid, aes(combination, forcats::fct_rev(check))) +
      geom_line(data = filter(grid, member), aes(group = combination),
                linewidth = 0.4, colour = "#52514e") +
      geom_point(aes(colour = member), size = 1.6) +
      scale_colour_manual(values = c(`TRUE` = "#0b0b0b", `FALSE` = "#e1e0dc"),
                          guide = "none") +
      labs(x = NULL, y = NULL) +
      theme(axis.text.x = element_blank(), panel.grid = element_blank())
    bars / dots + plot_layout(heights = c(2, 1.4))
  }
  panels <- Filter(Negate(is.null), lapply(meta$variables$name, upset))
  if (length(panels)) {
    plot <- wrap_plots(panels, nrow = 1)
    save_figure(plot, "items_combinations", height = 90)
  }
}

depth <- read_table("items_depth")
if (!is.null(depth)) {
  bands <- as_tibble(meta$pres_bands)
  band_levels <- bands$label[order(bands$band < 0, bands$band)]
  rates <- depth |>
    with_datasets() |>
    filter(pres_band >= 0) |>
    group_by(short, product, region_short, variable, pres_label) |>
    mutate(rate = n_obs / sum(n_obs)) |>
    ungroup() |>
    filter(status %in% c("original_only", "nrtqclib_only")) |>
    mutate(
      status = status_label(status),
      variable = variable_label(variable),
      pres_label = factor(pres_label, levels = rev(band_levels))
    )
  # Colour says the product and the line type the region: five datasets
  # are more than colour alone keeps apart.
  plot <- ggplot(rates, aes(rate, pres_label, colour = product,
                            linetype = region_short, group = short)) +
    geom_path(linewidth = 0.5) +
    geom_point(size = 1.1) +
    facet_grid(variable ~ status, scales = "free_x") +
    scale_x_continuous(labels = label_percent(), limits = c(0, NA)) +
    scale_colour_manual(values = product_colours) +
    labs(x = "Share of the observations in the pressure band",
         y = "Pressure (dbar)") +
    guides(linetype = guide_legend(nrow = 1))
  save_figure(plot, "items_depth", height = 110)
}

shapes <- read_table("items_shape")
if (!is.null(shapes)) {
  # Where each observation one source alone flags sits in its profile, as a
  # share of that source's one-sided observations. One figure per source,
  # for its own page.
  shape_levels <- meta$shapes$label
  shape_colours <- setNames(
    c("#6250d6", "#2a78d6", "#1baf7a", "#eb6834", "#f4b98f", "#b8c0c8"),
    shape_levels
  )
  shape_figure <- function(side, name) {
    shares <- shapes |>
      filter(status == side) |>
      with_datasets() |>
      mutate(shape = factor(meta$shapes$label[match(shape, meta$shapes$key)],
                            levels = shape_levels),
             variable = variable_label(variable)) |>
      group_by(short, variable, shape) |>
      summarise(n_obs = sum(n_obs), .groups = "drop") |>
      group_by(short, variable) |>
      mutate(share = n_obs / sum(n_obs), total = sum(n_obs)) |>
      ungroup()
    if (nrow(shares) == 0) return(invisible(NULL))
    totals <- distinct(shares, short, variable, total)
    plot <- ggplot(shares, aes(share, forcats::fct_rev(short), fill = shape)) +
      geom_col(width = 0.7, colour = "white", linewidth = 0.3,
               position = position_stack(reverse = TRUE)) +
      geom_text(data = totals, inherit.aes = FALSE,
                aes(1, forcats::fct_rev(short), label = comma0(total)),
                hjust = -0.15, size = 2.4) +
      facet_wrap(~variable) +
      scale_fill_manual(values = shape_colours) +
      scale_x_continuous(labels = label_percent(), breaks = c(0, 0.5, 1),
                         expand = expansion(mult = c(0, 0.3))) +
      labs(x = "Share of the observations only this source flags", y = NULL,
           fill = NULL) +
      theme(panel.grid.major.y = element_blank()) +
      guides(fill = guide_legend(nrow = 2))
    save_figure(plot, name, height = 70)
  }
  shape_figure("original_only", "items_shape_original")
  shape_figure("nrtqclib_only", "items_shape_nrtqclib")
}

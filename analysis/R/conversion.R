# Figures of the Data conversion page: what ctddump kept at each step, and
# the data modes it found.

source("analysis/R/setup.R")
message("Data conversion")

# The steps in a word each, for the axis; the page's table gives them in
# full. The numbers follow the order ctddump runs them in.
step_short <- c(
  convert = "1 Converted", dropqc = "2 QC", dropna = "3 All missing",
  filter = "4 Region", dedup = "5 Duplicates", nrtqc = "6 nrtqclib"
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
    labs(x = "ctddump step, then the nrtqclib output",
         y = "Profiles kept, share of converted") +
    theme(axis.text.x = element_text(angle = 40, hjust = 1))
  save_figure(plot, "conversion_steps", height = 82)
}

modes <- read_table("conversion_data_mode")
if (!is.null(modes)) {
  modes <- modes |>
    with_datasets() |>
    filter(variable == "temp") |>
    mutate(
      data_mode = recode(coalesce(data_mode, ""),
                         "R" = "R (real time)", "A" = "A (adjusted)",
                         "D" = "D (delayed mode)", .default = "Unknown"),
      data_mode = factor(data_mode, levels = names(data_mode_colours)),
      step = factor(recode(step, convert = "Converted", dedup = "Kept"),
                    levels = c("Converted", "Kept"))
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
    labs(x = "Temperature observations", y = NULL)
  save_figure(plot, "conversion_data_mode", height = 70)
}

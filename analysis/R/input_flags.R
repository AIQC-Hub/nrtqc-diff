# Figure of the Input QC flags page: how the observations of each product
# split between the input flag values, read the way the site reads them.

source("analysis/R/setup.R")
message("Input QC flags")

# The three ways the site reads an input flag other than good (1), from the
# variables' flag lists in meta.json: an anomaly, no judgement at all, or a
# usable value the producer marked in some other way.
flag_classes <- c(
  "Bad (3, 4, 6, 7)" = "#cc3355",
  "No flag (0, 9 or none)" = "#9a9a9a",
  "Usable, not 1 (2, 5, 8)" = "#2a78d6"
)

flags <- read_table("input_flags")
if (!is.null(flags)) {
  variables <- as_tibble(meta$variables)
  class_of <- function(flag, variable) {
    bad <- variables$bad_flag_values[match(variable, variables$name)]
    missing <- variables$missing_flag_values[match(variable, variables$name)]
    is_bad <- mapply(function(f, b) !is.na(f) && f %in% b, flag, bad)
    is_missing <- mapply(function(f, m) is.na(f) || f %in% m, flag, missing)
    case_when(
      is_bad ~ names(flag_classes)[1],
      is_missing ~ names(flag_classes)[2],
      flag == 1 ~ "Good (1)",
      TRUE ~ names(flag_classes)[3]
    )
  }

  shares <- flags |>
    with_datasets() |>
    mutate(class = class_of(original_flag, variable),
           variable = variable_label(variable)) |>
    group_by(short, variable, class) |>
    summarise(n_obs = sum(n_obs), .groups = "drop") |>
    group_by(short, variable) |>
    mutate(share = n_obs / sum(n_obs)) |>
    ungroup()

  # Good (1) is almost all of every bar, so it is left out and the rest is
  # drawn on its own scale; the label is the share not flagged 1.
  rest <- filter(shares, class != "Good (1)") |>
    mutate(class = factor(class, levels = names(flag_classes)))
  totals <- rest |>
    group_by(short, variable) |>
    summarise(share = sum(share), .groups = "drop")

  plot <- ggplot(rest, aes(share, forcats::fct_rev(short), fill = class)) +
    geom_col(width = 0.7, colour = "white", linewidth = 0.3,
             position = position_stack(reverse = TRUE)) +
    geom_text(data = totals, inherit.aes = FALSE,
              aes(share, forcats::fct_rev(short),
                  label = percent(share, accuracy = 0.01)),
              hjust = -0.15, size = 2.4) +
    facet_wrap(~variable) +
    scale_fill_manual(values = flag_classes, drop = FALSE) +
    scale_x_continuous(labels = label_percent(), expand = expansion(mult = c(0, 0.25))) +
    labs(x = "Observations with an input flag other than 1, share of the product's observations",
         y = NULL)
  save_figure(plot, "input_flags_shares", height = 70)
}

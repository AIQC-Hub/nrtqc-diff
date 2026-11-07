# Figures of the Depth page: where the bathymetry puts profiles on land, how
# far above sea level and from the coast they are, and whether the input's
# QC said anything about them.

source("analysis/R/setup.R")
message("Depth")

# Severity of what the input flags said about a land profile, worst first.
land_classes <- c(
  "Position flagged" = "#cc3355",
  "Every observation flagged" = "#6250d6",
  "Some observations flagged" = "#eb6834",
  "Not flagged" = "#b8c0c8"
)

land <- read_table("depth_land_profiles")
if (!is.null(land)) {
  vars <- meta$variables$name
  bad <- as.matrix(land[paste0(vars, "_n_original_bad")])
  usable <- as.matrix(land[paste0(vars, "_n_usable")])
  every <- rowSums(usable > 0) > 0 & rowSums(usable > 0 & bad < usable) == 0
  land <- land |>
    with_datasets() |>
    mutate(
      class = case_when(
        coalesce(position_qc %in% meta$rules$position_bad_values, FALSE) ~
          "Position flagged",
        every ~ "Every observation flagged",
        rowSums(bad) > 0 ~ "Some observations flagged",
        TRUE ~ "Not flagged"
      ),
      class = factor(class, levels = names(land_classes))
    )

  # How the land profiles split by what the input said, per dataset.
  counts <- land |> count(short, class)
  plot <- ggplot(counts, aes(n, forcats::fct_rev(short), fill = class)) +
    geom_col(width = 0.7, colour = "white", linewidth = 0.3,
             position = position_fill(reverse = TRUE), show.legend = TRUE) +
    scale_fill_manual(values = land_classes, drop = FALSE) +
    scale_x_continuous(labels = label_percent(), expand = expansion(0)) +
    labs(x = "Profiles on land", y = NULL)
  save_figure(plot, "depth_land_qc", height = 62)

  # Height above sea level against distance to the coast: a grid cell that
  # straddles the shore puts a coastal profile a few metres up, close in.
  # One panel per product and region: the products overlap in position, and
  # drawn together the largest hid the others. Heights of 0 m, the
  # shoreline itself, are drawn at 0.5 m so that the log scale keeps them.
  plot <- ggplot(land, aes(pmax(dist_to_coast, 0.01), pmax(elevation, 0.5))) +
    geom_point(colour = "#008300", size = 0.8, alpha = 0.35, stroke = 0) +
    facet_grid(product ~ region) +
    scale_x_log10(labels = label_number(drop0trailing = TRUE)) +
    scale_y_log10(labels = label_number(drop0trailing = TRUE)) +
    labs(x = "Distance to the coast (km)",
         y = "Height above sea level (m); 0 m drawn at 0.5 m")
  save_figure(plot, "depth_land_coast", height = 110)

  # Where they are: the Arctic around the pole, the two seas framed on their
  # own profiles. Unflagged profiles are drawn first, so the few the input
  # did flag sit on top.
  world <- map_data("world")
  land <- arrange(land, desc(class))
  map_panel <- function(name, polar = FALSE) {
    here <- filter(land, region == name)
    if (nrow(here) == 0) return(NULL)
    panel <- ggplot() +
      geom_point(data = here, aes(longitude, latitude, colour = class),
                 size = 0.9, alpha = 0.7, stroke = 0, show.legend = TRUE) +
      scale_colour_manual(values = land_classes, drop = FALSE) +
      labs(title = name, x = NULL, y = NULL) +
      theme(plot.title = element_text(face = "bold", size = 8),
            panel.grid.major = element_line(colour = "#ecebe7", linewidth = 0.2),
            axis.text = element_text(size = 6))
    if (polar) {
      south <- floor(min(here$latitude) / 5) * 5 - 2
      shore <- filter(world, lat >= south - 10)
      panel$layers <- c(
        geom_polygon(data = shore, aes(long, lat, group = group),
                     fill = "#ecebe7", colour = "#c9c8c2", linewidth = 0.15),
        panel$layers
      )
      panel + coord_map("ortho", orientation = c(90, 0, 0), ylim = c(south, 90)) +
        scale_x_continuous(breaks = NULL) +
        scale_y_continuous(breaks = NULL)
    } else {
      pad_x <- max(1, diff(range(here$longitude)) * 0.08)
      pad_y <- max(0.5, diff(range(here$latitude)) * 0.08)
      panel$layers <- c(
        geom_polygon(data = world, aes(long, lat, group = group),
                     fill = "#ecebe7", colour = "#c9c8c2", linewidth = 0.15),
        panel$layers
      )
      panel + coord_quickmap(xlim = range(here$longitude) + c(-pad_x, pad_x),
                             ylim = range(here$latitude) + c(-pad_y, pad_y),
                             expand = FALSE)
    }
  }
  arctic <- map_panel(region_levels[1], polar = TRUE)
  seas <- Filter(Negate(is.null), lapply(region_levels[-1], map_panel))
  plot <- (arctic | wrap_plots(seas, ncol = 1)) +
    plot_layout(widths = c(1.15, 1), guides = "collect") &
    theme(legend.position = "bottom") &
    guides(colour = guide_legend(override.aes = list(size = 2.2, alpha = 1)))
  save_figure(plot, "depth_land_map", height = 120)
}

hist <- read_table("depth_elevation_hist")
if (!is.null(hist)) {
  # The lowest bin gathers every profile deeper than the reach, most of
  # each dataset, and would flatten the rest; the page counts them instead.
  reach <- min(hist$bin_lo)
  hist <- hist |>
    filter(bin_lo > reach) |>
    with_datasets() |>
    mutate(side = if_else(bin_lo >= 0, "At or above sea level", "Under water"))
  plot <- ggplot(hist, aes(bin_lo + (bin_hi - bin_lo) / 2, n_profiles, fill = side)) +
    geom_col(width = 4.2) +
    geom_vline(xintercept = 0, linewidth = 0.3, colour = "#52514e") +
    facet_wrap(~short, ncol = 3, scales = "free_y") +
    scale_fill_manual(values = c("At or above sea level" = "#008300",
                                 "Under water" = "#9ab7d9")) +
    scale_y_sqrt(labels = comma0) +
    labs(x = "Sea floor height at the position (m); the last bar gathers everything higher",
         y = "Profiles (square root scale)")
  save_figure(plot, "depth_elevation", height = 110)
}

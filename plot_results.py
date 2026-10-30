#!/usr/bin/env python3
"""Plot numeric data from one or more CSV files or directories of CSV files."""

import argparse
import csv
import logging
from pathlib import Path
import re
import sys

import matplotlib.pyplot as plt


# By default, curves are evenly spaced across this colormap. Set PLOT_COLORS
# to a list of Matplotlib colors to use a custom palette instead.
PLOT_COLORMAP = "magma"
PLOT_COLORS = None
TEXT_SIZE = 12
SHOW_LEGEND = False

logger = logging.getLogger(__name__)
METADATA_PATTERN = re.compile(r"^\s*#\s*([^=]+?)\s*=\s*(.*?)\s*$")


def read_csv_data(path, x_column, y_column):
    """Read numeric X/Y columns and parameter metadata from a CSV file."""
    metadata = {}
    with path.open("r", newline="", encoding="utf-8") as input_file:
        content = []
        for line in input_file:
            match = METADATA_PATTERN.match(line)
            if match:
                key, value = match.groups()
                metadata[key.strip()] = value.strip()
            elif not line.lstrip().startswith("#"):
                content.append(line)

    reader = csv.DictReader(content)
    if not reader.fieldnames:
        raise ValueError(f"{path}: CSV header is missing.")
    missing_columns = [
        column
        for column in (x_column, y_column)
        if column not in reader.fieldnames
    ]
    if missing_columns:
        raise ValueError(
            f"{path}: requested column(s) not found: "
            f"{', '.join(missing_columns)}. "
            f"Available columns: {', '.join(reader.fieldnames)}"
        )

    x_values = []
    y_values = []
    for row_number, row in enumerate(reader, start=2):
        x_text = (row.get(x_column) or "").strip()
        y_text = (row.get(y_column) or "").strip()
        if not x_text and not y_text:
            continue
        try:
            x_values.append(float(x_text))
            y_values.append(float(y_text))
        except ValueError as error:
            raise ValueError(
                f"{path}: non-numeric value in requested columns "
                f"on data row {row_number}."
            ) from error

    if not x_values:
        raise ValueError(f"{path}: no numeric data found.")
    return x_values, y_values, metadata


def read_metadata(path):
    """Read key/value metadata comments from a CSV file."""
    metadata = {}
    with path.open("r", encoding="utf-8") as input_file:
        for line in input_file:
            if not line.lstrip().startswith("#"):
                break
            match = METADATA_PATTERN.match(line)
            if match:
                key, value = match.groups()
                metadata[key.strip()] = value.strip()
    return metadata


def metadata_number(metadata, parameter, path):
    """Get a numeric parameter value from metadata, ignoring its unit."""
    value = metadata.get(parameter)
    if value is None:
        return None
    match = re.match(
        r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?",
        value,
    )
    if not match:
        raise ValueError(
            f"{path}: metadata parameter '{parameter}' is not numeric: "
            f"{value}"
        )
    return float(match.group())


def discover_csv_files(inputs):
    """Expand explicit files/directories and return sorted unique CSV paths."""
    files = []
    for input_path in inputs:
        if not input_path.exists():
            raise FileNotFoundError(f"Input path does not exist: {input_path}")
        if input_path.is_file():
            if input_path.suffix.lower() != ".csv":
                raise ValueError(f"Input file is not a CSV: {input_path}")
            files.append(input_path)
        elif input_path.is_dir():
            files.extend(input_path.glob("*.csv"))
        else:
            raise ValueError(
                f"Input path is not a regular file or directory: {input_path}"
            )
    return sorted({path.resolve() for path in files})


def create_argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        help=(
            "CSV files and/or directories containing CSV files. "
            "Defaults to the results/ directory."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Save the figure instead of displaying it interactively",
    )
    parser.add_argument(
        "--x-column",
        default="bias_voltage_v",
        help=(
            "Numeric CSV column for the horizontal axis "
            "(default: bias_voltage_v)"
        ),
    )
    parser.add_argument(
        "--y-column",
        default="drain_current_a",
        help=(
            "Numeric CSV column for the vertical axis "
            "(default: drain_current_a)"
        ),
    )
    parser.add_argument(
        "--parameter",
        help="Filter on a numeric parameter recorded in CSV metadata comments",
    )
    parser.add_argument(
        "--min",
        dest="minimum",
        type=float,
        help=(
            "Inclusive minimum value for --parameter "
            "(in the CSV metadata unit)"
        ),
    )
    parser.add_argument(
        "--max",
        dest="maximum",
        type=float,
        help=(
            "Inclusive maximum value for --parameter "
            "(in the CSV metadata unit)"
        ),
    )
    return parser


def main(argv=None):
    parser = create_argument_parser()
    args = parser.parse_args(argv)

    if PLOT_COLORS is not None and not PLOT_COLORS:
        raise ValueError("PLOT_COLORS must contain at least one Matplotlib color.")
    if TEXT_SIZE <= 0:
        raise ValueError("TEXT_SIZE must be a positive number.")

    if args.parameter is None and (
        args.minimum is not None or args.maximum is not None
    ):
        parser.error("--min and --max require --parameter.")
    if args.parameter is not None and args.minimum is None and args.maximum is None:
        parser.error("--parameter requires at least one of --min or --max.")
    if (
        args.minimum is not None
        and args.maximum is not None
        and args.minimum > args.maximum
    ):
        parser.error("--min must be less than or equal to --max.")

    inputs = args.inputs or [Path("results")]
    files = discover_csv_files(inputs)
    if not files:
        parser.error(
            f"No CSV files found in input(s): {', '.join(map(str, inputs))}"
        )
    if args.output is not None and args.output.resolve() in files:
        parser.error("The output image path must not overwrite an input CSV file.")

    figure, axis = plt.subplots(figsize=(10, 6))
    plotted_curves = []
    plotted_files = []
    skipped_without_metadata = 0
    for path in files:
        if args.parameter is not None:
            metadata = read_metadata(path)
            value = metadata_number(metadata, args.parameter, path)
            if value is None:
                skipped_without_metadata += 1
                continue
            if args.minimum is not None and value < args.minimum:
                continue
            if args.maximum is not None and value > args.maximum:
                continue

        x_values, y_values, _ = read_csv_data(
            path,
            args.x_column,
            args.y_column,
        )

        plotted_curves.append((path, x_values, y_values))
        plotted_files.append(path)

    plotted_count = len(plotted_curves)
    if PLOT_COLORS is None:
        colormap = plt.get_cmap(PLOT_COLORMAP)
        colors = [
            colormap(0.5)
            if plotted_count == 1
            else colormap(index / (plotted_count - 1))
            for index in range(plotted_count)
        ]
    else:
        colors = [
            PLOT_COLORS[index % len(PLOT_COLORS)]
            for index in range(plotted_count)
        ]

    for (path, x_values, y_values), color in zip(plotted_curves, colors):
        axis.plot(
            x_values,
            y_values,
            linewidth=1.5,
            color=color,
            label=path.stem,
        )

    if not plotted_count:
        plt.close(figure)
        filter_message = (
            f" with {args.parameter} in range "
            f"[{args.minimum if args.minimum is not None else '-inf'}, "
            f"{args.maximum if args.maximum is not None else 'inf'}]"
            if args.parameter is not None
            else ""
        )
        raise ValueError(
            f"No CSV files matched the requested columns{filter_message}."
        )

    axis.set_xlabel(args.x_column, fontsize=TEXT_SIZE)
    axis.set_ylabel(args.y_column, fontsize=TEXT_SIZE)
    title = (
        plotted_files[0].stem
        if plotted_count == 1
        else f"{args.y_column} vs. {args.x_column}"
    )
    axis.set_title(title, fontsize=TEXT_SIZE)
    axis.grid(True, alpha=0.3)
    axis.tick_params(axis="both", labelsize=TEXT_SIZE)
    if SHOW_LEGEND:
        axis.legend(title="File", fontsize=TEXT_SIZE, title_fontsize=TEXT_SIZE)
    figure.tight_layout()

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(args.output, dpi=200)
        logger.info("Saved plot to %s", args.output)
    else:
        plt.show()

    if skipped_without_metadata:
        logger.info(
            "Skipped %d file(s) without '%s' metadata.",
            skipped_without_metadata,
            args.parameter,
        )
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    try:
        raise SystemExit(main())
    except (OSError, ValueError, csv.Error) as error:
        logger.error("%s", error)
        raise SystemExit(1) from error

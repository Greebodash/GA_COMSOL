#!/usr/bin/env python3
"""Plot all transmission spectra in a directory."""

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt

##conda run -n mph python plot_spectra.py spectra --output spectra.png
def extract_h_d_nm(path):
    """Return H_d in nanometers from a filename such as H_d_225nm__..."""
    prefix = path.stem.split("__", 1)[0]
    value = prefix.removeprefix("H_d_").removesuffix("nm")
    return float(value.replace("p", "."))


def read_spectrum(path):
    frequency = []
    transmission = []

    with path.open("r", newline="", encoding="utf-8") as spectrum_file:
        reader = csv.reader(
            line for line in spectrum_file if not line.lstrip().startswith("#")
        )
        next(reader, None)
        for row in reader:
            if len(row) < 2:
                continue
            try:
                frequency.append(float(row[0]))
                transmission.append(float(row[1]))
            except ValueError:
                continue

    return frequency, transmission


def make_label(path):
    label = path.stem.replace("__", ", ").replace("_", "=")
    return label.replace("p", ".")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=Path("spectra"),
        help="Directory containing spectrum CSV files (default: spectra)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Save the figure instead of only displaying it",
    )
    parser.add_argument(
        "--min-h-d",
        type=float,
        help="Plot only spectra with H_d greater than this value in nm",
    )
    args = parser.parse_args()

    files = sorted(args.directory.glob("*.csv"))
    if args.min_h_d is not None:
        files = [path for path in files if extract_h_d_nm(path) > args.min_h_d]
    if not files:
        raise SystemExit(f"No matching CSV spectra found in {args.directory}")

    figure, axis = plt.subplots(figsize=(10, 6))
    for path in files:
        frequency, transmission = read_spectrum(path)
        if frequency:
            axis.plot(frequency, transmission, linewidth=1.5, label=make_label(path))

    axis.set_xlabel("Frequency (THz)")
    axis.set_ylabel("Transmission")
    axis.set_title("Transmission spectra")
    axis.grid(True, alpha=0.3)
    axis.legend(title="Geometry", fontsize="small")
    figure.tight_layout()

    if args.output:
        figure.savefig(args.output, dpi=200)
        print(f"Saved plot to {args.output}")
    else:
        plt.show()


if __name__ == "__main__":
    main()


import logging
import os
import sys
import time

import mph
import numpy as np
from scipy.signal import find_peaks, peak_widths
import pygad


# ======================================================================
# LOGGING
# ======================================================================

def get_logger():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger(__name__)


def set_model_parameter(model, name, value, unit=""):
    """Set a COMSOL model parameter, appending its unit when needed."""
    name = str(name).strip()
    value_text = str(value).strip()
    if not name:
        raise ValueError("Parameter name cannot be empty.")
    if not value_text:
        raise ValueError(f"Parameter '{name}' has no value.")

    unit = str(unit).strip()
    if unit and not value_text.endswith("]") and "[" not in value_text:
        try:
            numeric_value = float(value_text)
        except ValueError:
            expression = f"{value_text}[{unit}]"
        else:
            expression = f"{numeric_value:.12g}[{unit}]"
    else:
        expression = value_text

    model.parameter(name, expression)


# ======================================================================
# RUN COMSOL SIMULATION
# ======================================================================

def run_comsol_simulation(input_file, output_file):

    logger = get_logger()

    if not os.path.exists(input_file):
        logger.error(
            f"Input file not found: {os.path.abspath(input_file)}"
        )
        return

    logger.info("Starting COMSOL Multiphysics MPh Automation Script.")

    start_time = time.time()
    client = None

    try:

        # ==============================================================
        # 1. INITIALIZE COMSOL CLIENT
        # ==============================================================

        logger.info("Connecting to COMSOL backend instance...")

        client = mph.start()

        logger.info(
            f"Connected successfully to COMSOL version: "
            f"{client.version}"
        )

        # ==============================================================
        # 2. LOAD MODEL
        # ==============================================================

        logger.info(f"Loading COMSOL model file: {input_file}")

        model = client.load(input_file)

        logger.info(
            "Model loaded successfully into Java backend memory."
        )

        # ==============================================================
        # 3. INSPECT GLOBAL PARAMETERS
        # ==============================================================

        logger.info("Retrieving existing global parameters:")

        for name, value in model.parameters().items():
            logger.info(
                f"  - Parameter '{name}': {value}"
            )

        # ==============================================================
        # 4. SOLVE MODEL
        # ==============================================================

        logger.info(
            "Initiating study solving sequence. "
            "This may take time..."
        )

        solve_start = time.time()

        model.solve()

        solve_duration = time.time() - solve_start

        logger.info(
            f"Simulation solve sequence completed in "
            f"{solve_duration:.2f} seconds."
        )

        # ==============================================================
        # 5. SAVE MODEL
        # ==============================================================

        logger.info(
            f"Saving solved simulation file to: {output_file}"
        )

        model.save(output_file)

        logger.info("File saved to disk successfully.")

    except Exception as e:

        logger.critical(
            f"An unexpected error occurred during execution: {str(e)}",
            exc_info=True,
        )

    finally:

        # ==============================================================
        # 6. CLEANUP
        # ==============================================================

        if client:

            logger.info(
                "Disconnecting client and shutting down COMSOL server."
            )

            client.clear()

        total_duration = time.time() - start_time

        logger.info(
            f"Script execution finished. "
            f"Total elapsed time: {total_duration:.2f} seconds."
        )


# ======================================================================
# EXTRACT TABLE FROM A COMSOL EVALUATION
# ======================================================================

def extract_table(
    model,
    evaluation_tag="gev1",
    temporary_table_tag="ga_temp_table",
    require_min_points=True,
    print_table=False,
):
    """
    Run a COMSOL numerical evaluation and extract its freshly
    generated table.

    Parameters
    ----------
    model :
        MPh model object.

    evaluation_tag : str
        COMSOL numerical evaluation feature tag.

        Example:
            "gev1"

        This is the evaluation feature, NOT the table tag.

    temporary_table_tag : str
        Table used exclusively for this extraction.

        The table is deleted and recreated on every call so that
        no data from a previous GA evaluation can survive.

    require_min_points : bool
        Require at least three rows for spectrum analysis.

    print_table : bool
        Print the raw table returned by COMSOL before extraction.

    Returns
    -------
    frequency_thz : numpy.ndarray
        Frequency axis in THz.

    transmission : numpy.ndarray
        Evaluated transmission values.
    """

    logger = get_logger()

    java_model = model.java

    # ==================================================================
    # 1. VERIFY EVALUATION FEATURE EXISTS
    # ==================================================================

    numerical_features = java_model.result().numerical()
    numerical_tags = list(numerical_features.tags())

    if evaluation_tag not in numerical_tags:
        raise ValueError(
            f"COMSOL numerical evaluation '{evaluation_tag}' "
            f"does not exist. Available evaluations: {numerical_tags}"
        )

    evaluation = numerical_features.get(evaluation_tag)

    logger.info(
        f"Using COMSOL numerical evaluation: {evaluation_tag}"
    )

    # ==================================================================
    # 2. GET TABLE COLLECTION
    # ==================================================================

    tables = java_model.result().table()

    # ==================================================================
    # 3. DELETE OLD TEMPORARY TABLE
    # ==================================================================

    table_tags = list(tables.tags())

    if temporary_table_tag in table_tags:

        logger.info(
            f"Removing previous temporary table "
            f"'{temporary_table_tag}'."
        )

        tables.remove(temporary_table_tag)

    # ==================================================================
    # 4. CREATE A COMPLETELY NEW TABLE
    # ==================================================================

    logger.info(
        f"Creating fresh COMSOL table '{temporary_table_tag}'."
    )

    tables.create(
        temporary_table_tag,
        "Table",
    )

    table = tables.get(temporary_table_tag)

    # ==================================================================
    # 5. CLEAR TABLE JUST IN CASE
    # ==================================================================

    try:
        table.clearTableData()
    except Exception:
        # Some COMSOL versions may not expose this method.
        # The table is already brand new, so this is harmless.
        pass

    # ==================================================================
    # 6. CONNECT gev1 -> NEW TABLE
    # ==================================================================

    logger.info(
        f"Assigning evaluation '{evaluation_tag}' "
        f"to table '{temporary_table_tag}'."
    )

    evaluation.set(
        "table",
        temporary_table_tag,
    )

    # ==================================================================
    # 7. RUN THE EVALUATION
    # ==================================================================

    logger.info(
        f"Executing COMSOL evaluation '{evaluation_tag}'..."
    )

    evaluation.run()
    evaluation.setResult()

    logger.info(
        f"Evaluation '{evaluation_tag}' completed."
    )

    # ==================================================================
    # 8. READ ONLY THE NEW TABLE
    # ==================================================================

    table_data = table.getReal()

    if table_data is None:
        raise ValueError(
            f"COMSOL evaluation '{evaluation_tag}' produced "
            f"no table data."
        )

    # COMSOL Java returns a Java array structure.
    clean_matrix = np.array(
        [list(column) for column in table_data],
        dtype=float,
    )

    logger.info(
        f"Fresh table '{temporary_table_tag}' shape: "
        f"{clean_matrix.shape}"
    )

    if print_table:
        print(
            f"COMSOL table '{temporary_table_tag}' "
            f"shape={clean_matrix.shape}:"
        )
        print(clean_matrix)

    # ==================================================================
    # 9. VALIDATE TABLE
    # ==================================================================

    if clean_matrix.ndim != 2:
        raise ValueError(
            f"COMSOL table '{temporary_table_tag}' is not 2D. "
            f"Shape = {clean_matrix.shape}"
        )

    if clean_matrix.shape[1] < 2:
        raise ValueError(
            f"COMSOL table '{temporary_table_tag}' contains fewer "
            f"than 2 columns. Shape = {clean_matrix.shape}"
        )

    if clean_matrix.shape[0] == 0:
        raise ValueError(
            f"COMSOL table '{temporary_table_tag}' is empty."
        )

    # ==================================================================
    # 10. EXTRACT FREQUENCY AND TRANSMISSION
    # ==================================================================

    frequency_thz = clean_matrix[:, -2] / 1e12
    transmission = clean_matrix[:, -1]

    # ==================================================================
    # 11. VALIDATE NUMERICAL DATA
    # ==================================================================

    if not np.all(np.isfinite(frequency_thz)):
        raise ValueError(
            "Frequency data contains NaN or Inf."
        )

    if not np.all(np.isfinite(transmission)):
        raise ValueError(
            "Transmission data contains NaN or Inf."
        )

    if require_min_points and len(frequency_thz) < 3:
        raise ValueError(
            "Not enough frequency points to analyze spectrum."
        )

    # ==================================================================
    # 12. LOG DATA IDENTITY
    # ==================================================================

    logger.info(
        f"Extracted {len(frequency_thz)} spectrum points."
    )

    logger.info(
        f"Frequency range: "
        f"{frequency_thz.min():.6f} - "
        f"{frequency_thz.max():.6f} THz"
    )

    logger.info(
        f"Transmission range: "
        f"{transmission.min():.6g} - "
        f"{transmission.max():.6g}"
    )

    logger.info(
        f"Fresh evaluation complete for '{evaluation_tag}'."
    )

    # ==================================================================
    # 13. OPTIONAL CLEANUP
    # ==================================================================

    # The table can be removed here because we already copied the
    # numerical data into NumPy arrays.
    #
    # If you want to inspect the table manually inside COMSOL after
    # the run, comment out this section.

    try:

        tables.remove(temporary_table_tag)

        logger.debug(
            f"Removed temporary table '{temporary_table_tag}'."
        )

    except Exception as exc:

        logger.warning(
            f"Could not remove temporary table "
            f"'{temporary_table_tag}': {exc}"
        )

    return frequency_thz, transmission


# ======================================================================
# CALCULATE FWHM / Q
# ======================================================================

def calculate_fwhm(
    freq_data,
    transmission,
    target_freq,
):

    transmission = np.asarray(
        transmission,
        dtype=float,
    )

    freq_data = np.asarray(
        freq_data,
        dtype=float,
    )

    if len(freq_data) != len(transmission):
        raise ValueError(
            "Frequency and transmission arrays have different lengths."
        )

    if len(freq_data) < 3:
        raise ValueError(
            "Not enough data points to calculate FWHM."
        )

    if not np.all(np.isfinite(freq_data)):
        raise ValueError(
            "Frequency data contains NaN or Inf."
        )

    if not np.all(np.isfinite(transmission)):
        raise ValueError(
            "Transmission data contains NaN or Inf."
        )

    # --------------------------------------------------------------
    # Remove baseline
    # --------------------------------------------------------------

    transmission_min = np.min(transmission)

    transmission_normalized = (
        transmission - transmission_min
    )

    data_range = np.ptp(
        transmission_normalized
    )

    if data_range <= 0:
        raise ValueError(
            "Transmission spectrum has zero dynamic range."
        )

    # --------------------------------------------------------------
    # Find peaks
    # --------------------------------------------------------------

    peaks, _ = find_peaks(
        transmission_normalized,
        prominence=0.05 * data_range,
    )

    if len(peaks) == 0:
        raise ValueError(
            "No distinct transmission peaks found."
        )

    # --------------------------------------------------------------
    # Select peak closest to target frequency
    # --------------------------------------------------------------

    closest_peak_idx = peaks[
        np.argmin(
            np.abs(
                freq_data[peaks] - target_freq
            )
        )
    ]

    peak_freq = float(
        freq_data[closest_peak_idx]
    )

    # --------------------------------------------------------------
    # Calculate FWHM
    # --------------------------------------------------------------

    widths, _, _, _ = peak_widths(
        transmission_normalized,
        [closest_peak_idx],
        rel_height=0.5,
    )

    if len(widths) == 0:
        raise ValueError(
            "Could not determine peak width."
        )

    # Use actual frequency coordinates rather than assuming
    # perfectly uniform frequency spacing.
    left_index = max(
        int(np.floor(
            closest_peak_idx - widths[0] / 2
        )),
        0,
    )

    right_index = min(
        int(np.ceil(
            closest_peak_idx + widths[0] / 2
        )),
        len(freq_data) - 1,
    )

    if right_index <= left_index:
        raise ValueError(
            "Invalid FWHM boundaries."
        )

    fwhm = float(
        freq_data[right_index]
        - freq_data[left_index]
    )

    # Fallback for very coarse grids.
    if fwhm <= 0:
        freq_resolution = float(
            np.mean(np.diff(freq_data))
        )

        fwhm = float(
            widths[0] * freq_resolution
        )

    if fwhm <= 0:
        raise ValueError(
            "Calculated FWHM is zero or negative."
        )

    q_factor = float(
        peak_freq / fwhm
    )

    return (
        peak_freq,
        fwhm,
        q_factor,
    )


# ======================================================================
# START COMSOL
# ======================================================================

def start_comsol(input_file):

    logger = get_logger()

    if not os.path.exists(input_file):

        raise FileNotFoundError(
            f"COMSOL model not found: "
            f"{os.path.abspath(input_file)}"
        )

    logger.info(
        "Starting COMSOL backend..."
    )

    client = mph.start()

    logger.info(
        f"Connected successfully to COMSOL version: "
        f"{client.version}"
    )

    logger.info(
        f"Loading COMSOL model: {input_file}"
    )

    model = client.load(input_file)

    logger.info(
        "Model loaded successfully."
    )

    return client, model

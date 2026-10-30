#!/usr/bin/env python3
"""
Fit a density-dependent mobility model to the experimental MoS2 Id(Vg) curve
(Figure 4 of the MoS2-FET repo's Report.pdf, monolayer, Vd = 50 mV).

WHY
---
The repo's COMSOL model uses a constant mobility, so Id(Vg) comes out linear
above threshold. The experiment is roughly parabolic (Id ~ (Vg - Vt)^2),
which is what you expect when Coulomb scattering from charged impurities
weakens as gate-induced carriers screen it, so mobility rises with density.

THE MODEL (above threshold, low Vd, uniform channel)
----------------------------------------------------
    dV      = Vg - Vt                          gate overdrive
    n       = (Cox / (q * t_MoS2)) * dV        3D electron density in the layer
    mu_C    = mu_C0 * (n / n_ref)**alpha       Coulomb-limited mobility
    mu_eff  = 1 / (1/mu_C + 1/mu_ph)           Matthiessen's rule (phonon cap)
    Id      = G * (mu_eff / 100) * dV          linear-regime drain current

G (nA/V at mu = 100 cm^2/Vs) is calibrated from the COMSOL model itself: a run
with constant mobility 100 cm^2/Vs gave a slope of 148 nA/V (the ideal formula
mu*Cox*(W/L)*Vd gives 112; the extra ~1.3x is geometry in the 2D model).
With this calibration the model predicted the COMSOL alpha=1 run within ~5%.

Free parameters: Vt, mu_C0 (mobility at n_ref), mu_ph, alpha.
n_ref is fixed (1e19 cm^-3) because it is degenerate with mu_C0.
Only Vg >= 6 V is fitted: the 0-6 V tail (1-9 nA) is subthreshold/trap
behaviour that this model does not describe.

KNOWN LIMITS
------------
* alpha and Vt trade off: alpha=1 with Vt~+2.6 V and alpha=2 with Vt~-0.8 V
  fit about equally well. The script prints both. alpha=2 needs much less
  change to the device (see nd_for_vt).
* Threshold vs doping, measured from COMSOL constant-mobility runs:
  Vt = VT0 - Nd * t_MoS2 * q / Cox, with VT0 ~ -0.80 V.
* In COMSOL, a floor on mu (mu_min ~ 1 cm^2/Vs) was needed so the thin region
  next to each contact (where n is tiny) does not dominate the resistance.

USAGE
-----
    python fit_mos2_mobility.py                 # uses the embedded data
    python fit_mos2_mobility.py --pdf Report.pdf  # re-digitize Figure 4
Needs numpy, scipy, matplotlib (and Pillow + poppler's pdftoppm for --pdf).
"""
import argparse
import subprocess

import numpy as np
from scipy.optimize import least_squares

# ----------------------------------------------------------------------------
# Device constants (monolayer model, MoS2_ML.mph)
# ----------------------------------------------------------------------------
Q = 1.602e-19             # C
EPS0 = 8.854e-14          # F/cm
COX = EPS0 * 3.9 / 300e-7  # F/cm^2, 300 nm SiO2
T_MOS2 = 0.7e-7           # cm
N_PER_V = COX / (Q * T_MOS2)  # cm^-3 per volt of overdrive (~1.03e18)
G_CAL = 148.0             # nA/V at mu = 100 cm^2/Vs (from COMSOL)
N_REF = 1e19              # cm^-3, fixed reference density
VT0 = -0.80               # V, threshold of an undoped channel
FIT_FROM_VG = 6.0         # V, fit only above this gate voltage

# Experimental curve digitized from Report.pdf, Figure 4 (Vg in V, Id in nA)
EXP = np.array([
    (0, 1.1), (0.5, 1.8), (1, 2.2), (1.5, 2.8), (2, 3.2), (2.5, 3.8), (3, 4.2),
    (3.5, 4.9), (4, 5.5), (4.5, 5.9), (5, 6.9), (5.5, 7.9), (6, 9), (6.5, 10.2),
    (7, 11.9), (7.5, 13.3), (8, 15.3), (8.5, 17.5), (9, 21), (9.5, 24.8),
    (10, 29), (10.5, 33.1), (11, 38.1), (11.5, 42.4), (12, 47.2), (12.5, 51.9),
    (13, 56.5), (13.5, 62), (14, 68.6), (14.5, 74.6), (15, 80.2), (15.5, 86.6),
    (16, 95.3), (16.5, 101.9), (17, 110.3), (17.5, 117.6), (18, 126.9),
    (18.5, 134.3), (19, 143.8), (19.5, 153.5), (20, 160.5)])


# ----------------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------------
def mu_eff(n, mu_c0, alpha, mu_ph, mu_min=0.0, n_ref=N_REF):
    """Mobility (cm^2/Vs) at 3D density n (cm^-3). Same form as the COMSOL
    variable: 1/(1/max(mu_C0*(n/n_ref)^alpha, mu_min) + 1/mu_ph)."""
    mu_c = np.maximum(mu_c0 * (n / n_ref) ** alpha, mu_min)
    return 1.0 / (1.0 / mu_c + 1.0 / mu_ph)


def id_model(vg, vt, mu_c0, mu_ph, alpha, n_ref=N_REF, mu_min=0.0):
    """Drain current (nA) at Vd = 50 mV for gate voltage vg (V)."""
    vg = np.asarray(vg, float)
    dv = np.clip(vg - vt, 1e-9, None)
    mu = mu_eff(N_PER_V * dv, mu_c0, alpha, mu_ph, mu_min, n_ref)
    return np.where(vg > vt, G_CAL * mu / 100.0 * dv, 0.0)


def nd_for_vt(vt):
    """Donor concentration (cm^-3) that puts the threshold at vt (V)."""
    return max((VT0 - vt) * COX / (Q * T_MOS2), 0.0)


# ----------------------------------------------------------------------------
# Fitting
# ----------------------------------------------------------------------------
def fit(vg, i, alpha=None):
    """Multi-start least squares. alpha=None fits alpha too."""
    free_alpha = alpha is None
    lo = [-10, 0.05, 5] + ([0.0] if free_alpha else [])
    hi = [10, 500, 3000] + ([4.0] if free_alpha else [])
    best = None
    for vt0 in (-2, 0, 2, 4):
        for m0 in (3, 10, 30):
            for mp in (50, 150, 500):
                x0 = [vt0, m0, mp] + ([1.0] if free_alpha else [])

                def resid(p):
                    a = p[3] if free_alpha else alpha
                    return id_model(vg, p[0], p[1], p[2], a) - i

                r = least_squares(resid, x0, bounds=(lo, hi))
                if best is None or r.cost < best.cost:
                    best = r
    p = list(best.x) + ([] if free_alpha else [alpha])
    rms = float(np.sqrt(2 * best.cost / len(vg)))
    return dict(vt=p[0], mu_c0=p[1], mu_ph=p[2], alpha=p[3], rms=rms)


# ----------------------------------------------------------------------------
# Optional: re-digitize Figure 4 from the PDF (page 7, rendered at 400 dpi)
# ----------------------------------------------------------------------------
def digitize_pdf(pdf, page=7, dpi=400):
    from PIL import Image
    subprocess.run(["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page),
                    "-png", pdf, "_fig"], check=True)
    png = f"_fig-{page:02d}.png"
    a = np.asarray(Image.open(png).convert("RGB")).astype(int)
    # axes frame of Figure 4 in the 400 dpi render: Vg -20..20, Id 0..250 nA
    xl, xr, yt, yb = 960.0, 2469.0, 2366.5, 3577.5
    x0 = int(xl + 17 / 40 * (xr - xl))            # keep Vg >= -3 (skip legend)
    sub = a[int(yt):int(yb) + 1, x0:int(xr)]
    mask = np.abs(sub - np.array([126, 47, 142])).sum(axis=2) < 90  # purple
    ys, xs = np.nonzero(mask)
    vg = -20 + (xs + x0 - xl) / (xr - xl) * 40
    idn = (yb - (ys + int(yt))) / (yb - yt) * 250
    out = {}
    for v, c in zip(np.round(vg * 2) / 2, idn):   # bin to 0.5 V, take median
        out.setdefault(v, []).append(c)
    return np.array([(v, np.median(c)) for v, c in sorted(out.items()) if v >= 0])


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", help="Report.pdf: re-digitize Figure 4 instead of "
                                  "using the embedded points")
    ap.add_argument("--plot", default="mos2_idvg_fit.png")
    args = ap.parse_args()

    data = digitize_pdf(args.pdf) if args.pdf else EXP
    vg, i = data[:, 0], data[:, 1]
    m = vg >= FIT_FROM_VG

    fits = {"alpha free": fit(vg[m], i[m]),
            "alpha = 2": fit(vg[m], i[m], 2.0),
            "alpha = 1": fit(vg[m], i[m], 1.0)}

    print(f"Fitting {m.sum()} points, Vg >= {FIT_FROM_VG} V\n")
    for name, f in fits.items():
        cap = "  (at upper bound: no phonon cap needed)" if f["mu_ph"] > 2990 else ""
        print(f"{name:11s} Vt={f['vt']:6.2f} V  mu_C0={f['mu_c0']:6.2f}  "
              f"mu_ph={f['mu_ph']:7.1f}  alpha={f['alpha']:4.2f}  "
              f"RMS={f['rms']:.2f} nA{cap}")

    f = fits["alpha = 2"]
    print("\nCOMSOL parameters for the alpha = 2 fit (n_ref fixed at 1e19):")
    print(f"  alpha_mu 2\n  mu_C0    {f['mu_c0']:.2f}[cm^2/(V*s)]\n"
          f"  mu_ph    {f['mu_ph']:.1f}[cm^2/(V*s)]\n  n_ref    1e19[1/cm^3]\n"
          f"  mu_min   1[cm^2/(V*s)]   (contact-region floor; see docstring)")
    print(f"  donor_concentration ~ {nd_for_vt(f['vt']):.0e}[1/cm^3] to put Vt "
          f"near {f['vt']:.2f} V (+/- {0.1 * N_PER_V:.0e} for a +/-0.1 V "
          f"uncertainty in Vt, so 'nearly undoped')")
    print("  mu_ele = 1/(1/max(mu_C0*(max(semi.N,1[1/cm^3])/n_ref)^alpha_mu,"
          "mu_min)+1/mu_ph)")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    v = np.linspace(-2, 20, 300)
    fig, ax = plt.subplots(figsize=(6.2, 4.6))
    ax.plot(vg, i, ".", color="purple", label="Experimental (digitized)")
    for (name, f), st in zip(fits.items(), ("k-", "b--", "g:")):
        ax.plot(v, id_model(v, f["vt"], f["mu_c0"], f["mu_ph"], f["alpha"]), st,
                label=f"{name}: Vt={f['vt']:.2f} V, RMS {f['rms']:.2f} nA")
    ax.axvline(FIT_FROM_VG, color="0.8", lw=0.8)
    ax.set_xlim(-2, 20)
    ax.set_ylim(0, 200)
    ax.set_xlabel("Vg (V)")
    ax.set_ylabel("Id (nA)")
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(args.plot, dpi=150)
    print(f"\nPlot saved to {args.plot}")


if __name__ == "__main__":
    main()

# Copyright (c) 2026 Chamiseul. All rights reserved.
"""Savitzky-Golay smoothing with numpy alone.

The facial-mocap importer used `scipy.signal.savgol_filter`. Cascadeur's
Python has numpy but no scipy, so importing it failed at module load and the
command never appeared in the menu at all - which is a poor trade for one
function. This is that function.

A Savitzky-Golay filter fits a polynomial of degree `polyorder` to a sliding
window of `window_length` samples and takes the fitted value at the centre.
Because the fit is linear least squares and the window offsets are the same
everywhere, the whole thing collapses to one fixed set of weights:

    A            Vandermonde of the offsets -half..half, powers 0..polyorder
    pinv(A)      the least-squares solution operator
    pinv(A)[0]   the weights that produce the fitted value at offset 0

The ends are the only subtlety. There is no full window there, so scipy's
default (mode="interp") fits one polynomial to the first (and last) window and
evaluates it at each of those positions instead of padding the signal. The
same pinv gives those weights too - evaluate at offset t rather than 0 - so
the ends cost nothing extra and match scipy rather than approximating it.

Verified against scipy.signal.savgol_filter to ~1e-12 on random signals and
on real curve shapes; see savgol_test.py.
"""

import numpy as np


def coefficients(window_length, polyorder):
    """(interior weights, edge weight matrix) for one window."""
    if window_length % 2 == 0:
        raise ValueError("window_length must be odd")
    if polyorder >= window_length:
        raise ValueError("polyorder must be less than window_length")

    half = window_length // 2
    offsets = np.arange(-half, half + 1, dtype=float)
    design = np.vander(offsets, polyorder + 1, increasing=True)
    solve = np.linalg.pinv(design)          # (polyorder+1, window_length)

    # Evaluating the fitted polynomial at offset t is sum_k solve[k] * t**k.
    powers = np.vander(offsets, polyorder + 1, increasing=True)  # (w, p+1)
    at_offset = powers @ solve                                   # (w, w)
    return at_offset[half], at_offset


def smooth(values, window_length, polyorder):
    """Smooth a 1-D sequence. Returns a numpy array of the same length."""
    data = np.asarray(values, dtype=float)
    if data.ndim != 1:
        raise ValueError("smooth() takes a 1-D sequence")

    # Too short to fit a window: nothing to smooth, and padding it would be
    # inventing data. scipy raises here; a mocap column of three frames is
    # not worth failing an import over.
    if data.size < window_length:
        return data.copy()

    half = window_length // 2
    interior, at_offset = coefficients(window_length, polyorder)

    out = np.convolve(data, interior[::-1], mode="same")
    # The ends, from one polynomial fitted to the first/last full window.
    out[:half] = at_offset[:half] @ data[:window_length]
    out[data.size - half:] = at_offset[half + 1:] @ data[-window_length:]
    return out


def valid_window(window_length, polyorder):
    """The complaint about these two numbers, or "" if there is none."""
    if window_length % 2 == 0:
        return "Window length must be an odd number."
    if polyorder >= window_length:
        return "Window length must be greater than the polynomial order."
    if polyorder < 1:
        return "Polynomial order must be at least 1."
    return ""

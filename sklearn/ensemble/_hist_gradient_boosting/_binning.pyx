# cython: cdivision=True
# cython: boundscheck=False
# cython: wraparound=False
# cython: nonecheck=False
# cython: language_level=3

# Author: Nicolas Hug

cimport cython

import numpy as np
cimport numpy as np
from numpy.math cimport INFINITY
from cython.parallel import prange
from libc.math cimport isnan

from .common cimport X_DTYPE_C, X_BINNED_DTYPE_C

np.import_array()


def _map_to_bins(const X_DTYPE_C [:, :] data,
                 list binning_thresholds,
                 const unsigned char missing_values_bin_idx,
                 const unsigned char[::1] is_categorical,
                 X_BINNED_DTYPE_C [::1, :] binned):
    """Bin numerical and categorical values to discrete integer-coded levels.

    Parameters
    ----------
    data : ndarray, shape (n_samples, n_features)
        The numerical data to bin.
    binning_thresholds : list of arrays
        For each feature, stores the increasing numeric values that are
        used to separate the bins.
    is_categorical : ndarray, shape (n_features,)
        Indicates categorical features.
    binned : ndarray, shape (n_samples, n_features)
        Output array, must be fortran aligned.
    """
    cdef:
        int feature_idx

    for feature_idx in range(data.shape[1]):
        _map_num_col_to_bins(data[:, feature_idx],
                             binning_thresholds[feature_idx],
                             missing_values_bin_idx,
                             is_categorical[feature_idx],
                             binned[:, feature_idx])


cdef void _map_num_col_to_bins(const X_DTYPE_C [:] data,
                               const X_DTYPE_C [:] binning_thresholds,
                               const unsigned char missing_values_bin_idx,
                               const unsigned char is_categorical,
                               X_BINNED_DTYPE_C [:] binned):
    """Binary search to find the bin index for each value in the data."""
    cdef:
        int i
        int left
        int right
        int middle

    for i in prange(data.shape[0], schedule='static', nogil=True):

        if isnan(data[i]):
            binned[i] = missing_values_bin_idx
        else:
            # for known values, use binary search
            left, right = 0, binning_thresholds.shape[0]
            while left < right:
                # equal to (right + left - 1) // 2 but avoids overflow
                middle = left + (right - left - 1) // 2
                if data[i] <= binning_thresholds[middle]:
                    right = middle
                else:
                    left = middle + 1

            if is_categorical and binning_thresholds[left] != data[i]:
                # categorical data needs the values to match exactly. When they
                # don't, then the category is considered missing. This happens
                # when a category isn't seen during fit but seen at transform.
                binned[i] = missing_values_bin_idx
            else:
                binned[i] = left

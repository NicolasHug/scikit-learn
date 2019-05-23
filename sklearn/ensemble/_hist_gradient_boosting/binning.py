"""
This module contains the BinMapper class.

BinMapper is used for mapping a real-valued dataset into integer-valued bins.
Bin thresholds are computed with the quantiles so that each bin contains
approximately the same number of samples.
"""
# Author: Nicolas Hug

import numpy as np

from ...utils import check_random_state, check_array
from ...base import BaseEstimator, TransformerMixin
from ...utils.validation import check_is_fitted
from ._binning import _map_to_bins
from .types import X_DTYPE, X_BINNED_DTYPE


def _find_binning_thresholds(data, max_bins, subsample, random_state):
    """Extract feature-wise quantiles from numerical data.

    Missing values are ignored for finding the thresholds.

    Parameters
    ----------
    data : array-like, shape (n_samples, n_features)
        The data to bin.
    max_bins : int
        The maximum number of bins to use. If for a given feature the number of
        unique values is less than ``max_bins``, then those unique values
        will be used to compute the bin thresholds, instead of the quantiles.
    subsample : int or None
        If ``n_samples > subsample``, then ``sub_samples`` samples will be
        randomly choosen to compute the quantiles. If ``None``, the whole data
        is used.
    random_state: int or numpy.random.RandomState or None
        Pseudo-random number generator to control the random sub-sampling.
        See :term:`random_state`.

    Return
    ------
    binning_thresholds: list of arrays
        For each feature, stores the increasing numeric values that can
        be used to separate the bins. Thus ``len(binning_thresholds) ==
        n_features``.
    has_missing_values: list of bool
        For each feature, indicates whether missing values were encountered.
    """
    if not (2 <= max_bins <= 256):
        raise ValueError('max_bins={} should be no smaller than 2 '
                         'and no larger than 256.'.format(max_bins))
    rng = check_random_state(random_state)
    if subsample is not None and data.shape[0] > subsample:
        subset = rng.choice(np.arange(data.shape[0]), subsample, replace=False)
        data = data.take(subset, axis=0)

    binning_thresholds = []
    has_missing_values = []
    for f_idx in range(data.shape[1]):
        col_data = data[:, f_idx]
        # ignore missing values when computing bin thresholds
        missing_mask = np.isnan(col_data)
        if missing_mask.any():
            col_data = col_data[~missing_mask]
            has_missing_values.append(True)
        else:
            has_missing_values.append(False)
        col_data = np.ascontiguousarray(col_data, dtype=X_DTYPE)
        distinct_values = np.unique(col_data)
        if len(distinct_values) + has_missing_values[-1] <= max_bins:
            midpoints = distinct_values[:-1] + distinct_values[1:]
            midpoints *= .5
        else:
            # We sort again the data in this case. We could compute
            # approximate midpoint percentiles using the output of
            # np.unique(col_data, return_counts) instead but this is more
            # work and the performance benefit will be limited because we
            # work on a fixed-size subsample of the full data.
            n_percentiles = (max_bins if has_missing_values[-1]
                             else max_bins + 1)
            percentiles = np.linspace(0, 100, num=n_percentiles)
            percentiles = percentiles[1:-1]
            midpoints = np.percentile(col_data, percentiles,
                                      interpolation='midpoint').astype(X_DTYPE)
        binning_thresholds.append(midpoints)
    return binning_thresholds, has_missing_values


class _BinMapper(BaseEstimator, TransformerMixin):
    """Transformer that maps a dataset into integer-valued bins.

    The bins are created in a feature-wise fashion, using quantiles so that
    each bins contains approximately the same number of samples.

    For large datasets, quantiles are computed on a subset of the data to
    speed-up the binning, but the quantiles should remain stable.

    If the number of unique values for a given feature is less than
    ``max_bins``, then the unique values of this feature are used instead of
    the quantiles.

    Parameters
    ----------
    max_bins : int, optional (default=256)
        The maximum number of bins to use (including the bin for missing
        values, if any). If for a given feature the number of unique values
        is less than ``max_bins``, then those unique values will be used to
        compute the bin thresholds, instead of the quantiles.
    subsample : int or None, optional (default=2e5)
        If ``n_samples > subsample``, then ``sub_samples`` samples will be
        randomly choosen to compute the quantiles. If ``None``, the whole data
        is used.
    random_state: int or numpy.random.RandomState or None, \
        optional (default=None)
        Pseudo-random number generator to control the random sub-sampling.
        See :term:`random_state`.
    """
    def __init__(self, max_bins=256, subsample=int(2e5), random_state=None):
        self.max_bins = max_bins
        self.subsample = subsample
        self.random_state = random_state

    def fit(self, X, y=None):
        """Fit data X by computing the binning thresholds.

        The first bin is reserved for missing values, if any.

        Parameters
        ----------
        X : array-like, shape (n_samples, n_features)
            The data to bin.
        y: None
            Ignored.

        Returns
        -------
        self : object
        """
        X = check_array(X, dtype=[X_DTYPE], force_all_finite='allow-nan')
        self.bin_thresholds_, has_missing_values = _find_binning_thresholds(
                    X, self.max_bins, subsample=self.subsample,
                    random_state=self.random_state)

        self.has_missing_values_ = np.array(has_missing_values, dtype=np.uint8)

        self.actual_n_bins_ = np.array(
            [thresholds.shape[0] + 1 + has_missing_values
             for (thresholds, has_missing_values)
             in zip(self.bin_thresholds_, self.has_missing_values_)],
            dtype=np.uint32)

        return self

    def transform(self, X):
        """Bin data X.

        Missing values will be mapped to the first bin (whether or not missing
        values were encountered at fit time). For this reason, `X` should be
        the fitting data, though we do not enforce this. Note that the GBDT
        code only ever uses mapper.fit_transform(), so this assumption is OK.

        Parameters
        ----------
        X : array-like, shape (n_samples, n_features)
            The data to bin. Must be the fitting data.

        Returns
        -------
        X_binned : array-like, shape (n_samples, n_features)
            The binned data.
        """
        X = check_array(X, dtype=[X_DTYPE], force_all_finite='allow-nan')
        check_is_fitted(self, ['bin_thresholds_', 'actual_n_bins_'])
        if X.shape[1] != self.actual_n_bins_.shape[0]:
            raise ValueError(
                'This estimator was fitted with {} features but {} got passed '
                'to transform()'.format(self.actual_n_bins_.shape[0],
                                        X.shape[1])
            )
        binned = np.zeros_like(X, dtype=X_BINNED_DTYPE, order='F')
        _map_to_bins(X, self.bin_thresholds_, self.has_missing_values_, binned)
        return binned

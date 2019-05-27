import numpy as np
import pytest

from sklearn.ensemble._hist_gradient_boosting.types import HISTOGRAM_DTYPE
from sklearn.ensemble._hist_gradient_boosting.types import G_H_DTYPE
from sklearn.ensemble._hist_gradient_boosting.types import X_BINNED_DTYPE
from sklearn.ensemble._hist_gradient_boosting.splitting import Splitter
from sklearn.ensemble._hist_gradient_boosting.histogram import HistogramBuilder


@pytest.mark.parametrize('n_bins', [3, 32, 256])
def test_histogram_split(n_bins):
    rng = np.random.RandomState(42)
    feature_idx = 0
    l2_regularization = 0
    min_hessian_to_split = 1e-3
    min_samples_leaf = 1
    min_gain_to_split = 0.
    X_binned = np.asfortranarray(
        rng.randint(0, n_bins, size=(int(1e4), 1)), dtype=X_BINNED_DTYPE)
    binned_feature = X_binned.T[feature_idx]
    sample_indices = np.arange(binned_feature.shape[0], dtype=np.uint32)
    ordered_hessians = np.ones_like(binned_feature, dtype=G_H_DTYPE)
    all_hessians = ordered_hessians
    sum_hessians = all_hessians.sum()
    hessians_are_constant = False

    for true_bin in range(1, n_bins - 1):
        for sign in [-1, 1]:
            ordered_gradients = np.full_like(binned_feature, sign,
                                             dtype=G_H_DTYPE)
            ordered_gradients[binned_feature <= true_bin] *= -1
            all_gradients = ordered_gradients
            sum_gradients = all_gradients.sum()

            actual_n_bins = np.array([n_bins] * X_binned.shape[1],
                                     dtype=np.uint32)
            has_missing_values = np.array([False] * X_binned.shape[1],
                                          dtype=np.uint8)
            support_missing_values = has_missing_values
            builder = HistogramBuilder(X_binned,
                                       n_bins,
                                       all_gradients,
                                       all_hessians,
                                       hessians_are_constant)
            splitter = Splitter(X_binned,
                                n_bins,
                                actual_n_bins,
                                has_missing_values,
                                support_missing_values,
                                l2_regularization,
                                min_hessian_to_split,
                                min_samples_leaf, min_gain_to_split,
                                hessians_are_constant)

            histograms = builder.compute_histograms_brute(sample_indices)
            split_info = splitter.find_node_split(
                sample_indices, histograms, sum_gradients,
                sum_hessians)

            assert split_info.bin_idx == true_bin
            assert split_info.gain >= 0
            assert split_info.feature_idx == feature_idx
            assert (split_info.n_samples_left + split_info.n_samples_right
                    == sample_indices.shape[0])
            # Constant hessian: 1. per sample.
            assert split_info.n_samples_left == split_info.sum_hessian_left


@pytest.mark.parametrize('constant_hessian', [True, False])
def test_gradient_and_hessian_sanity(constant_hessian):
    # This test checks that the values of gradients and hessians are
    # consistent in different places:
    # - in split_info: si.sum_gradient_left + si.sum_gradient_right must be
    #   equal to the gradient at the node. Same for hessians.
    # - in the histograms: summing 'sum_gradients' over the bins must be
    #   constant across all features, and those sums must be equal to the
    #   node's gradient. Same for hessians.

    rng = np.random.RandomState(42)

    n_bins = 10
    n_features = 20
    n_samples = 500
    l2_regularization = 0.
    min_hessian_to_split = 1e-3
    min_samples_leaf = 1
    min_gain_to_split = 0.

    X_binned = rng.randint(0, n_bins, size=(n_samples, n_features),
                           dtype=X_BINNED_DTYPE)
    X_binned = np.asfortranarray(X_binned)
    sample_indices = np.arange(n_samples, dtype=np.uint32)
    all_gradients = rng.randn(n_samples).astype(G_H_DTYPE)
    sum_gradients = all_gradients.sum()
    if constant_hessian:
        all_hessians = np.ones(1, dtype=G_H_DTYPE)
        sum_hessians = 1 * n_samples
    else:
        all_hessians = rng.lognormal(size=n_samples).astype(G_H_DTYPE)
        sum_hessians = all_hessians.sum()

    actual_n_bins = np.array([n_bins] * X_binned.shape[1],
                             dtype=np.uint32)
    has_missing_values = np.array([False] * X_binned.shape[1], dtype=np.uint8)
    support_missing_values = has_missing_values
    builder = HistogramBuilder(X_binned, n_bins, all_gradients,
                               all_hessians, constant_hessian)
    splitter = Splitter(X_binned, n_bins, actual_n_bins, has_missing_values,
                        support_missing_values, l2_regularization,
                        min_hessian_to_split, min_samples_leaf,
                        min_gain_to_split, constant_hessian)

    hists_parent = builder.compute_histograms_brute(sample_indices)
    si_parent = splitter.find_node_split(sample_indices, hists_parent,
                                         sum_gradients, sum_hessians)
    sample_indices_left, sample_indices_right, _ = splitter.split_indices(
        si_parent, sample_indices)

    hists_left = builder.compute_histograms_brute(sample_indices_left)
    hists_right = builder.compute_histograms_brute(sample_indices_right)
    si_left = splitter.find_node_split(sample_indices_left, hists_left,
                                       si_parent.sum_gradient_left,
                                       si_parent.sum_hessian_left)
    si_right = splitter.find_node_split(sample_indices_right, hists_right,
                                        si_parent.sum_gradient_right,
                                        si_parent.sum_hessian_right)

    # make sure that si.sum_gradient_left + si.sum_gradient_right have their
    # expected value, same for hessians
    for si, indices in (
            (si_parent, sample_indices),
            (si_left, sample_indices_left),
            (si_right, sample_indices_right)):
        gradient = si.sum_gradient_right + si.sum_gradient_left
        expected_gradient = all_gradients[indices].sum()
        hessian = si.sum_hessian_right + si.sum_hessian_left
        if constant_hessian:
            expected_hessian = indices.shape[0] * all_hessians[0]
        else:
            expected_hessian = all_hessians[indices].sum()

        assert np.isclose(gradient, expected_gradient)
        assert np.isclose(hessian, expected_hessian)

    # make sure sum of gradients in histograms are the same for all features,
    # and make sure they're equal to their expected value
    hists_parent = np.asarray(hists_parent, dtype=HISTOGRAM_DTYPE)
    hists_left = np.asarray(hists_left, dtype=HISTOGRAM_DTYPE)
    hists_right = np.asarray(hists_right, dtype=HISTOGRAM_DTYPE)
    for hists, indices in (
            (hists_parent, sample_indices),
            (hists_left, sample_indices_left),
            (hists_right, sample_indices_right)):
        # note: gradients and hessians have shape (n_features,),
        # we're comparing them to *scalars*. This has the benefit of also
        # making sure that all the entries are equal across features.
        gradients = hists['sum_gradients'].sum(axis=1)  # shape = (n_features,)
        expected_gradient = all_gradients[indices].sum()  # scalar
        hessians = hists['sum_hessians'].sum(axis=1)
        if constant_hessian:
            # 0 is not the actual hessian, but it's not computed in this case
            expected_hessian = 0.
        else:
            expected_hessian = all_hessians[indices].sum()

        assert np.allclose(gradients, expected_gradient)
        assert np.allclose(hessians, expected_hessian)


def test_split_indices():
    # Check that split_indices returns the correct splits and that
    # splitter.partition is consistent with what is returned.
    rng = np.random.RandomState(421)

    n_bins = 5
    n_samples = 10
    l2_regularization = 0.
    min_hessian_to_split = 1e-3
    min_samples_leaf = 1
    min_gain_to_split = 0.

    # split will happen on feature 1 and on bin 3
    X_binned = [[0, 0],
                [0, 3],
                [0, 4],
                [0, 0],
                [0, 0],
                [0, 0],
                [0, 0],
                [0, 4],
                [0, 0],
                [0, 4]]
    X_binned = np.asfortranarray(X_binned, dtype=X_BINNED_DTYPE)
    sample_indices = np.arange(n_samples, dtype=np.uint32)
    all_gradients = rng.randn(n_samples).astype(G_H_DTYPE)
    all_hessians = np.ones(1, dtype=G_H_DTYPE)
    sum_gradients = all_gradients.sum()
    sum_hessians = 1 * n_samples
    hessians_are_constant = True

    actual_n_bins = np.array([n_bins] * X_binned.shape[1],
                             dtype=np.uint32)
    has_missing_values = np.array([False] * X_binned.shape[1], dtype=np.uint8)
    support_missing_values = has_missing_values
    builder = HistogramBuilder(X_binned, n_bins,
                               all_gradients, all_hessians,
                               hessians_are_constant)
    splitter = Splitter(X_binned, n_bins, actual_n_bins, has_missing_values,
                        support_missing_values, l2_regularization,
                        min_hessian_to_split, min_samples_leaf,
                        min_gain_to_split, hessians_are_constant)

    assert np.all(sample_indices == splitter.partition)

    histograms = builder.compute_histograms_brute(sample_indices)
    si_root = splitter.find_node_split(sample_indices, histograms,
                                       sum_gradients, sum_hessians)

    # sanity checks for best split
    assert si_root.feature_idx == 1
    assert si_root.bin_idx == 3

    samples_left, samples_right, position_right = splitter.split_indices(
        si_root, splitter.partition)
    assert set(samples_left) == set([0, 1, 3, 4, 5, 6, 8])
    assert set(samples_right) == set([2, 7, 9])

    assert list(samples_left) == list(splitter.partition[:position_right])
    assert list(samples_right) == list(splitter.partition[position_right:])

    # Check that the resulting split indices sizes are consistent with the
    # count statistics anticipated when looking for the best split.
    assert samples_left.shape[0] == si_root.n_samples_left
    assert samples_right.shape[0] == si_root.n_samples_right


def test_min_gain_to_split():
    # Try to split a pure node (all gradients are equal, same for hessians)
    # with min_gain_to_split = 0 and make sure that the node is not split (best
    # possible gain = -1). Note: before the strict inequality comparison, this
    # test would fail because the node would be split with a gain of 0.
    rng = np.random.RandomState(42)
    l2_regularization = 0
    min_hessian_to_split = 0
    min_samples_leaf = 1
    min_gain_to_split = 0.
    n_bins = 255
    n_samples = 100
    X_binned = np.asfortranarray(
        rng.randint(0, n_bins, size=(n_samples, 1)), dtype=X_BINNED_DTYPE)
    binned_feature = X_binned[:, 0]
    sample_indices = np.arange(n_samples, dtype=np.uint32)
    all_hessians = np.ones_like(binned_feature, dtype=G_H_DTYPE)
    all_gradients = np.ones_like(binned_feature, dtype=G_H_DTYPE)
    sum_gradients = all_gradients.sum()
    sum_hessians = all_hessians.sum()
    hessians_are_constant = False

    actual_n_bins = np.array([n_bins] * X_binned.shape[1],
                             dtype=np.uint32)
    has_missing_values = np.array([False] * X_binned.shape[1], dtype=np.uint8)
    support_missing_values = has_missing_values
    builder = HistogramBuilder(X_binned, n_bins, all_gradients,
                               all_hessians, hessians_are_constant)
    splitter = Splitter(X_binned, n_bins, actual_n_bins, has_missing_values,
                        support_missing_values, l2_regularization,
                        min_hessian_to_split, min_samples_leaf,
                        min_gain_to_split, hessians_are_constant)

    histograms = builder.compute_histograms_brute(sample_indices)
    split_info = splitter.find_node_split(sample_indices, histograms,
                                          sum_gradients, sum_hessians)
    assert split_info.gain == -1


@pytest.mark.parametrize(
    'X_binned, all_gradients, has_missing_values, expected_bin_idx, '
    'expected_go_to_left', [

        # basic sanity check: given the gradient values, the split must occur
        # on bin_idx=3
        ([0, 1, 2, 3, 4, 5, 6, 7, 8, 9],  # X_binned
         [1, 1, 1, 1, 5, 5, 5, 5, 5, 5],  # gradients
         False,  # no missing values
         3,  # expected_bin_idx
         'not_applicable'),

        # We replace 2 samples by NaNs (bin_idx=0)
        # These 2 samples were mapped to the left node before, so they should
        # be mapped to left node again
        # Notice how the bin_idx threshold changes from 3 to 2.
        # Also, the bins of the previous non-nan samples have bin shiffted by
        # one
        ([0, 1, 2, 0, 3, 4, 5, 6, 7, 8],
         [1, 1, 1, 1, 2, 2, 2, 2, 2, 2],
         True,  # missing values (bin_idx=0)
         2,  # cut on bin_idx=2
         True),  # missing values go to left

        # Same, this time replacing 2 samples that were on the right.
        ([1, 2, 3, 4, 0, 5, 0, 6, 7, 8],
         [1, 1, 1, 1, 5, 5, 5, 5, 5, 5],
         True,  # missing values (bin_idx=0)
         4,  # cut on bin_idx=4 (like in first case, with +1 because of offset)
         False),  # missing values go to right
    ]
)
def test_splitting_missing_values(X_binned, all_gradients,
                                  has_missing_values, expected_bin_idx,
                                  expected_go_to_left):
    # Make sure missing values are properly supported.
    # we build an artificial example with gradients such that the best split
    # is on bin_idx=3, when there are no missing values.
    # Then we introduce missing values and:
    #   - make sure the chosen bin is still correct (find_best_bin()): it's
    #     still the same bin, even though its index changes
    #   - make sure the missing values are mapped to the correct child
    #     (split_indices())

    max_bins = 10  # TO REMOVE
    n_samples = len(X_binned)
    l2_regularization = 0.
    min_hessian_to_split = 1e-3
    min_samples_leaf = 1
    min_gain_to_split = 0.

    sample_indices = np.arange(n_samples, dtype=np.uint32)
    X_binned = np.array(X_binned, dtype=X_BINNED_DTYPE).reshape(-1, 1)
    X_binned = np.asfortranarray(X_binned)
    all_gradients = np.array(all_gradients, dtype=G_H_DTYPE)
    has_missing_values = np.array([has_missing_values], dtype=np.uint8)
    support_missing_values = has_missing_values
    all_hessians = np.ones(1, dtype=G_H_DTYPE)
    sum_gradients = all_gradients.sum()
    sum_hessians = 1 * n_samples
    hessians_are_constant = True

    actual_n_bins = np.array([X_binned.max() + 1], dtype=np.uint32)
    builder = HistogramBuilder(X_binned, max_bins,
                               all_gradients, all_hessians,
                               hessians_are_constant)
    splitter = Splitter(X_binned, max_bins, actual_n_bins, has_missing_values,
                        support_missing_values, l2_regularization,
                        min_hessian_to_split, min_samples_leaf,
                        min_gain_to_split, hessians_are_constant)

    histograms = builder.compute_histograms_brute(sample_indices)
    split_info = splitter.find_node_split(sample_indices, histograms,
                                          sum_gradients, sum_hessians)

    assert split_info.bin_idx == expected_bin_idx
    if has_missing_values:
        assert split_info.missing_go_to_left == expected_go_to_left

    # Whatever the missing values, the split should always be the same. This
    # also make sure missing values are properly assigned to the correct child
    # in split_indices()
    samples_left, samples_right, _ = splitter.split_indices(
        split_info, splitter.partition)
    assert set(samples_left) == set([0, 1, 2, 3])
    assert set(samples_right) == set([4, 5, 6, 7, 8, 9])

"""
This module contains the loss classes.

Specific losses are used for regression, binary classification or multiclass
classification.
"""
# Author: Nicolas Hug

from abc import ABC, abstractmethod

import numpy as np
from scipy.special import expit
try:
    from scipy.special import logsumexp
except ImportError:
    from scipy.misc import logsumexp

from .types import Y_DTYPE
from .types import G_H_DTYPE
from ._loss import _update_gradients_least_squares
from ._loss import _update_gradients_hessians_binary_crossentropy
from ._loss import _update_gradients_hessians_categorical_crossentropy


class BaseLoss(ABC):
    """Base class for a loss."""

    def init_gradients_and_hessians(self, n_samples, prediction_dim):
        """Return initial gradients and hessians.

        Unless hessians are constant, arrays are initialized with undefined
        values.

        Parameters
        ----------
        n_samples : int
            The number of samples passed to `fit()`
        prediction_dim : int
            The dimension of a raw prediction, i.e. the number of trees
            built at each iteration. Equals 1 for regression and binary
            classification, or K where K is the number of classes for
            multiclass classification.

        Returns
        -------
        gradients : array-like, shape=(prediction_dim, n_samples)
        hessians : array-like, shape=(prediction_dim, n_samples).
            If hessians are constant (e.g. for ``LeastSquares`` loss, the
            array is initialized to ``1``.
        """
        shape = (prediction_dim, n_samples)
        gradients = np.empty(shape=shape, dtype=G_H_DTYPE)
        if self.hessians_are_constant:
            # if the hessians are constant, we consider they are equal to 1.
            # this is correct as long as we adjust the gradients. See e.g. LS
            # loss
            hessians = np.ones(shape=(1, 1), dtype=G_H_DTYPE)
        else:
            hessians = np.empty(shape=shape, dtype=G_H_DTYPE)

        return gradients, hessians

    @abstractmethod
    def get_baseline_prediction(self, y_train, prediction_dim):
        """Return initial predictions (before the first iteration).

        Parameters
        ----------
        y_train : array-like, shape=(n_samples,)
            The target training values.
        prediction_dim : int
            The dimension of one prediction: 1 for binary classification and
            regression, n_classes for multiclass classification.

        Returns
        -------
        baseline_prediction: float or array of shape (1, prediction_dim)
            The baseline prediction.
        """
        pass

    @abstractmethod
    def update_gradients_and_hessians(self, gradients, hessians, y_true,
                                      raw_predictions):
        """Update gradients and hessians arrays, inplace.

        The gradients (resp. hessians) are the first (resp. second) order
        derivatives of the loss for each sample with respect to the
        predictions of model, evaluated at iteration ``i - 1``.

        Parameters
        ----------
        gradients : array-like, shape=(prediction_dim, n_samples)
            The gradients (treated as OUT array).
        hessians : array-like, shape=(prediction_dim, n_samples) or \
            (1,)
            The hessians (treated as OUT array).
        y_true : array-like, shape=(n_samples,)
            The true target values or each training sample.
        raw_predictions : array-like, shape=(prediction_dim, n_samples)
            The raw_predictions (i.e. values from the trees) of the tree
            ensemble at iteration ``i - 1``.
        """
        pass


class LeastSquares(BaseLoss):
    """Least squares loss, for regression.

    For a given sample x_i, least squares loss is defined as::

        loss(x_i) = (y_true_i - raw_pred_i)**2
    """

    hessians_are_constant = True

    def __call__(self, y_true, raw_predictions, average=True):
        # shape (1, n_samples) --> (n_samples,). reshape(-1) is more likely to
        # return a view.
        raw_predictions = raw_predictions.reshape(-1)
        loss = np.power(y_true - raw_predictions, 2)
        return loss.mean() if average else loss

    def get_baseline_prediction(self, y_train, prediction_dim):
        return np.mean(y_train).astype(Y_DTYPE)

    @staticmethod
    def inverse_link_function(raw_predictions):
        return raw_predictions

    def update_gradients_and_hessians(self, gradients, hessians, y_true,
                                      raw_predictions):
        # shape (1, n_samples) --> (n_samples,). reshape(-1) is more likely to
        # return a view.
        raw_predictions = raw_predictions.reshape(-1)
        gradients = gradients.reshape(-1)
        _update_gradients_least_squares(gradients, y_true,
                                        raw_predictions)


class BinaryCrossEntropy(BaseLoss):
    """Binary cross-entropy loss, for binary classification.

    For a given sample x_i, the binary cross-entropy loss is defined as the
    negative log-likelihood of the model which can be expressed as::

        loss(x_i) = log(1 + exp(raw_pred_i)) - y_true_i * raw_pred_i

    See The Elements of Statistical Learning, by Hastie, Tibshirani, Friedman.
    """

    hessians_are_constant = False
    inverse_link_function = staticmethod(expit)

    def __call__(self, y_true, raw_predictions, average=True):
        # shape (1, n_samples) --> (n_samples,). reshape(-1) is more likely to
        # return a view.
        raw_predictions = raw_predictions.reshape(-1)
        # logaddexp(0, x) = log(1 + exp(x))
        loss = np.logaddexp(0, raw_predictions) - y_true * raw_predictions
        return loss.mean() if average else loss

    def get_baseline_prediction(self, y_train, prediction_dim):
        proba_positive_class = np.mean(y_train)
        eps = np.finfo(y_train.dtype).eps
        proba_positive_class = np.clip(proba_positive_class, eps, 1 - eps)
        # log(x / 1 - x) is the anti function of sigmoid, or the link function
        # of the Binomial model.
        return np.log(proba_positive_class / (1 - proba_positive_class))

    def update_gradients_and_hessians(self, gradients, hessians, y_true,
                                      raw_predictions):
        # shape (1, n_samples) --> (n_samples,). reshape(-1) is more likely to
        # return a view.
        raw_predictions = raw_predictions.reshape(-1)
        gradients = gradients.reshape(-1)
        hessians = hessians.reshape(-1)
        _update_gradients_hessians_binary_crossentropy(
            gradients, hessians, y_true, raw_predictions)

    def predict_proba(self, raw_predictions):
        # shape (1, n_samples) --> (n_samples,). reshape(-1) is more likely to
        # return a view.
        raw_predictions = raw_predictions.reshape(-1)
        proba = np.empty((raw_predictions.shape[0], 2), dtype=Y_DTYPE)
        proba[:, 1] = expit(raw_predictions)
        proba[:, 0] = 1 - proba[:, 1]
        return proba


class CategoricalCrossEntropy(BaseLoss):
    """Categorical cross-entropy loss, for multiclass classification.

    For a given sample x_i, the categorical cross-entropy loss is defined as
    the negative log-likelihood of the model and generalizes the binary
    cross-entropy to more than 2 classes.
    """

    hessians_are_constant = False

    def __call__(self, y_true, raw_predictions, average=True):
        one_hot_true = np.zeros_like(raw_predictions)
        prediction_dim = raw_predictions.shape[0]
        for k in range(prediction_dim):
            one_hot_true[k, :] = (y_true == k)

        loss = (logsumexp(raw_predictions, axis=0) -
                (one_hot_true * raw_predictions).sum(axis=0))
        return loss.mean() if average else loss

    def get_baseline_prediction(self, y_train, prediction_dim):
        init_value = np.zeros(shape=(prediction_dim, 1), dtype=Y_DTYPE)
        eps = np.finfo(y_train.dtype).eps
        for k in range(prediction_dim):
            proba_kth_class = np.mean(y_train == k)
            proba_kth_class = np.clip(proba_kth_class, eps, 1 - eps)
            init_value[k, :] += np.log(proba_kth_class)

        return init_value

    def update_gradients_and_hessians(self, gradients, hessians, y_true,
                                      raw_predictions):
        _update_gradients_hessians_categorical_crossentropy(
            gradients, hessians, y_true, raw_predictions)

    def predict_proba(self, raw_predictions):
        # TODO: This could be done in parallel
        # compute softmax (using exp(log(softmax)))
        proba = np.exp(raw_predictions -
                       logsumexp(raw_predictions, axis=0)[np.newaxis, :])
        return proba.T


_LOSSES = {
    'least_squares': LeastSquares,
    'binary_crossentropy': BinaryCrossEntropy,
    'categorical_crossentropy': CategoricalCrossEntropy
}

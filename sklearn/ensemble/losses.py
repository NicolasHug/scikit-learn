"""Losses and corresponding default initial estimators for gradient boosting
decision trees.
"""

from abc import ABCMeta
from abc import abstractmethod

import numpy as np
from scipy.special import expit

from ..tree._tree import TREE_LEAF
from ..utils.fixes import logsumexp
from ..utils.stats import _weighted_percentile
from ..dummy import DummyClassifier
from ..dummy import DummyRegressor


class LossFunction(object, metaclass=ABCMeta):
    """Abstract base class for various loss functions.

    Parameters
    ----------
    n_classes : int
        Number of classes

    Attributes
    ----------
    K : int
        The number of regression trees to be induced;
        1 for regression and binary classification;
        ``n_classes`` for multi-class classification.
    """

    is_multi_class = False

    def __init__(self, n_classes):
        self.K = n_classes

    def init_estimator(self):
        """Default ``init`` estimator for loss function. """
        raise NotImplementedError()

    @abstractmethod
    def __call__(self, y, pred, sample_weight=None):
        """Compute the loss.

        Parameters
        ----------
        y : array, shape (n_samples,)
            True labels

        pred : array, shape (n_samples,)
            Predicted labels

        sample_weight : array-like, shape (n_samples,), optional
            Sample weights.
        """

    @abstractmethod
    def negative_gradient(self, y, y_pred, **kargs):
        """Compute the negative gradient.

        Parameters
        ----------
        y : array, shape (n_samples,)
            The target labels.

        y_pred : array, shape (n_samples,)
            The predictions.
        """

    def update_terminal_regions(self, tree, X, y, residual, y_pred,
                                sample_weight, sample_mask,
                                learning_rate=0.1, k=0):
        """Update the terminal regions (=leaves) of the given tree and
        updates the current predictions of the model. Traverses tree
        and invokes template method `_update_terminal_region`.

        Parameters
        ----------
        tree : tree.Tree
            The tree object.
        X : array, shape (n, m)
            The data array.
        y : array, shape (n,)
            The target labels.
        residual : array, shape (n,)
            The residuals (usually the negative gradient).
        y_pred : array, shape (n,)
            The predictions.
        sample_weight : array, shape (n,)
            The weight of each sample.
        sample_mask : array, shape (n,)
            The sample mask to be used.
        learning_rate : float, default=0.1
            learning rate shrinks the contribution of each tree by
             ``learning_rate``.
        k : int, default 0
            The index of the estimator being updated.

        """
        # compute leaf for each sample in ``X``.
        terminal_regions = tree.apply(X)

        # mask all which are not in sample mask.
        masked_terminal_regions = terminal_regions.copy()
        masked_terminal_regions[~sample_mask] = -1

        # update each leaf (= perform line search)
        for leaf in np.where(tree.children_left == TREE_LEAF)[0]:
            self._update_terminal_region(tree, masked_terminal_regions,
                                         leaf, X, y, residual,
                                         y_pred[:, k], sample_weight)

        # update predictions (both in-bag and out-of-bag)
        y_pred[:, k] += (learning_rate
                         * tree.value[:, 0, 0].take(terminal_regions, axis=0))

    @abstractmethod
    def _update_terminal_region(self, tree, terminal_regions, leaf, X, y,
                                residual, pred, sample_weight):
        """Template method for updating terminal regions (=leaves). """

    @abstractmethod
    def get_init_raw_predictions(self, X, estimator):
        """
        Return the initial raw predictions

        Parameters
        ----------
        X : array, shape = (n_samples, n_features)
            The data array.
        estimator : Estimator
            The estimator to use to compute the predictions

        Returns
        -------
        raw_predictions : 2d array of shape (n_samples, K)
            The initial raw predictions. K is equal to 1 for binary
            classification and regression, and equal to the number of classes
            for multiclass classification.
        """
        pass


class RegressionLossFunction(LossFunction, metaclass=ABCMeta):
    """Base class for regression loss functions.

    Parameters
    ----------
    n_classes : int
        Number of classes
    """
    def __init__(self, n_classes):
        if n_classes != 1:
            raise ValueError("``n_classes`` must be 1 for regression but "
                             "was %r" % n_classes)
        super().__init__(n_classes)

    def check_init_estimator(self, estimator):
        """Make sure estimator has the required fit and predict methods.

        Parameters
        ----------
        estimator : estimator
            The init estimator to check
        """
        if not (hasattr(estimator, 'fit') and hasattr(estimator, 'predict')):
            raise ValueError(
                "The init parameter must be a valid estimator and "
                "support both fit and predict"
            )


class LeastSquaresError(RegressionLossFunction):
    """Loss function for least squares (LS) estimation.
    Terminal regions need not to be updated for least squares.

    Parameters
    ----------
    n_classes : int
        Number of classes
    """

    def init_estimator(self):
        return DummyRegressor(strategy='mean')

    def __call__(self, y, pred, sample_weight=None):
        """Compute the least squares loss.

        Parameters
        ----------
        y : array, shape (n_samples,)
            True labels

        pred : array, shape (n_samples,)
            Predicted labels

        sample_weight : array-like, shape (n_samples,), optional
            Sample weights.
        """
        if sample_weight is None:
            return np.mean((y - pred.ravel()) ** 2.0)
        else:
            return (1.0 / sample_weight.sum() *
                    np.sum(sample_weight * ((y - pred.ravel()) ** 2.0)))

    def negative_gradient(self, y, pred, **kargs):
        """Compute the negative gradient.

        Parameters
        ----------
        y : array, shape (n_samples,)
            The target labels.

        pred : array, shape (n_samples,)
            The predictions.
        """
        return y - pred.ravel()

    def update_terminal_regions(self, tree, X, y, residual, y_pred,
                                sample_weight, sample_mask,
                                learning_rate=0.1, k=0):
        """Least squares does not need to update terminal regions.

        But it has to update the predictions.

        Parameters
        ----------
        tree : tree.Tree
            The tree object.
        X : array, shape (n, m)
            The data array.
        y : array, shape (n,)
            The target labels.
        residual : array, shape (n,)
            The residuals (usually the negative gradient).
        y_pred : array, shape (n,)
            The predictions.
        sample_weight : array, shape (n,)
            The weight of each sample.
        sample_mask : array, shape (n,)
            The sample mask to be used.
        learning_rate : float, default=0.1
            learning rate shrinks the contribution of each tree by
             ``learning_rate``.
        k : int, default 0
            The index of the estimator being updated.
        """
        # update predictions
        y_pred[:, k] += learning_rate * tree.predict(X).ravel()

    def _update_terminal_region(self, tree, terminal_regions, leaf, X, y,
                                residual, pred, sample_weight):
        pass

    def get_init_raw_predictions(self, X, estimator):
        return estimator.predict(X).reshape(-1, 1).astype(np.float64)


class LeastAbsoluteError(RegressionLossFunction):
    """Loss function for least absolute deviation (LAD) regression.

    Parameters
    ----------
    n_classes : int
        Number of classes
    """
    def init_estimator(self):
        # Predicts the median
        return DummyRegressor(strategy='quantile', quantile=.5)

    def __call__(self, y, pred, sample_weight=None):
        """Compute the least absolute error.

        Parameters
        ----------
        y : array, shape (n_samples,)
            True labels

        pred : array, shape (n_samples,)
            Predicted labels

        sample_weight : array-like, shape (n_samples,), optional
            Sample weights.
        """
        if sample_weight is None:
            return np.abs(y - pred.ravel()).mean()
        else:
            return (1.0 / sample_weight.sum() *
                    np.sum(sample_weight * np.abs(y - pred.ravel())))

    def negative_gradient(self, y, pred, **kargs):
        """Compute the negative gradient.

        1.0 if y - pred > 0.0 else -1.0

        Parameters
        ----------
        y : array, shape (n_samples,)
            The target labels.

        pred : array, shape (n_samples,)
            The predictions.
        """
        pred = pred.ravel()
        return 2.0 * (y - pred > 0.0) - 1.0

    def _update_terminal_region(self, tree, terminal_regions, leaf, X, y,
                                residual, pred, sample_weight):
        """LAD updates terminal regions to median estimates. """
        terminal_region = np.where(terminal_regions == leaf)[0]
        sample_weight = sample_weight.take(terminal_region, axis=0)
        diff = y.take(terminal_region, axis=0) - pred.take(terminal_region,
                                                           axis=0)
        tree.value[leaf, 0, 0] = _weighted_percentile(diff, sample_weight,
                                                      percentile=50)

    def get_init_raw_predictions(self, X, estimator):
        return estimator.predict(X).reshape(-1, 1).astype(np.float64)


class HuberLossFunction(RegressionLossFunction):
    """Huber loss function for robust regression.

    M-Regression proposed in Friedman 2001.

    References
    ----------
    J. Friedman, Greedy Function Approximation: A Gradient Boosting
    Machine, The Annals of Statistics, Vol. 29, No. 5, 2001.

    Parameters
    ----------
    n_classes : int
        Number of classes

    alpha : float
        Percentile at which to extract score
    """

    def __init__(self, n_classes, alpha=0.9):
        super().__init__(n_classes)
        self.alpha = alpha
        self.gamma = None

    def init_estimator(self):
        return DummyRegressor(strategy='quantile', quantile=.5)

    def __call__(self, y, pred, sample_weight=None):
        """Compute the Huber loss.

        Parameters
        ----------
        y : array, shape (n_samples,)
            True labels

        pred : array, shape (n_samples,)
            Predicted labels

        sample_weight : array-like, shape (n_samples,), optional
            Sample weights.
        """
        pred = pred.ravel()
        diff = y - pred
        gamma = self.gamma
        if gamma is None:
            if sample_weight is None:
                gamma = np.percentile(np.abs(diff), self.alpha * 100)
            else:
                gamma = _weighted_percentile(np.abs(diff), sample_weight,
                                             self.alpha * 100)

        gamma_mask = np.abs(diff) <= gamma
        if sample_weight is None:
            sq_loss = np.sum(0.5 * diff[gamma_mask] ** 2.0)
            lin_loss = np.sum(gamma * (np.abs(diff[~gamma_mask]) -
                                       gamma / 2.0))
            loss = (sq_loss + lin_loss) / y.shape[0]
        else:
            sq_loss = np.sum(0.5 * sample_weight[gamma_mask] *
                             diff[gamma_mask] ** 2.0)
            lin_loss = np.sum(gamma * sample_weight[~gamma_mask] *
                              (np.abs(diff[~gamma_mask]) - gamma / 2.0))
            loss = (sq_loss + lin_loss) / sample_weight.sum()
        return loss

    def negative_gradient(self, y, pred, sample_weight=None, **kargs):
        """Compute the negative gradient.

        Parameters
        ----------
        y : array, shape (n_samples,)
            The target labels.

        pred : array, shape (n_samples,)
            The predictions.

        sample_weight : array-like, shape (n_samples,), optional
            Sample weights.
        """
        pred = pred.ravel()
        diff = y - pred
        if sample_weight is None:
            gamma = np.percentile(np.abs(diff), self.alpha * 100)
        else:
            gamma = _weighted_percentile(np.abs(diff), sample_weight,
                                         self.alpha * 100)
        gamma_mask = np.abs(diff) <= gamma
        residual = np.zeros((y.shape[0],), dtype=np.float64)
        residual[gamma_mask] = diff[gamma_mask]
        residual[~gamma_mask] = gamma * np.sign(diff[~gamma_mask])
        self.gamma = gamma
        return residual

    def _update_terminal_region(self, tree, terminal_regions, leaf, X, y,
                                residual, pred, sample_weight):
        terminal_region = np.where(terminal_regions == leaf)[0]
        sample_weight = sample_weight.take(terminal_region, axis=0)
        gamma = self.gamma
        diff = (y.take(terminal_region, axis=0)
                - pred.take(terminal_region, axis=0))
        median = _weighted_percentile(diff, sample_weight, percentile=50)
        diff_minus_median = diff - median
        tree.value[leaf, 0] = median + np.mean(
            np.sign(diff_minus_median) *
            np.minimum(np.abs(diff_minus_median), gamma))

    def get_init_raw_predictions(self, X, estimator):
        return estimator.predict(X).reshape(-1, 1).astype(np.float64)


class QuantileLossFunction(RegressionLossFunction):
    """Loss function for quantile regression.

    Quantile regression allows to estimate the percentiles
    of the conditional distribution of the target.

    Parameters
    ----------
    n_classes : int
        Number of classes.

    alpha : float, optional (default = 0.9)
        The percentile
    """
    def __init__(self, n_classes, alpha=0.9):
        super().__init__(n_classes)
        self.alpha = alpha
        self.percentile = alpha * 100.0

    def init_estimator(self):
        return DummyRegressor(strategy='quantile', quantile=self.alpha)

    def __call__(self, y, pred, sample_weight=None):
        """Compute the Quantile loss.

        Parameters
        ----------
        y : array, shape (n_samples,)
            True labels

        pred : array, shape (n_samples,)
            Predicted labels

        sample_weight : array-like, shape (n_samples,), optional
            Sample weights.
        """
        pred = pred.ravel()
        diff = y - pred
        alpha = self.alpha

        mask = y > pred
        if sample_weight is None:
            loss = (alpha * diff[mask].sum() -
                    (1.0 - alpha) * diff[~mask].sum()) / y.shape[0]
        else:
            loss = ((alpha * np.sum(sample_weight[mask] * diff[mask]) -
                    (1.0 - alpha) * np.sum(sample_weight[~mask] *
                                           diff[~mask])) / sample_weight.sum())
        return loss

    def negative_gradient(self, y, pred, **kargs):
        """Compute the negative gradient.

        Parameters
        ----------
        y : array, shape (n_samples,)
            The target labels.

        pred : array, shape (n_samples,)
            The predictions.
        """
        alpha = self.alpha
        pred = pred.ravel()
        mask = y > pred
        return (alpha * mask) - ((1.0 - alpha) * ~mask)

    def _update_terminal_region(self, tree, terminal_regions, leaf, X, y,
                                residual, pred, sample_weight):
        terminal_region = np.where(terminal_regions == leaf)[0]
        diff = (y.take(terminal_region, axis=0)
                - pred.take(terminal_region, axis=0))
        sample_weight = sample_weight.take(terminal_region, axis=0)

        val = _weighted_percentile(diff, sample_weight, self.percentile)
        tree.value[leaf, 0] = val

    def get_init_raw_predictions(self, X, estimator):
        return estimator.predict(X).reshape(-1, 1).astype(np.float64)


class ClassificationLossFunction(LossFunction, metaclass=ABCMeta):
    """Base class for classification loss functions. """

    def _score_to_proba(self, score):
        """Template method to convert scores to probabilities.

         the does not support probabilities raises AttributeError.
        """
        raise TypeError(
            '%s does not support predict_proba' % type(self).__name__)

    @abstractmethod
    def _score_to_decision(self, score):
        """Template method to convert scores to decisions.

        Returns int arrays.
        """

    def check_init_estimator(self, estimator):
        """Make sure estimator has fit and predict_proba methods.

        Parameters
        ----------
        estimator : estimator
            The init estimator to check
        """
        if not (hasattr(estimator, 'fit') and
                hasattr(estimator, 'predict_proba')):
            raise ValueError(
                "The init parameter must be a valid estimator "
                "and support both fit and predict_proba"
            )


class BinomialDeviance(ClassificationLossFunction):
    """Binomial deviance loss function for binary classification.

    Binary classification is a special case; here, we only need to
    fit one tree instead of ``n_classes`` trees.

    Parameters
    ----------
    n_classes : int
        Number of classes.
    """
    def __init__(self, n_classes):
        if n_classes != 2:
            raise ValueError("{0:s} requires 2 classes; got {1:d} class(es)"
                             .format(self.__class__.__name__, n_classes))
        # we only need to fit one tree for binary clf.
        super().__init__(1)

    def init_estimator(self):
        # return the most common class, taking into account the samples
        # weights
        return DummyClassifier(strategy='prior')

    def __call__(self, y, pred, sample_weight=None):
        """Compute the deviance (= 2 * negative log-likelihood).

        Parameters
        ----------
        y : array, shape (n_samples,)
            True labels

        pred : array, shape (n_samples,)
            Predicted labels

        sample_weight : array-like, shape (n_samples,), optional
            Sample weights.
        """
        # logaddexp(0, v) == log(1.0 + exp(v))
        pred = pred.ravel()
        if sample_weight is None:
            return -2.0 * np.mean((y * pred) - np.logaddexp(0.0, pred))
        else:
            return (-2.0 / sample_weight.sum() *
                    np.sum(sample_weight *
                           ((y * pred) - np.logaddexp(0.0, pred))))

    def negative_gradient(self, y, pred, **kargs):
        """Compute the residual (= negative gradient).

        Parameters
        ----------
        y : array, shape (n_samples,)
            True labels

        pred : array, shape (n_samples,)
            Predicted labels
        """
        return y - expit(pred.ravel())

    def _update_terminal_region(self, tree, terminal_regions, leaf, X, y,
                                residual, pred, sample_weight):
        """Make a single Newton-Raphson step.

        our node estimate is given by:

            sum(w * (y - prob)) / sum(w * prob * (1 - prob))

        we take advantage that: y - prob = residual
        """
        terminal_region = np.where(terminal_regions == leaf)[0]
        residual = residual.take(terminal_region, axis=0)
        y = y.take(terminal_region, axis=0)
        sample_weight = sample_weight.take(terminal_region, axis=0)

        numerator = np.sum(sample_weight * residual)
        denominator = np.sum(sample_weight *
                             (y - residual) * (1 - y + residual))

        # prevents overflow and division by zero
        if abs(denominator) < 1e-150:
            tree.value[leaf, 0, 0] = 0.0
        else:
            tree.value[leaf, 0, 0] = numerator / denominator

    def _score_to_proba(self, score):
        proba = np.ones((score.shape[0], 2), dtype=np.float64)
        proba[:, 1] = expit(score.ravel())
        proba[:, 0] -= proba[:, 1]
        return proba

    def _score_to_decision(self, score):
        proba = self._score_to_proba(score)
        return np.argmax(proba, axis=1)

    def get_init_raw_predictions(self, X, estimator):
        proba_pos_class = estimator.predict_proba(X)[:, 1]
        eps = np.finfo(np.float32).eps
        proba_pos_class = np.clip(proba_pos_class, eps, 1 - eps)
        # log(x / (1 - x)) is the inverse of the sigmoid (expit) function
        raw_predictions = np.log(proba_pos_class / (1 - proba_pos_class))
        return raw_predictions.reshape(-1, 1).astype(np.float64)


class MultinomialDeviance(ClassificationLossFunction):
    """Multinomial deviance loss function for multi-class classification.

    For multi-class classification we need to fit ``n_classes`` trees at
    each stage.

    Parameters
    ----------
    n_classes : int
        Number of classes
    """

    is_multi_class = True

    def __init__(self, n_classes):
        if n_classes < 3:
            raise ValueError("{0:s} requires more than 2 classes.".format(
                self.__class__.__name__))
        super().__init__(n_classes)

    def init_estimator(self):
        return DummyClassifier(strategy='prior')

    def __call__(self, y, pred, sample_weight=None):
        """Compute the Multinomial deviance.

        Parameters
        ----------
        y : array, shape (n_samples,)
            True labels

        pred : array, shape (n_samples,)
            Predicted labels

        sample_weight : array-like, shape (n_samples,), optional
            Sample weights.
        """
        # create one-hot label encoding
        Y = np.zeros((y.shape[0], self.K), dtype=np.float64)
        for k in range(self.K):
            Y[:, k] = y == k

        if sample_weight is None:
            return np.sum(-1 * (Y * pred).sum(axis=1) +
                          logsumexp(pred, axis=1))
        else:
            return np.sum(-1 * sample_weight * (Y * pred).sum(axis=1) +
                          logsumexp(pred, axis=1))

    def negative_gradient(self, y, pred, k=0, **kwargs):
        """Compute negative gradient for the ``k``-th class.

        Parameters
        ----------
        y : array, shape (n_samples,)
            The target labels.

        pred : array, shape (n_samples,)
            The predictions.

        k : int, optional (default=0)
            The index of the class
        """
        return y - np.nan_to_num(np.exp(pred[:, k] -
                                        logsumexp(pred, axis=1)))

    def _update_terminal_region(self, tree, terminal_regions, leaf, X, y,
                                residual, pred, sample_weight):
        """Make a single Newton-Raphson step. """
        terminal_region = np.where(terminal_regions == leaf)[0]
        residual = residual.take(terminal_region, axis=0)
        y = y.take(terminal_region, axis=0)
        sample_weight = sample_weight.take(terminal_region, axis=0)

        numerator = np.sum(sample_weight * residual)
        numerator *= (self.K - 1) / self.K

        denominator = np.sum(sample_weight * (y - residual) *
                             (1.0 - y + residual))

        # prevents overflow and division by zero
        if abs(denominator) < 1e-150:
            tree.value[leaf, 0, 0] = 0.0
        else:
            tree.value[leaf, 0, 0] = numerator / denominator

    def _score_to_proba(self, score):
        return np.nan_to_num(
            np.exp(score - (logsumexp(score, axis=1)[:, np.newaxis])))

    def _score_to_decision(self, score):
        proba = self._score_to_proba(score)
        return np.argmax(proba, axis=1)

    def get_init_raw_predictions(self, X, estimator):
        probas = estimator.predict_proba(X)
        eps = np.finfo(np.float32).eps
        probas = np.clip(probas, eps, 1 - eps)

        raw_predictions = np.log(probas).astype(np.float64)
        return raw_predictions


class ExponentialLoss(ClassificationLossFunction):
    """Exponential loss function for binary classification.

    Same loss as AdaBoost.

    References
    ----------
    Greg Ridgeway, Generalized Boosted Models: A guide to the gbm package, 2007

    Parameters
    ----------
    n_classes : int
        Number of classes.
    """
    def __init__(self, n_classes):
        if n_classes != 2:
            raise ValueError("{0:s} requires 2 classes; got {1:d} class(es)"
                             .format(self.__class__.__name__, n_classes))
        # we only need to fit one tree for binary clf.
        super().__init__(1)

    def init_estimator(self):
        return DummyClassifier(strategy='prior')

    def __call__(self, y, pred, sample_weight=None):
        """Compute the exponential loss

        Parameters
        ----------
        y : array, shape (n_samples,)
            True labels

        pred : array, shape (n_samples,)
            Predicted labels

        sample_weight : array-like, shape (n_samples,), optional
            Sample weights.
        """
        pred = pred.ravel()
        if sample_weight is None:
            return np.mean(np.exp(-(2. * y - 1.) * pred))
        else:
            return (1.0 / sample_weight.sum() *
                    np.sum(sample_weight * np.exp(-(2 * y - 1) * pred)))

    def negative_gradient(self, y, pred, **kargs):
        """Compute the residual (= negative gradient).

        Parameters
        ----------
        y : array, shape (n_samples,)
            True labels

        pred : array, shape (n_samples,)
            Predicted labels
        """
        y_ = -(2. * y - 1.)
        return y_ * np.exp(y_ * pred.ravel())

    def _update_terminal_region(self, tree, terminal_regions, leaf, X, y,
                                residual, pred, sample_weight):
        terminal_region = np.where(terminal_regions == leaf)[0]
        pred = pred.take(terminal_region, axis=0)
        y = y.take(terminal_region, axis=0)
        sample_weight = sample_weight.take(terminal_region, axis=0)

        y_ = 2. * y - 1.

        numerator = np.sum(y_ * sample_weight * np.exp(-y_ * pred))
        denominator = np.sum(sample_weight * np.exp(-y_ * pred))

        # prevents overflow and division by zero
        if abs(denominator) < 1e-150:
            tree.value[leaf, 0, 0] = 0.0
        else:
            tree.value[leaf, 0, 0] = numerator / denominator

    def _score_to_proba(self, score):
        proba = np.ones((score.shape[0], 2), dtype=np.float64)
        proba[:, 1] = expit(2.0 * score.ravel())
        proba[:, 0] -= proba[:, 1]
        return proba

    def _score_to_decision(self, score):
        return (score.ravel() >= 0.0).astype(np.int)

    def get_init_raw_predictions(self, X, estimator):
        proba_pos_class = estimator.predict_proba(X)[:, 1]
        eps = np.finfo(np.float32).eps
        proba_pos_class = np.clip(proba_pos_class, eps, 1 - eps)
        # according to The Elements of Statistical Learning sec. 10.5, the
        # minimizer of the exponential loss is .5 * log odds ratio.
        raw_predictions = .5 * np.log(proba_pos_class / (1 - proba_pos_class))
        return raw_predictions.reshape(-1, 1).astype(np.float64)


LOSS_FUNCTIONS = {
    'ls': LeastSquaresError,
    'lad': LeastAbsoluteError,
    'huber': HuberLossFunction,
    'quantile': QuantileLossFunction,
    'deviance': None,    # for both, multinomial and binomial
    'exponential': ExponentialLoss,
}

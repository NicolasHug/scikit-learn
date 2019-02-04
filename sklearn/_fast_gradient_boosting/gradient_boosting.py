"""Fast Gradient Boosting decision trees for classification and regression."""
from abc import ABC, abstractmethod

import numpy as np
from time import time
from sklearn.base import BaseEstimator, RegressorMixin, ClassifierMixin
from sklearn.utils import check_X_y, check_random_state, check_array
from sklearn.utils.validation import check_is_fitted
from sklearn.utils.multiclass import check_classification_targets
from sklearn.metrics import check_scoring
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from ._gradient_boosting import _update_raw_predictions
from .types import Y_DTYPE, X_DTYPE, X_BINNED_DTYPE

from .binning import BinMapper
from .grower import TreeGrower
from .loss import _LOSSES


class BaseFastGradientBoosting(BaseEstimator, ABC):
    """Base class for fast gradient boosting estimators."""

    @abstractmethod
    def __init__(self, loss, learning_rate, n_estimators, max_leaf_nodes,
                 max_depth, min_samples_leaf, l2_regularization, max_bins,
                 scoring, validation_fraction, n_iter_no_change, tol, verbose,
                 random_state):
        self.loss = loss
        self.learning_rate = learning_rate
        self.n_estimators = n_estimators
        self.max_leaf_nodes = max_leaf_nodes
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        self.l2_regularization = l2_regularization
        self.max_bins = max_bins
        self.n_iter_no_change = n_iter_no_change
        self.validation_fraction = validation_fraction
        self.scoring = scoring
        self.tol = tol
        self.verbose = verbose
        self.random_state = random_state

    def _validate_parameters(self):
        """Validate parameters passed to __init__.

        The parameters that are directly passed to the grower are checked in
        TreeGrower."""

        if self.loss not in self._VALID_LOSSES:
            raise ValueError(
                "Loss {} is not supported for {}. Accepted losses: "
                "{}.".format(self.loss, self.__class__.__name__,
                             ', '.join(self._VALID_LOSSES)))

        if self.learning_rate <= 0:
            raise ValueError('learning_rate={} must '
                             'be strictly positive'.format(self.learning_rate))
        if self.n_estimators < 1:
            raise ValueError('n_estimators={} must not be smaller '
                             'than 1.'.format(self.n_estimators))
        if self.n_iter_no_change is not None and self.n_iter_no_change < 0:
            raise ValueError('n_iter_no_change={} must be '
                             'positive.'.format(self.n_iter_no_change))
        if (self.validation_fraction is not None and
                self.validation_fraction <= 0):
            raise ValueError(
                'validation_fraction={} must be strictly '
                'positive, or None.'.format(self.validation_fraction))
        if self.tol is not None and self.tol < 0:
            raise ValueError('tol={} '
                             'must not be smaller than 0.'.format(self.tol))

    def fit(self, X, y):
        """Fit the gradient boosting model.

        Parameters
        ----------
        X : array-like, shape=(n_samples, n_features)
            The input samples.

        y : array-like, shape=(n_samples,)
            Target values.

        Returns
        -------
        self : object
        """

        fit_start_time = time()
        acc_find_split_time = 0.  # time spent finding the best splits
        acc_apply_split_time = 0.  # time spent splitting nodes
        # time spent predicting X for gradient and hessians update
        acc_prediction_time = 0.
        X, y = check_X_y(X, y, dtype=[X_DTYPE])
        y = self._encode_y(y)
        rng = check_random_state(self.random_state)

        self._validate_parameters()
        self.n_features_ = X.shape[1]  # used for validation in predict()

        # we need this stateful variable to tell raw_predict() that it was
        # called from fit(), which only passes pre-binned data to
        # raw_predict() via the scorer_ attribute. predicting is faster on
        # pre-binned data.
        self._in_fit = True

        # bin the data
        if self.verbose:
            print("Binning {:.3f} GB of data: ".format(X.nbytes / 1e9), end="",
                  flush=True)
        tic = time()
        self.bin_mapper_ = BinMapper(max_bins=self.max_bins, random_state=rng)
        X_binned = self.bin_mapper_.fit_transform(X)
        toc = time()
        if self.verbose:
            duration = toc - tic
            print("{:.3f} s".format(duration))

        self.loss_ = self._get_loss()

        self.do_early_stopping_ = (self.n_iter_no_change is not None and
                                   self.n_iter_no_change > 0)

        # create validation data if needed
        if self.do_early_stopping_ and self.validation_fraction is not None:
            # stratify for classification
            stratify = y if hasattr(self.loss_, 'predict_proba') else None

            X_binned_train, X_binned_val, y_train, y_val = train_test_split(
                X_binned, y, test_size=self.validation_fraction,
                stratify=stratify, random_state=rng)
            if X_binned_train.size == 0 or X_binned_val.size == 0:
                raise ValueError(
                    'Not enough data (n_samples={}) to '
                    'perform early stopping with validation_fraction='
                    '{}. Use more training data or '
                    'adjust validation_fraction.'.format(
                        X_binned.shape[0],
                        self.validation_fraction)
                )
            # Predicting is faster of C-contiguous arrays, training is faster
            # on Fortran arrays.
            X_binned_val = np.ascontiguousarray(X_binned_val)
            X_binned_train = np.asfortranarray(X_binned_train)
        else:
            X_binned_train, y_train = X_binned, y
            X_binned_val, y_val = None, None

        # Subsample the training set for early stopping and score monitoring
        if self.do_early_stopping_:
            subsample_size = 10000  # should we expose this parameter?
            indices = np.arange(X_binned_train.shape[0])
            if X_binned_train.shape[0] > subsample_size:
                indices = rng.choice(indices, subsample_size)
            X_binned_small_train = X_binned_train[indices]
            y_small_train = y_train[indices]
            # Predicting is faster on C-contiguous arrays.
            X_binned_small_train = np.ascontiguousarray(X_binned_small_train)

        if self.verbose:
            print("Fitting gradient boosted rounds:")

        # initialize raw_predictions: those are the accumulated values
        # predicted by the trees for the training data. raw_predictions has
        # shape (n_trees_per_iteration, n_samples) where
        # n_trees_per_iterations is n_classes in multiclass classification,
        # else 1.
        n_samples = X_binned_train.shape[0]
        self.baseline_prediction_ = self.loss_.get_baseline_prediction(
            y_train, self.n_trees_per_iteration_)
        raw_predictions = np.zeros(
            shape=(self.n_trees_per_iteration_, n_samples),
            dtype=self.baseline_prediction_.dtype
        )
        raw_predictions += self.baseline_prediction_

        # initialize gradients and hessians (empty arrays).
        # shape = (n_trees_per_iteration, n_samples).
        gradients, hessians = self.loss_.init_gradients_and_hessians(
            n_samples=n_samples,
            prediction_dim=self.n_trees_per_iteration_
        )

        # estimators_ is a matrix (list of lists) of TreePredictor objects
        # with shape (n_iter_, n_trees_per_iteration)
        self.estimators_ = estimators = []

        # scorer_ is a callable with signature (est, X, y) and calls
        # est.predict() or est.predict_proba() depending on its nature.
        if self.scoring != 'loss':
            self.scorer_ = check_scoring(self, self.scoring)
        else:
            self.scorer_ = None
        self.train_score_ = []
        self.validation_score_ = []
        if self.do_early_stopping_:
            # Add predictions of the initial model (before the first tree)
            self.train_score_.append(
                self._get_scores(X_binned_small_train, y_small_train))

            if self.validation_fraction is not None:
                self.validation_score_.append(
                    self._get_scores(X_binned_val, y_val))

        for iteration in range(self.n_estimators):

            if self.verbose:
                iteration_start_time = time()
                print("[{}/{}] ".format(iteration + 1, self.n_estimators),
                      end='', flush=True)

            # Update gradients and hessians, inplace
            self.loss_.update_gradients_and_hessians(gradients, hessians,
                                                     y_train, raw_predictions)

            estimators.append([])

            # Build `n_trees_per_iteration` trees.
            for k in range(self.n_trees_per_iteration_):

                grower = TreeGrower(
                    X_binned_train, gradients[k, :], hessians[k, :],
                    max_bins=self.max_bins,
                    n_bins_per_feature=self.bin_mapper_.n_bins_per_feature_,
                    max_leaf_nodes=self.max_leaf_nodes,
                    max_depth=self.max_depth,
                    min_samples_leaf=self.min_samples_leaf,
                    l2_regularization=self.l2_regularization,
                    shrinkage=self.learning_rate)
                grower.grow()

                acc_apply_split_time += grower.total_apply_split_time
                acc_find_split_time += grower.total_find_split_time

                estimator = grower.make_predictor(
                    bin_thresholds=self.bin_mapper_.bin_thresholds_)
                estimators[-1].append(estimator)

                # Update raw_predictions with the predictions of the newly
                # created tree.
                tic_pred = time()
                _update_raw_predictions(raw_predictions[k, :], grower)
                toc_pred = time()
                acc_prediction_time += toc_pred - tic_pred

            should_early_stop = False
            if self.do_early_stopping_:
                should_early_stop = self._check_early_stopping(
                    X_binned_small_train, y_small_train,
                    X_binned_val, y_val)

            if self.verbose:
                self._print_iteration_stats(iteration_start_time)

            # maybe we could also early stop if all the trees are stumps?
            if should_early_stop:
                break

        if self.verbose:
            duration = time() - fit_start_time
            n_total_leaves = sum(
                estimator.get_n_leaf_nodes()
                for predictors_at_ith_iteration in self.estimators_
                for estimator in predictors_at_ith_iteration)
            n_predictors = sum(
                len(predictors_at_ith_iteration)
                for predictors_at_ith_iteration in self.estimators_)
            print("Fit {} trees in {:.3f} s, ({} total leaves)".format(
                n_predictors, duration, n_total_leaves))
            print("{:<32} {:.3f}s".format('Time spent finding best splits:',
                                          acc_find_split_time))
            print("{:<32} {:.3f}s".format('Time spent applying splits:',
                                          acc_apply_split_time))
            print("{:<32} {:.3f}s".format('Time spent predicting:',
                                          acc_prediction_time))

        self.train_score_ = np.asarray(self.train_score_)
        self.validation_score_ = np.asarray(self.validation_score_)
        self._in_fit = False
        return self

    def _check_early_stopping(self, X_binned_train, y_train,
                              X_binned_val, y_val):
        """Check if fitting should be early-stopped.

        Scores are computed on validation data or on training data.
        """

        self.train_score_.append(
            self._get_scores(X_binned_train, y_train))

        if self.validation_fraction is not None:
            self.validation_score_.append(
                self._get_scores(X_binned_val, y_val))
            return self._should_stop(self.validation_score_)

        return self._should_stop(self.train_score_)

    def _should_stop(self, scores):
        """
        Return True (do early stopping) if the last n scores aren't better
        than the (n-1)th-to-last score, up to some tolerance.
        """
        reference_position = self.n_iter_no_change + 1
        if len(scores) < reference_position:
            return False

        # A higher score is always better. Higher tol means that it will be
        # harder for subsequent iteration to be considered an improvement upon
        # the reference score, and therefore it is more likely to early stop
        # because of the lack of significant improvement.
        tol = 0 if self.tol is None else self.tol
        reference_score = scores[-reference_position] + tol
        recent_scores = scores[-reference_position + 1:]
        recent_improvements = [score > reference_score
                               for score in recent_scores]
        return not any(recent_improvements)

    def _get_scores(self, X, y):
        """Compute scores on data X with target y.

        Scores are computed with a scorer if scoring parameter is not
        'loss', else with the loss. As higher is always better, we return
        -loss_value.
        """

        if self.scoring != 'loss':
            return self.scorer_(self, X, y)

        # Else, use loss
        raw_predictions = self._raw_predict(X)
        return -self.loss_(y, raw_predictions)

    def _print_iteration_stats(self, iteration_start_time):
        """Print info about the current fitting iteration."""
        log_msg = ''

        predictors_of_ith_iteration = [
            predictors_list for predictors_list in self.estimators_[-1]
            if predictors_list
        ]
        n_trees = len(predictors_of_ith_iteration)
        max_depth = max(estimator.get_max_depth()
                        for estimator in predictors_of_ith_iteration)
        n_leaves = sum(estimator.get_n_leaf_nodes()
                       for estimator in predictors_of_ith_iteration)

        if n_trees == 1:
            log_msg += ("{} tree, {} leaves, ".format(n_trees, n_leaves))
        else:
            log_msg += ("{} trees, {} leaves ".format(n_trees, n_leaves))
            log_msg += ("({} on avg), ".format(int(n_leaves / n_trees)))

        log_msg += "max depth = {}, ".format(max_depth)

        if self.do_early_stopping_:
            name = 'neg-loss' if self.scoring == 'loss' else 'score'
            log_msg += "train {}: {:.5f}, ".format(name, self.train_score_[-1])
            if self.validation_fraction is not None:
                log_msg += "val {}: {:.5f}, ".format(
                    name, self.validation_score_[-1])

        iteration_time = time() - iteration_start_time
        log_msg += "in {:0.3f}s".format(iteration_time)

        print(log_msg)

    def _raw_predict(self, X):
        """Return the sum of the leaves values over all predictors.

        Parameters
        ----------
        X : array-like, shape=(n_samples, n_features)
            The input samples.

        Returns
        -------
        raw_predictions : array, shape (n_samples * n_trees_per_iteration,)
            The raw predicted values.
        """
        X = check_array(X, dtype=[X_DTYPE, X_BINNED_DTYPE])
        check_is_fitted(self, 'estimators_')
        if X.shape[1] != self.n_features_:
            raise ValueError(
                'X has {} features but this estimator was trained with '
                '{} features.'.format(X.shape[1], self.n_features_)
            )
        is_binned = self._in_fit and X.dtype == X_BINNED_DTYPE
        n_samples = X.shape[0]
        raw_predictions = np.zeros(
            shape=(self.n_trees_per_iteration_, n_samples),
            dtype=self.baseline_prediction_.dtype
        )
        raw_predictions += self.baseline_prediction_
        for predictors_of_ith_iteration in self.estimators_:
            for k, estimator in enumerate(predictors_of_ith_iteration):
                predict = (estimator.predict_binned if is_binned
                           else estimator.predict)
                raw_predictions[k, :] += predict(X)

        return raw_predictions

    @abstractmethod
    def _get_loss(self):
        pass

    @abstractmethod
    def _encode_y(self, y=None):
        pass

    @property
    def n_estimators_(self):
        check_is_fitted(self, 'estimators_')
        return len(self.estimators_)


class FastGradientBoostingRegressor(BaseFastGradientBoosting, RegressorMixin):
    """Fast Gradient Boosting Regression Tree.

    This estimator is much faster than
    :class:`GradientBoostingRegressor<sklearn.ensemble.GradientBoostingRegressor>`
    for big datasets (n_samples >= 10 000). The input data `X` is pre-binned
    into integer-valued bins, which considerably reduces the number of
    splitting points to consider.

    Parameters
    ----------
    loss : {'least_squares'}, optional(default='least_squares')
        The loss function to use in the boosting process.
    learning_rate : float, optional(default=0.1)
        The learning rate, also known as *shrinkage*. This is used as a
        multiplicative factor for the leaves values. Use ``1`` for no
        shrinkage.
    n_estimators : int, optional(default=100)
        The maximum number of iterations of the boosting process, i.e. the
        maximum number of trees.
    max_leaf_nodes : int or None, optional(default=None)
        The maximum number of leaves for each tree. If None, there is no
        maximum limit.
    max_depth : int or None, optional(default=None)
        The maximum depth of each tree. The depth of a tree is the number of
        nodes to go from the root to the deepest leaf.
    min_samples_leaf : int, optional(default=5)
        The minimum number of samples per leaf.
    l2_regularization : float, optional(default=0)
        The L2 regularization parameter. Use 0 for no regularization.
    max_bins : int, optional(default=256)
        The maximum number of bins to use. Before training, each feature of
        the input array ``X`` is binned into at most ``max_bins`` bins, which
        allows for a much faster training stage. Features with a small
        number of unique values may use less than ``max_bins`` bins. Must be no
        larger than 256.
    scoring : str or callable or None, optional (default=None)
        Scoring parameter to use for early stopping. It can be a single
        string (see :ref:`scoring_parameter`) or a callable (see
        :ref:`scoring`). If None, the estimator's default scorer is used. If
        ``scoring='loss'``, early stopping is checked w.r.t the loss value.
        Only used if ``n_iter_no_change`` is not None.
    validation_fraction : int or float or None, optional(default=0.1)
        Proportion (or absolute size) of training data to set aside as
        validation data for early stopping. If None, early stopping is done on
        the training data. Only used if ``n_iter_no_change`` is not None.
    n_iter_no_change : int or None, optional (default=None)
        Used to determine when to "early stop". The fitting process is
        stopped when none of the last ``n_iter_no_change`` scores are better
        than the ``n_iter_no_change - 1``th-to-last one, up to some
        tolerance. If None or 0, no early-stopping is done.
    tol : float or None optional (default=1e-7)
        The absolute tolerance to use when comparing scores during early
        stopping. The higher the tolerance, the more likely we are to early
        stop: higher tolerance means that it will be harder for subsequent
        iterations to be considered an improvement upon the reference score.
    verbose: int, optional (default=0)
        The verbosity level. If not zero, print some information about the
        fitting process.
    random_state : int, np.random.RandomStateInstance or None, \
        optional (default=None)
        Pseudo-random number generator to control the subsampling in the
        binning process, and the train/validation data split if early stopping
        is enabled. See :term:`random_state`.

    Attributes
    ----------
    n_estimators_ : int
        The number of estimators as selected by early stopping (if
        n_iter_no_change is not None). Otherwise it is set to n_estimators.
    estimators_ : list of lists, shape=(n_estimators, n_trees_per_iteration)
        The collection of fitted sub-estimators. The number of trees per
        iteration is ``n_classes`` in multiclass classification, else 1.
    train_score_ : array, shape=(n_estimators + 1)
        The scores at each iteration on the training data. The first entry is
        the score of the ensemble before the first iteration. Scores are
        computed according to the ``scoring`` parameter. Empty if no early
        stopping.
    validation_score_ : array, shape=(n_estimators + 1)
        The scores at each iteration on the held-out validation data. The
        first entry is the score of the ensemble before the first iteration.
        Scores are computed according to the ``scoring`` parameter. Empty if
        no early stopping or if ``validation_fraction`` is None.

    Examples
    --------
    >>> from sklearn.datasets import load_boston
    >>> from sklearn.ensemble import FastGradientBoostingRegressor
    >>> X, y = load_boston(return_X_y=True)
    >>> est = FastGradientBoostingRegressor().fit(X, y)
    >>> est.score(X, y)
    0.99...
    """

    _VALID_LOSSES = ('least_squares',)

    def __init__(self, loss='least_squares', learning_rate=0.1,
                 n_estimators=100, max_leaf_nodes=31, max_depth=None,
                 min_samples_leaf=5, l2_regularization=0., max_bins=256,
                 scoring=None, validation_fraction=0.1, n_iter_no_change=None,
                 tol=1e-7, verbose=0, random_state=None):
        super(FastGradientBoostingRegressor, self).__init__(
            loss=loss, learning_rate=learning_rate, n_estimators=n_estimators,
            max_leaf_nodes=max_leaf_nodes, max_depth=max_depth,
            min_samples_leaf=min_samples_leaf,
            l2_regularization=l2_regularization, max_bins=max_bins,
            scoring=scoring, validation_fraction=validation_fraction,
            n_iter_no_change=n_iter_no_change, tol=tol, verbose=verbose,
            random_state=random_state)

    def predict(self, X):
        """Predict values for X.

        Parameters
        ----------
        X : array-like, shape=(n_samples, n_features)
            The input samples.

        Returns
        -------
        y : array, shape (n_samples,)
            The predicted values.
        """
        # Return raw predictions after converting shape
        # (n_samples, 1) to (n_samples,)
        return self._raw_predict(X).ravel()

    def _encode_y(self, y):
        # Just convert y to the expected dtype
        self.n_trees_per_iteration_ = 1
        y = y.astype(Y_DTYPE, copy=False)
        return y

    def _get_loss(self):
        return _LOSSES[self.loss]()


class FastGradientBoostingClassifier(BaseFastGradientBoosting,
                                     ClassifierMixin):
    """Fast Gradient Boosting Classification Tree.

    This estimator is much faster than
    :class:`GradientBoostingClassifier<sklearn.ensemble.GradientBoostingClassifier>`
    for big datasets (n_samples >= 10 000). The input data `X` is pre-binned
    into integer-valued bins, which considerably reduces the number of
    splitting points to consider.

    Parameters
    ----------
    loss : {'auto', 'binary_crossentropy', 'categorical_crossentropy'}, \
        optional(default='auto')
        The loss function to use in the boosting process. 'binary_crossentropy'
        (also known as logistic loss) is used for binary classification and
        generalizes to 'categorical_crossentropy' for multiclass
        classification. 'auto' will automatically choose either loss depending
        on the nature of the problem.
    learning_rate : float, optional(default=1)
        The learning rate, also known as *shrinkage*. This is used as a
        multiplicative factor for the leaves values. Use ``1`` for no
        shrinkage.
    n_estimators : int, optional(default=100)
        The maximum number of iterations of the boosting process, i.e. the
        maximum number of trees for binary classification. For multiclass
        classification, `n_classes` trees per iteration are built.
    max_leaf_nodes : int or None, optional(default=None)
        The maximum number of leaves for each tree. If None, there is no
        maximum limit.
    max_depth : int or None, optional(default=None)
        The maximum depth of each tree. The depth of a tree is the number of
        nodes to go from the root to the deepest leaf.
    min_samples_leaf : int, optional(default=5)
        The minimum number of samples per leaf.
    l2_regularization : float, optional(default=0)
        The L2 regularization parameter. Use 0 for no regularization.
    max_bins : int, optional(default=256)
        The maximum number of bins to use. Before training, each feature of
        the input array ``X`` is binned into at most ``max_bins`` bins, which
        allows for a much faster training stage. Features with a small
        number of unique values may use less than ``max_bins`` bins. Must be no
        larger than 256.
    scoring : str or callable or None, optional (default=None)
        Scoring parameter to use for early stopping. It can be a single
        string (see :ref:`scoring_parameter`) or a callable (see
        :ref:`scoring`). If None, the estimator's default scorer
        is used. If ``scoring='loss'``, early stopping is checked
        w.r.t the loss value. Only used if ``n_iter_no_change`` is not None.
    validation_fraction : int or float or None, optional(default=0.1)
        Proportion (or absolute size) of training data to set aside as
        validation data for early stopping. If None, early stopping is done on
        the training data.
    n_iter_no_change : int or None, optional (default=None)
        Used to determine when to "early stop". The fitting process is
        stopped when none of the last ``n_iter_no_change`` scores are better
        than the ``n_iter_no_change - 1``th-to-last one, up to some
        tolerance. If None or 0, no early-stopping is done.
    tol : float or None optional (default=1e-7)
        The absolute tolerance to use when comparing scores. The higher the
        tolerance, the more likely we are to early stop: higher tolerance
        means that it will be harder for subsequent iterations to be
        considered an improvement upon the reference score.
    verbose: int, optional(default=0)
        The verbosity level. If not zero, print some information about the
        fitting process.
    random_state : int, np.random.RandomStateInstance or None, \
        optional(default=None)
        Pseudo-random number generator to control the subsampling in the
        binning process, and the train/validation data split if early stopping
        is enabled. See :term:`random_state`.

    Attributes
    ----------
    n_estimators_ : int
        The number of estimators as selected by early stopping (if
        n_iter_no_change is not None). Otherwise it is set to n_estimators.
    estimators_ : list of lists, shape=(n_estimators, n_trees_per_iteration)
        The collection of fitted sub-estimators. The number of trees per
        iteration is ``n_classes`` in multiclass classification, else 1.
    train_score_ : array, shape=(n_estimators + 1)
        The scores at each iteration on the training data. The first entry is
        the score of the ensemble before the first iteration. Scores are
        computed according to the ``scoring`` parameter. Empty if no early
        stopping.
    validation_score_ : array, shape=(n_estimators + 1)
        The scores at each iteration on the held-out validation data. The
        first entry is the score of the ensemble before the first iteration.
        Scores are computed according to the ``scoring`` parameter. Empty if
        no early stopping or if ``validation_fraction`` is None.

    Examples
    --------
    >>> from sklearn.datasets import load_iris
    >>> from sklearn.ensemble import FastGradientBoostingClassifier
    >>> X, y = load_iris(return_X_y=True)
    >>> clf = FastGradientBoostingClassifier().fit(X, y)
    >>> clf.score(X, y)
    1.0
    """

    _VALID_LOSSES = ('binary_crossentropy', 'categorical_crossentropy',
                     'auto')

    def __init__(self, loss='auto', learning_rate=0.1, n_estimators=100,
                 max_leaf_nodes=31, max_depth=None, min_samples_leaf=5,
                 l2_regularization=0., max_bins=256, scoring=None,
                 validation_fraction=0.1, n_iter_no_change=None, tol=1e-7,
                 verbose=0, random_state=None):
        super(FastGradientBoostingClassifier, self).__init__(
            loss=loss, learning_rate=learning_rate, n_estimators=n_estimators,
            max_leaf_nodes=max_leaf_nodes, max_depth=max_depth,
            min_samples_leaf=min_samples_leaf,
            l2_regularization=l2_regularization, max_bins=max_bins,
            scoring=scoring, validation_fraction=validation_fraction,
            n_iter_no_change=n_iter_no_change, tol=tol, verbose=verbose,
            random_state=random_state)

    def predict(self, X):
        """Predict classes for X.

        Parameters
        ----------
        X : array-like, shape=(n_samples, n_features)
            The input samples.

        Returns
        -------
        y : array, shape (n_samples,)
            The predicted classes.
        """
        # This could be done in parallel
        encoded_classes = np.argmax(self.predict_proba(X), axis=1)
        return self.classes_[encoded_classes]

    def predict_proba(self, X):
        """Predict class probabilities for X.

        Parameters
        ----------
        X : array-like, shape=(n_samples, n_features)
            The input samples.

        Returns
        -------
        p : array, shape (n_samples, n_classes)
            The class probabilities of the input samples.
        """
        raw_predictions = self._raw_predict(X)
        return self.loss_.predict_proba(raw_predictions)

    def decision_function(self, X):
        """Compute the decision function of X

        Parameters
        ----------
        X : array-like, shape=(n_samples, n_features)
            The input samples.

        Returns
        -------
        decision : array, shape (n_samples,) or \
                (n_samples, n_trees_per_iteration)
            The raw predicted values (i.e. the sum of the trees leaves) for
            each sample. n_trees_per_iteration is equal to the number of
            classes in multiclass classification.
        """
        decision = self._raw_predict(X)
        if decision.shape[0] == 1:
            decision = decision.ravel()
        return decision.T

    def _encode_y(self, y):
        # encode classes into 0 ... n_classes - 1 and sets attributes classes_
        # and n_trees_per_iteration_
        check_classification_targets(y)

        label_encoder = LabelEncoder()
        encoded_y = label_encoder.fit_transform(y)
        self.classes_ = label_encoder.classes_
        n_classes = self.classes_.shape[0]
        # only 1 tree for binary classification. For multiclass classification,
        # we build 1 tree per class.
        self.n_trees_per_iteration_ = 1 if n_classes <= 2 else n_classes
        encoded_y = encoded_y.astype(Y_DTYPE, copy=False)
        return encoded_y

    def _get_loss(self):
        if self.loss == 'auto':
            if self.n_trees_per_iteration_ == 1:
                return _LOSSES['binary_crossentropy']()
            else:
                return _LOSSES['categorical_crossentropy']()

        return _LOSSES[self.loss]()

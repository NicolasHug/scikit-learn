"""
Linear Discriminant Analysis and Quadratic Discriminant Analysis
"""

# Authors: Clemens Brunner
#          Martin Billinger
#          Matthieu Perrot
#          Mathieu Blondel

# License: BSD 3-Clause

import warnings
import numpy as np
from .exceptions import ChangedBehaviorWarning
from scipy import linalg
from scipy.special import expit

from .base import BaseEstimator, TransformerMixin, ClassifierMixin
from .linear_model._base import LinearClassifierMixin
from .covariance import ledoit_wolf, empirical_covariance, shrunk_covariance
from .utils.multiclass import unique_labels
from .utils import check_array, check_X_y
from .utils.validation import check_is_fitted
from .utils.multiclass import check_classification_targets
from .utils.extmath import softmax
from .preprocessing import StandardScaler


__all__ = ['LinearDiscriminantAnalysis', 'QuadraticDiscriminantAnalysis']


def _cov(X, shrinkage=None, covariance_estimator=None):
    """Estimate covariance matrix (using optional covariance_estimator).
    Parameters
    ----------
    X : array-like of shape (n_samples, n_features)
        Input data.

    shrinkage : {'empirical', 'auto'} or float, default=None
        Shrinkage parameter, possible values:
          - None or 'empirical': no shrinkage (default).
          - 'auto': automatic shrinkage using the Ledoit-Wolf lemma.
          - float between 0 and 1: fixed shrinkage parameter.

        Shrinkage parameter is ignored if  `covariance_estimator`
        is not None.

    covariance_estimator : estimator, default=None
        If not None, `covariance_estimator` is used to estimate
        the covariance matrices instead of relying the empirical
        covariance estimator (with potential shrinkage).
        The object should have a fit method and a covariance_ attribute
        like the estimators in sklearn.covariance.
        if None the shrinkage parameter drives the estimate.

        .. versionadded:: 0.23

    Returns
    -------
    s : ndarray of shape (n_features, n_features)
        Estimated covariance matrix.
    """
    if covariance_estimator is None:
        shrinkage = "empirical" if shrinkage is None else shrinkage
        if isinstance(shrinkage, str):
            if shrinkage == 'auto':
                sc = StandardScaler()  # standardize features
                X = sc.fit_transform(X)
                s = ledoit_wolf(X)[0]
                # rescale
                s = sc.scale_[:, np.newaxis] * s * sc.scale_[np.newaxis, :]
            elif shrinkage == 'empirical':
                s = empirical_covariance(X)
            else:
                raise ValueError('unknown shrinkage parameter')
        elif isinstance(shrinkage, float) or isinstance(shrinkage, int):
            if shrinkage < 0 or shrinkage > 1:
                raise ValueError('shrinkage parameter must be between 0 and 1')
            s = shrunk_covariance(empirical_covariance(X), shrinkage)
        else:
            raise TypeError('shrinkage must be a float or a string')
    else:
        if shrinkage is not None and shrinkage != 0:
            raise ValueError("covariance_estimator and shrinkage parameters "
                             "are not None. Only one of the two can be set.")
        covariance_estimator.fit(X)
        if not hasattr(covariance_estimator, 'covariance_'):
            raise ValueError("%s does not have a covariance_ attribute" %
                             covariance_estimator.__class__.__name__)
        s = covariance_estimator.covariance_
    return s


def _class_means(X, y):
    """Compute class means.

    Parameters
    ----------
    X : array-like of shape (n_samples, n_features)
        Input data.

    y : array-like of shape (n_samples,) or (n_samples, n_targets)
        Target values.

    Returns
    -------
    means : array-like of shape (n_classes, n_features)
        Class means.
    """
    classes, y = np.unique(y, return_inverse=True)
    cnt = np.bincount(y)
    means = np.zeros(shape=(len(classes), X.shape[1]))
    np.add.at(means, y, X)
    means /= cnt[:, None]
    return means


def _class_cov(X, y, priors, shrinkage=None, covariance_estimator=None):
    """Compute class covariance matrix.

    Parameters
    ----------
    X : array-like of shape (n_samples, n_features)
        Input data.

    y : array-like of shape (n_samples,) or (n_samples, n_targets)
        Target values.

    priors : array-like of shape (n_classes,)
        Class priors.

    shrinkage : 'auto' or float, default=None
        Shrinkage parameter, possible values:
          - None: no shrinkage (default).
          - 'auto': automatic shrinkage using the Ledoit-Wolf lemma.
          - float between 0 and 1: fixed shrinkage parameter.

        Shrinkage parameter is ignored if `covariance_estimator` is not None.

    covariance_estimator : estimator, default=None
        If not None, `covariance_estimator` is used to estimate
        the covariance matrices instead of relying the empirical
        covariance estimator (with potential shrinkage).
        The object should have a fit method and a covariance_ attribute
        like the estimators in sklearn.covariance.
        if None the shrinkage parameter drives the estimate.

        .. versionadded:: 0.23

    Returns
    -------
    cov : array-like of shape (n_features, n_features)
        Class covariance matrix.
    """
    classes = np.unique(y)
    cov = np.zeros(shape=(X.shape[1], X.shape[1]))
    for idx, group in enumerate(classes):
        Xg = X[y == group, :]
        cov += priors[idx] * np.atleast_2d(
            _cov(Xg, shrinkage, covariance_estimator))
    return cov


def _classes_cov(X, y, shrinkage=None, covariance_estimator=None):
    """Compute covariance matrix of each class.
    Parameters
    ----------
    X : array-like, shape (n_samples, n_features)
        Input data.

    y : array-like, shape (n_samples,) or (n_samples, n_targets)
        Target values.

    shrinkage : string or float, optional
        Shrinkage parameter, possible values:
          - None: no shrinkage (default).
          - 'auto': automatic shrinkage using the Ledoit-Wolf lemma.
          - float between 0 and 1: fixed shrinkage parameter.

        Shrinkage parameter is ignored if  `covariance_estimator`\
    is not None.

    covariance_estimator : estimator, default=None
        If not None, `covariance_estimator` is used to estimate
        the covariance matrices instead of relying the empirical
        covariance estimator (with potential shrinkage).
        The object should have a fit method and a covariance_ attribute
        like the estimators in sklearn.covariance.
        if None the shrinkage parameter drives the estimate.

        .. versionadded:: 0.23

    Returns
    -------
    cov : n_classes list of array-like shape (n_features, n_features)
        Class covariance matrix of each class.
    """
    classes = np.unique(y)
    covs = []
    for idx, group in enumerate(classes):
        Xg = X[y == group, :]
        if len(Xg) == 1:
            raise ValueError(
                'y contains a class with'
                'only 1 sample. Covariance '
                'is ill defined.')
        covs.append(np.atleast_2d(
            _cov(Xg, shrinkage, covariance_estimator)))
    return np.array(covs)


class LinearDiscriminantAnalysis(BaseEstimator, LinearClassifierMixin,
                                 TransformerMixin):
    """Linear Discriminant Analysis

    A classifier with a linear decision boundary, generated by fitting class
    conditional densities to the data and using Bayes' rule.

    The model fits a Gaussian density to each class, assuming that all classes
    share the same covariance matrix.

    The fitted model can also be used to reduce the dimensionality of the input
    by projecting it to the most discriminative directions.

    .. versionadded:: 0.17
       *LinearDiscriminantAnalysis*.

    Read more in the :ref:`User Guide <lda_qda>`.

    Parameters
    ----------
    solver : {'svd', 'lsqr', 'eigen'}, default='svd'
        Solver to use, possible values:
          - 'svd': Singular value decomposition (default).
            Does not compute the covariance matrix, therefore this solver is
            recommended for data with a large number of features.
          - 'lsqr': Least squares solution.
            Can be combined with shrinkage or custom covariance estimator.
          - 'eigen': Eigenvalue decomposition.
            Can be combined with shrinkage or custom covariance estimator.

    shrinkage : 'auto' or float, default=None
        Shrinkage parameter, possible values:
          - None: no shrinkage (default).
          - 'auto': automatic shrinkage using the Ledoit-Wolf lemma.
          - float between 0 and 1: fixed shrinkage parameter.

        Shrinkage parameter is ignored if  `covariance_estimator`\
    is not None.

        Note that shrinkage works only with 'lsqr' and 'eigen' solvers.

    priors : array-like of shape (n_classes,), default=None
        Class priors.

    n_components : int, default=None
        Number of components (<= min(n_classes - 1, n_features)) for
        dimensionality reduction. If None, will be set to
        min(n_classes - 1, n_features).

    store_covariance : bool, default=False
        Additionally compute class covariance matrix (default False), used
        only in 'svd' solver.

        .. versionadded:: 0.17

    tol : float, default=1.0e-4
        Threshold used for rank estimation in SVD solver.

        .. versionadded:: 0.17

    covariance_estimator : estimator, default=None
        If not None, `covariance_estimator` is used to estimate
        the covariance matrices instead of relying the empirical
        covariance estimator (with potential shrinkage).
        The object should have a fit method and a covariance_ attribute
        like the estimators in sklearn.covariance.
        if None the shrinkage parameter drives the estimate.

        Note that covariance_estimator works only with 'lsqr' and 'eigen'
        solvers.

        .. versionadded:: 0.23

    Attributes
    ----------
    coef_ : ndarray of shape (n_features,) or (n_classes, n_features)
        Weight vector(s).

    intercept_ : ndarray of shape (n_classes,)
        Intercept term.

    covariance_ : array-like of shape (n_features, n_features)
        Covariance matrix (shared by all classes).

    explained_variance_ratio_ : ndarray of shape (n_components,)
        Percentage of variance explained by each of the selected components.
        If ``n_components`` is not set then all components are stored and the
        sum of explained variances is equal to 1.0. Only available when eigen
        or svd solver is used.

    means_ : array-like of shape (n_classes, n_features)
        Class means.

    priors_ : array-like of shape (n_classes,)
        Class priors (sum to 1).

    scalings_ : array-like of shape (rank, n_classes - 1)
        Scaling of the features in the space spanned by the class centroids.

    xbar_ : array-like of shape (n_features,)
        Overall mean.

    classes_ : array-like of shape (n_classes,)
        Unique class labels.

    See also
    --------
    sklearn.discriminant_analysis.QuadraticDiscriminantAnalysis: Quadratic
        Discriminant Analysis

    Notes
    -----
    The default solver is 'svd'. It can perform both classification and
    transform, and it does not rely on the calculation of the covariance
    matrix. This can be an advantage in situations where the number of features
    is large. However, the 'svd' solver cannot be used with shrinkage or
    custom covariance estimator.

    The 'lsqr' solver is an efficient algorithm that only works for
    classification. It supports shrinkage and custom covariance estimator.

    The 'eigen' solver is based on the optimization of the between class
    scatter to within class scatter ratio. It can be used for both
    classification and transform, and it supports shrinkage and
    covariance estimator.
    However, the 'eigen' solver needs to compute the covariance matrix,
    so it might not be suitable for situations with a high number of features.

    Examples
    --------
    >>> import numpy as np
    >>> from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
    >>> X = np.array([[-1, -1], [-2, -1], [-3, -2], [1, 1], [2, 1], [3, 2]])
    >>> y = np.array([1, 1, 1, 2, 2, 2])
    >>> clf = LinearDiscriminantAnalysis()
    >>> clf.fit(X, y)
    LinearDiscriminantAnalysis()
    >>> print(clf.predict([[-0.8, -1]]))
    [1]
    """

    def __init__(self, solver='svd', shrinkage=None, priors=None,
                 n_components=None, store_covariance=False, tol=1e-4,
                 covariance_estimator=None):
        self.solver = solver
        self.shrinkage = shrinkage
        self.priors = priors
        self.n_components = n_components
        self.store_covariance = store_covariance  # used only in svd solver
        self.tol = tol  # used only in svd solver
        self.covariance_estimator = covariance_estimator

    def _solve_lsqr(self, X, y, shrinkage, covariance_estimator):
        """Least squares solver.

        The least squares solver computes a straightforward solution of the
        optimal decision rule based directly on the discriminant functions. It
        can only be used for classification (with any covariance estimator),
        because
        estimation of eigenvectors is not performed. Therefore, dimensionality
        reduction with the transform is not supported.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Training data.

        y : array-like of shape (n_samples,) or (n_samples, n_classes)
            Target values.

        shrinkage : 'auto', float or None
            Shrinkage parameter, possible values:
              - None: no shrinkage.
              - 'auto': automatic shrinkage using the Ledoit-Wolf lemma.
              - float between 0 and 1: fixed shrinkage parameter.

            Shrinkage parameter is ignored if  `covariance_estimator` is \
        not None

        covariance_estimator : estimator, default=None
            If not None, `covariance_estimator` is used to estimate
            the covariance matrices instead of relying the empirical
            covariance estimator (with potential shrinkage).
            The object should have a fit method and a covariance_ attribute
            like the estimators in sklearn.covariance.
            if None the shrinkage parameter drives the estimate.

            .. versionadded:: 0.23

        Notes
        -----
        This solver is based on [1]_, section 2.6.2, pp. 39-41.

        References
        ----------
        .. [1] R. O. Duda, P. E. Hart, D. G. Stork. Pattern Classification
           (Second Edition). John Wiley & Sons, Inc., New York, 2001. ISBN
           0-471-05669-3.
        """
        self.means_ = _class_means(X, y)
        self.covariance_ = _class_cov(X, y, self.priors_, shrinkage,
                                      covariance_estimator)
        self.coef_ = linalg.lstsq(self.covariance_, self.means_.T)[0].T
        self.intercept_ = (-0.5 * np.diag(np.dot(self.means_, self.coef_.T)) +
                           np.log(self.priors_))

    def _solve_eigen(self, X, y, shrinkage, covariance_estimator,
                     ):
        """Eigenvalue solver.

        The eigenvalue solver computes the optimal solution of the Rayleigh
        coefficient (basically the ratio of between class scatter to within
        class scatter). This solver supports both classification and
        dimensionality reduction (with any covariance estimator).

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Training data.

        y : array-like of shape (n_samples,) or (n_samples, n_targets)
            Target values.

        shrinkage : 'auto', float or None
            Shrinkage parameter, possible values:
              - None: no shrinkage.
              - 'auto': automatic shrinkage using the Ledoit-Wolf lemma.
              - float between 0 and 1: fixed shrinkage constant.

            Shrinkage parameter is ignored if  `covariance_estimator` is\
        not None

        covariance_estimator : estimator, default=None
            If not None, `covariance_estimator` is used to estimate
            the covariance matrices instead of relying the empirical
            covariance estimator (with potential shrinkage).
            The object should have a fit method and a covariance_ attribute
            like the estimators in sklearn.covariance.
            if None the shrinkage parameter drives the estimate.

            .. versionadded:: 0.23

        Notes
        -----
        This solver is based on [1]_, section 3.8.3, pp. 121-124.

        References
        ----------
        .. [1] R. O. Duda, P. E. Hart, D. G. Stork. Pattern Classification
           (Second Edition). John Wiley & Sons, Inc., New York, 2001. ISBN
           0-471-05669-3.
        """
        self.means_ = _class_means(X, y)
        self.covariance_ = _class_cov(X, y, self.priors_, shrinkage,
                                      covariance_estimator)

        Sw = self.covariance_  # within scatter
        # total scatter
        St = _cov(X, shrinkage, covariance_estimator)
        Sb = St - Sw  # between scatter

        evals, evecs = linalg.eigh(Sb, Sw)
        self.explained_variance_ratio_ = np.sort(evals / np.sum(evals)
                                                 )[::-1][:self._max_components]
        evecs = evecs[:, np.argsort(evals)[::-1]]  # sort eigenvectors

        self.scalings_ = evecs
        self.coef_ = np.dot(self.means_, evecs).dot(evecs.T)
        self.intercept_ = (-0.5 * np.diag(np.dot(self.means_, self.coef_.T)) +
                           np.log(self.priors_))

    def _solve_svd(self, X, y):
        """SVD solver.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Training data.

        y : array-like of shape (n_samples,) or (n_samples, n_targets)
            Target values.
        """
        n_samples, n_features = X.shape
        n_classes = len(self.classes_)

        self.means_ = _class_means(X, y)
        if self.store_covariance:
            self.covariance_ = _class_cov(X, y, self.priors_)

        Xc = []
        for idx, group in enumerate(self.classes_):
            Xg = X[y == group, :]
            Xc.append(Xg - self.means_[idx])

        self.xbar_ = np.dot(self.priors_, self.means_)

        Xc = np.concatenate(Xc, axis=0)

        # 1) within (univariate) scaling by with classes std-dev
        std = Xc.std(axis=0)
        # avoid division by zero in normalization
        std[std == 0] = 1.
        fac = 1. / (n_samples - n_classes)

        # 2) Within variance scaling
        X = np.sqrt(fac) * (Xc / std)
        # SVD of centered (within)scaled data
        U, S, V = linalg.svd(X, full_matrices=False)

        rank = np.sum(S > self.tol)
        if rank < n_features:
            warnings.warn("Variables are collinear.")
        # Scaling of within covariance is: V' 1/S
        scalings = (V[:rank] / std).T / S[:rank]

        # 3) Between variance scaling
        # Scale weighted centers
        X = np.dot(((np.sqrt((n_samples * self.priors_) * fac)) *
                    (self.means_ - self.xbar_).T).T, scalings)
        # Centers are living in a space with n_classes-1 dim (maximum)
        # Use SVD to find projection in the space spanned by the
        # (n_classes) centers
        _, S, V = linalg.svd(X, full_matrices=0)

        self.explained_variance_ratio_ = (S**2 / np.sum(
            S**2))[:self._max_components]
        rank = np.sum(S > self.tol * S[0])
        self.scalings_ = np.dot(scalings, V.T[:, :rank])
        coef = np.dot(self.means_ - self.xbar_, self.scalings_)
        self.intercept_ = (-0.5 * np.sum(coef ** 2, axis=1) +
                           np.log(self.priors_))
        self.coef_ = np.dot(coef, self.scalings_.T)
        self.intercept_ -= np.dot(self.xbar_, self.coef_.T)

    def fit(self, X, y):
        """Fit LinearDiscriminantAnalysis model according to the given
           training data and parameters.

           .. versionchanged:: 0.19
              *store_covariance* has been moved to main constructor.

           .. versionchanged:: 0.19
              *tol* has been moved to main constructor.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Training data.

        y : array-like of shape (n_samples,)
            Target values.
        """
        X, y = check_X_y(X, y, ensure_min_samples=2, estimator=self,
                         dtype=[np.float64, np.float32])
        self.classes_ = unique_labels(y)
        n_samples, _ = X.shape
        n_classes = len(self.classes_)

        if n_samples == n_classes:
            raise ValueError("The number of samples must be more "
                             "than the number of classes.")

        if self.priors is None:  # estimate priors from sample
            _, y_t = np.unique(y, return_inverse=True)  # non-negative ints
            self.priors_ = np.bincount(y_t) / float(len(y))
        else:
            self.priors_ = np.asarray(self.priors)

        if (self.priors_ < 0).any():
            raise ValueError("priors must be non-negative")
        if not np.isclose(self.priors_.sum(), 1.0):
            warnings.warn("The priors do not sum to 1. Renormalizing",
                          UserWarning)
            self.priors_ = self.priors_ / self.priors_.sum()

        # Maximum number of components no matter what n_components is
        # specified:
        max_components = min(len(self.classes_) - 1, X.shape[1])

        if self.n_components is None:
            self._max_components = max_components
        else:
            if self.n_components > max_components:
                raise ValueError(
                    "n_components cannot be larger than min(n_features, "
                    "n_classes - 1)."
                )
            self._max_components = self.n_components

        if self.solver == 'svd':
            if self.shrinkage is not None:
                raise NotImplementedError('shrinkage not supported')
            if self.covariance_estimator is not None:
                raise NotImplementedError(
                        'covariance estimator '
                        'is not supported '
                        'with svd solver. Try another solver')
            self._solve_svd(X, y)
        elif self.solver == 'lsqr':
            self._solve_lsqr(X, y, shrinkage=self.shrinkage,
                             covariance_estimator=self.covariance_estimator)
        elif self.solver == 'eigen':
            self._solve_eigen(X, y,
                              shrinkage=self.shrinkage,
                              covariance_estimator=self.covariance_estimator)
        else:
            raise ValueError("unknown solver {} (valid solvers are 'svd', "
                             "'lsqr', and 'eigen').".format(self.solver))
        if self.classes_.size == 2:  # treat binary case as a special case
            self.coef_ = np.array(self.coef_[1, :] - self.coef_[0, :], ndmin=2,
                                  dtype=X.dtype)
            self.intercept_ = np.array(self.intercept_[1] - self.intercept_[0],
                                       ndmin=1, dtype=X.dtype)
        return self

    def transform(self, X):
        """Project data to maximize class separation.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Input data.

        Returns
        -------
        X_new : ndarray of shape (n_samples, n_components)
            Transformed data.
        """
        if self.solver == 'lsqr':
            raise NotImplementedError("transform not implemented for 'lsqr' "
                                      "solver (use 'svd' or 'eigen').")
        check_is_fitted(self)

        X = check_array(X)
        if self.solver == 'svd':
            X_new = np.dot(X - self.xbar_, self.scalings_)
        elif self.solver == 'eigen':
            X_new = np.dot(X, self.scalings_)

        return X_new[:, :self._max_components]

    def predict_proba(self, X):
        """Estimate probability.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Input data.

        Returns
        -------
        C : ndarray of shape (n_samples, n_classes)
            Estimated probabilities.
        """
        check_is_fitted(self)

        decision = self.decision_function(X)
        if self.classes_.size == 2:
            proba = expit(decision)
            return np.vstack([1-proba, proba]).T
        else:
            return softmax(decision)

    def predict_log_proba(self, X):
        """Estimate log probability.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Input data.

        Returns
        -------
        C : ndarray of shape (n_samples, n_classes)
            Estimated log probabilities.
        """
        return np.log(self.predict_proba(X))


class QuadraticDiscriminantAnalysis(ClassifierMixin, BaseEstimator):
    """Quadratic Discriminant Analysis

    A classifier with a quadratic decision boundary, generated
    by fitting class conditional densities to the data
    and using Bayes' rule.

    The model fits a Gaussian density to each class.

    .. versionadded:: 0.17
       *QuadraticDiscriminantAnalysis*

    Read more in the :ref:`User Guide <lda_qda>`.

    Parameters
    ----------
    priors : ndarray of shape (n_classes,), default=None
        Priors on classes

    reg_param : float, default=0.0
        Regularizes the covariance estimate as
        ``(1-reg_param)*Sigma + reg_param*np.eye(n_features)``

    store_covariance : bool, default=False
        If True the covariance matrices are computed and stored in the
        `self.covariance_` attribute.

        .. versionadded:: 0.17

    tol : float, default=1.0e-4
        Threshold used for rank estimation.

        .. versionadded:: 0.17

    solver : string, optional
        Solver to use, possible values:
          - 'svd': Singular value decomposition (default).
            Does not compute the covariance matrix, therefore this solver is
            recommended for data with a large number of features.
          - 'lsqr': Least squares solution.
            Can be combined with custom covariance estimator.

        .. versionadded:: 0.23

    covariance_estimator : estimator, default=None
        If not None, `covariance_estimator` is used to estimate
        the covariance matrices instead of relying the empirical
        covariance estimator (with potential shrinkage).
        The object should have a fit method and a covariance_ attribute
        like the estimators in sklearn.covariance.
        if None the reg_param parameter drives the estimate.

        Note that covariance_estimator works only with 'lsqr' solver

    Attributes
    ----------
    covariance_ : list of array-like of shape (n_features, n_features)
        Covariance matrices of each class.

    means_ : array-like of shape (n_classes, n_features)
        Class means.

    priors_ : array-like of shape (n_classes,)
        Class priors (sum to 1).

    rotations_ : list of ndarrays
        For each class k an array of shape (n_features, n_k), with
        ``n_k = min(n_features, number of elements in class k)``
        It is the rotation of the Gaussian distribution, i.e. its
        principal axis.
        Note: only available when solver="svd"

    scalings_ : list of ndarrays
        For each class k an array of shape (n_k,). It contains the scaling
        of the Gaussian distributions along its principal axes, i.e. the
        variance in the rotated coordinate system.
        Note: only available when solver="svd"

    classes_ : array-like of shape (n_classes,)
        Unique class labels.

    Examples
    --------
    >>> from sklearn.discriminant_analysis import QuadraticDiscriminantAnalysis
    >>> import numpy as np
    >>> X = np.array([[-1, -1], [-2, -1], [-3, -2], [1, 1], [2, 1], [3, 2]])
    >>> y = np.array([1, 1, 1, 2, 2, 2])
    >>> clf = QuadraticDiscriminantAnalysis()
    >>> clf.fit(X, y)
    QuadraticDiscriminantAnalysis()
    >>> print(clf.predict([[-0.8, -1]]))
    [1]

    See also
    --------
    sklearn.discriminant_analysis.LinearDiscriminantAnalysis: Linear
        Discriminant Analysis
    """

    def __init__(self, priors=None, reg_param=0., store_covariance=False,
                 tol=1.0e-4, solver='svd', covariance_estimator=None):
        self.priors = np.asarray(priors) if priors is not None else None
        self.reg_param = reg_param
        self.store_covariance = store_covariance
        self.tol = tol
        self.solver = solver
        self.covariance_estimator = covariance_estimator

    def _solve_svd(self, X, y):
        """SVD solver

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Training vector, where n_samples is the number of samples and
            n_features is the number of features.

        y : array-like of shape (n_samples,)
            Target values (integers)
        """
        n_samples, n_features = X.shape
        n_classes = len(self.classes_)

        cov = None
        store_covariance = self.store_covariance
        if store_covariance:
            cov = []
        means = []
        scalings = []
        rotations = []
        for ind in range(n_classes):
            Xg = X[y == ind, :]
            meang = Xg.mean(0)
            means.append(meang)
            if len(Xg) == 1:
                raise ValueError('y has only 1 sample in class %s, covariance '
                                 'is ill defined.' % str(self.classes_[ind]))
            Xgc = Xg - meang
            # Xgc = U * S * V.T
            U, S, Vt = np.linalg.svd(Xgc, full_matrices=False)
            rank = np.sum(S > self.tol)
            if rank < n_features:
                warnings.warn("Variables are collinear")
            S2 = (S ** 2) / (len(Xg) - 1)
            S2 = ((1 - self.reg_param) * S2) + self.reg_param
            if self.store_covariance or store_covariance:
                # cov = V * (S^2 / (n-1)) * V.T
                cov.append(np.dot(S2 * Vt.T, Vt))
            scalings.append(S2)
            rotations.append(Vt.T)
        if self.store_covariance or store_covariance:
            self.covariance_ = cov
        self.means_ = np.asarray(means)
        self.scalings_ = scalings
        self.rotations_ = rotations

        return self

    def _solve_lsqr(
            self,
            X,
            y,
            reg_param,
            covariance_estimator):
        """Least squares solver.

        The least squares solver computes a straightforward solution of the
        optimal decision rule based directly on the discriminant functions.

        .. versionadded:: 0.23

        Parameters
        ----------
        X : array-like, shape (n_samples, n_features)
            Training data.

        y : array-like, shape (n_samples,) or (n_samples, n_classes)
            Target values.

        reg_param : float, optional
            Regularizes the covariance estimate as
            ``(1-reg_param)*Sigma + reg_param*np.eye(n_features)``

        covariance_estimator : estimator, default=None
            If not None, `covariance_estimator` is used to estimate
            the covariance matrices instead of relying the empirical
            covariance estimator (with potential shrinkage).
            The object should have a fit method and a covariance_ attribute
            like the estimators in sklearn.covariance.
            if None the reg_param parameter drives the estimate.

        Notes
        -----
        This solver is based on [1]_, section 2.6.2, pp. 39-41.

        References
        ----------
        .. [1] R. O. Duda, P. E. Hart, D. G. Stork. Pattern Classification
           (Second Edition). John Wiley & Sons, Inc., New York, 2001. ISBN
           0-471-05669-3.
        """
        self.means_ = _class_means(X, y)
        self.covariance_ = _classes_cov(X, y, reg_param, covariance_estimator)
        return self

    def fit(self, X, y):
        """Fit the model according to the given training data and parameters.

            .. versionchanged:: 0.19
               ``store_covariances`` has been moved to main constructor as
               ``store_covariance``

            .. versionchanged:: 0.19
               ``tol`` has been moved to main constructor.

        Parameters
        ----------
        X : array-like, shape = [n_samples, n_features]
            Training vector, where n_samples is the number of samples and
            n_features is the number of features.

        y : array, shape = [n_samples]
            Target values (integers)
        """
        # FIXME: Future warning to be removed in 0.23
        X, y = check_X_y(X, y, ensure_min_samples=2, estimator=self,
                         dtype=[np.float64, np.float32])
        check_classification_targets(y)
        self.classes_, y = np.unique(y, return_inverse=True)
        n_samples, _ = X.shape
        n_classes = len(self.classes_)

        if n_classes < 2:
            raise ValueError('The number of classes has to be greater than'
                             ' one; got %d class' % (n_classes))
        if n_samples == n_classes:
            raise ValueError("The number of samples must be more "
                             "than the number of classes.")

        if self.priors is None:  # estimate priors from sample
            _, y_t = np.unique(y, return_inverse=True)  # non-negative ints
            self.priors_ = np.bincount(y_t) / float(len(y))
        else:
            self.priors_ = np.asarray(self.priors)

        if (self.priors_ < 0).any():
            raise ValueError("priors must be non-negative")
        if not np.isclose(self.priors_.sum(), 1.0):
            warnings.warn("The priors do not sum to 1. Renormalizing",
                          UserWarning)
            self.priors_ = self.priors_ / self.priors_.sum()

        if self.solver == 'svd':
            if self.covariance_estimator is not None:
                raise NotImplementedError(
                    'covariance estimator is not '
                    'supported with svd solver. Try another solver')
            self._solve_svd(X, y)
        elif self.solver == 'lsqr':
            self._solve_lsqr(X, y, reg_param=self.reg_param,
                             covariance_estimator=self.covariance_estimator)
        else:
            raise ValueError("unknown solver {} (valid solvers are 'svd' "
                             "and 'lsqr').".format(self.solver))
        return self

    def _decision_function(self, X):
        check_is_fitted(self)

        X = check_array(X)

        if self.solver == "svd":
            norm2 = []
            for i in range(len(self.classes_)):
                R = self.rotations_[i]
                S = self.scalings_[i]
                Xm = X - self.means_[i]
                X2 = np.dot(Xm, R * (S ** (-0.5)))
                norm2.append(np.sum(X2 ** 2, 1))
            norm2 = np.array(norm2).T  # shape = [len(X), n_classes]
            u = np.asarray([np.sum(np.log(s)) for s in self.scalings_])
            return -0.5 * (norm2 + u) + np.log(self.priors_)

        if self.solver == "lsqr":
            n_classes = len(self.classes_)
            dec_func = []
            for k in range(n_classes):
                mean = self.means_[k]
                covariance = self.covariance_[k]
                centeredX = X - mean
                sign, log_det_cov_k = np.linalg.slogdet(covariance)
                if sign <= 0:
                    raise ValueError(
                        "Determinant of covariance matrix"
                        " corresponding to class %s was"
                        " <= 0" % self.classes_[k])
                log_prior = np.log(self.priors_[k])
                quadratic_part = np.sum(centeredX * linalg.lstsq(
                    covariance,
                    centeredX.T)[0].T, axis=1)
                decision_function_k = - 0.5 * quadratic_part
                decision_function_k += - 0.5 * log_det_cov_k
                decision_function_k += log_prior
                dec_func.append(decision_function_k)
            return np.array(dec_func).T

    def decision_function(self, X):
        """Apply decision function to an array of samples.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Array of samples (test vectors).

        Returns
        -------
        C : ndarray of shape (n_samples,) or (n_samples, n_classes)
            Decision function values related to each class, per sample.
            In the two-class case, the shape is [n_samples,], giving the
            log likelihood ratio of the positive class.
        """
        dec_func = self._decision_function(X)
        # handle special case of two classes
        if len(self.classes_) == 2:
            return dec_func[:, 1] - dec_func[:, 0]
        return dec_func

    def predict(self, X):
        """Perform classification on an array of test vectors X.

        The predicted class C for each sample in X is returned.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)

        Returns
        -------
        C : ndarray of shape (n_samples,)
        """
        check_is_fitted(self)
        d = self._decision_function(X)
        y_pred = self.classes_.take(d.argmax(1))
        return y_pred

    def predict_proba(self, X):
        """Return posterior probabilities of classification.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Array of samples/test vectors.

        Returns
        -------
        C : ndarray of shape (n_samples, n_classes)
            Posterior probabilities of classification per class.
        """
        check_is_fitted(self)
        values = self._decision_function(X)
        # compute the likelihood of the underlying gaussian models
        # up to a multiplicative constant.
        likelihood = np.exp(values - values.max(axis=1)[:, np.newaxis])
        # compute posterior probabilities
        return likelihood / likelihood.sum(axis=1)[:, np.newaxis]

    def predict_log_proba(self, X):
        """Return posterior probabilities of classification.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Array of samples/test vectors.

        Returns
        -------
        C : ndarray of shape (n_samples, n_classes)
            Posterior log-probabilities of classification per class.
        """
        # XXX : can do better to avoid precision overflows
        probas_ = self.predict_proba(X)
        return np.log(probas_)

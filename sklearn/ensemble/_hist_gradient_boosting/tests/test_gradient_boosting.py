import numpy as np
import pytest
from numpy.testing import assert_allclose
from sklearn.datasets import make_classification, make_regression
from sklearn.preprocessing import KBinsDiscretizer, MinMaxScaler
from sklearn.model_selection import train_test_split
from sklearn.base import clone

# To use this experimental feature, we need to explicitly ask for it:
from sklearn.experimental import enable_hist_gradient_boosting  # noqa
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.ensemble._hist_gradient_boosting.binning import _BinMapper
from sklearn.utils import shuffle


X_classification, y_classification = make_classification(random_state=0)
X_regression, y_regression = make_regression(random_state=0)


@pytest.mark.parametrize('GradientBoosting, X, y', [
    (HistGradientBoostingClassifier, X_classification, y_classification),
    (HistGradientBoostingRegressor, X_regression, y_regression)
])
@pytest.mark.parametrize(
    'params, err_msg',
    [({'loss': 'blah'}, 'Loss blah is not supported for'),
     ({'learning_rate': 0}, 'learning_rate=0 must be strictly positive'),
     ({'learning_rate': -1}, 'learning_rate=-1 must be strictly positive'),
     ({'max_iter': 0}, 'max_iter=0 must not be smaller than 1'),
     ({'max_leaf_nodes': 0}, 'max_leaf_nodes=0 should not be smaller than 2'),
     ({'max_leaf_nodes': 1}, 'max_leaf_nodes=1 should not be smaller than 2'),
     ({'max_depth': 0}, 'max_depth=0 should not be smaller than 2'),
     ({'max_depth': 1}, 'max_depth=1 should not be smaller than 2'),
     ({'min_samples_leaf': 0}, 'min_samples_leaf=0 should not be smaller'),
     ({'l2_regularization': -1}, 'l2_regularization=-1 must be positive'),
     ({'max_bins': 1}, 'max_bins=1 should be no smaller than 2 and no larger'),
     ({'max_bins': 256}, 'max_bins=256 should be no smaller than 2 and no'),
     ({'n_iter_no_change': -1}, 'n_iter_no_change=-1 must be positive'),
     ({'validation_fraction': -1}, 'validation_fraction=-1 must be strictly'),
     ({'validation_fraction': 0}, 'validation_fraction=0 must be strictly'),
     ({'tol': -1}, 'tol=-1 must not be smaller than 0')]
)
def test_init_parameters_validation(GradientBoosting, X, y, params, err_msg):

    with pytest.raises(ValueError, match=err_msg):
        GradientBoosting(**params).fit(X, y)


def test_invalid_classification_loss():
    binary_clf = HistGradientBoostingClassifier(loss="binary_crossentropy")
    err_msg = ("loss='binary_crossentropy' is not defined for multiclass "
               "classification with n_classes=3, use "
               "loss='categorical_crossentropy' instead")
    with pytest.raises(ValueError, match=err_msg):
        binary_clf.fit(np.zeros(shape=(3, 2)), np.arange(3))


@pytest.mark.parametrize(
    'scoring, validation_fraction, n_iter_no_change, tol', [
        ('neg_mean_squared_error', .1, 5, 1e-7),  # use scorer
        ('neg_mean_squared_error', None, 5, 1e-1),  # use scorer on train data
        (None, .1, 5, 1e-7),  # same with default scorer
        (None, None, 5, 1e-1),
        ('loss', .1, 5, 1e-7),  # use loss
        ('loss', None, 5, 1e-1),  # use loss on training data
        (None, None, None, None),  # no early stopping
        ])
def test_early_stopping_regression(scoring, validation_fraction,
                                   n_iter_no_change, tol):

    max_iter = 200

    X, y = make_regression(n_samples=50, random_state=0)

    gb = HistGradientBoostingRegressor(
        verbose=1,  # just for coverage
        min_samples_leaf=5,  # easier to overfit fast
        scoring=scoring,
        tol=tol,
        validation_fraction=validation_fraction,
        max_iter=max_iter,
        n_iter_no_change=n_iter_no_change,
        random_state=0
    )
    gb.fit(X, y)

    if n_iter_no_change is not None:
        assert n_iter_no_change <= gb.n_iter_ < max_iter
    else:
        assert gb.n_iter_ == max_iter


@pytest.mark.parametrize('data', (
    make_classification(n_samples=30, random_state=0),
    make_classification(n_samples=30, n_classes=3, n_clusters_per_class=1,
                        random_state=0)
))
@pytest.mark.parametrize(
    'scoring, validation_fraction, n_iter_no_change, tol', [
        ('accuracy', .1, 5, 1e-7),  # use scorer
        ('accuracy', None, 5, 1e-1),  # use scorer on training data
        (None, .1, 5, 1e-7),  # same with default scorerscor
        (None, None, 5, 1e-1),
        ('loss', .1, 5, 1e-7),  # use loss
        ('loss', None, 5, 1e-1),  # use loss on training data
        (None, None, None, None),  # no early stopping
        ])
def test_early_stopping_classification(data, scoring, validation_fraction,
                                       n_iter_no_change, tol):

    max_iter = 50

    X, y = data

    gb = HistGradientBoostingClassifier(
        verbose=1,  # just for coverage
        min_samples_leaf=5,  # easier to overfit fast
        scoring=scoring,
        tol=tol,
        validation_fraction=validation_fraction,
        max_iter=max_iter,
        n_iter_no_change=n_iter_no_change,
        random_state=0
    )
    gb.fit(X, y)

    if n_iter_no_change is not None:
        assert n_iter_no_change <= gb.n_iter_ < max_iter
    else:
        assert gb.n_iter_ == max_iter


@pytest.mark.parametrize(
    'scores, n_iter_no_change, tol, stopping',
    [
        ([], 1, 0.001, False),  # not enough iterations
        ([1, 1, 1], 5, 0.001, False),  # not enough iterations
        ([1, 1, 1, 1, 1], 5, 0.001, False),  # not enough iterations
        ([1, 2, 3, 4, 5, 6], 5, 0.001, False),  # significant improvement
        ([1, 2, 3, 4, 5, 6], 5, 0., False),  # significant improvement
        ([1, 2, 3, 4, 5, 6], 5, 0.999, False),  # significant improvement
        ([1, 2, 3, 4, 5, 6], 5, 5 - 1e-5, False),  # significant improvement
        ([1] * 6, 5, 0., True),  # no significant improvement
        ([1] * 6, 5, 0.001, True),  # no significant improvement
        ([1] * 6, 5, 5, True),  # no significant improvement
    ]
)
def test_should_stop(scores, n_iter_no_change, tol, stopping):

    gbdt = HistGradientBoostingClassifier(
        n_iter_no_change=n_iter_no_change, tol=tol
    )
    assert gbdt._should_stop(scores) == stopping


def test_binning_train_validation_are_separated():
    # Make sure training and validation data are binned separately.
    # See issue 13926

    rng = np.random.RandomState(0)
    validation_fraction = .2
    gb = HistGradientBoostingClassifier(
        n_iter_no_change=5,
        validation_fraction=validation_fraction,
        random_state=rng
    )
    gb.fit(X_classification, y_classification)
    mapper_training_data = gb.bin_mapper_

    # Note that since the data is small there is no subsampling and the
    # random_state doesn't matter
    mapper_whole_data = _BinMapper(random_state=0)
    mapper_whole_data.fit(X_classification)

    n_samples = X_classification.shape[0]
    assert np.all(mapper_training_data.n_bins_non_missing_ ==
                  int((1 - validation_fraction) * n_samples))
    assert np.all(mapper_training_data.n_bins_non_missing_ !=
                  mapper_whole_data.n_bins_non_missing_)


def test_missing_values_trivial():
    # sanity check for missing values support. With only one feature and
    # y == isnan(X), the gbdt is supposed to reach perfect accuracy on the
    # training set.

    n_samples = 100
    n_features = 1
    rng = np.random.RandomState(0)

    X = rng.normal(size=(n_samples, n_features))
    mask = rng.binomial(1, .5, size=X.shape).astype(np.bool)
    X[mask] = np.nan
    y = mask.ravel()
    gb = HistGradientBoostingClassifier()
    gb.fit(X, y)

    assert gb.score(X, y) == pytest.approx(1)


@pytest.mark.parametrize('problem', ('classification', 'regression'))
@pytest.mark.parametrize(
    'missing_proportion, expected_min_score_classification, '
    'expected_min_score_regression', [
        (.1, .97, .89),
        (.2, .93, .81),
        (.5, .79, .52)])
def test_missing_values_resilience(problem, missing_proportion,
                                   expected_min_score_classification,
                                   expected_min_score_regression):
    # Make sure the estimators can deal with missing values and still yield
    # decent predictions

    rng = np.random.RandomState(0)
    n_samples = 1000
    n_features = 2
    if problem == 'regression':
        X, y = make_regression(n_samples=n_samples, n_features=n_features,
                               n_informative=n_features, random_state=rng)
        gb = HistGradientBoostingRegressor()
        expected_min_score = expected_min_score_regression
    else:
        X, y = make_classification(n_samples=n_samples, n_features=n_features,
                                   n_informative=n_features, n_redundant=0,
                                   n_repeated=0, random_state=rng)
        gb = HistGradientBoostingClassifier()
        expected_min_score = expected_min_score_classification

    mask = rng.binomial(1, missing_proportion, size=X.shape).astype(np.bool)
    X[mask] = np.nan

    gb.fit(X, y)

    assert gb.score(X, y) > expected_min_score


@pytest.mark.parametrize('data', [
    make_classification(random_state=0, n_classes=2),
    make_classification(random_state=0, n_classes=3, n_informative=3)
], ids=['binary_crossentropy', 'categorical_crossentropy'])
def test_zero_division_hessians(data):
    # non regression test for issue #14018
    # make sure we avoid zero division errors when computing the leaves values.

    # If the learning rate is too high, the raw predictions are bad and will
    # saturate the softmax (or sigmoid in binary classif). This leads to
    # probabilities being exactly 0 or 1, gradients being constant, and
    # hessians being zero.
    X, y = data
    gb = HistGradientBoostingClassifier(learning_rate=100, max_iter=10)
    gb.fit(X, y)


def test_small_trainset():
    # Make sure that the small trainset is stratified and has the expected
    # length (10k samples)
    n_samples = 20000
    original_distrib = {0: 0.1, 1: 0.2, 2: 0.3, 3: 0.4}
    rng = np.random.RandomState(42)
    X = rng.randn(n_samples).reshape(n_samples, 1)
    y = [[class_] * int(prop * n_samples) for (class_, prop)
         in original_distrib.items()]
    y = shuffle(np.concatenate(y))
    gb = HistGradientBoostingClassifier()

    # Compute the small training set
    X_small, y_small = gb._get_small_trainset(X, y, seed=42)

    # Compute the class distribution in the small training set
    unique, counts = np.unique(y_small, return_counts=True)
    small_distrib = {class_: count / 10000 for (class_, count)
                     in zip(unique, counts)}

    # Test that the small training set has the expected length
    assert X_small.shape[0] == 10000
    assert y_small.shape[0] == 10000

    # Test that the class distributions in the whole dataset and in the small
    # training set are identical
    assert small_distrib == pytest.approx(original_distrib)


@pytest.mark.parametrize('seed', range(100))
def test_missing_values_minmax_imputation(seed=0):
    # Compare the buit-in missing value handling of Histogram GBC with an
    # a-priori missing value imputation strategy that should yield the same
    # results in terms of decision function.
    #
    # Assuming the data is such that there is never a tie to select the best
    # feature to split on during training, the learned decision trees should be
    # strictly equivalent (learn a sequence of splits that encode the same
    # decision function).
    rng = np.random.RandomState(seed)
    X, y = make_regression(n_samples=int(1e4), n_features=3, random_state=rng)

    # Pre-bin the data to ensure a deterministic handling by the 2 strategies
    # and also make it easier to insert np.nan in a structured way:
    X = KBinsDiscretizer(n_bins=42, encode="ordinal").fit_transform(X)

    # First feature has missing values completely at random:
    rnd_mask = rng.rand(X.shape[0]) > 0.9
    X[rnd_mask, 0] = np.nan

    # Second and third features have missing values for extreme values
    # (censoring missingness).
    low_mask = X[:, 1] == 0
    X[low_mask, 1] = np.nan

    high_mask = X[:, 2] == X[:, 2].max()
    X[high_mask, 2] = np.nan

    # Check that there is at least one missing value in each feature:
    for feature_idx in range(X.shape[1]):
        assert any(np.isnan(X[:, feature_idx]))

    # Let's use a test set to check that the learned decision function is the
    # same as evaluated on unseen data. Otherwise it could just be the case
    # that we find two independent ways to overfit the training set.
    X_train, X_test, y_train, y_test = train_test_split(X, y, random_state=rng)

    # Use a small number of leaf nodes and iterations so as to keep
    # under-fitting models to minimize the likelihood of ties when training the
    # model.
    builtin_gbm = HistGradientBoostingRegressor(max_iter=10,
                                                max_leaf_nodes=5,
                                                random_state=0)
    builtin_gbm.fit(X_train, y_train)
    y_builtin_predict_train = builtin_gbm.predict(X_train)
    y_builtin_predict_test = builtin_gbm.predict(X_test)

    # Implement min-max feature imputation: we use MinMaxScaler to easily
    # extract the min and max values of non-missing numerical data for each
    # feature.
    mm = MinMaxScaler().fit(X_train)
    X_train_min, X_train_max = X_train.copy(), X_train.copy()
    X_test_min, X_test_max = X_test.copy(), X_test.copy()
    for feature_idx in range(X.shape[1]):
        nan_mask = np.isnan(X_train[:, feature_idx])
        X_train_min[nan_mask, feature_idx] = mm.data_min_[feature_idx] - 1
        X_train_max[nan_mask, feature_idx] = mm.data_max_[feature_idx] + 1

        nan_mask = np.isnan(X_test[:, feature_idx])
        X_test_min[nan_mask, feature_idx] = mm.data_min_[feature_idx] - 1
        X_test_max[nan_mask, feature_idx] = mm.data_max_[feature_idx] + 1

    X_train_imputed = np.concatenate([X_train_min, X_train_max], axis=1)
    X_test_imputed = np.concatenate([X_test_min, X_test_max], axis=1)

    imputed_gbm = clone(builtin_gbm)
    imputed_gbm.fit(X_train_imputed, y_train)
    y_imputed_predict_train = imputed_gbm.predict(X_train_imputed)
    y_imputed_predict_test = imputed_gbm.predict(X_test_imputed)

    assert_allclose(y_builtin_predict_train, y_imputed_predict_train)
    assert_allclose(y_builtin_predict_test, y_imputed_predict_test)

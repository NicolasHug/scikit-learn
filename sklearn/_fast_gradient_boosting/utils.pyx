# cython: cdivision=True
# cython: boundscheck=False
# cython: wraparound=False
# cython: language_level=3
"""This module contains utility routines."""
# Author: Nicolas Hug

from cython.parallel import prange

from .binning import BinMapper
from .types cimport G_H_DTYPE_C
from .types cimport Y_DTYPE_C
from ..base import is_classifier


def get_equivalent_estimator(estimator, lib='lightgbm'):
    """Return an unfitted estimator from another lib with matching hyperparams.

    This utility function takes care of renaming the sklearn parameters into
    their LightGBM, XGBoost or CatBoost equivalent parameters.
    """
    # unmapped XGB parameters:
    # - min_samples_leaf
    # - min_data_in_bin
    # - min_split_gain (there is min_split_loss though?)

    # unmapped Catboost parameters:
    # max_leaves
    # min_*

    if lib not in ('lightgbm', 'xgboost', 'catboost'):
        raise ValueError('accepted libs are lightgbm, xgboost, and catboost. '
                         ' got {}'.format(lib))

    sklearn_params = estimator.get_params()

    if sklearn_params['loss'] == 'auto':
        raise ValueError('auto loss is not accepted. We need to know if '
                         'the problem is binary or multiclass classification.')
    if sklearn_params['n_iter_no_change'] is not None:
        raise NotImplementedError('Early stopping should be deactivated.')

    # LGBM
    lgbm_loss_mapping = {
        'least_squares': 'regression_l2',
        'binary_crossentropy': 'binary',
        'categorical_crossentropy': 'multiclass'
    }

    lgbm_params = {
        'objective': lgbm_loss_mapping[sklearn_params['loss']],
        'learning_rate': sklearn_params['learning_rate'],
        'n_estimators': sklearn_params['n_estimators'],
        'num_leaves': sklearn_params['max_leaf_nodes'],
        'max_depth': sklearn_params['max_depth'],
        'min_child_samples': sklearn_params['min_samples_leaf'],
        'reg_lambda': sklearn_params['l2_regularization'],
        'max_bin': sklearn_params['max_bins'],
        'min_data_in_bin': 1,
        'min_child_weight': 1e-3,
        'min_sum_hessian_in_leaf': 1e-3,
        'min_split_gain': 0,
        'verbosity': 10 if sklearn_params['verbose'] else -10,
        'boost_from_average': True,
        'enable_bundle': False,  # also makes feature order consistent
        'min_data_in_bin': 1,
        'subsample_for_bin': BinMapper().subsample,
    }

    if sklearn_params['loss'] == 'categorical_crossentropy':
        # LGBM multiplies hessians by 2 in multiclass loss.
        lgbm_params['min_sum_hessian_in_leaf'] *= 2
        lgbm_params['learning_rate'] *= 2

    # XGB
    xgb_loss_mapping = {
        'least_squares': 'reg:linear',
        'binary_crossentropy': 'reg:logistic',
        'categorical_crossentropy': 'multi:softmax'
    }

    xgb_params = {
        'tree_method': 'hist',
        'grow_policy': 'lossguide',  # so that we can set max_leaves
        'objective': xgb_loss_mapping[sklearn_params['loss']],
        'learning_rate': sklearn_params['learning_rate'],
        'n_estimators': sklearn_params['n_estimators'],
        'max_leaves': sklearn_params['max_leaf_nodes'],
        'max_depth': sklearn_params['max_depth'] or 0,
        'lambda': sklearn_params['l2_regularization'],
        'max_bin': sklearn_params['max_bins'],
        'min_child_weight': 1e-3,
        'verbosity': 2 if sklearn_params['verbose'] else 0,
        'silent': sklearn_params['verbose'] == 0,
        'n_jobs': -1,
    }

    # Catboost
    cat_loss_mapping = {
        'least_squares': 'RMSE',
        'binary_crossentropy': 'Logloss',
        'categorical_crossentropy': 'MultiClass'
    }

    cat_params = {
        'loss_function': cat_loss_mapping[sklearn_params['loss']],
        'learning_rate': sklearn_params['learning_rate'],
        'iterations': sklearn_params['n_estimators'],
        'depth': sklearn_params['max_depth'],
        'reg_lambda': sklearn_params['l2_regularization'],
        'max_bin': sklearn_params['max_bins'],
        'feature_border_type': 'Median',
        'leaf_estimation_method': 'Newton',
        'verbose': bool(sklearn_params['verbose']),
    }

    if lib == 'lightgbm':
        from lightgbm import LGBMRegressor
        from lightgbm import LGBMClassifier
        if is_classifier(estimator):
            return LGBMClassifier(**lgbm_params)
        else:
            return LGBMRegressor(**lgbm_params)

    elif lib == 'xgboost':
        from xgboost import XGBRegressor
        from xgboost import XGBClassifier
        if is_classifier(estimator):
            return XGBClassifier(**xgb_params)
        else:
            return XGBRegressor(**xgb_params)

    else:
        from catboost import CatBoostRegressor
        from catboost import CatBoostClassifier
        if is_classifier(estimator):
            return CatBoostClassifier(**cat_params)
        else:
            return CatBoostRegressor(**cat_params)


def sum_parallel(G_H_DTYPE_C [:] array):

    cdef:
        Y_DTYPE_C out = 0.
        int i = 0

    for i in prange(array.shape[0], schedule='static', nogil=True):
        out += array[i]

    return out

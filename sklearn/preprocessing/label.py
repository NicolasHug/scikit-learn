# Authors: Alexandre Gramfort <alexandre.gramfort@inria.fr>
#          Mathieu Blondel <mathieu@mblondel.org>
#          Olivier Grisel <olivier.grisel@ensta.org>
#          Andreas Mueller <amueller@ais.uni-bonn.de>
#          Joel Nothman <joel.nothman@gmail.com>
#          Hamzeh Alsalhi <ha258@cornell.edu>
# License: BSD 3 clause

from collections import defaultdict
import itertools
import array
import warnings

import numpy as np
import scipy.sparse as sp

from ..base import BaseEstimator, TransformerMixin

from ..utils.sparsefuncs import min_max_axis
from ..utils import column_or_1d
from ..utils.validation import check_array
from ..utils.validation import check_is_fitted
from ..utils.validation import _num_samples
from ..utils.multiclass import unique_labels
from ..utils.multiclass import type_of_target


__all__ = [
    'label_binarize',
    'LabelBinarizer',
    'LabelEncoder',
    'MultiLabelBinarizer',
]


def _encode_numpy(values, uniques=None, encode=False, check_unknown=True):
    # only used in _encode below, see docstring there for details
    if uniques is None:
        if encode:
            uniques, encoded = np.unique(values, return_inverse=True)
            return uniques, encoded
        else:
            # unique sorts
            return np.unique(values)
    if encode:
        if check_unknown:
            diff = _encode_check_unknown(values, uniques)
            if diff:
                raise ValueError("y contains previously unseen labels: %s"
                                 % str(diff))
        encoded = np.searchsorted(uniques, values)
        return uniques, encoded
    else:
        return uniques


def _encode_python(values, uniques=None, encode=False):
    # only used in _encode below, see docstring there for details
    if uniques is None:
        uniques = sorted(set(values))
        uniques = np.array(uniques, dtype=values.dtype)
    if encode:
        table = {val: i for i, val in enumerate(uniques)}
        try:
            encoded = np.array([table[v] for v in values])
        except KeyError as e:
            raise ValueError("y contains previously unseen labels: %s"
                             % str(e))
        return uniques, encoded
    else:
        return uniques


def _encode(values, uniques=None, encode=False, check_unknown=True):
    """Helper function to factorize (find uniques) and encode values.

    Uses pure python method for object dtype, and numpy method for
    all other dtypes.
    The numpy method has the limitation that the `uniques` need to
    be sorted. Importantly, this is not checked but assumed to already be
    the case. The calling method needs to ensure this for all non-object
    values.

    Parameters
    ----------
    values : array
        Values to factorize or encode.
    uniques : array, optional
        If passed, uniques are not determined from passed values (this
        can be because the user specified categories, or because they
        already have been determined in fit).
    encode : bool, default False
        If True, also encode the values into integer codes based on `uniques`.
    check_unknown : bool, default True
        If True, check for values in ``values`` that are not in ``unique``
        and raise an error. This is ignored for object dtype and treated as
        True in this case). This parameter is useful for
        _BaseEncoder._transform() to avoid calling _encode_check_unknown()
        twice.

    Returns
    -------
    uniques
        If ``encode=False``. The unique values are sorted if the `uniques`
        parameter was None (and thus inferred from the data).
    (uniques, encoded)
        If ``encode=True``.

    """
    if values.dtype == object:
        try:
            res = _encode_python(values, uniques, encode)
        except TypeError:
            raise TypeError("argument must be a string or number")
        return res
    else:
        return _encode_numpy(values, uniques, encode,
                             check_unknown=check_unknown)


def _encode_check_unknown(values, uniques, return_mask=False):
    """
    Helper function to check for unknowns in values to be encoded.

    Uses pure python method for object dtype, and numpy method for
    all other dtypes.

    Parameters
    ----------
    values : array
        Values to check for unknowns.
    uniques : array
        Allowed uniques values.
    return_mask : bool, default False
        If True, return a mask of the same shape as `values` indicating
        the valid values.

    Returns
    -------
    diff : list
        The unique values present in `values` and not in `uniques` (the
        unknown values).
    valid_mask : boolean array
        Additionally returned if ``return_mask=True``.

    """
    if values.dtype == object:
        uniques_set = set(uniques)
        diff = list(set(values) - uniques_set)
        if return_mask:
            if diff:
                valid_mask = np.array([val in uniques_set for val in values])
            else:
                valid_mask = np.ones(len(values), dtype=bool)
            return diff, valid_mask
        else:
            return diff
    else:
        unique_values = np.unique(values)
        diff = list(np.setdiff1d(unique_values, uniques, assume_unique=True))
        if return_mask:
            if diff:
                valid_mask = np.in1d(values, uniques)
            else:
                valid_mask = np.ones(len(values), dtype=bool)
            return diff, valid_mask
        else:
            return diff


class LabelEncoder(BaseEstimator, TransformerMixin):
    """Encode labels with value between 0 and n_classes-1.

    Read more in the :ref:`User Guide <preprocessing_targets>`.

    Attributes
    ----------
    classes_ : array of shape (n_class,)
        Holds the label for each class.

    Examples
    --------
    `LabelEncoder` can be used to normalize labels.

    >>> from sklearn import preprocessing
    >>> le = preprocessing.LabelEncoder()
    >>> le.fit([1, 2, 2, 6])
    LabelEncoder()
    >>> le.classes_
    array([1, 2, 6])
    >>> le.transform([1, 1, 2, 6]) #doctest: +ELLIPSIS
    array([0, 0, 1, 2]...)
    >>> le.inverse_transform([0, 0, 1, 2])
    array([1, 1, 2, 6])

    It can also be used to transform non-numerical labels (as long as they are
    hashable and comparable) to numerical labels.

    >>> le = preprocessing.LabelEncoder()
    >>> le.fit(["paris", "paris", "tokyo", "amsterdam"])
    LabelEncoder()
    >>> list(le.classes_)
    ['amsterdam', 'paris', 'tokyo']
    >>> le.transform(["tokyo", "tokyo", "paris"]) #doctest: +ELLIPSIS
    array([2, 2, 1]...)
    >>> list(le.inverse_transform([2, 2, 1]))
    ['tokyo', 'tokyo', 'paris']

    See also
    --------
    sklearn.preprocessing.OrdinalEncoder : encode categorical features
        using a one-hot or ordinal encoding scheme.
    """

    def fit(self, y):
        """Fit label encoder

        Parameters
        ----------
        y : array-like of shape (n_samples,)
            Target values.

        Returns
        -------
        self : returns an instance of self.
        """
        y = column_or_1d(y, warn=True)
        self.classes_ = _encode(y)
        return self

    def fit_transform(self, y):
        """Fit label encoder and return encoded labels

        Parameters
        ----------
        y : array-like of shape [n_samples]
            Target values.

        Returns
        -------
        y : array-like of shape [n_samples]
        """
        y = column_or_1d(y, warn=True)
        self.classes_, y = _encode(y, encode=True)
        return y

    def transform(self, y):
        """Transform labels to normalized encoding.

        Parameters
        ----------
        y : array-like of shape [n_samples]
            Target values.

        Returns
        -------
        y : array-like of shape [n_samples]
        """
        check_is_fitted(self, 'classes_')
        y = column_or_1d(y, warn=True)
        # transform of empty array is empty array
        if _num_samples(y) == 0:
            return np.array([])

        _, y = _encode(y, uniques=self.classes_, encode=True)
        return y

    def inverse_transform(self, y):
        """Transform labels back to original encoding.

        Parameters
        ----------
        y : numpy array of shape [n_samples]
            Target values.

        Returns
        -------
        y : numpy array of shape [n_samples]
        """
        check_is_fitted(self, 'classes_')
        y = column_or_1d(y, warn=True)
        # inverse transform of empty array is empty array
        if _num_samples(y) == 0:
            return np.array([])

        diff = np.setdiff1d(y, np.arange(len(self.classes_)))
        if len(diff):
            raise ValueError(
                    "y contains previously unseen labels: %s" % str(diff))
        y = np.asarray(y)
        return self.classes_[y]

    def _more_tags(self):
        return {'X_types': ['1dlabels']}


class LabelBinarizer(BaseEstimator, TransformerMixin):
    """Binarize labels in a one-vs-all fashion

    Several regression and binary classification algorithms are
    available in scikit-learn. A simple way to extend these algorithms
    to the multi-class classification case is to use the so-called
    one-vs-all scheme.

    At learning time, this simply consists in learning one regressor
    or binary classifier per class. In doing so, one needs to convert
    multi-class labels to binary labels (belong or does not belong
    to the class). LabelBinarizer makes this process easy with the
    transform method.

    At prediction time, one assigns the class for which the corresponding
    model gave the greatest confidence. LabelBinarizer makes this easy
    with the inverse_transform method.

    Read more in the :ref:`User Guide <preprocessing_targets>`.

    Parameters
    ----------

    neg_label : int (default: 0)
        Value with which negative labels must be encoded.

    pos_label : int (default: 1)
        Value with which positive labels must be encoded.

    sparse_output : boolean (default: False)
        True if the returned array from transform is desired to be in sparse
        CSR format.

    Attributes
    ----------

    classes_ : array of shape [n_class]
        Holds the label for each class.

    y_type_ : str,
        Represents the type of the target data as evaluated by
        utils.multiclass.type_of_target. Possible type are 'continuous',
        'continuous-multioutput', 'binary', 'multiclass',
        'multiclass-multioutput', 'multilabel-indicator', and 'unknown'.

    sparse_input_ : boolean,
        True if the input data to transform is given as a sparse matrix, False
        otherwise.

    Examples
    --------
    >>> from sklearn import preprocessing
    >>> lb = preprocessing.LabelBinarizer()
    >>> lb.fit([1, 2, 6, 4, 2])
    LabelBinarizer(neg_label=0, pos_label=1, sparse_output=False)
    >>> lb.classes_
    array([1, 2, 4, 6])
    >>> lb.transform([1, 6])
    array([[1, 0, 0, 0],
           [0, 0, 0, 1]])

    Binary targets transform to a column vector

    >>> lb = preprocessing.LabelBinarizer()
    >>> lb.fit_transform(['yes', 'no', 'no', 'yes'])
    array([[1],
           [0],
           [0],
           [1]])

    Passing a 2D matrix for multilabel classification

    >>> import numpy as np
    >>> lb.fit(np.array([[0, 1, 1], [1, 0, 0]]))
    LabelBinarizer(neg_label=0, pos_label=1, sparse_output=False)
    >>> lb.classes_
    array([0, 1, 2])
    >>> lb.transform([0, 1, 2, 1])
    array([[1, 0, 0],
           [0, 1, 0],
           [0, 0, 1],
           [0, 1, 0]])

    See also
    --------
    label_binarize : function to perform the transform operation of
        LabelBinarizer with fixed classes.
    sklearn.preprocessing.OneHotEncoder : encode categorical features
        using a one-hot aka one-of-K scheme.
    """

    def __init__(self, neg_label=0, pos_label=1, sparse_output=False):
        if neg_label >= pos_label:
            raise ValueError("neg_label={0} must be strictly less than "
                             "pos_label={1}.".format(neg_label, pos_label))

        if sparse_output and (pos_label == 0 or neg_label != 0):
            raise ValueError("Sparse binarization is only supported with non "
                             "zero pos_label and zero neg_label, got "
                             "pos_label={0} and neg_label={1}"
                             "".format(pos_label, neg_label))

        self.neg_label = neg_label
        self.pos_label = pos_label
        self.sparse_output = sparse_output

    def fit(self, y):
        """Fit label binarizer

        Parameters
        ----------
        y : array of shape [n_samples,] or [n_samples, n_classes]
            Target values. The 2-d matrix should only contain 0 and 1,
            represents multilabel classification.

        Returns
        -------
        self : returns an instance of self.
        """
        self.y_type_ = type_of_target(y)
        if 'multioutput' in self.y_type_:
            raise ValueError("Multioutput target data is not supported with "
                             "label binarization")
        if _num_samples(y) == 0:
            raise ValueError('y has 0 samples: %r' % y)

        self.sparse_input_ = sp.issparse(y)
        self.classes_ = unique_labels(y)
        return self

    def fit_transform(self, y):
        """Fit label binarizer and transform multi-class labels to binary
        labels.

        The output of transform is sometimes referred to as
        the 1-of-K coding scheme.

        Parameters
        ----------
        y : array or sparse matrix of shape [n_samples,] or \
            [n_samples, n_classes]
            Target values. The 2-d matrix should only contain 0 and 1,
            represents multilabel classification. Sparse matrix can be
            CSR, CSC, COO, DOK, or LIL.

        Returns
        -------
        Y : array or CSR matrix of shape [n_samples, n_classes]
            Shape will be [n_samples, 1] for binary problems.
        """
        return self.fit(y).transform(y)

    def transform(self, y):
        """Transform multi-class labels to binary labels

        The output of transform is sometimes referred to by some authors as
        the 1-of-K coding scheme.

        Parameters
        ----------
        y : array or sparse matrix of shape [n_samples,] or \
            [n_samples, n_classes]
            Target values. The 2-d matrix should only contain 0 and 1,
            represents multilabel classification. Sparse matrix can be
            CSR, CSC, COO, DOK, or LIL.

        Returns
        -------
        Y : numpy array or CSR matrix of shape [n_samples, n_classes]
            Shape will be [n_samples, 1] for binary problems.
        """
        check_is_fitted(self, 'classes_')

        y_is_multilabel = type_of_target(y).startswith('multilabel')
        if y_is_multilabel and not self.y_type_.startswith('multilabel'):
            raise ValueError("The object was not fitted with multilabel"
                             " input.")

        return label_binarize(y, self.classes_,
                              pos_label=self.pos_label,
                              neg_label=self.neg_label,
                              sparse_output=self.sparse_output)

    def inverse_transform(self, Y, threshold=None):
        """Transform binary labels back to multi-class labels

        Parameters
        ----------
        Y : numpy array or sparse matrix with shape [n_samples, n_classes]
            Target values. All sparse matrices are converted to CSR before
            inverse transformation.

        threshold : float or None
            Threshold used in the binary and multi-label cases.

            Use 0 when ``Y`` contains the output of decision_function
            (classifier).
            Use 0.5 when ``Y`` contains the output of predict_proba.

            If None, the threshold is assumed to be half way between
            neg_label and pos_label.

        Returns
        -------
        y : numpy array or CSR matrix of shape [n_samples] Target values.

        Notes
        -----
        In the case when the binary labels are fractional
        (probabilistic), inverse_transform chooses the class with the
        greatest value. Typically, this allows to use the output of a
        linear model's decision_function method directly as the input
        of inverse_transform.
        """
        check_is_fitted(self, 'classes_')

        if threshold is None:
            threshold = (self.pos_label + self.neg_label) / 2.

        if self.y_type_ == "multiclass":
            y_inv = _inverse_binarize_multiclass(Y, self.classes_)
        else:
            y_inv = _inverse_binarize_thresholding(Y, self.y_type_,
                                                   self.classes_, threshold)

        if self.sparse_input_:
            y_inv = sp.csr_matrix(y_inv)
        elif sp.issparse(y_inv):
            y_inv = y_inv.toarray()

        return y_inv

    def _more_tags(self):
        return {'X_types': ['1dlabels']}


def label_binarize(y, classes, neg_label=0, pos_label=1, sparse_output=False):
    """Binarize labels in a one-vs-all fashion

    Several regression and binary classification algorithms are
    available in scikit-learn. A simple way to extend these algorithms
    to the multi-class classification case is to use the so-called
    one-vs-all scheme.

    This function makes it possible to compute this transformation for a
    fixed set of class labels known ahead of time.

    Parameters
    ----------
    y : array-like
        Sequence of integer labels or multilabel data to encode.

    classes : array-like of shape [n_classes]
        Uniquely holds the label for each class.

    neg_label : int (default: 0)
        Value with which negative labels must be encoded.

    pos_label : int (default: 1)
        Value with which positive labels must be encoded.

    sparse_output : boolean (default: False),
        Set to true if output binary array is desired in CSR sparse format

    Returns
    -------
    Y : numpy array or CSR matrix of shape [n_samples, n_classes]
        Shape will be [n_samples, 1] for binary problems.

    Examples
    --------
    >>> from sklearn.preprocessing import label_binarize
    >>> label_binarize([1, 6], classes=[1, 2, 4, 6])
    array([[1, 0, 0, 0],
           [0, 0, 0, 1]])

    The class ordering is preserved:

    >>> label_binarize([1, 6], classes=[1, 6, 4, 2])
    array([[1, 0, 0, 0],
           [0, 1, 0, 0]])

    Binary targets transform to a column vector

    >>> label_binarize(['yes', 'no', 'no', 'yes'], classes=['no', 'yes'])
    array([[1],
           [0],
           [0],
           [1]])

    See also
    --------
    LabelBinarizer : class used to wrap the functionality of label_binarize and
        allow for fitting to classes independently of the transform operation
    """
    if not isinstance(y, list):
        # XXX Workaround that will be removed when list of list format is
        # dropped
        y = check_array(y, accept_sparse='csr', ensure_2d=False, dtype=None)
    else:
        if _num_samples(y) == 0:
            raise ValueError('y has 0 samples: %r' % y)
    if neg_label >= pos_label:
        raise ValueError("neg_label={0} must be strictly less than "
                         "pos_label={1}.".format(neg_label, pos_label))

    if (sparse_output and (pos_label == 0 or neg_label != 0)):
        raise ValueError("Sparse binarization is only supported with non "
                         "zero pos_label and zero neg_label, got "
                         "pos_label={0} and neg_label={1}"
                         "".format(pos_label, neg_label))

    # To account for pos_label == 0 in the dense case
    pos_switch = pos_label == 0
    if pos_switch:
        pos_label = -neg_label

    y_type = type_of_target(y)
    if 'multioutput' in y_type:
        raise ValueError("Multioutput target data is not supported with label "
                         "binarization")
    if y_type == 'unknown':
        raise ValueError("The type of target data is not known")

    n_samples = y.shape[0] if sp.issparse(y) else len(y)
    n_classes = len(classes)
    classes = np.asarray(classes)

    if y_type == "binary":
        if n_classes == 1:
            if sparse_output:
                return sp.csr_matrix((n_samples, 1), dtype=int)
            else:
                Y = np.zeros((len(y), 1), dtype=np.int)
                Y += neg_label
                return Y
        elif len(classes) >= 3:
            y_type = "multiclass"

    sorted_class = np.sort(classes)
    if (y_type == "multilabel-indicator" and classes.size != y.shape[1]):
        raise ValueError("classes {0} missmatch with the labels {1}"
                         "found in the data".format(classes, unique_labels(y)))

    if y_type in ("binary", "multiclass"):
        y = column_or_1d(y)

        # pick out the known labels from y
        y_in_classes = np.in1d(y, classes)
        y_seen = y[y_in_classes]
        indices = np.searchsorted(sorted_class, y_seen)
        indptr = np.hstack((0, np.cumsum(y_in_classes)))

        data = np.empty_like(indices)
        data.fill(pos_label)
        Y = sp.csr_matrix((data, indices, indptr),
                          shape=(n_samples, n_classes))
    elif y_type == "multilabel-indicator":
        Y = sp.csr_matrix(y)
        if pos_label != 1:
            data = np.empty_like(Y.data)
            data.fill(pos_label)
            Y.data = data
    else:
        raise ValueError("%s target data is not supported with label "
                         "binarization" % y_type)

    if not sparse_output:
        Y = Y.toarray()
        Y = Y.astype(int, copy=False)

        if neg_label != 0:
            Y[Y == 0] = neg_label

        if pos_switch:
            Y[Y == pos_label] = 0
    else:
        Y.data = Y.data.astype(int, copy=False)

    # preserve label ordering
    if np.any(classes != sorted_class):
        indices = np.searchsorted(sorted_class, classes)
        Y = Y[:, indices]

    if y_type == "binary":
        if sparse_output:
            Y = Y.getcol(-1)
        else:
            Y = Y[:, -1].reshape((-1, 1))

    return Y


def _inverse_binarize_multiclass(y, classes):
    """Inverse label binarization transformation for multiclass.

    Multiclass uses the maximal score instead of a threshold.
    """
    classes = np.asarray(classes)

    if sp.issparse(y):
        # Find the argmax for each row in y where y is a CSR matrix

        y = y.tocsr()
        n_samples, n_outputs = y.shape
        outputs = np.arange(n_outputs)
        row_max = min_max_axis(y, 1)[1]
        row_nnz = np.diff(y.indptr)

        y_data_repeated_max = np.repeat(row_max, row_nnz)
        # picks out all indices obtaining the maximum per row
        y_i_all_argmax = np.flatnonzero(y_data_repeated_max == y.data)

        # For corner case where last row has a max of 0
        if row_max[-1] == 0:
            y_i_all_argmax = np.append(y_i_all_argmax, [len(y.data)])

        # Gets the index of the first argmax in each row from y_i_all_argmax
        index_first_argmax = np.searchsorted(y_i_all_argmax, y.indptr[:-1])
        # first argmax of each row
        y_ind_ext = np.append(y.indices, [0])
        y_i_argmax = y_ind_ext[y_i_all_argmax[index_first_argmax]]
        # Handle rows of all 0
        y_i_argmax[np.where(row_nnz == 0)[0]] = 0

        # Handles rows with max of 0 that contain negative numbers
        samples = np.arange(n_samples)[(row_nnz > 0) &
                                       (row_max.ravel() == 0)]
        for i in samples:
            ind = y.indices[y.indptr[i]:y.indptr[i + 1]]
            y_i_argmax[i] = classes[np.setdiff1d(outputs, ind)][0]

        return classes[y_i_argmax]
    else:
        return classes.take(y.argmax(axis=1), mode="clip")


def _inverse_binarize_thresholding(y, output_type, classes, threshold):
    """Inverse label binarization transformation using thresholding."""

    if output_type == "binary" and y.ndim == 2 and y.shape[1] > 2:
        raise ValueError("output_type='binary', but y.shape = {0}".
                         format(y.shape))

    if output_type != "binary" and y.shape[1] != len(classes):
        raise ValueError("The number of class is not equal to the number of "
                         "dimension of y.")

    classes = np.asarray(classes)

    # Perform thresholding
    if sp.issparse(y):
        if threshold > 0:
            if y.format not in ('csr', 'csc'):
                y = y.tocsr()
            y.data = np.array(y.data > threshold, dtype=np.int)
            y.eliminate_zeros()
        else:
            y = np.array(y.toarray() > threshold, dtype=np.int)
    else:
        y = np.array(y > threshold, dtype=np.int)

    # Inverse transform data
    if output_type == "binary":
        if sp.issparse(y):
            y = y.toarray()
        if y.ndim == 2 and y.shape[1] == 2:
            return classes[y[:, 1]]
        else:
            if len(classes) == 1:
                return np.repeat(classes[0], len(y))
            else:
                return classes[y.ravel()]

    elif output_type == "multilabel-indicator":
        return y

    else:
        raise ValueError("{0} format is not supported".format(output_type))


class MultiLabelBinarizer(BaseEstimator, TransformerMixin):
    """Transform between iterable of iterables and a multilabel format

    Although a list of sets or tuples is a very intuitive format for multilabel
    data, it is unwieldy to process. This transformer converts between this
    intuitive format and the supported multilabel format: a (samples x classes)
    binary matrix indicating the presence of a class label.

    Parameters
    ----------
    classes : array-like of shape [n_classes] (optional)
        Indicates an ordering for the class labels.
        All entries should be unique (cannot contain duplicate classes).

    sparse_output : boolean (default: False),
        Set to true if output binary array is desired in CSR sparse format

    Attributes
    ----------
    classes_ : array of labels
        A copy of the `classes` parameter where provided,
        or otherwise, the sorted set of classes found when fitting.

    Examples
    --------
    >>> from sklearn.preprocessing import MultiLabelBinarizer
    >>> mlb = MultiLabelBinarizer()
    >>> mlb.fit_transform([(1, 2), (3,)])
    array([[1, 1, 0],
           [0, 0, 1]])
    >>> mlb.classes_
    array([1, 2, 3])

    >>> mlb.fit_transform([{'sci-fi', 'thriller'}, {'comedy'}])
    array([[0, 1, 1],
           [1, 0, 0]])
    >>> list(mlb.classes_)
    ['comedy', 'sci-fi', 'thriller']

    See also
    --------
    sklearn.preprocessing.OneHotEncoder : encode categorical features
        using a one-hot aka one-of-K scheme.
    """

    def __init__(self, classes=None, sparse_output=False):
        self.classes = classes
        self.sparse_output = sparse_output

    def fit(self, y):
        """Fit the label sets binarizer, storing `classes_`

        Parameters
        ----------
        y : iterable of iterables
            A set of labels (any orderable and hashable object) for each
            sample. If the `classes` parameter is set, `y` will not be
            iterated.

        Returns
        -------
        self : returns this MultiLabelBinarizer instance
        """
        self._cached_dict = None
        if self.classes is None:
            classes = sorted(set(itertools.chain.from_iterable(y)))
        elif len(set(self.classes)) < len(self.classes):
            raise ValueError("The classes argument contains duplicate "
                             "classes. Remove these duplicates before passing "
                             "them to MultiLabelBinarizer.")
        else:
            classes = self.classes
        dtype = np.int if all(isinstance(c, int) for c in classes) else object
        self.classes_ = np.empty(len(classes), dtype=dtype)
        self.classes_[:] = classes
        return self

    def fit_transform(self, y):
        """Fit the label sets binarizer and transform the given label sets

        Parameters
        ----------
        y : iterable of iterables
            A set of labels (any orderable and hashable object) for each
            sample. If the `classes` parameter is set, `y` will not be
            iterated.

        Returns
        -------
        y_indicator : array or CSR matrix, shape (n_samples, n_classes)
            A matrix such that `y_indicator[i, j] = 1` iff `classes_[j]` is in
            `y[i]`, and 0 otherwise.
        """
        self._cached_dict = None

        if self.classes is not None:
            return self.fit(y).transform(y)

        # Automatically increment on new class
        class_mapping = defaultdict(int)
        class_mapping.default_factory = class_mapping.__len__
        yt = self._transform(y, class_mapping)

        # sort classes and reorder columns
        tmp = sorted(class_mapping, key=class_mapping.get)

        # (make safe for tuples)
        dtype = np.int if all(isinstance(c, int) for c in tmp) else object
        class_mapping = np.empty(len(tmp), dtype=dtype)
        class_mapping[:] = tmp
        self.classes_, inverse = np.unique(class_mapping, return_inverse=True)
        # ensure yt.indices keeps its current dtype
        yt.indices = np.array(inverse[yt.indices], dtype=yt.indices.dtype,
                              copy=False)

        if not self.sparse_output:
            yt = yt.toarray()

        return yt

    def transform(self, y):
        """Transform the given label sets

        Parameters
        ----------
        y : iterable of iterables
            A set of labels (any orderable and hashable object) for each
            sample. If the `classes` parameter is set, `y` will not be
            iterated.

        Returns
        -------
        y_indicator : array or CSR matrix, shape (n_samples, n_classes)
            A matrix such that `y_indicator[i, j] = 1` iff `classes_[j]` is in
            `y[i]`, and 0 otherwise.
        """
        check_is_fitted(self, 'classes_')

        class_to_index = self._build_cache()
        yt = self._transform(y, class_to_index)

        if not self.sparse_output:
            yt = yt.toarray()

        return yt

    def _build_cache(self):
        if self._cached_dict is None:
            self._cached_dict = dict(zip(self.classes_,
                                         range(len(self.classes_))))

        return self._cached_dict

    def _transform(self, y, class_mapping):
        """Transforms the label sets with a given mapping

        Parameters
        ----------
        y : iterable of iterables
        class_mapping : Mapping
            Maps from label to column index in label indicator matrix

        Returns
        -------
        y_indicator : sparse CSR matrix, shape (n_samples, n_classes)
            Label indicator matrix
        """
        indices = array.array('i')
        indptr = array.array('i', [0])
        unknown = set()
        for labels in y:
            index = set()
            for label in labels:
                try:
                    index.add(class_mapping[label])
                except KeyError:
                    unknown.add(label)
            indices.extend(index)
            indptr.append(len(indices))
        if unknown:
            warnings.warn('unknown class(es) {0} will be ignored'
                          .format(sorted(unknown, key=str)))
        data = np.ones(len(indices), dtype=int)

        return sp.csr_matrix((data, indices, indptr),
                             shape=(len(indptr) - 1, len(class_mapping)))

    def inverse_transform(self, yt):
        """Transform the given indicator matrix into label sets

        Parameters
        ----------
        yt : array or sparse matrix of shape (n_samples, n_classes)
            A matrix containing only 1s ands 0s.

        Returns
        -------
        y : list of tuples
            The set of labels for each sample such that `y[i]` consists of
            `classes_[j]` for each `yt[i, j] == 1`.
        """
        check_is_fitted(self, 'classes_')

        if yt.shape[1] != len(self.classes_):
            raise ValueError('Expected indicator for {0} classes, but got {1}'
                             .format(len(self.classes_), yt.shape[1]))

        if sp.issparse(yt):
            yt = yt.tocsr()
            if len(yt.data) != 0 and len(np.setdiff1d(yt.data, [0, 1])) > 0:
                raise ValueError('Expected only 0s and 1s in label indicator.')
            return [tuple(self.classes_.take(yt.indices[start:end]))
                    for start, end in zip(yt.indptr[:-1], yt.indptr[1:])]
        else:
            unexpected = np.setdiff1d(yt, [0, 1])
            if len(unexpected) > 0:
                raise ValueError('Expected only 0s and 1s in label indicator. '
                                 'Also got {0}'.format(unexpected))
            return [tuple(self.classes_.compress(indicators)) for indicators
                    in yt]

    def _more_tags(self):
        return {'X_types': ['2dlabels']}

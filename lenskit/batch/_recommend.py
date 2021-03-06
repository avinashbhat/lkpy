import logging
import warnings

from joblib import Parallel, delayed

import pandas as pd
import numpy as np

from ..algorithms import Recommender
from .. import util
from ..sharing import get_store, NoopModelStore

_logger = logging.getLogger(__name__)


def _recommend_user(client, key, user, n, candidates):
    algo = client.get_model(key)

    _logger.debug('generating recommendations for %s', user)
    watch = util.Stopwatch()
    res = algo.recommend(user, n, candidates)
    _logger.debug('%s recommended %d/%s items for %s in %s', algo, len(res), n, user, watch)
    res['user'] = user
    res['rank'] = np.arange(1, len(res) + 1)
    return res


def __standard_cand_fun(candidates):
    """
    Convert candidates from the forms accepted by :py:fun:`recommend` into
    a standard form, a function that takes a user and returns a candidate
    list.
    """
    if isinstance(candidates, dict):
        return candidates.get
    elif candidates is None:
        return lambda u: None
    else:
        return candidates


def recommend(algo, users, n, candidates=None, *, n_jobs=None, **kwargs):
    """
    Batch-recommend for multiple users.  The provided algorithm should be a
    :py:class:`algorithms.Recommender`.

    Args:
        algo: the algorithm
        users(array-like): the users to recommend for
        n(int): the number of recommendations to generate (None for unlimited)
        candidates:
            the users' candidate sets. This can be a function, in which case it will
            be passed each user ID; it can also be a dictionary, in which case user
            IDs will be looked up in it.  Pass ``None`` to use the recommender's
            built-in candidate selector (usually recommended).
        n_jobs(int):
            The number of processes to use for parallel recommendations.  Passed as
            ``n_jobs`` to :class:`joblib.Parallel`.  The default, ``None``, will result
            in a call to :func:`util.proc_count`(``None``), so the process will be
            the process sequential _unless_ called inside the :func:`joblib.parallel_backend`
            context manager or the ``LK_NUM_PROCS`` environment variable is set.

    Returns:
        A frame with at least the columns ``user``, ``rank``, and ``item``; possibly also
        ``score``, and any other columns returned by the recommender.
    """

    if n_jobs is None and 'nprocs' in kwargs:
        n_jobs = kwargs['nprocs']
        warnings.warn('nprocs is deprecated, use n_jobs', DeprecationWarning)

    if n_jobs is None:
        n_jobs = util.proc_count(None)

    rec_algo = Recommender.adapt(algo)
    if candidates is None and rec_algo is not algo:
        warnings.warn('no candidates provided and algo is not a recommender, unlikely to work')
    del algo  # don't need reference any more

    if 'ratings' in kwargs:
        warnings.warn('Providing ratings to recommend is not supported', DeprecationWarning)

    candidates = __standard_cand_fun(candidates)

    loop = Parallel(n_jobs=n_jobs)

    path = None
    try:
        _logger.debug('activating recommender loop')
        with loop:
            store = get_store(in_process=loop._effective_n_jobs() == 1)
            _logger.info('using model store %s', store)
            astr = str(rec_algo)

            with store:
                key = store.put_model(rec_algo)
                del rec_algo
                client = store.client()

                _logger.info('recommending with %s for %d users (n_jobs=%s)',
                             astr, len(users), n_jobs)
                timer = util.Stopwatch()
                results = loop(delayed(_recommend_user)(client, key, user, n, candidates(user))
                               for user in users)

            results = pd.concat(results, ignore_index=True, copy=False)
            _logger.info('recommended for %d users in %s', len(users), timer)
    finally:
        util.delete_sometime(path)

    return results

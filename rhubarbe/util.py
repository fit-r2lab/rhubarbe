# from https://bitbucket.org/ssc/aiomas/src/92ddbe7f8c6dc86c03dd9d00c155a709ef330a89/aiomas/util.py?at=default&fileviewer=file-view-default

import asyncio
    
def self_manage(coro_or_future, ignore_cancel=True, loop=None):
    """Run :func:`asyncio.async()` with *coro_or_future* and set a callback
    that instantly raises all exceptions.

    If *ignore_cancel* is left ``True``, no exception is raised if the task was
    canceled.  If you also want to raise the ``CancelledError``, set the flag
    to ``False.``.

    Return an :class:`asyncio.Task` object.

    The difference between this function and :func:`asyncio.async()` subtle,
    but important if an error is raised by the task:

    :func:`asyncio.async()` returns a future (:class:`asyncio.Task` is
    a subclass of :class:`asyncio.Future`) for the task that you created.  By
    the time that future goes out of scope, asyncio checks if someone was
    interested in its result or not.  If the result was never retrieved, the
    exception is printed to *stderr*.

    If you call it like ``asyncio.async(mytask())`` (note that we don't keep
    a reference to the future here), an exception in *mytask* will pre printed
    immediately when the task is done.  If, however, we store a reference to
    the future (``fut = asyncio.async(mytask())``), the exception only gets
    printed when ``fut`` goes out of scope.  That means if, for example, an
    :class:`~aiomas.agent.Agent` creates a task and stores it as an instance
    attribute, our system may keep running for a long time after the exception
    has occured (or even block forever) and we won't see any stacktrace.  This
    is because the reference to the task is still there and we could, in
    theory, still retrieve the exception from there.

    Since this can make debugging very hard, this method simply registers a
    callback to the future.  The callback will try to get the result from the
    future when it is done and will thus print any exceptions immediately.

    """
    task = asyncio.ensure_future(coro_or_future, loop=loop)

    def cb(f):
        if f.cancelled() and ignore_cancel:
            return
        f.result()  # Let the future raise the exception

    task.add_done_callback(cb)

    return task

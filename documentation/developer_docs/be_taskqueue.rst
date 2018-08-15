Task Queue Module
=================

This module is intended to track long running tasks in the background.

Please read this `mailing <https://groups.google.com/forum/#!topic/openattic-users/1-MTS9B60rI>`_
first, before continuing.

Tasks are functions which are scheduled from our Django backend and are executed in our
openATTIC-systemD daemon.

Hello World
-----------

Let's build our first task:

.. code-block:: Python

    from taskqueue.models import task

    @task
    def hello(name):
        print 'hello {}!'.format(name)


To schedule this task, restart our openattic-systemd and run

.. code-block:: Python

    hello.delay("world")

Now, our daemon should print `hello world!` into the console. Keep in mind that our daemon needs to
import our hello task, thus if you see ``AttributeError: 'module' object has no attribute 'hello'``
in your log file, make the task importable by putting it into a python file.

.. note:: There is no guarantee that a task will not be executed multiple times. Although, multiple
   executions of one task will not happen concurrently.

.. note:: A task is expected to be a top-level function of a module. Class methods or inner
   functions may not work as expected.

Recursion
---------

Let's wait for a long running task:

.. code-block:: Python

    @task
    def wait(times):
        if times:
            return wait(times - 1)
        else:
            print 'finished'

This tasks schedules itself, similar to a recursive function. As we're using a
`trampoline <https://en.wikipedia.org/wiki/Trampoline_(computing)>`_, this will not grow the stack. This
also means that you cannot synchronously wait for a task to finish, thus if you want to run
multiple iterations, you can only use end-recursion. This is similar to
`continuation passing style <https://de.wikipedia.org/wiki/Continuation-passing_style>`_, where
the next task is continuation of our current iteration.

We're JSON serializing the parameters into the database, so you are limited to basic data types,
like int, str, float, list, dict, tuple. As an extension to JSON, you can also use a task as a
parameter. For example, you can use this to chain tasks into one task:

.. code-block:: Python

    from taskqueue.models import deserialize_task
    @task
    def chain(values):
        tasks = map(deserialize_task, values) # need to manually deserialize the tasks
        first, *rest = tasks
        res = first.run_once()
        if isinstance(res, Task):
            return chain([res] + rest, total_count)
        elif not rest:
            return res
        else:
            # Ignoring res here.
            return chain(rest, total_count)

    @task
    def wait_and_print(times, name):
        return chain([wait(times), hello(name)])

When calling ``wait_and_print.delay(3, 'Sebastian')``, Task Queue will run a state machine like this:

.. image:: http://g.gravizo.com/g?digraph%20G%20{rankdir=%22LR%22;%20chain1%20-%3E%20wait1%20-%3E%20wait2%20-%3E%20wait3%20-%3E%20chain2%20-%3E%20hello;}

A ready-to-use chain task is available by importing ``taskqueue.models.chain``.

Progress Percentage
-------------------

A task also has an attached progress percentage value. In case you have a long running task where a
progress may be useful to a user, you can provide a ``percent`` argument to ``@task`` like so:

.. code-block:: Python

    @task(percent=lambda total, remaining: 100 * remaining / total)
    def wait(total, remaining):
        if remaining:
            return wait(total, remaining - 1)
        else:
            print 'finished'

The percent parameter will be called with the same parameters as your task.

.. note:: The function is expected not to have any side effects, as it may be called multiple times
   or never.

.. note:: Always use keyword arguments for the task decorator, as positional arguments may not work
   as expected.

Revision Upgrades
-----------------

.. warning:: Keep in mind, that we're serializing the tasks into the database.

If you modify code, keep these restrictions in mind:

#. A task, including all parameters are serialized into the database,
#. thus be prepared to be called with a **outdated and ancient** function arguments.
#. Deleting the Python source of a task will eventually throw an exception.
#. Rule of thumb, **only** add optional parameters at the end to existing tasks.
#. If something goes wrong, a task may be aborted between function calls.
#. Try not to run important modifying commands later on.
#. Validate your function parameters.
#. As long as you only modify the implementation, everything is fine.

Referencing a newly created TaskQueue object
--------------------------------------------

The ``taskqueue`` module provides a Python mixin for referencing a ``TaskQueue`` object in a
HTTP header from another REST API. First, add the ``TaskQueueMixin`` to your ViewSet class like so:

.. code-block:: Python

   from taskqueue.restapi import TaskQueueLocationMixin

   class MyModelViewSet(TaskQueueLocationMixin, ViewSet):
      pass

Second, create a ``_task_queue`` attribute of your saved model instance in your ``save`` method:

.. code-block:: Python

    class MyModel(Model):
       def save(self, *args, **kwargs):
           # ...
           self._task_queue = app.tasks.my_task.delay()

Now, if a ``MyModel`` instance is saved, a ``Taskqueue-Location`` HTTP header pointing to the
``TaskQueue`` object is added to your response.

Integration with openATTIC-systemD
----------------------------------

Tasks are executed in our openATTIC-systemD process, thus they are independent of Apache worker
processes and can run without being interrupted.

On the other hand, openATTIC-systemD runs in
`glibs MainLoop <https://lazka.github.io/pgi-docs/GLib-2.0/structs/MainLoop.html>`_. In order to
integrate with it, we need to create a GObject with a periodic timer event. Here is the code to
start the timer of ``TaskQueueManager``:

.. code-block:: Python

     try:
         import taskqueue.manager
         taskqueue_manager = taskqueue.manager.TaskQueueManager()
     except ImportError:
         pass

Background
----------

As the architecture is similar to other `task queues <https://www.fullstackpython.com/task-queues.html>`_,
I've tried to make a task definition similar to the API of
`Celery <http://docs.celeryproject.org/en/latest/getting-started/first-steps-with-celery.html#application>`_,
which also uses a task decorator.

Task Queue is also similar to a Haskell package called `Workflow <https://hackage.haskell.org/package/Workflow>`_,
quote:

    Transparent support for interruptable computations. A workflow can be seen as a persistent
    thread that executes a `monadic <https://en.wikipedia.org/wiki/Monad_(functional_programming)>`_
    computation. Therefore, it can be used in very time consuming
    computations such are CPU intensive calculations or procedures that are most of the time
    waiting for the action of a process or an user, that are prone to communication failures,
    timeouts or shutdowns. It also can be used if you like to restart your program at the point
    where the user left it last time. The computation can be restarted at the interrupted
    point thanks to its logged state in permanent storage.

Task Queue stores the computation context between each trampoline call. Workflow uses some kind of
`continuation monad <http://www.haskellforall.com/2012/12/the-continuation-monad.html>`_ to hide
interruptions between restarts. Task queue use a similar idea, although in a greatly reduced
variant, as the syntax of Python is not as `expressive <http://www.fh-wedel.de/~si/seminare/ss13/Ausarbeitung/07.Monaden/haskell.html#3>`_
as other Languages, like C#.

You can even think of a task as being a `green thread <https://en.wikipedia.org/wiki/Green_threads>`_,
because you can schedule multiple tasks at once. Each of them will be executed interleaved.

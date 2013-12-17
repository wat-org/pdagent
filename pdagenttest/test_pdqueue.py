
import os
import shutil
from threading import Lock, Thread
import time
import unittest

from pdagent.pdqueue import PDQueue, EmptyQueue


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))

TEST_QUEUE_DIR = os.path.join(_TEST_DIR, "test_queue")


class NoOpLock:

    def __init__(self, lockfile):
        pass

    def acquire(self):
        pass

    def release(self):
        pass


class PDQueueTest(unittest.TestCase):

    def setUp(self):
        if os.path.exists(TEST_QUEUE_DIR):
            shutil.rmtree(TEST_QUEUE_DIR)

    def newQueue(self):
        return PDQueue(TEST_QUEUE_DIR, NoOpLock)

    def test__open_creat_excl_with_retry(self):
        from pdagent.pdqueue import _open_creat_excl
        q = self.newQueue()
        fname_abs = q._abspath("_open_creat_excl_with_retry.txt")
        fd1 = _open_creat_excl(fname_abs)
        self.assertNotEquals(fd1, None)
        fd2 = None
        try:
            fd2 = _open_creat_excl(fname_abs)
            self.assertEquals(fd2, None)
        finally:
            os.close(fd1)
            if fd2:
                os.close(fd2)

    def test_init_creates_directory(self):
        self.assertFalse(os.path.exists(TEST_QUEUE_DIR))
        self.newQueue()
        self.assertTrue(os.path.exists(TEST_QUEUE_DIR))

    def test_enqueue_and_dequeue(self):
        q = self.newQueue()

        self.assertEquals(q._queued_files(), [])

        f_foo = q.enqueue("foo")
        self.assertEquals(q._queued_files(), [f_foo])
        self.assertEquals(open(q._abspath(f_foo)).read(), "foo")

        f_bar = q.enqueue("bar")
        self.assertEquals(q._queued_files(), [f_foo, f_bar])
        self.assertEquals(open(q._abspath(f_foo)).read(), "foo")
        self.assertEquals(open(q._abspath(f_bar)).read(), "bar")

        def consume_foo(s):
            self.assertEquals("foo", s)
            return True
        q.dequeue(consume_foo)

        def consume_bar(s):
            self.assertEquals("bar", s)
            return True
        q.dequeue(consume_bar)

        # check queue is empty
        self.assertEquals(q._queued_files(), [])
        self.assertRaises(EmptyQueue, q.dequeue, lambda s: True)

    def test_dont_consume(self):
        # The item should stay in the queue if we don't consume it.
        q = self.newQueue()
        q.enqueue("foo")

        def dont_consume_foo(s):
            self.assertEquals("foo", s)
            return False
        q.dequeue(dont_consume_foo)
        q.dequeue(dont_consume_foo)

        def consume_foo(s):
            self.assertEquals("foo", s)
            return True
        q.dequeue(consume_foo)

        self.assertRaises(EmptyQueue, q.dequeue, lambda s: True)

    def test_enqueue_never_blocks(self):
        # test that a read lock during dequeue does not block an enqueue
        q = self.newQueue()
        f_foo = q.enqueue("foo")

        trace = []

        class LockClass:
            def __init__(self, lockfile):
                trace.append("Li")

            def acquire(self):
                trace.append("La")

            def release(self):
                trace.append("Lr")
        q.lock_class = LockClass

        def dequeue():
            try:
                def consume(s):
                    trace.append("C1")
                    time.sleep(0.2)
                    trace.append("C2")
                    return True
                q.dequeue(consume)
            except EmptyQueue:
                trace.append("q_EQ")

        thread_dequeue = Thread(target=dequeue)
        thread_dequeue.start()
        time.sleep(0.1)  # give the thread time to acquire the lock & sleep

        self.assertEquals(trace, ["Li", "La", "C1"])
        self.assertEquals(q._queued_files(), [f_foo])

        f_bar = q.enqueue("bar")

        self.assertEquals(trace, ["Li", "La", "C1"])
        self.assertEquals(q._queued_files(), [f_foo, f_bar])

        time.sleep(0.2)

        self.assertEquals(trace, ["Li", "La", "C1", "C2", "Lr"])
        self.assertEquals(q._queued_files(), [f_bar])

    def test_parallel_dequeque(self):
        # test that a dequeue blocks another dequeue using locking

        q1 = self.newQueue()
        q2 = self.newQueue()
        q1.enqueue("foo")

        dequeue_lockfile = q1._dequeue_lockfile
        trace = []
        lock = Lock()

        def make_lock_class(name):
            # so that pydev doesn't complain about self naming
            outer_self = self

            class LockClass:
                def __init__(self, lockfile):
                    outer_self.assertEquals(dequeue_lockfile, lockfile)

                def acquire(self):
                    trace.append(name + "_A1")
                    lock.acquire()
                    trace.append(name + "_A2")

                def release(self):
                    trace.append(name + "_R")
                    lock.release()
            return LockClass

        q1.lock_class = make_lock_class("q1")
        q2.lock_class = make_lock_class("q2")

        def dequeue2():
            try:
                def consume2(s):
                    self.fail()  # consume2 shouldn't be called!
                q2.dequeue(consume2)
            except EmptyQueue:
                trace.append("q2_EQ")

        thread_dequeue2 = Thread(target=dequeue2)

        def consume1(s):
            # check that q1 acquired the lock
            self.assertEquals(trace, ["q1_A1", "q1_A2"])
            # start dequeue2 in separate thread to recreate concurrent access
            thread_dequeue2.start()
            # give thread time to run - it should be stuck in lock acquire
            time.sleep(0.1)
            self.assertEquals(trace, ["q1_A1", "q1_A2", "q2_A1"])
            # consume the item
            trace.append("q1_C:" + s)
            return True

        q1.dequeue(consume1)
        # give the thread time to acquire the just released
        # lock & run to completion
        time.sleep(0.1)

        self.assertEquals(trace, [
            "q1_A1", "q1_A2", "q2_A1",
            "q1_C:foo", "q1_R",
            "q2_A2", "q2_R",
            "q2_EQ",
             ])


if __name__ == '__main__':
    unittest.main()
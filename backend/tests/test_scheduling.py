import asyncio
import time
import unittest
from types import SimpleNamespace
from backend.app.checker import interleave_by_group, GroupRateLimiter


class TestInterleaveByGroup(unittest.TestCase):
    def test_empty_and_single(self):
        self.assertEqual(interleave_by_group([]), [])
        t1 = SimpleNamespace(id=1, group="A")
        self.assertEqual(interleave_by_group([t1]), [t1])

    def test_single_group(self):
        targets = [
            SimpleNamespace(id=1, group="A"),
            SimpleNamespace(id=2, group="A"),
            SimpleNamespace(id=3, group="A"),
        ]
        res = interleave_by_group(targets)
        self.assertEqual([t.id for t in res], [1, 2, 3])

    def test_multi_group_unequal_sizes(self):
        # A: 3 items, B: 2 items, C: 1 item
        targets = [
            SimpleNamespace(id="A1", group="A"),
            SimpleNamespace(id="A2", group="A"),
            SimpleNamespace(id="A3", group="A"),
            SimpleNamespace(id="B1", group="B"),
            SimpleNamespace(id="B2", group="B"),
            SimpleNamespace(id="C1", group="C"),
        ]
        res = interleave_by_group(targets)
        ids = [t.id for t in res]
        # Round 1: A1, B1, C1
        # Round 2: A2, B2
        # Round 3: A3
        self.assertEqual(ids, ["A1", "B1", "C1", "A2", "B2", "A3"])

    def test_order_preservation_and_integrity(self):
        import random
        groups = ["group_" + str(i) for i in range(5)]
        original = []
        group_items = {g: [] for g in groups}

        target_id = 0
        for _ in range(500):
            g = random.choice(groups)
            t = SimpleNamespace(id=target_id, group=g)
            target_id += 1
            original.append(t)
            group_items[g].append(t)

        interleaved = interleave_by_group(original)

        # Integrity checks
        self.assertEqual(len(interleaved), len(original))
        self.assertEqual(set(t.id for t in interleaved), set(t.id for t in original))

        # Check FIFO within each group
        for g in groups:
            sub_interleaved = [t for t in interleaved if t.group == g]
            self.assertEqual(sub_interleaved, group_items[g])


class TestGroupRateLimiter(unittest.IsolatedAsyncioTestCase):
    async def test_initial_burst_available(self):
        # With rate=10, initial bucket has 10 tokens
        limiter = GroupRateLimiter(rate=10)
        start = time.monotonic()
        for _ in range(10):
            await limiter.acquire()
        elapsed = time.monotonic() - start
        # All 10 initial tokens should be consumed virtually instantaneously (< 0.05s)
        self.assertLess(elapsed, 0.05)

    async def test_rate_limiting_delays(self):
        # Rate = 20 QPS (0.05s per token)
        limiter = GroupRateLimiter(rate=20)
        # Drain initial tokens
        for _ in range(20):
            await limiter.acquire()

        # Next 4 acquires should each wait ~0.05s => ~0.20s total
        start = time.monotonic()
        for _ in range(4):
            await limiter.acquire()
        elapsed = time.monotonic() - start

        # Should be around 0.20s (allow 0.15s - 0.40s tolerance)
        self.assertGreaterEqual(elapsed, 0.15)
        self.assertLessEqual(elapsed, 0.40)

    async def test_concurrent_acquire(self):
        limiter = GroupRateLimiter(rate=15)
        results = []

        async def worker(i):
            await limiter.acquire()
            results.append((i, time.monotonic()))

        start = time.monotonic()
        # Launch 20 concurrent workers
        await asyncio.gather(*(worker(i) for i in range(20)))
        elapsed = time.monotonic() - start

        self.assertEqual(len(results), 20)
        # 15 tokens initial, 5 extra tokens needed at 15 QPS -> ~0.33s
        self.assertGreaterEqual(elapsed, 0.25)


class TestLockOrderingSafety(unittest.IsolatedAsyncioTestCase):
    async def test_group_sem_before_global_sem_prevents_starvation(self):
        """
        Verify that acquiring group_sem before global_sem prevents a group with
        concurrency limit 1 from hoarding all global semaphore permits.
        """
        global_sem = asyncio.Semaphore(2)
        group_a_sem = asyncio.Semaphore(1)
        group_b_sem = asyncio.Semaphore(1)

        execution_order = []

        async def task_a(name):
            # Correct order: group_sem then global_sem
            async with group_a_sem:
                async with global_sem:
                    execution_order.append(f"{name}_started")
                    await asyncio.sleep(0.05)
                    execution_order.append(f"{name}_done")

        async def task_b(name):
            async with group_b_sem:
                async with global_sem:
                    execution_order.append(f"{name}_started")
                    await asyncio.sleep(0.02)
                    execution_order.append(f"{name}_done")

        # Start 3 tasks for group A, and 1 task for group B
        # In correct order, task_b will run immediately in parallel with A1,
        # because A2 and A3 are waiting at group_a_sem and do NOT block global_sem.
        t_a1 = asyncio.create_task(task_a("A1"))
        t_a2 = asyncio.create_task(task_a("A2"))
        t_a3 = asyncio.create_task(task_a("A3"))
        t_b1 = asyncio.create_task(task_b("B1"))

        await asyncio.gather(t_a1, t_a2, t_a3, t_b1)

        # B1 should have started before A1 finished or alongside A1,
        # definitely before A2 finishes!
        b1_start_idx = execution_order.index("B1_started")
        a2_done_idx = execution_order.index("A2_done")
        self.assertLess(b1_start_idx, a2_done_idx)


if __name__ == "__main__":
    unittest.main()

class TestEdgeCases(unittest.TestCase):
    def test_none_group(self):
        targets = [
            SimpleNamespace(id=1, group=None),
            SimpleNamespace(id=2, group="A"),
            SimpleNamespace(id=3, group=None),
        ]
        res = interleave_by_group(targets)
        self.assertEqual([t.id for t in res], [1, 2, 3])

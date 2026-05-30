import asyncio
import threading
import time
from contextlib import asynccontextmanager
from typing import Dict, Optional, Tuple

class LockManagerWithIdleTTL:
    def __init__(self, idle_ttl: int = 3600):
        self._start_lock = threading.Lock()
        self._cleanup_started = False
        self._locks: Dict[int, Tuple[asyncio.Lock, float]] = {}
        self._creation_lock = asyncio.Lock()
        self._idle_ttl = idle_ttl
        self._cleanup_task: Optional[asyncio.Task] = None

    def start_cleanup(self):
        with self._start_lock:
            if not self._cleanup_started:
                self._cleanup_started = True
                self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(60)
            now = time.time()
            async with self._creation_lock:
                expired = [
                    uid for uid, (lock, last_used) in self._locks.items()
                    if now - last_used >= self._idle_ttl and not lock.locked()
                ]
                for uid in expired:
                    del self._locks[uid]
    
    async def get_lock(self, user_id: int) -> asyncio.Lock:

        if not self._cleanup_started:
            self.start_cleanup()

        now = time.time()
        
        if user_id in self._locks:
            lock, _ = self._locks[user_id]
            self._locks[user_id] = (lock, now)
            return lock
        
        async with self._creation_lock:
            if user_id in self._locks:
                lock, _ = self._locks[user_id]
                self._locks[user_id] = (lock, now)
                return lock
            
            new_lock = asyncio.Lock()
            self._locks[user_id] = (new_lock, now)
            return new_lock
    
    @asynccontextmanager
    async def lock(self, user_id: int):
        lock = await self.get_lock(user_id)
        async with lock:
            yield
        if user_id in self._locks:
            self._locks[user_id] = (self._locks[user_id][0], time.time())
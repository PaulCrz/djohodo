"""Djohodo watcher: daily news watch over portfolio holdings.

The watcher is the "roof-perched observer" of the system: it scans the last
24 hours of financial news for each holding and produces a concise Markdown
digest. It only *reports* — it does not recommend. See ``analyst/`` for the
future recommendation layer.
"""

from watcher.agent import run_watch
from watcher.delivery import deliver

__all__ = ["run_watch", "deliver"]

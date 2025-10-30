import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

DB_PATH = Path(__file__).parent / "story.db"


class _DB:
    def __init__(self):
        # Allow usage from FastAPI worker threads
        self._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Better concurrency characteristics for a simple API
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        self._init()

    def _init(self):
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
              id TEXT PRIMARY KEY,
              parent_id TEXT,
              story_id TEXT,
              prompt TEXT,
              choice_text TEXT,
              sora_job_id TEXT,
              status TEXT,
              video_path TEXT,
              last_frame_path TEXT,
              seconds INTEGER,
              size TEXT,
              model TEXT,
              options TEXT,
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Add options column to existing tables if missing
        try:
            cur.execute("ALTER TABLE nodes ADD COLUMN options TEXT")
            self._conn.commit()
        except sqlite3.OperationalError:
            # Column already exists
            pass
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS story_state (
              story_id TEXT PRIMARY KEY,
              summary TEXT,
              latest_node_id TEXT
            )
            """
        )
        self._conn.commit()

    def add_node(self, id, parent_id, story_id, prompt, choice_text, sora_job_id, status,
                 video_path, last_frame_path, seconds, size, model):
        self._conn.execute(
            """
            INSERT INTO nodes (id, parent_id, story_id, prompt, choice_text, sora_job_id, status,
                               video_path, last_frame_path, seconds, size, model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (id, parent_id, story_id, prompt, choice_text, sora_job_id, status,
             video_path, last_frame_path, seconds, size, model),
        )
        # ensure story_state exists
        cur = self._conn.execute("SELECT story_id FROM story_state WHERE story_id = ?", (story_id,))
        if not cur.fetchone():
            self._conn.execute(
                "INSERT INTO story_state (story_id, summary, latest_node_id) VALUES (?, ?, ?)",
                (story_id, "", id),
            )
        self._conn.commit()

    def get_by_job(self, job_id: str) -> Optional[SimpleNamespace]:
        cur = self._conn.execute("SELECT * FROM nodes WHERE sora_job_id = ?", (job_id,))
        row = cur.fetchone()
        return SimpleNamespace(**dict(row)) if row else None

    def get_node(self, node_id: str) -> Optional[SimpleNamespace]:
        cur = self._conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
        row = cur.fetchone()
        return SimpleNamespace(**dict(row)) if row else None

    def mark_done(self, node_id: str, video_path: str, frame_path: str):
        self._conn.execute(
            "UPDATE nodes SET status = 'completed', video_path = ?, last_frame_path = ? WHERE id = ?",
            (video_path, frame_path, node_id),
        )
        self._conn.commit()

    def set_options(self, node_id: str, options_json: str):
        """Cache the generated options for this node."""
        self._conn.execute(
            "UPDATE nodes SET options = ? WHERE id = ?",
            (options_json, node_id),
        )
        self._conn.commit()

    def get_options(self, node_id: str) -> Optional[str]:
        """Retrieve cached options JSON string for this node."""
        cur = self._conn.execute("SELECT options FROM nodes WHERE id = ?", (node_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else None

    def update_job(self, node_id: str, sora_job_id: str | None = None, status: str | None = None, prompt: str | None = None):
        sets = []
        vals = []
        if sora_job_id is not None:
            sets.append("sora_job_id = ?")
            vals.append(sora_job_id)
        if status is not None:
            sets.append("status = ?")
            vals.append(status)
        if prompt is not None:
            sets.append("prompt = ?")
            vals.append(prompt)
        if not sets:
            return
        vals.append(node_id)
        sql = f"UPDATE nodes SET {', '.join(sets)} WHERE id = ?"
        self._conn.execute(sql, tuple(vals))
        self._conn.commit()

    def get_summary(self, story_id: str) -> str:
        cur = self._conn.execute("SELECT summary FROM story_state WHERE story_id = ?", (story_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else ""

    def set_latest(self, story_id: str, node_id: str):
        self._conn.execute("UPDATE story_state SET latest_node_id = ? WHERE story_id = ?", (node_id, story_id))
        self._conn.commit()

    def set_initial_summary(self, story_id: str, base_prompt: str):
        cur = self._conn.execute("SELECT summary FROM story_state WHERE story_id = ?", (story_id,))
        row = cur.fetchone()
        current = (row[0] if row else "") or ""
        if not current.strip():
            initial = f"- Setup: {base_prompt[:200]}"
            self._conn.execute(
                "UPDATE story_state SET summary = ? WHERE story_id = ?",
                (initial, story_id),
            )
            self._conn.commit()

    def update_summary(self, story_id: str, parent_node, choice_label: str, sora_prompt: str):
        """Append a compact bullet line; keep last ~8 bullets."""
        prev = self.get_summary(story_id)
        bullets = [b.strip() for b in prev.split("\n-") if b.strip()]
        new_line = f"- Choice: {choice_label}. Next: {sora_prompt[:140]}"  # trim
        bullets.append(new_line)
        bullets = bullets[-8:]
        new_summary = "\n".join([b if b.startswith("-") else f"- {b}" for b in bullets])
        self._conn.execute(
            "UPDATE story_state SET summary = ?, latest_node_id = ? WHERE story_id = ?",
            (new_summary, parent_node.id, story_id),
        )
        self._conn.commit()

    def list_story(self, story_id: str):
        nodes = [dict(r) for r in self._conn.execute("SELECT * FROM nodes WHERE story_id = ? ORDER BY created_at", (story_id,))]
        state = self._conn.execute("SELECT * FROM story_state WHERE story_id = ?", (story_id,)).fetchone()
        return {
            "nodes": nodes,
            "state": dict(state) if state else None,
        }


DB = _DB()

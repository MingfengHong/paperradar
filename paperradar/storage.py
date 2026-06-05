from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from .models import Paper, Recommendation, RunResult, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
  key TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  topic_id TEXT NOT NULL,
  subscription_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  candidate_count INTEGER NOT NULL,
  recommendation_count INTEGER NOT NULL,
  report_markdown TEXT NOT NULL,
  report_html TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pushed_items (
  subscription_id TEXT NOT NULL,
  paper_key TEXT NOT NULL,
  pushed_at TEXT NOT NULL,
  PRIMARY KEY(subscription_id, paper_key)
);

CREATE TABLE IF NOT EXISTS feedback (
  paper_key TEXT NOT NULL,
  topic_id TEXT NOT NULL,
  action TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def save_papers(self, papers: Iterable[Paper]) -> None:
        now = utc_now()
        for paper in papers:
            payload = json.dumps(paper.__dict__, ensure_ascii=False)
            key = paper.normalized_key()
            self.conn.execute(
                """
                INSERT INTO papers(key, payload, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, last_seen_at=excluded.last_seen_at
                """,
                (key, payload, now, now),
            )
        self.conn.commit()

    def already_pushed(self, subscription_id: str, paper: Paper) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM pushed_items WHERE subscription_id=? AND paper_key=?",
            (subscription_id, paper.normalized_key()),
        ).fetchone()
        return row is not None

    def mark_pushed(self, subscription_id: str, papers: Iterable[Paper]) -> None:
        now = utc_now()
        for paper in papers:
            self.conn.execute(
                "INSERT OR IGNORE INTO pushed_items(subscription_id, paper_key, pushed_at) VALUES (?, ?, ?)",
                (subscription_id, paper.normalized_key(), now),
            )
        self.conn.commit()

    def save_run(self, result: RunResult) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO runs(
              id, topic_id, subscription_id, created_at, candidate_count,
              recommendation_count, report_markdown, report_html
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.run_id,
                result.topic.id,
                result.subscription.id,
                result.created_at,
                len(result.candidates),
                len([rec for rec in result.recommendations if not rec.filtered]),
                result.report_markdown,
                result.report_html,
            ),
        )
        self.conn.commit()

    def recent_runs(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_feedback(self, paper_key: str, topic_id: str, action: str, note: str = "") -> None:
        self.conn.execute(
            "INSERT INTO feedback(paper_key, topic_id, action, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (paper_key, topic_id, action, note, utc_now()),
        )
        self.conn.commit()

    def feedback_for_topic(self, topic_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM feedback WHERE topic_id=? ORDER BY created_at DESC",
            (topic_id,),
        ).fetchall()
        return [dict(row) for row in rows]

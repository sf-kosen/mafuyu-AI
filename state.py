# Agent State Management
import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from config import LOGS_DIR


@dataclass
class AgentState:
    task_id: str
    goal: str
    done: bool = False
    steps: int = 0
    next: str = ""
    artifacts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    pending_notes: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)  # Agent conversation history

    def save(self) -> Path:
        """Save state to JSON file."""
        path = LOGS_DIR / f"state_{self.task_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
        return path

    @classmethod
    def load(cls, task_id: str) -> Optional["AgentState"]:
        """Load state from JSON file."""
        path = LOGS_DIR / f"state_{task_id}.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**data)

    @classmethod
    def create(cls, goal: str) -> "AgentState":
        """Create new agent state with unique ID."""
        task_id = uuid.uuid4().hex[:8]
        return cls(task_id=task_id, goal=goal)

    def add_note(self, note: str):
        """Add a pending note."""
        self.pending_notes.append(note)
        self.save()

    def consume_notes(self) -> list[str]:
        """Consume and clear pending notes."""
        notes = self.pending_notes.copy()
        self.pending_notes.clear()
        return notes

    def add_error(self, error: str):
        """Record an error."""
        self.errors.append(error)
        self.save()

    def add_artifact(self, artifact: str):
        """Record an artifact."""
        self.artifacts.append(artifact)
        self.save()

    def increment_step(self):
        """Increment step counter."""
        self.steps += 1

    def mark_done(self):
        """Mark task as complete."""
        self.done = True
        self.save()

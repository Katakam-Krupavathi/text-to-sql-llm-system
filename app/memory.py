from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Dict, List, Optional
import uuid


@dataclass
class ConversationTurn:
    question: str
    reasoning_plan: Optional[str]
    sql_query: Optional[str]
    answer: str
    timestamp: float = field(default_factory=time.time)


class ConversationMemoryStore:
    """In-memory store holding recent conversation turns per session."""

    def __init__(self, max_turns_per_session: int = 5, session_ttl_seconds: int = 3600):
        self.max_turns = max_turns_per_session
        self.session_ttl = session_ttl_seconds
        self.sessions: Dict[str, List[ConversationTurn]] = defaultdict(list)

    def get_or_create_session_id(self, session_id: Optional[str] = None) -> str:
        if session_id and session_id.strip():
            return session_id.strip()
        return str(uuid.uuid4())

    def add_turn(
        self,
        session_id: str,
        question: str,
        reasoning_plan: Optional[str],
        sql_query: Optional[str],
        answer: str,
    ) -> None:
        """Adds a completed turn to the session history."""
        self._cleanup_old_turns(session_id)
        turn = ConversationTurn(
            question=question,
            reasoning_plan=reasoning_plan,
            sql_query=sql_query,
            answer=answer,
        )
        self.sessions[session_id].append(turn)

        # Enforce max turns
        if len(self.sessions[session_id]) > self.max_turns:
            self.sessions[session_id] = self.sessions[session_id][-self.max_turns :]

    def get_recent_history(self, session_id: str, limit: int = 2) -> List[ConversationTurn]:
        """Retrieves the last N turns for the given session."""
        self._cleanup_old_turns(session_id)
        turns = self.sessions.get(session_id, [])
        return turns[-limit:]

    def format_history_for_prompt(self, session_id: str, limit: int = 2) -> str:
        """Formats the last N turns into a clean markdown block for LLM prompt context."""
        recent_turns = self.get_recent_history(session_id, limit=limit)
        if not recent_turns:
            return ""

        history_lines = ["Previous Conversation Context:"]
        for i, turn in enumerate(recent_turns, 1):
            history_lines.append(f"Turn {i}:")
            history_lines.append(f"  User Question: {turn.question}")
            if turn.sql_query:
                history_lines.append(f"  Generated SQL: {turn.sql_query}")
            if turn.answer:
                history_lines.append(f"  Answer: {turn.answer}")
        
        return "\n".join(history_lines) + "\n\n"

    def clear_session(self, session_id: str) -> None:
        if session_id in self.sessions:
            del self.sessions[session_id]

    def _cleanup_old_turns(self, session_id: str):
        cutoff = time.time() - self.session_ttl
        if session_id in self.sessions:
            self.sessions[session_id] = [
                t for t in self.sessions[session_id] if t.timestamp > cutoff
            ]
            if not self.sessions[session_id]:
                del self.sessions[session_id]


# Global in-memory conversation memory instance
memory_store = ConversationMemoryStore()

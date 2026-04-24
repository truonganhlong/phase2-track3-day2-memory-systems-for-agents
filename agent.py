from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import TypedDict

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# LangGraph-style State
# ---------------------------------------------------------------------------

class MemoryState(TypedDict):
    messages: list[dict]
    user_profile: dict
    episodes: list[dict]
    semantic_hits: list[str]
    memory_budget: int


# ---------------------------------------------------------------------------
# Memory Backends
# ---------------------------------------------------------------------------

@dataclass
class ShortTermMemory:
    """Sliding-window conversation buffer (short-term)."""
    max_turns: int = 6
    _messages: list[dict] = field(default_factory=list)

    def save_turn(self, role: str, content: str) -> None:
        self._messages.append({"role": role, "content": content})
        keep = self.max_turns * 2
        if len(self._messages) > keep:
            self._messages = self._messages[-keep:]

    def retrieve(self) -> list[dict]:
        return list(self._messages)


@dataclass
class LongTermProfileMemory:
    """JSON-backed key-value store for user profile facts (long-term)."""
    path: Path
    _profile: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.path.exists():
            self._profile = json.loads(self.path.read_text(encoding="utf-8"))

    def retrieve(self) -> dict:
        return dict(self._profile)

    def set_fact(self, key: str, value: str) -> None:
        """Last-write-wins — prevents contradictory profile facts."""
        self._profile[key] = value
        self._flush()

    def _flush(self) -> None:
        self.path.write_text(
            json.dumps(self._profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )


@dataclass
class EpisodicMemory:
    """JSON log of significant past events / task completions (episodic)."""
    path: Path
    _episodes: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.path.exists():
            self._episodes = json.loads(self.path.read_text(encoding="utf-8"))

    def save_episode(self, title: str, outcome: str) -> None:
        self._episodes.append({"title": title, "outcome": outcome})
        self.path.write_text(
            json.dumps(self._episodes, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def retrieve(self, top_k: int = 3) -> list[dict]:
        return self._episodes[-top_k:]


@dataclass
class SemanticMemory:
    """Keyword-scored chunk retrieval — fallback for vector search (semantic)."""
    corpus_path: Path
    _chunks: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.corpus_path.exists():
            self._chunks = json.loads(self.corpus_path.read_text(encoding="utf-8"))

    def retrieve(self, query: str, top_k: int = 3) -> list[str]:
        query_terms = set(re.findall(r"\w+", query.lower()))
        scored: list[tuple[int, str]] = []
        for chunk in self._chunks:
            terms = set(re.findall(r"\w+", chunk.lower()))
            score = len(query_terms.intersection(terms))
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k]]


# ---------------------------------------------------------------------------
# LangGraph-style router + agent
# ---------------------------------------------------------------------------

@dataclass
class MultiMemoryAgent:
    short_term: ShortTermMemory
    profile: LongTermProfileMemory
    episodic: EpisodicMemory
    semantic: SemanticMemory
    memory_budget: int = 400
    _client: OpenAI = field(default_factory=OpenAI)

    # ---- Router node -------------------------------------------------------

    def retrieve_memory(self, state: MemoryState, user_message: str) -> MemoryState:
        """LangGraph node: gather all memory backends into state."""
        state["messages"] = self.short_term.retrieve()
        state["user_profile"] = self.profile.retrieve()
        state["episodes"] = self.episodic.retrieve()
        state["semantic_hits"] = self.semantic.retrieve(user_message)
        state["memory_budget"] = self.memory_budget
        return state

    # ---- Prompt builder ----------------------------------------------------

    def _trim_to_budget(self, text: str, budget_words: int) -> str:
        words = text.split()
        if len(words) <= budget_words:
            return text
        return " ".join(words[-budget_words:])

    def _build_system_prompt(self, state: MemoryState) -> str:
        profile_section = (
            json.dumps(state["user_profile"], ensure_ascii=False)
            if state["user_profile"]
            else "(empty)"
        )
        episodic_section = (
            json.dumps(state["episodes"], ensure_ascii=False)
            if state["episodes"]
            else "(empty)"
        )
        semantic_section = (
            "\n".join(f"- {h}" for h in state["semantic_hits"])
            if state["semantic_hits"]
            else "(none)"
        )

        system = f"""You are a helpful, memory-aware assistant. You speak Vietnamese fluently.
Always use the memory sections below when answering — do NOT ignore them.

[PROFILE MEMORY]  ← long-term facts about this user
{profile_section}

[EPISODIC MEMORY]  ← past events / task completions
{episodic_section}

[SEMANTIC MEMORY]  ← relevant knowledge chunks
{semantic_section}

Respond naturally. Reference memory when relevant. Be concise."""
        return self._trim_to_budget(system, state["memory_budget"])

    # ---- Profile update extraction ----------------------------------------

    def _extract_profile_updates(self, user_message: str) -> list[tuple[str, str]]:
        updates: list[tuple[str, str]] = []
        lower = user_message.lower()

        # Name
        m = re.search(r"tên(?:\s+tôi)?\s+là\s+([^\s,.!?\n]+)", lower)
        if m:
            updates.append(("name", m.group(1).strip().title()))

        # City
        m = re.search(r"(?:tôi\s+)?(?:sống|ở)\s+(?:tại\s+)?([^\s,.!?\n]+)", lower)
        if m:
            updates.append(("city", m.group(1).strip().title()))

        # Allergy — handle correction ("nhầm … chứ không phải …")
        if "nhầm" in lower or "sửa lại" in lower:
            m = re.search(r"dị\s*ứng\s+([^\s,.!?\n]+)(?:\s+chứ\s+không\s+phải)?", lower)
            if m:
                raw = m.group(1).split("chứ")[0].strip()
                updates.append(("allergy", raw))
        else:
            m = re.search(r"dị\s*ứng\s+([^\s,.!?\n]+)", lower)
            if m:
                updates.append(("allergy", m.group(1).strip()))

        # Job
        m = re.search(r"(?:tôi\s+)?(?:làm|nghề)\s+([^\s,.!?\n]+)", lower)
        if m:
            updates.append(("job", m.group(1).strip()))

        return updates

    # ---- Save memories after each turn ------------------------------------

    def _save_memories(self, user_message: str, assistant_reply: str) -> None:
        # Profile facts
        for key, value in self._extract_profile_updates(user_message):
            self.profile.set_fact(key, value)

        # Episodic — capture completed tasks
        done_keywords = ["xong", "hoàn tất", "đã làm xong", "done", "fixed", "đã xong"]
        if any(w in user_message.lower() for w in done_keywords):
            self.episodic.save_episode(
                title="Task completion",
                outcome=user_message[:120],
            )

        # Short-term sliding window
        self.short_term.save_turn("user", user_message)
        self.short_term.save_turn("assistant", assistant_reply)

    # ---- Main chat entrypoint ---------------------------------------------

    def chat(self, user_message: str) -> str:
        # 1. Init blank state
        state: MemoryState = {
            "messages": [],
            "user_profile": {},
            "episodes": [],
            "semantic_hits": [],
            "memory_budget": self.memory_budget,
        }

        # 2. Router node: retrieve all memory into state
        state = self.retrieve_memory(state, user_message)

        # 3. Build system prompt with injected memory
        system_prompt = self._build_system_prompt(state)

        # 4. Build OpenAI messages (recent conversation + new user message)
        openai_messages = [{"role": "system", "content": system_prompt}]
        for m in state["messages"]:
            openai_messages.append({"role": m["role"], "content": m["content"]})
        openai_messages.append({"role": "user", "content": user_message})

        # 5. Call real LLM
        response = self._client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=openai_messages,
            max_tokens=512,
            temperature=0.7,
        )
        assistant_reply = response.choices[0].message.content.strip()

        # 6. Save memories
        self._save_memories(user_message, assistant_reply)

        return assistant_reply


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def bootstrap_agent(root: Path) -> MultiMemoryAgent:
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    semantic_path = data_dir / "semantic_chunks.json"
    if not semantic_path.exists():
        semantic_path.write_text(
            json.dumps([
                "Docker Compose: các services giao tiếp qua service name, không phải localhost.",
                "Redis thường dùng cho profile memory kiểu key-value nhanh.",
                "Token budget trimming giúp tránh prompt quá dài.",
                "Khi user cập nhật fact, giá trị mới ghi đè giá trị cũ (last-write-wins).",
                "Semantic retrieval có thể fallback về keyword matching thay vì vector search.",
                "LangGraph dùng TypedDict để định nghĩa MemoryState qua các nodes.",
                "Episodic memory lưu kết quả của các task đã hoàn thành, không phải mọi turn.",
            ], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return MultiMemoryAgent(
        short_term=ShortTermMemory(max_turns=6),
        profile=LongTermProfileMemory(data_dir / "profile.json"),
        episodic=EpisodicMemory(data_dir / "episodes.json"),
        semantic=SemanticMemory(semantic_path),
        memory_budget=400,
    )


# ---------------------------------------------------------------------------
# Interactive CLI
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌  OPENAI_API_KEY chưa được set. Kiểm tra file .env!")
        sys.exit(1)

    root = Path(__file__).parent
    agent = bootstrap_agent(root)

    print("🤖  Multi-Memory Agent (GPT) — gõ 'exit' để thoát\n")
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "thoát"}:
            print("Bye!")
            break
        reply = agent.chat(user_input)
        print(f"Agent: {reply}\n")


if __name__ == "__main__":
    main()

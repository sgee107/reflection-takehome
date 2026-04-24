"""Rich console callback handler for observing the LangGraph ReAct loop."""

from __future__ import annotations

import json
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

MAX_RESULT_LEN = 500


class RichCallbackHandler(BaseCallbackHandler):
    """Streams agent loop activity to the console with rich formatting.

    Tracks ReAct iterations and displays LLM reasoning, tool calls,
    and tool results in real-time.
    """

    def __init__(self) -> None:
        self.console = Console()
        self.iteration = 0
        self._tool_start_time: float = 0.0

    # ── LLM events ──────────────────────────────────────────────

    def on_llm_start(
        self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any
    ) -> None:
        self.iteration += 1
        self.console.print()
        self.console.rule(
            f"[bold cyan]Agent Loop: Iteration {self.iteration}[/bold cyan]"
        )
        self.console.print("[dim]LLM thinking...[/dim]")

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for generation in response.generations:
            for gen in generation:
                msg = gen.message if hasattr(gen, "message") else None
                if msg is None:
                    continue

                # Show text reasoning (if any)
                if msg.content:
                    text_parts = []
                    if isinstance(msg.content, list):
                        text_parts = [
                            block["text"]
                            for block in msg.content
                            if isinstance(block, dict) and block.get("type") == "text"
                        ]
                    elif isinstance(msg.content, str) and msg.content.strip():
                        text_parts = [msg.content]

                    if text_parts:
                        reasoning = " ".join(text_parts)
                        self.console.print(
                            Panel(
                                _truncate(reasoning, 800),
                                title="[bold green]Reasoning[/bold green]",
                                border_style="green",
                                padding=(0, 1),
                            )
                        )

                # Show tool call intentions
                tool_calls = getattr(msg, "tool_calls", None) or []
                for tc in tool_calls:
                    args_str = json.dumps(tc.get("args", {}), default=str)
                    label = Text.assemble(
                        ("-> Tool call: ", "bold yellow"),
                        (tc.get("name", "?"), "bold white"),
                        ("(", "dim"),
                        (_truncate(args_str, 120), "dim"),
                        (")", "dim"),
                    )
                    self.console.print(label)

                # If no tool calls, this is the final answer
                if not tool_calls and text_parts:
                    self.console.print(
                        "[bold green]Agent reached final answer.[/bold green]"
                    )

    # ── Tool events ─────────────────────────────────────────────

    def on_tool_start(
        self, serialized: dict[str, Any], input_str: str, **kwargs: Any
    ) -> None:
        self._tool_start_time = time.monotonic()
        tool_name = kwargs.get("name") or serialized.get("name", "?")
        self.console.print()
        header = Text.assemble(("Tool: ", "bold yellow"), (tool_name, "bold white"))
        self.console.print(header)

        # Show args
        try:
            args = json.loads(input_str) if isinstance(input_str, str) else input_str
            args_formatted = json.dumps(args, indent=2, default=str)
        except (json.JSONDecodeError, TypeError):
            args_formatted = str(input_str)
        self.console.print(f"  [dim]Args:[/dim] {_truncate(args_formatted, 200)}")

    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        elapsed = time.monotonic() - self._tool_start_time
        result_str = str(output)
        self.console.print(
            f"  [dim]Result:[/dim] {_truncate(result_str, MAX_RESULT_LEN)}"
        )
        self.console.print(f"  [dim]Duration:[/dim] {elapsed:.2f}s")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "... (truncated)"

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass
from typing import Protocol


@dataclass
class ContentBlock:
    type: str  # "text" | "tool_use"
    text: str | None = None
    id: str | None = None  # tool_use id
    name: str | None = None  # tool name
    input: dict | None = None  # tool input


@dataclass
class LLMResponse:
    content: list[ContentBlock]
    stop_reason: str
    input_tokens: int
    output_tokens: int
    latency_ms: float = 0.0


class ContextTooLargeError(Exception):

    def __init__(self, estimated_tokens: int, limit: int):
        self.estimated_tokens = estimated_tokens
        self.limit = limit
        super().__init__(f"Estimated request size {estimated_tokens} exceeds provider limit {limit}")


class LLMClient(Protocol):
    def create_message(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        tool_choice: dict | None,
        max_tokens: int,
    ) -> LLMResponse: ...


class AnthropicLLMClient:

    def __init__(self, model: str):
        import anthropic

        self._client = anthropic.Anthropic()
        self._model = model

    def create_message(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        tool_choice: dict | None,
        max_tokens: int,
    ) -> LLMResponse:
        start = time.monotonic()
        kwargs = dict(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        response = self._client.messages.create(**kwargs)
        latency_ms = (time.monotonic() - start) * 1000

        blocks = []
        for b in response.content:
            if b.type == "text":
                blocks.append(ContentBlock(type="text", text=b.text))
            elif b.type == "tool_use":
                blocks.append(ContentBlock(type="tool_use", id=b.id, name=b.name, input=b.input))

        return LLMResponse(
            content=blocks,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
        )


class _TokenPerMinuteThrottle:
 
    def __init__(self, tpm_limit: int):
        self._tpm_limit = tpm_limit
        self._window: deque[tuple[float, int]] = deque()

    def _prune(self, now: float) -> None:
        while self._window and now - self._window[0][0] > 60:
            self._window.popleft()

    def _used(self, now: float) -> int:
        self._prune(now)
        return sum(tokens for _, tokens in self._window)

    def wait_for_capacity(self, estimated_tokens: int) -> None:
        while True:
            now = time.monotonic()
            used = self._used(now)
            if used + estimated_tokens <= self._tpm_limit:
                return
            if not self._window:

                return
            oldest_ts = self._window[0][0]
            sleep_for = max(60 - (now - oldest_ts) + 0.1, 0.1)
            time.sleep(sleep_for)

    def record(self, actual_tokens: int) -> None:
        self._window.append((time.monotonic(), actual_tokens))


def _estimate_tokens(system: str, messages: list[dict], max_tokens: int) -> int:

    text_len = len(system) + sum(len(json.dumps(m)) for m in messages)
    return text_len // 4 + max_tokens


def _anthropic_tool_to_openai(tool: dict) -> dict:
    fn = {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "parameters": tool["input_schema"],
    }
    return {"type": "function", "function": fn}


def _anthropic_tool_choice_to_openai(tool_choice: dict | None):
    if tool_choice is None:
        return "auto"
    return {"type": "function", "function": {"name": tool_choice["name"]}}


def _anthropic_messages_to_openai(system: str, messages: list[dict]) -> list[dict]:
    openai_messages: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        role = m["role"]
        content = m["content"]

        if role == "user" and isinstance(content, str):
            openai_messages.append({"role": "user", "content": content})

        elif role == "user" and isinstance(content, list):
            
            for block in content:
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": block["tool_use_id"],
                    "content": block["content"],
                })

        elif role == "assistant":
            text_parts = [b["text"] for b in content if b.get("type") == "text" and b.get("text")]
            tool_use_blocks = [b for b in content if b.get("type") == "tool_use"]
            assistant_msg: dict = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_use_blocks:
                assistant_msg["tool_calls"] = [
                    {
                        "id": b["id"],
                        "type": "function",
                        "function": {"name": b["name"], "arguments": json.dumps(b["input"] or {})},
                    }
                    for b in tool_use_blocks
                ]
            openai_messages.append(assistant_msg)
        else:
            raise ValueError(f"Unrecognized message shape for Groq translation: role={role!r}")
    return openai_messages


class GroqLLMClient:

    def __init__(self, model: str, tpm_limit: int, api_key: str | None = None, account_tpm_limit: int | None = None):
        import os

        import openai

        api_key = api_key or os.environ.get("GROQ_API_KEY")
        self._client = openai.OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        self._model = model
        self._throttle = _TokenPerMinuteThrottle(tpm_limit)
        
        self._account_tpm_limit = account_tpm_limit if account_tpm_limit is not None else tpm_limit

    def create_message(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        tool_choice: dict | None,
        max_tokens: int,
    ) -> LLMResponse:
        openai_messages = _anthropic_messages_to_openai(system, messages)
        openai_tools = [_anthropic_tool_to_openai(t) for t in tools]
        openai_tool_choice = _anthropic_tool_choice_to_openai(tool_choice)

        estimated_tokens = _estimate_tokens(system, messages, max_tokens)
        if estimated_tokens > self._account_tpm_limit:
            raise ContextTooLargeError(estimated_tokens, self._account_tpm_limit)
        self._throttle.wait_for_capacity(estimated_tokens)

        start = time.monotonic()
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=openai_messages,
            tools=openai_tools,
            tool_choice=openai_tool_choice,
        )
        latency_ms = (time.monotonic() - start) * 1000

        choice = response.choices[0]
        blocks: list[ContentBlock] = []
        if choice.message.content:
            blocks.append(ContentBlock(type="text", text=choice.message.content))
        for tc in (choice.message.tool_calls or []):
            blocks.append(ContentBlock(
                type="tool_use", id=tc.id, name=tc.function.name, input=json.loads(tc.function.arguments)
            ))
        stop_reason = "tool_use" if choice.message.tool_calls else "end_turn"

        input_tokens = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens
        self._throttle.record(input_tokens + output_tokens)

        return LLMResponse(
            content=blocks,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )


class MockLLMClient:

    def __init__(self, decision: str = "insufficient_evidence", confidence: float = 0.3):
        self._decision = decision
        self._confidence = confidence

    def create_message(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        tool_choice: dict | None,
        max_tokens: int,
    ) -> LLMResponse:
        block = ContentBlock(
            type="tool_use",
            id="mock_tool_call_1",
            name="submit_decision",
            input={
                "decision": self._decision,
                "candidate_ids": [],
                "confidence": self._confidence,
                "reason": "mock client: no real reasoning performed, always defers",
            },
        )
        return LLMResponse(
            content=[block],
            stop_reason="tool_use",
            input_tokens=0,
            output_tokens=0,
            latency_ms=0.0,
        )

"""LLM client abstraction: real Anthropic- and Groq-backed clients, plus a zero-cost
mock for testing the tool-calling loop's plumbing without network access or spend.

llm_tier.py's bounded loop is written entirely against the Anthropic-shaped
`messages`/`tools`/`tool_choice` inputs and LLMResponse's minimal output shape
(content blocks, stop_reason, token usage) -- it has no provider-specific code at
all. Every client, including GroqLLMClient, is responsible for translating that one
shape into whatever its own API needs internally; this keeps llm_tier.py's
orchestration completely untouched regardless of which provider is behind it.
"""

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
    """Raised by any LLMClient whose provider has a hard per-request token ceiling
    the current call cannot fit under, even with no other traffic in the rate-limit
    window (see GroqLLMClient -- discovered via a real 413 from Groq on a
    10-candidate-subset case before the deduped-pool context fix in
    llm_tier.py::build_context cut typical context size ~4x). Distinct from the
    rate limiter's wait-then-retry path: waiting cannot help here, since the request
    is too large regardless of how empty the window is. llm_tier.py catches this to
    produce an honest insufficient_evidence deferral rather than a crash -- a real,
    reported limitation, not a silent failure or a forced guess. Anthropic's client
    is not expected to ever raise this in practice (1M-token context window)."""

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
    """Real Anthropic API calls. Requires the `anthropic` package and credentials
    resolvable by the SDK (ANTHROPIC_API_KEY, an `ant auth login` profile, etc.)."""

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
    """Sleep-based rate limiter: tracks actual tokens consumed in a rolling 60-second
    window and blocks before a call that would push cumulative usage over the
    configured TPM ceiling. Lives inside GroqLLMClient (not llm_tier.py) -- Claude
    never needed this, and the isolation principle says provider-specific
    infrastructure stays behind the LLMClient interface, not leaked into the
    orchestration loop.

    Token usage for the call about to be made isn't known until the response comes
    back (Groq doesn't report it up front), so capacity is checked against a
    conservative pre-call ESTIMATE (rough chars/4 heuristic for the input, plus the
    full max_tokens reserved as a worst-case output) before sending, and the ledger
    is corrected to the real total afterwards via record()."""

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
                # Window is already fully empty -- nothing left to age out -- but the
                # pre-call estimate alone still exceeds the ceiling. This happens for
                # a large-context case (many candidate subsets): the estimate reserves
                # the full max_tokens as a worst-case output allowance on top of the
                # chars/4 input estimate, which can overshoot the true need. There is
                # nothing further to wait for at this point, so proceed rather than
                # loop forever or crash (found via a real IndexError on a 10-subset
                # dry-run case, not spotted by code review) -- record() after the real
                # call will correct the ledger to actual usage regardless.
                return
            oldest_ts = self._window[0][0]
            sleep_for = max(60 - (now - oldest_ts) + 0.1, 0.1)
            time.sleep(sleep_for)

    def record(self, actual_tokens: int) -> None:
        self._window.append((time.monotonic(), actual_tokens))


def _estimate_tokens(system: str, messages: list[dict], max_tokens: int) -> int:
    """Rough chars/4 heuristic for the input side, plus the full max_tokens reserved
    as a worst-case output allowance -- deliberately conservative (over-throttles
    slightly rather than risking a 429)."""
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
            # Anthropic shape: a list of tool_result blocks -> one OpenAI "tool" message each
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
    """Real calls to Groq's OpenAI-compatible API. Uses the `openai` package pointed
    at Groq's base URL -- not the `groq` package, since it hasn't been confirmed to
    match this project's tool-calling shape, per the instruction to only reach for it
    after that confirmation. Requires GROQ_API_KEY to be set (or passed explicitly).

    Translates llm_tier.py's Anthropic-shaped tools/tool_choice/messages into OpenAI's
    function-calling shape on every call -- see the module-level `_anthropic_*`
    helpers above. `strict` is deliberately NOT set on the translated tool schemas:
    Groq's docs confirm gpt-oss-120b supports tool_choice (including forcing a named
    function) and structured JSON output via response_format, but strict per-tool
    schema enforcement specifically was not confirmed for this model as of this
    writing -- left off rather than assumed.
    """

    def __init__(self, model: str, tpm_limit: int, api_key: str | None = None, account_tpm_limit: int | None = None):
        import os

        import openai

        # openai.OpenAI() only auto-reads OPENAI_API_KEY -- Groq's own env var
        # (GROQ_API_KEY) is not one it knows about, so it must be read explicitly here.
        api_key = api_key or os.environ.get("GROQ_API_KEY")
        self._client = openai.OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        self._model = model
        self._throttle = _TokenPerMinuteThrottle(tpm_limit)
        # The account's real hard ceiling -- a single request above this can never be
        # sent no matter how much rate-limit headroom is available. Defaults to
        # tpm_limit if not given separately (conservative: treats the throttle target
        # as the hard cap too).
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
    """Zero-cost stand-in for testing the escalation-tier plumbing. Never calls the
    optional lookup tool -- immediately submits a fixed, deliberately unopinionated
    decision (insufficient_evidence, empty candidate_ids) so the loop, JSON output
    shapes, and reporting can be exercised end-to-end with no network access and no
    claim of real reasoning. Not used for any grading/accuracy claim."""

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

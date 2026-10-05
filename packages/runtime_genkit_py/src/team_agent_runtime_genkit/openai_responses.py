"""Thin Genkit model adapter for OpenAI's Responses API."""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, Protocol, cast

from genkit import ActionRunContext, FinishReason, GenkitError, Role
from genkit.exp import Genkit
from genkit.model import (
    Message,
    ModelInfo,
    ModelRequest,
    ModelResponse,
    ModelResponseChunk,
    ModelUsage,
    Part,
    Supports,
)
from openai import AsyncOpenAI
from openai.types.responses import ResponseReasoningItem
from pydantic import ValidationError

DEFAULT_MODEL_ID = "gpt-6-astra"
MODEL_PREFIX = "openai-responses"
PHASE_METADATA_KEY = "openai_responses_phase"
REASONING_METADATA_KEY = "openai_responses_reasoning_item"
_PHASES = {"commentary", "final_answer"}
_REASONING_FIELDS = {
    "id",
    "summary",
    "type",
    "content",
    "encrypted_content",
    "status",
}
_SUMMARY_FIELDS = {"text", "type"}
_CONTENT_FIELDS = {"text", "type"}


class ResponsesResource(Protocol):
    async def create(self, **kwargs: Any) -> Any: ...


class ResponsesClient(Protocol):
    @property
    def responses(self) -> ResponsesResource: ...


def _role(role: Role | str) -> str:
    if role == Role.MODEL:
        return "assistant"
    if role == Role.SYSTEM:
        return "developer"
    if role == Role.USER:
        return "user"
    raise GenkitError(
        status="INVALID_ARGUMENT",
        message="OpenAI Responses adapter received an unsupported message role",
    )


def _flush_text(
    result: list[dict[str, Any]],
    role: Role | str,
    text_parts: list[str],
    phase: str | None,
) -> None:
    if text_parts:
        item = {"role": _role(role), "content": "".join(text_parts)}
        if phase is not None:
            if role != Role.MODEL or phase not in _PHASES:
                raise GenkitError(
                    status="INVALID_ARGUMENT",
                    message="OpenAI Responses adapter received an invalid message phase",
                )
            item["phase"] = phase
        result.append(item)
        text_parts.clear()


def _validate_reasoning_item(value: Any, *, provider_output: bool) -> dict[str, Any]:
    status: Literal["UNAVAILABLE", "INVALID_ARGUMENT"] = (
        "UNAVAILABLE" if provider_output else "INVALID_ARGUMENT"
    )
    message = (
        "OpenAI Responses returned an invalid reasoning item"
        if provider_output
        else "OpenAI Responses adapter received an invalid reasoning item"
    )
    if not isinstance(value, Mapping) or set(value) - _REASONING_FIELDS:
        raise GenkitError(status=status, message=message)
    for field, allowed in (("summary", _SUMMARY_FIELDS), ("content", _CONTENT_FIELDS)):
        nested = value.get(field)
        if nested is None and field == "content":
            continue
        if not isinstance(nested, list) or any(
            not isinstance(item, Mapping) or set(item) - allowed for item in nested
        ):
            raise GenkitError(status=status, message=message)
    try:
        validated = ResponseReasoningItem.model_validate(value)
    except ValidationError as error:
        raise GenkitError(status=status, message=message) from error
    return validated.model_dump(exclude_none=True)


def _reasoning_from_output(item: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(item, Mapping):
        raw = dict(item)
    else:
        raw = {
            field: getattr(item, field)
            for field in _REASONING_FIELDS
            if hasattr(item, field) and getattr(item, field) is not None
        }
    for field in ("summary", "content"):
        if field in raw and raw[field] is not None:
            raw[field] = [
                entry.model_dump(exclude_none=True)
                if hasattr(entry, "model_dump")
                else dict(entry)
                if isinstance(entry, Mapping)
                else {
                    name: getattr(entry, name) for name in ("type", "text") if hasattr(entry, name)
                }
                for entry in raw[field]
            ]
    serialized = _validate_reasoning_item(raw, provider_output=True)
    reasoning_text = "\n".join(entry["text"] for entry in serialized.get("summary", []))
    return reasoning_text, serialized


def _response_input(request: ModelRequest[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in request.messages:
        text_parts: list[str] = []
        text_phase: str | None = None

        for part in message.content:
            if part.reasoning is not None:
                _flush_text(result, message.role, text_parts, text_phase)
                text_phase = None
                metadata = part.metadata or {}
                if set(metadata) != {REASONING_METADATA_KEY}:
                    raise GenkitError(
                        status="INVALID_ARGUMENT",
                        message="OpenAI Responses adapter received invalid reasoning metadata",
                    )
                result.append(
                    _validate_reasoning_item(
                        metadata[REASONING_METADATA_KEY], provider_output=False
                    )
                )
                continue
            if any(
                value is not None
                for value in (
                    part.media,
                    part.data,
                    part.resource,
                    part.custom,
                )
            ):
                raise GenkitError(
                    status="INVALID_ARGUMENT",
                    message="OpenAI Responses adapter received an unsupported message part",
                )
            if part.text is not None:
                phase_value = (part.metadata or {}).get(PHASE_METADATA_KEY)
                if phase_value is not None and not isinstance(phase_value, str):
                    raise GenkitError(
                        status="INVALID_ARGUMENT",
                        message="OpenAI Responses adapter received an invalid message phase",
                    )
                if text_parts and phase_value != text_phase:
                    _flush_text(result, message.role, text_parts, text_phase)
                text_phase = phase_value
                text_parts.append(part.text)
            elif part.tool_request is not None:
                _flush_text(result, message.role, text_parts, text_phase)
                text_phase = None
                result.append(
                    {
                        "type": "function_call",
                        "call_id": part.tool_request.ref or part.tool_request.name,
                        "name": part.tool_request.name,
                        "arguments": json.dumps(
                            part.tool_request.input or {}, separators=(",", ":")
                        ),
                    }
                )
            elif part.tool_response is not None:
                _flush_text(result, message.role, text_parts, text_phase)
                text_phase = None
                result.append(
                    {
                        "type": "function_call_output",
                        "call_id": part.tool_response.ref or part.tool_response.name,
                        "output": json.dumps(part.tool_response.output, separators=(",", ":")),
                    }
                )
            else:
                raise GenkitError(
                    status="INVALID_ARGUMENT",
                    message="OpenAI Responses adapter received an unsupported message part",
                )
        _flush_text(result, message.role, text_parts, text_phase)
    return result


def _tools(request: ModelRequest[Any]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for tool in request.tools or []:
        schema = copy.deepcopy(tool.input_schema or {"type": "object", "properties": {}})
        strict = _is_strict_schema(schema)
        tools.append(
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": schema,
                "strict": strict,
            }
        )
    return tools


def _is_strict_schema(value: Any) -> bool:
    """Report strict compatibility without changing the caller's JSON Schema."""

    if isinstance(value, list):
        return all(_is_strict_schema(item) for item in value)
    if not isinstance(value, dict):
        return True
    strict = all(_is_strict_schema(item) for item in value.values())
    if value.get("type") == "object":
        properties = value.get("properties", {})
        if isinstance(properties, dict):
            required = value.get("required", [])
            strict = (
                strict
                and value.get("additionalProperties") is False
                and set(required) == set(properties)
            )
    return strict


def _text_config(request: ModelRequest[Any]) -> dict[str, Any] | None:
    schema = request.output_schema
    if schema is None:
        return None
    schema = copy.deepcopy(schema)
    if not _is_strict_schema(schema):
        raise GenkitError(
            status="INVALID_ARGUMENT",
            message=(
                "Structured output schema cannot be made strict without changing its semantics"
            ),
        )
    return {
        "format": {
            "type": "json_schema",
            "name": "genkit_output",
            "schema": schema,
            "strict": True,
        }
    }


def _model_response(response: Any) -> ModelResponse:
    status = getattr(response, "status", None)
    if status == "failed":
        raise GenkitError(status="UNAVAILABLE", message="OpenAI Responses request failed")
    if status not in {"completed", "incomplete"}:
        raise GenkitError(
            status="UNAVAILABLE", message="OpenAI Responses returned an unknown state"
        )
    parts: list[Part] = []
    refused = False
    for item in getattr(response, "output", []) or []:
        item_type = getattr(item, "type", None)
        if item_type == "function_call":
            raw_arguments = getattr(item, "arguments", "{}")
            try:
                arguments = json.loads(raw_arguments)
            except (json.JSONDecodeError, TypeError) as error:
                raise GenkitError(
                    status="INVALID_ARGUMENT",
                    message="OpenAI returned invalid tool arguments",
                ) from error
            parts.append(
                Part.from_tool_request(
                    name=item.name,
                    input=arguments,
                    ref=getattr(item, "call_id", None),
                )
            )
        elif item_type == "message":
            phase = getattr(item, "phase", None)
            if phase is not None and phase not in _PHASES:
                raise GenkitError(
                    status="UNAVAILABLE",
                    message="OpenAI Responses returned an unsupported message phase",
                )
            metadata = {PHASE_METADATA_KEY: phase} if phase is not None else None
            for content in getattr(item, "content", []) or []:
                content_type = getattr(content, "type", None)
                if content_type == "output_text":
                    parts.append(Part.from_text(getattr(content, "text", ""), metadata=metadata))
                elif content_type == "refusal":
                    refused = True
                    parts.append(Part.from_text(getattr(content, "refusal", ""), metadata=metadata))
                else:
                    raise GenkitError(
                        status="UNAVAILABLE",
                        message="OpenAI Responses returned unsupported message content",
                    )
        elif item_type == "reasoning":
            reasoning_text, reasoning_item = _reasoning_from_output(item)
            parts.append(
                Part(
                    reasoning=reasoning_text,
                    metadata={REASONING_METADATA_KEY: reasoning_item},
                )
            )
        else:
            raise GenkitError(
                status="UNAVAILABLE",
                message="OpenAI Responses returned an unsupported output item",
            )
    if not parts and getattr(response, "output_text", None):
        parts.append(Part.from_text(response.output_text))
    finish_reason = FinishReason.STOP
    finish_message: str | None = None
    if refused:
        finish_reason = FinishReason.BLOCKED
        finish_message = "OpenAI declined to provide the requested output"
    elif status == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        if reason == "max_output_tokens":
            finish_reason = FinishReason.LENGTH
            finish_message = "OpenAI response reached the configured output limit"
        elif reason == "content_filter":
            finish_reason = FinishReason.BLOCKED
            finish_message = "OpenAI response was blocked"
        else:
            finish_reason = FinishReason.OTHER
            finish_message = "OpenAI response was incomplete"
    usage = getattr(response, "usage", None)
    mapped_usage = None
    if usage is not None:
        mapped_usage = ModelUsage(
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
            cached_content_tokens=getattr(
                getattr(usage, "input_tokens_details", None), "cached_tokens", None
            ),
            thoughts_tokens=getattr(
                getattr(usage, "output_tokens_details", None), "reasoning_tokens", None
            ),
        )
    return ModelResponse(
        message=Message(role=Role.MODEL, content=parts),
        finish_reason=finish_reason,
        finish_message=finish_message,
        usage=mapped_usage,
    )


def _request_options(model_id: str, request: ModelRequest[Any], *, stream: bool) -> dict[str, Any]:
    options: dict[str, Any] = {
        "model": model_id,
        "input": _response_input(request),
        "store": False,
        "stream": stream,
        "include": ["reasoning.encrypted_content"],
    }
    tools = _tools(request)
    if tools:
        options["tools"] = tools
    text = _text_config(request)
    if text is not None:
        options["text"] = text
    config = request.config
    if config is not None and not isinstance(config, Mapping):
        model_dump = getattr(config, "model_dump", None)
        if model_dump is None:
            raise GenkitError(
                status="INVALID_ARGUMENT", message="Unsupported OpenAI Responses config"
            )
        config = model_dump(exclude_none=True)
    if isinstance(config, Mapping):
        unknown = set(config) - {"max_output_tokens", "reasoning_effort"}
        if unknown:
            raise GenkitError(
                status="INVALID_ARGUMENT",
                message="OpenAI Responses config contains unsupported fields",
            )
        reasoning_effort = config.get("reasoning_effort")
        if reasoning_effort is not None:
            if reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}:
                raise GenkitError(status="INVALID_ARGUMENT", message="Invalid reasoning_effort")
            options["reasoning"] = {"effort": reasoning_effort}
        max_output_tokens = config.get("max_output_tokens")
        if max_output_tokens is not None:
            if (
                isinstance(max_output_tokens, bool)
                or not isinstance(max_output_tokens, int)
                or not 1 <= max_output_tokens <= 128_000
            ):
                raise GenkitError(status="INVALID_ARGUMENT", message="Invalid max_output_tokens")
            options["max_output_tokens"] = max_output_tokens
    if request.tool_choice is not None:
        options["tool_choice"] = request.tool_choice.value
    return options


async def _stream_response(
    stream: Any, context: ActionRunContext[ModelResponseChunk]
) -> ModelResponse:
    final_response: Any | None = None
    iterator = aiter(stream)
    try:
        while True:
            try:
                event = await _await_or_abort(anext(iterator), context.abort_signal)
            except StopAsyncIteration:
                break
            event_type = getattr(event, "type", None)
            if event_type == "response.output_text.delta":
                context.send_chunk(
                    ModelResponseChunk(role=Role.MODEL, content=[Part.from_text(event.delta)])
                )
            elif event_type in {"response.completed", "response.incomplete"}:
                final_response = event.response
            elif event_type in {"error", "response.failed"}:
                raise GenkitError(status="UNAVAILABLE", message="OpenAI Responses request failed")
    finally:
        await _close(stream)
    if final_response is None:
        raise GenkitError(
            status="UNAVAILABLE",
            message="OpenAI Responses stream ended without completion",
        )
    return _model_response(final_response)


async def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if close is not None:
        closed = close()
        if inspect.isawaitable(closed):
            await closed


async def _await_or_abort(
    awaitable: Awaitable[Any],
    abort_signal: asyncio.Event,
) -> Any:
    provider_task = asyncio.ensure_future(awaitable)
    abort_task = asyncio.create_task(abort_signal.wait())
    try:
        done, _pending = await asyncio.wait(
            {provider_task, abort_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if abort_task in done and abort_signal.is_set():
            provider_task.cancel()
            await asyncio.gather(provider_task, return_exceptions=True)
            raise asyncio.CancelledError
        return await provider_task
    except asyncio.CancelledError:
        provider_task.cancel()
        await asyncio.gather(provider_task, return_exceptions=True)
        raise
    finally:
        abort_task.cancel()
        await asyncio.gather(abort_task, return_exceptions=True)


def define_openai_responses_model(
    ai: Genkit,
    *,
    model_id: str = DEFAULT_MODEL_ID,
    client: Any | None = None,
    usage_observer: Callable[[ModelUsage], None] | None = None,
    request_observer: Callable[[ModelRequest[Any]], None] | None = None,
) -> str:
    """Register a Responses-backed Genkit model and return its registry name."""

    if not model_id or "/" in model_id:
        raise ValueError("model_id must be an explicit unprefixed OpenAI API model ID")
    responses_client = cast(ResponsesClient, client or AsyncOpenAI())
    name = f"{MODEL_PREFIX}/{model_id}"

    async def model_fn(
        request: ModelRequest[Any],
        context: ActionRunContext[ModelResponseChunk],
    ) -> ModelResponse:
        try:
            if request_observer is not None:
                request_observer(request)
            response = await _await_or_abort(
                responses_client.responses.create(
                    **_request_options(model_id, request, stream=context.is_streaming)
                ),
                context.abort_signal,
            )
            if context.is_streaming:
                result = await _stream_response(response, context)
            else:
                result = _model_response(response)
            if usage_observer is not None and result.usage is not None:
                usage_observer(result.usage)
            return result
        except asyncio.CancelledError:
            raise
        except GenkitError:
            raise
        except Exception as error:
            raise GenkitError(
                status="UNAVAILABLE", message="OpenAI Responses request failed"
            ) from error

    ai.define_model(
        name=name,
        fn=model_fn,
        info=ModelInfo(
            label=f"OpenAI Responses {model_id}",
            supports=Supports(
                multiturn=True,
                tools=True,
                system_role=True,
                output=["text", "json"],
            ),
        ),
    )
    return name

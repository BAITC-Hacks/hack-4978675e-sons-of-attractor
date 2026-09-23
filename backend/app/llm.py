"""Small shared transport for OpenAI Responses and Anthropic Messages.

No credentials, provider response bodies or user prompts are logged. Models have
no tools and cannot access files or change the deterministic selection result.
"""
import asyncio
import json
import logging
from copy import deepcopy

import httpx

from .core.config import ModelConfig

logger = logging.getLogger(__name__)
AI_TIMEOUT_SECONDS = 8


class ProviderError(RuntimeError):
    pass


def portable_schema(schema: dict) -> dict:
    """Keep a portable strict schema; Pydantic enforces extra bounds locally."""
    schema = deepcopy(schema)

    def visit(node):
        if isinstance(node, dict):
            for key in ("default", "title", "minLength", "maxLength", "minimum", "maximum",
                        "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems", "format"):
                node.pop(key, None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    return schema


async def generate_json(config: ModelConfig, instructions: str, data: dict, schema: dict, name: str) -> dict:
    content = json.dumps(data, ensure_ascii=False)
    schema = portable_schema(schema)
    if config.provider == "openai":
        url = "https://api.openai.com/v1/responses"
        headers = {"Authorization": f"Bearer {config.api_key}"}
        payload = {
            "model": config.model, "store": False, "instructions": instructions,
            "input": content, "max_output_tokens": 4096,
            "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
        }
        if config.model.startswith(("gpt-5", "gpt-6")):
            payload["reasoning"] = {"effort": "low"}
    elif config.provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": config.api_key, "anthropic-version": "2023-06-01"}
        payload = {
            "model": config.model, "max_tokens": 4096, "system": instructions,
            "messages": [{"role": "user", "content": content}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
    else:
        raise ProviderError("Unsupported provider")

    async def request():
        async with httpx.AsyncClient(timeout=httpx.Timeout(AI_TIMEOUT_SECONDS, connect=2), follow_redirects=False) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
        if config.provider == "openai":
            if result.get("status") != "completed":
                raise ValueError("Incomplete response")
            blocks = [block for item in result.get("output", []) if item.get("type") == "message"
                      for block in item.get("content", [])]
            if any(block.get("type") == "refusal" for block in blocks):
                raise ValueError("Refusal")
            text = "".join(block["text"] for block in blocks if block.get("type") == "output_text")
        else:
            if result.get("stop_reason") != "end_turn":
                raise ValueError("Incomplete response")
            text = "".join(block["text"] for block in result.get("content", []) if block.get("type") == "text")
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("Expected an object")
        return value

    try:
        return await asyncio.wait_for(request(), timeout=AI_TIMEOUT_SECONDS)
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, AttributeError) as exc:
        # Exception messages from HTTP clients can contain URLs or response data.
        logger.warning("AI request failed: provider=%s reason=%s", config.provider, type(exc).__name__)
        raise ProviderError("AI provider unavailable or returned an invalid response") from None

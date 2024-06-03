import asyncio
import aiohttp
import json
import dataclasses
import codecs
import json
import os
from typing import Any, Dict, MutableSet
from attrs import define
import logging
from livekit.agents import llm

from .models import ChatModels

@define
class LLMOptions:
    model: str | ChatModels
    base_url: str
    api_key: str


class LLM(llm.LLM):
    def __init__(
        self,
        *,
        model: str | ChatModels = "qwen-plus",
        api_key: str = "",
        base_url: str = "",
    ) -> None:
        api_key = api_key or os.environ.get("LLM_API_KEY")
        base_url = base_url or os.environ.get("LLM_BASE_URL") 
        if not api_key or not base_url:
            dashscope_api_key = os.environ.get("DASHSCOPE_API_KEY")
            if not dashscope_api_key:
              raise ValueError("both DASHSCOPE_API_KEY or other  llm base_url and api_key must be set")
            else:
                self._opts = LLMOptions(model=model,base_url="xxxx",api_key=dashscope_api_key)
        else: 
            self._opts = LLMOptions(model=model,base_url=base_url,api_key=api_key)
        self._running_fncs: MutableSet[asyncio.Task] = set()
    async def fetch_stream(self, url, headers, data):
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=json.dumps(data), timeout=60000) as resp:
                lines = []
                async for block in resp.content.iter_any():
                    lines = block.decode('utf-8').splitlines(keepends=False)
                    for line in lines:
                        if line:
                            yield line.encode('utf-8')
    async def chat(
        self,
        history: llm.ChatContext,
        fnc_ctx: llm.FunctionContext | None = None,
        temperature: float | None = None,
        n: int | None = None,
    ) -> "LLMStream":
        llm_config = self._opts
        url = llm_config.base_url
        headers = {
            'Authorization': f"Bearer {llm_config.api_key}",
            'Content-Type': 'application/json'
        }

        req_data = {
            "messages": to_openai_ctx(history),
            "stream": True,
            "max_tokens": 50
        }
        async_gen = self.fetch_stream(url, headers, req_data)
        return LLMStream(async_gen, fnc_ctx)


class LLMStream(llm.LLMStream):
    def __init__(
        self, oai_stream, fnc_ctx: llm.FunctionContext | None
    ) -> None:
        super().__init__()
        self._oai_stream = oai_stream
        self._fnc_ctx = fnc_ctx
        self._running_fncs: MutableSet[asyncio.Task] = set()

    def __aiter__(self) -> "LLMStream":
        return self

 
    async def __anext__(self) -> llm.ChatChunk:
        answer = ""
        async for line in self._oai_stream:
            if line:
                line = codecs.decode(line)
                if line.startswith("data:"):
                    line = line[5:].strip()
                    try:
                        chunk = json.loads(line)
                        if "choices" in chunk and len(
                                chunk["choices"]) > 0 and "delta" in chunk["choices"][0] and "content" in chunk["choices"][0]["delta"]:
                            delta = chunk["choices"][0]["delta"]
                            answer += delta["content"]
                            return llm.ChatChunk(
                                choices=[
                                    llm.Choice(
                                        delta=llm.ChoiceDelta(
                                            content=delta["content"],
                                            role='assistant',
                                        ),
                                        index=0,
                                    )
                                ]
                            )
                    except json.JSONDecodeError as err:
                        # logging.info(f"error : {err}")
                        raise StopAsyncIteration
            else:
                raise StopAsyncIteration
        raise StopAsyncIteration

    async def aclose(self, wait: bool = True) -> None:
        if not wait:
            for task in self._running_fncs:
                task.cancel()

        await asyncio.gather(*self._running_fncs, return_exceptions=True)


def to_openai_ctx(chat_ctx: llm.ChatContext) -> list:
    return [
        {
            "role": msg.role.value,
            "content": msg.text,
        }
        for msg in chat_ctx.messages
    ]


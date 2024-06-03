import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass
from typing import Any, AsyncIterable, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor
import aiohttp
from livekit import rtc
from livekit.agents import tts
from .models import TTSModels
from dashscope.api_entities.dashscope_response import SpeechSynthesisResponse
from dashscope.audio.tts import ResultCallback, SpeechSynthesizer, SpeechSynthesisResult





@dataclass
class Voice:
    id: str
    name: str
    category: str
    settings: Optional["VoiceSettings"] = None


@dataclass
class VoiceSettings:
    stability: float  # [0.0 - 1.0]
    similarity_boost: float  # [0.0 - 1.0]
    style: Optional[float] = None  # [0.0 - 1.0]
    use_speaker_boost: Optional[bool] = False


# DEFAULT_VOICE = Voice(
#     id="EXAVITQu4vr4xnSDxMaL",
#     name="Bella",
#     category="premade",
#     settings=VoiceSettings(
#         stability=0.71, similarity_boost=0.5, style=0.0, use_speaker_boost=True
#     ),
# )

STREAM_EOS = "EOS"


@dataclass
class TTSOptions:
    api_key: str
    model_id: TTSModels
    base_url: str
    sample_rate: int
    latency: int
class Callback(ResultCallback):
    def __init__(self, _tts: tts.SynthesizeStream):
        self._tts = _tts
    def on_open(self):
        logging.info("Speech synthesizer is opened.")


    def on_complete(self):
        logging.info("Speech synthesizer is completed.")
        
        
    def on_error(self, response: SpeechSynthesisResponse):
        logging.info('Speech synthesizer failed, response is %s' % (str(response)))
        self._tts._queue.task_done()
    def on_close(self):
        logging.info('Speech synthesizer is closed.')
        self._tts._queue.task_done()

    def on_event(self, result: SpeechSynthesisResult):
        audio_frame = result.get_audio_frame()
        if  audio_frame is not None:
            audio_frame = rtc.AudioFrame(
                data=audio_frame,
                sample_rate=24000,
                num_channels=1,
                samples_per_channel=len(audio_frame) // 2,
            )
            
            self._tts._event_queue.put_nowait(
                tts.SynthesisEvent(
                    type=tts.SynthesisEventType.AUDIO,
                    audio=tts.SynthesizedAudio(text="", data=audio_frame),
                )
            )
            
            
class TTS(tts.TTS):
    def __init__(
        self,
        *,
        model_id: TTSModels = "sambert-zhichu-v1",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        sample_rate: int = 24000,
        num_channels: int = 1,
        latency: int = 2,
    ) -> None:
        super().__init__(streaming_supported=True, sample_rate=sample_rate, num_channels=num_channels)
        api_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY must be set")

        self._config = TTSOptions(
            model_id=model_id,
            api_key=api_key,
            base_url=base_url,
            sample_rate=sample_rate,
            latency=latency,
        )


    def synthesize(
        self,
        text: str,
    ) -> AsyncIterable[tts.SynthesizedAudio]:

        async def generator():
            pass

        return generator()

    def stream(
        self,
    ) -> "SynthesizeStream":
        return SynthesizeStream(self._config)


class SynthesizeStream(tts.SynthesizeStream):
    def __init__(
        self,
        config: TTSOptions,
    ):
        self._config = config
        self._executor = ThreadPoolExecutor()
        self._queue = asyncio.Queue[str]()
        self._tts_queue = asyncio.Queue[str]()
        self._event_queue = asyncio.Queue[tts.SynthesisEvent]()
        self._closed = False

        self._main_task = asyncio.create_task(self._run())

        def log_exception(task: asyncio.Task) -> None:
            if not task.cancelled() and task.exception():
                logging.error(f"dashscope synthesis task failed: {task.exception()}")

        self._main_task.add_done_callback(log_exception)
        self._text = ""


    def push_text(self, token: str | None) -> None:
        if self._closed:
            raise ValueError("cannot push to a closed stream")

        if not token or len(token) == 0:
            return
        self._text += token
    async def _run(self) -> None:
        callback = Callback(self)
        started = False
        while True:
            try:
                text = None
                text = await self._queue.get()
                tts_str = str(text).strip()
                logging.info(f"stt text: {tts_str}")
                if not started:
                        self._event_queue.put_nowait(
                            tts.SynthesisEvent(type=tts.SynthesisEventType.STARTED)
                        )
                        started = True
                if(tts_str != STREAM_EOS and tts_str != ""):
                    loop = asyncio.get_event_loop()
                    loop.run_in_executor(
                        self._executor, 
                        lambda: SpeechSynthesizer.call(
                            model='sambert-zhiwei-v1',
                            text=tts_str,
                            sample_rate=24000,
                            rate=1,
                            format='pcm',
                            callback=callback,
                        ),
                    ) 
                elif tts_str == STREAM_EOS:
                    self._event_queue.put_nowait(
                        tts.SynthesisEvent(type=tts.SynthesisEventType.FINISHED)
                    )
                    break
                elif tts_str == "":
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(e)

    async def end(self) -> None:
        self._queue.put_nowait(STREAM_EOS)
    async def flush(self) -> None:
        _text = self._text
        self._queue.put_nowait(_text)
        self._text = ""
            

    async def aclose(self) -> None:
        self._main_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._main_task

    async def __anext__(self) -> tts.SynthesisEvent:
        if self._closed and self._event_queue.empty():
            raise StopAsyncIteration

        return await self._event_queue.get()




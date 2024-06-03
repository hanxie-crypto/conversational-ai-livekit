import asyncio
import dataclasses
import json
import logging
import os
from dashscope.audio.asr import (Recognition, RecognitionCallback, RecognitionResult)
from contextlib import suppress
from dataclasses import dataclass
from typing import List
import aiohttp
from livekit import rtc
from livekit.agents import stt
from livekit.agents.utils import AudioBuffer

from .models import STTModels


class Callback(RecognitionCallback):
    def __init__(self, _stt: stt.SpeechStream):
        self._stt = _stt
        self._sentence = ''
    def on_open(self) -> None:
        self._sentence = ''
        start_event = stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH)
        self._stt._event_queue.put_nowait(start_event)
    def on_error(self, result: RecognitionResult) -> None:
        # 错误处理
        self._stt._end_speech()
    def on_close(self) -> None:
        logging.info("speech stream closed")

    def on_event(self, result: RecognitionResult) -> None:
    
        try:
            self._sentence = result.get_sentence()
            dg_alts = live_transcription_to_speech_data(self._stt._config.language, self._sentence)
            if(result.is_sentence_end(self._sentence) == False):
                interim_event = stt.SpeechEvent(
                        type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                        alternatives=dg_alts,
                    )
                self._stt._event_queue.put_nowait(interim_event)
            else:
                final_event = stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                    alternatives=dg_alts,
                )
                self._stt._event_queue.put_nowait(final_event)
                # self._stt._end_speech()
        except Exception as e:
            pass
        




@dataclass
class STTOptions:
    language: str | None
    detect_language: bool
    interim_results: bool
    punctuate: bool
    model: STTModels
    smart_format: bool
    endpointing: int | None


class STT(stt.STT):
    def __init__(
        self,
        *,
        language = "zh-CN",
        detect_language: bool = False,
        interim_results: bool = True,
        punctuate: bool = True,
        smart_format: bool = True,
        model: STTModels = "paraformer-realtime-v1",
        api_key: str | None = None,
        min_silence_duration: int = 100,  # 100ms for a RTC app seems like a strong default
    ) -> None:
        super().__init__(streaming_supported=True)
        api_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        if api_key is None:
            raise ValueError("DASHSCOPE API key is required")
        self._api_key = api_key
        self._config = STTOptions(
            language=language,
            detect_language=detect_language,
            interim_results=interim_results,
            punctuate=punctuate,
            model=model,
            smart_format=smart_format,
            endpointing=min_silence_duration,
        )

    async def recognize(
        self,
        *,
        buffer: AudioBuffer,
        language:  str | None = None,
    ) -> stt.SpeechEvent:
        pass

    def stream(
        self,
        *,
        language:  str | None = None,
    ) -> "SpeechStream":
        config = self._sanitize_options(language=language)
        return SpeechStream(config, api_key=self._api_key)

    def _sanitize_options(
        self,
        *,
        language: str | None = None,
    ) -> STTOptions:
        config = dataclasses.replace(self._config)
        config.language = language or config.language

        if config.detect_language:
            config.language = None

        return config


class SpeechStream(stt.SpeechStream):
    _KEEPALIVE_MSG: str = json.dumps({"type": "KeepAlive"})
    _CLOSE_MSG: str = json.dumps({"type": "CloseStream"})

    def __init__(
        self,
        config: STTOptions,
        api_key: str,
        sample_rate: int = 16000,
        num_channels: int = 1
    ) -> None:
        super().__init__()

        if config.language is None:
            raise ValueError("language detection is not supported in streaming mode")
        self._config = config
        self._sample_rate = sample_rate
        self._num_channels = num_channels
        self._api_key = api_key
        self._speaking = False
        callback = Callback(self)
        self.recognition = Recognition(model='paraformer-realtime-v1',
                          format='pcm',
                          sample_rate=16000,
                          callback=callback)
        # self._session = aiohttp.ClientSession()
        self._queue = asyncio.Queue[rtc.AudioFrame | str]()
        self._event_queue = asyncio.Queue[stt.SpeechEvent | None]()
        self._closed = False
        self._main_task = asyncio.create_task(self._run())

        # keep a list of final transcripts to combine them inside the END_OF_SPEECH event
        self._final_events: List[stt.SpeechEvent] = []

        def log_exception(task: asyncio.Task) -> None:
            if not task.cancelled() and task.exception():
                logging.error(f"dashscope task failed: {task.exception()}")

        self._main_task.add_done_callback(log_exception)

    def push_frame(self, frame: rtc.AudioFrame) -> None:
        if self._closed:
            raise ValueError("cannot push frame to closed stream")

        self._queue.put_nowait(frame)

    async def aclose(self, wait: bool = True) -> None:
        self._closed = True
        self._queue.put_nowait(SpeechStream._CLOSE_MSG)

        if not wait:
            self._main_task.cancel()

        with suppress(asyncio.CancelledError):
            await self._main_task

        # await self._session.close()

    async def _run(self) -> None:
        self.recognition.start()
        while not self._closed:
            try:
                data = await self._queue.get()
                if isinstance(data, rtc.AudioFrame):
                    data_bytes = data._data
                    # TODO(theomonnom): The remix_and_resample method is low quality
                    # and should be replaced with a continuous resampling
                    frame = data.remix_and_resample(
                        self._sample_rate, self._num_channels
                    )
                    self.recognition.send_audio_frame(frame.data.tobytes()) 
                       
                elif data == SpeechStream._CLOSE_MSG:
                    self.recognition.stop()
                    break
                # await self._run_exe(recognition)
            except Exception as e:
                logging.error(f"error: {e}")
                self.recognition.start()
                await asyncio.sleep(20)

    async def _run_exe(self, recognition) -> None:
        async def send_task():
            while True:
                try:
                    data = await self._queue.get()
                    self._queue.task_done()
                    if isinstance(data, rtc.AudioFrame):
                        # TODO(theomonnom): The remix_and_resample method is low quality
                        # and should be replaced with a continuous resampling
                        frame = data.remix_and_resample(
                            self._sample_rate, self._num_channels
                        )
                        recognition.send_audio_frame(frame.data.tobytes())
                    elif data == SpeechStream._CLOSE_MSG:
                        recognition.stop()
                        break
                except Exception as e:
                    logging.error(f"STT ERROR: {e}")

        await asyncio.gather(send_task())

    def _end_speech(self) -> None:
        if not self._speaking:
            logging.warning(
                "trying to commit final events without being in the speaking state"
            )
            return

        if len(self._final_events) == 0:
            logging.warning("received end of speech without any final transcription")
            return

        self._speaking = False

        # combine all final transcripts since the start of the speech
        sentence = ""
        confidence = 0.0
        for alt in self._final_events:
            sentence += f"{alt.alternatives[0].text.strip()} "
            confidence += alt.alternatives[0].confidence

        sentence = sentence.rstrip()
        confidence /= len(self._final_events)  # avg. of confidence

        end_event = stt.SpeechEvent(
            type=stt.SpeechEventType.END_OF_SPEECH,
            alternatives=[
                stt.SpeechData(
                    language=str(self._config.language),
                    start_time=self._final_events[0].alternatives[0].start_time,
                    end_time=self._final_events[-1].alternatives[0].end_time,
                    confidence=confidence,
                    text=sentence,
                )
            ],
        )
        self._event_queue.put_nowait(end_event)
        self._final_events = []

        

    async def __anext__(self) -> stt.SpeechEvent:
        evt = await self._event_queue.get()
        if evt is None:
            raise StopAsyncIteration
        return evt


def live_transcription_to_speech_data(
    language: str,
    data,
) -> List[stt.SpeechData]:
  
    return [
         stt.SpeechData(
            language=language,
            start_time=data['begin_time'],
            end_time=data['end_time'],
            confidence=0.0,
            text=data['text'],
        )
    ]



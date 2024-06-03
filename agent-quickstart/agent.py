import asyncio
import json
import logging
from livekit import agents, rtc
from livekit.agents import (
    JobContext,
    JobRequest,
    WorkerOptions,
    cli,
)
from inference_job import EventType, InferenceJob
from alibabacloud.stt import STT
from state_manager import StateManager

SIP_INTRO = ""

PROMPT = "你是一个有用的助手"
INTRO = "你好，我是阿里云函数计算小助手"


async def entrypoint(job: JobContext):
    # LiveKit Entities
    source = rtc.AudioSource(24000, 1)
    track = rtc.LocalAudioTrack.create_audio_track("agent-mic", source)
    options = rtc.TrackPublishOptions()
    options.source = rtc.TrackSource.SOURCE_MICROPHONE

    # Plugins
    stt = STT()
    stt_stream = stt.stream()

    # Agent state
    state = StateManager(job.room, PROMPT)
    inference_task: asyncio.Task | None = None
    current_transcription = ""

    audio_stream_future = asyncio.Future[rtc.AudioStream]()
    async def start_new_inference(force_text: str | None = None):
        logging.info("start a new inference")
        nonlocal current_transcription

        state.agent_thinking = True
        job = InferenceJob(
            transcription=current_transcription,
            audio_source=source,
            chat_history=state.chat_history,
            force_text_response=force_text,
        )

        try:
            agent_done_thinking = False
            agent_has_spoken = False
            comitted_agent = False

            def commit_agent_text_if_needed():
                nonlocal agent_has_spoken, agent_done_thinking, comitted_agent
                if agent_done_thinking and agent_has_spoken and not comitted_agent:
                    comitted_agent = True
                    state.commit_agent_response(job.current_response)

            async for e in job:
                # Allow cancellation
                if e.type == EventType.AGENT_RESPONSE:
                    if e.finished_generating:
                        state.agent_thinking = False
                        agent_done_thinking = True
                        commit_agent_text_if_needed()
                    
                elif e.type == EventType.AGENT_SPEAKING:
                    state.agent_speaking = e.speaking
                    if e.speaking:
                        agent_has_spoken = True
                        # Only commit user text for real transcriptions
                        if not force_text:
                            state.commit_user_transcription(job.transcription)
                        commit_agent_text_if_needed()
                        current_transcription = ""
        except asyncio.CancelledError:
            logging.info("Cancelled")
            await job.acancel()
    def on_track_subscribed(track: rtc.Track, *_):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            audio_stream_future.set_result(rtc.AudioStream(track))

    async def on_data(dp: rtc.DataPacket):
        nonlocal current_transcription
        
        # Ignore if the agent is speaking
        if state.agent_speaking:
            return
        if dp.topic != "lk-chat-topic":
            return
        payload = json.loads(dp.data) 
        message = payload["message"]
        current_transcription = message
        inference_task = asyncio.create_task(start_new_inference())
        await inference_task

    for participant in job.room.participants.values():
        for track_pub in participant.tracks.values():
            # This track is not yet subscribed, when it is subscribed it will
            # call the on_track_subscribed callback
            if track_pub.track is None:
                continue
            audio_stream_future.set_result(rtc.AudioStream(track_pub.track))

    job.room.on("track_subscribed", on_track_subscribed)
    job.room.on("data_received", on_data)

    # Wait for user audio
    audio_stream = await audio_stream_future

    # Publish agent mic after waiting for user audio (simple way to avoid subscribing to self)
    await job.room.local_participant.publish_track(track, options)

    
    # rtc语音流过来
    async def audio_stream_task():
        async for audio_frame_event in audio_stream:
            stt_stream.push_frame(audio_frame_event.frame)
    # 语音转文本
    async def stt_stream_task():
        nonlocal current_transcription, inference_task
        async for stt_event in stt_stream:
            # We eagerly try to run inference to keep the latency as low as possible.
            # If we get a new transcript, we update the working text, cancel in-flight inference,
            # and run new inference.
            if stt_event.type == agents.stt.SpeechEventType.FINAL_TRANSCRIPT:
                delta = stt_event.alternatives[0].text
                # Do nothing
                if delta == "":
                    continue
                current_transcription += delta
                # Cancel in-flight inference
                if inference_task:
                    inference_task.cancel()
                    await inference_task
                # Start new inference
                inference_task = asyncio.create_task(start_new_inference())
                await inference_task

    try:
        # sip = job.room.name.startswith("sip")
        # intro_text = SIP_INTRO if sip else INTRO
        # inference_task = asyncio.create_task(start_new_inference(force_text=intro_text))
        async with asyncio.TaskGroup() as tg:
            tg.create_task(audio_stream_task())
            tg.create_task(stt_stream_task())
    except BaseExceptionGroup as e:
        for exc in e.exceptions:
            logging.error(f"exception: {exc}")
    except Exception as e:
        logging.error(f"common exception: {e}")


async def request_fnc(req: JobRequest) -> None:
    await req.accept(entrypoint, auto_subscribe=agents.AutoSubscribe.AUDIO_ONLY)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(request_fnc=request_fnc,port=8080))
import os
import json
import base64
import asyncio
import websockets
import config
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from s3_client import s3_client  # Import the S3 client object



# Configuration
OPENAI_API_KEY = config.OPENAI_API_KEY
PORT = int(os.getenv('PORT', 8000))
SYSTEM_MESSAGE = (
    "You are a helpful and bubbly AI assistant who loves to chat about "
    "anything the user is interested in and is prepared to offer them facts. "
    "You have a penchant for dad jokes, owl jokes, and rickrolling – subtly. "
    "Always stay positive, but work in a joke when appropriate."
)
VOICE = 'alloy'
LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated',
    'response.done', 'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped', 'input_audio_buffer.speech_started',
    'session.created'
]
SHOW_TIMING_MATH = False

app = FastAPI()

S3_BUCKET_NAME = "audio-calls-info"


if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    response = VoiceResponse()
    # <Say> punctuation to improve text-to-speech flow
    response.say("Welcome to Fremont Park Golf course. I'm the AI assistant. What can I do for you?")

    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
    print("Client connected")
    await websocket.accept()

    # headers = {
    #     "Authorization": f"Bearer {OPENAI_API_KEY}",
    #     "OpenAI-Beta": "realtime=v1"
    # }
    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17',
        extra_headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        
        async def initialize_session(openai_ws):
            """Control initial session with OpenAI."""
            session_update = {
                "type": "session.update",
                "session": {
                    "turn_detection": {"type": "server_vad"},
                    "input_audio_format": "g711_ulaw",
                    "output_audio_format": "g711_ulaw",
                    "voice": VOICE,
                    "instructions": SYSTEM_MESSAGE,
                    "modalities": ["text", "audio"],
                    "temperature": 0.8,
                }
            }
            print('Sending session update:', json.dumps(session_update))
            await openai_ws.send(json.dumps(session_update))

        await initialize_session(openai_ws)

        async def send_mark(connection, stream_sid):
            if stream_sid:
                mark_event = {
                    "event": "mark",
                    "streamSid": stream_sid,
                    "mark": {"name": "responsePart"}
                }
                await connection.send_json(mark_event)
                mark_queue.append('responsePart')

        # Connection specific state
        stream_sid = None
        latest_media_timestamp = 0
        last_assistant_item = None
        mark_queue = []
        response_start_timestamp_twilio = None
        s3_initialized = False  # Shared across all calls to receive_from_twilio
        upload_id = None
        
        # if stream_sid is None:
        #     raise ValueError("stream_sid is None. Cannot initialize S3 multipart upload.")
        
        # S3 multipart upload specific state
        # combined_audio_upload = s3_client.create_multipart_upload(
        #     Bucket=S3_BUCKET_NAME, Key=f"{stream_sid}_combined_audio.raw"
        # )
        # upload_id = combined_audio_upload["UploadId"]

        part_number = 1  # Start with part number 1
        parts = []  # Metadata for uploaded parts
        combined_audio_buffer = bytearray()
        
        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal s3_initialized, stream_sid, latest_media_timestamp, combined_audio_buffer, upload_id
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)

                    if data['event'] == 'media' and openai_ws.open:
                        latest_media_timestamp = int(data['media']['timestamp'])

                        audio_chunk = base64.b64decode(data['media']['payload'])
                        combined_audio_buffer.extend(audio_chunk)  # Append Twilio audio to buffer
                        
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        await openai_ws.send(json.dumps(audio_append))

                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Incoming stream has started {stream_sid}")

                        # Initialize S3 multipart upload only once
                        if not s3_initialized:
                            combined_audio_upload = s3_client.create_multipart_upload(
                                Bucket=S3_BUCKET_NAME,
                                Key=f"{stream_sid}_combined_audio.raw"
                            )

                        upload_id = combined_audio_upload["UploadId"]
                        print(f"S3 multipart upload initialized with Key: {stream_sid}_combined_audio.raw")

                        #response_start_timestamp_twilio = None
                        latest_media_timestamp = 0
                        #last_assistant_item = None

                    elif data['event'] == 'mark':
                        if mark_queue:
                            mark_queue.pop(0)

            except WebSocketDisconnect:
                print("Twilio WebSocket disconnected.")
            except Exception as e:
                print(f"Error in receive_from_twilio: {e}")
                if openai_ws.open:
                    print("Closing OpenAI WebSocket due to error in receive_from_twilio.")
                    await openai_ws.close()

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    if response['type'] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)

                    if response.get('type') == 'response.audio.delta' and 'delta' in response:
                        audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                        audio_delta = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": audio_payload
                            }
                        }
                        await websocket.send_json(audio_delta)

                        if response_start_timestamp_twilio is None:
                            response_start_timestamp_twilio = latest_media_timestamp
                            if SHOW_TIMING_MATH:
                                print(f"Setting start timestamp for new response: {response_start_timestamp_twilio}ms")

                        # Update last_assistant_item safely
                        if response.get('item_id'):
                            last_assistant_item = response['item_id']

                        await send_mark(websocket, stream_sid)

                    # Trigger an interruption. Your use case might work better using `input_audio_buffer.speech_stopped`, or combining the two.
                    if response.get('type') == 'input_audio_buffer.speech_started':
                        print("Speech started detected.")
                        if last_assistant_item:
                            print(f"Interrupting response with id: {last_assistant_item}")
                            await handle_speech_started_event()
            except Exception as e:
                print(f"Error in send_to_twilio: {e}")

        async def stream_combined_audio_to_s3():
            """Stream the combined audio buffer to S3."""
            nonlocal combined_audio_buffer, part_number, parts
            try:
                while True:
                    if len(combined_audio_buffer) >= 5 * 1024 * 1024:  # Stream every 5 MB
                        chunk = combined_audio_buffer[:5 * 1024 * 1024]
                        combined_audio_buffer = combined_audio_buffer[5 * 1024 * 1024:]  # Trim buffer

                        response = s3_client.upload_part(
                            Bucket=S3_BUCKET_NAME,
                            Key="{stream_sid}_combined_audio.raw",
                            PartNumber=part_number,
                            UploadId=upload_id,
                            Body=chunk,
                        )
                        parts.append({"PartNumber": part_number, "ETag": response["ETag"]})
                        part_number += 1

                    await asyncio.sleep(1)  # Check periodically for new data

            except Exception as e:
                print(f"Error in stream_combined_audio_to_s3: {e}")

        async def complete_s3_upload():
            """Complete the multipart upload to S3."""
            try:
                if combined_audio_buffer:  # Upload any remaining data
                    response = s3_client.upload_part(
                        Bucket=S3_BUCKET_NAME,
                        Key=f"{stream_sid}_combined_audio.raw",
                        PartNumber=part_number,
                        UploadId=upload_id,
                        Body=combined_audio_buffer,
                    )
                    parts.append({"PartNumber": part_number, "ETag": response["ETag"]})

                # Complete the multipart upload
                s3_client.complete_multipart_upload(
                    Bucket=S3_BUCKET_NAME,
                    Key=f"{stream_sid}_combined_audio.raw",
                    MultipartUpload={"Parts": parts},
                    UploadId=upload_id,
                )
                print("Combined audio successfully uploaded to S3.")
            except Exception as e:
                print(f"Error in complete_s3_upload: {e}")
                # Abort incomplete uploads
                s3_client.abort_multipart_upload(
                    Bucket=S3_BUCKET_NAME,
                    Key=f"{stream_sid}_combined_audio.raw",
                    UploadId=upload_id,
                )
                print("Aborted incomplete multipart upload.")
        async def handle_speech_started_event():
            """Handle interruption when the caller's speech starts."""
            nonlocal response_start_timestamp_twilio, last_assistant_item
            print("Handling speech started event.")
            if mark_queue and response_start_timestamp_twilio is not None:
                elapsed_time = latest_media_timestamp - response_start_timestamp_twilio
                if SHOW_TIMING_MATH:
                    print(f"Calculating elapsed time for truncation: {latest_media_timestamp} - {response_start_timestamp_twilio} = {elapsed_time}ms")

                if last_assistant_item:
                    if SHOW_TIMING_MATH:
                        print(f"Truncating item with ID: {last_assistant_item}, Truncated at: {elapsed_time}ms")

                    truncate_event = {
                        "type": "conversation.item.truncate",
                        "item_id": last_assistant_item,
                        "content_index": 0,
                        "audio_end_ms": elapsed_time
                    }
                    await openai_ws.send(json.dumps(truncate_event))

                await websocket.send_json({
                    "event": "clear",
                    "streamSid": stream_sid
                })

                mark_queue.clear()
                last_assistant_item = None
                response_start_timestamp_twilio = None

        
        
        async def send_initial_conversation_item(openai_ws):
            """Send initial conversation item if AI talks first."""
            initial_conversation_item = {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Greet the user with 'Hello there! I am an AI voice assistant powered by Twilio and the OpenAI Realtime API. You can ask me for facts, jokes, or anything you can imagine. How can I help you?'"
                        }
                    ]
                }
            }
            await openai_ws.send(json.dumps(initial_conversation_item))
            await openai_ws.send(json.dumps({"type": "response.create"}))

    # Uncomment the next line to have the AI speak first
    # await send_initial_conversation_item(openai_ws)

        try:
            await asyncio.gather(receive_from_twilio(), send_to_twilio(), stream_combined_audio_to_s3())
        finally:
            await complete_s3_upload()



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
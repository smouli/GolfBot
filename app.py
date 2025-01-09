import os
import json
import base64
import asyncio
import websockets
import config
import uvicorn

from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from s3_client import s3_client  # Import the S3 client object

# ssh -i ec2key.pem ec2-user@54.234.196.83 -vvv

# Configuration
OPENAI_API_KEY = config.OPENAI_API_KEY
PORT = int(os.getenv('PORT', 8000))
SYSTEM_MESSAGE = (
    "You are an AI answering machine for the Fremont Park golf course in Fremont, CA. The weather is 42 degrees fahrenheit low and 55 high. The course opens at 7:30am and last tee time is around 4pm. The restaurant in the clubhouse is open toda. There are clubs available for rent. Do not talk too much. Give concise responses and speak quickly. Be very courteous."
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
    response.say("Hello")

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
        
        part_number = 1  # Start with part number 1
        parts = []  # Metadata for uploaded parts
        combined_audio_buffer = bytearray()
        
        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal s3_initialized, stream_sid, latest_media_timestamp, combined_audio_buffer, upload_id
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)

                    if data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Incoming stream has started with SID: {stream_sid}")

                        # Initialize S3 multipart upload
                        if not s3_initialized:
                            combined_audio_upload = s3_client.create_multipart_upload(
                                Bucket=S3_BUCKET_NAME,
                                Key=f"{stream_sid}_combined_audio.raw"
                            )
                            upload_id = combined_audio_upload["UploadId"]
                            s3_initialized = True
                            print(f"S3 multipart upload initialized with Key: {stream_sid}_combined_audio.raw")

                        response_start_timestamp_twilio = None
                        latest_media_timestamp = 0
                        last_assistant_item = None

                    elif data['event'] == 'media' and openai_ws.open:
                        latest_media_timestamp = int(data['media']['timestamp'])

                        # Append incoming audio to the buffer
                        audio_chunk = base64.b64decode(data['media']['payload'])
                        combined_audio_buffer.extend(audio_chunk)

                        # Send audio to OpenAI
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }

                        print("SENT ONE MSG TO OPENAI")
                        await openai_ws.send(json.dumps(audio_append))

                        # Trigger upload if buffer size exceeds 5 MB
                        # await upload_audio_to_s3()

                    elif data['event'] == 'mark':
                        if mark_queue:
                           mark_queue.pop(0)  # Remove the acknowledged mark
                    
                    elif data['event'] == 'stop':
                        print("Call has ended. Stopping processing.")
                        await complete_s3_upload()
                        break  # Exit the loop to trigger cleanup

            except WebSocketDisconnect:
                print("Twilio WebSocket disconnected.")
            except Exception as e:
                print(f"Error in receive_from_twilio: {e}")

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio, combined_audio_buffer
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    
                    if response.get('type') in LOG_EVENT_TYPES:
                        print(f"OpenAI event: {response['type']}, details: {response}")

                    if response.get('type') == 'response.audio.delta' and 'delta' in response:
                        # Append outgoing audio to the buffer
                        audio_chunk = base64.b64decode(response['delta'])

                        # No need to record stream here also
                        combined_audio_buffer.extend(audio_chunk)

                        # Prepare audio payload for Twilio
                        audio_payload = base64.b64encode(audio_chunk).decode('utf-8')
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


                        # Trigger upload if buffer size exceeds 5 MB
                        # await upload_audio_to_s3()

                   
            except Exception as e:
                print(f"Error in send_to_twilio: {e}")

        async def upload_audio_to_s3():
            """Upload a single chunk of audio data to S3 if the buffer size exceeds the threshold."""
            nonlocal combined_audio_buffer, part_number, parts, upload_id
            try:
                if len(combined_audio_buffer) >= 5 * 1024 * 1024:  # Check if the buffer has enough data
                    # Extract a chunk of 5 MB
                    chunk = combined_audio_buffer[:5 * 1024 * 1024]
                    combined_audio_buffer = combined_audio_buffer[5 * 1024 * 1024:]  # Trim the buffer

                    # Upload the chunk to S3
                    response = s3_client.upload_part(
                        Bucket=S3_BUCKET_NAME,
                        Key=f"{stream_sid}_combined_audio.raw",
                        PartNumber=part_number,
                        UploadId=upload_id,
                        Body=chunk,
                    )
                    # Record the part metadata
                    parts.append({"PartNumber": part_number, "ETag": response["ETag"]})
                    print(f"Uploaded part {part_number} to S3.")
                    part_number += 1
                        #part_number += 1

                    await asyncio.sleep(1)  # Check periodically for new data

                    part_number += 1

                    await asyncio.sleep(1)  # Check periodically for new data

            except Exception as e:
                print(f"Error in upload_audio_to_s3: {e}")

        async def complete_s3_upload():
            """Complete the multipart upload to S3."""
            print ("came into complete_s3_upload 1")
            try:
                # Upload remaining data
                if combined_audio_buffer:
                    response = s3_client.upload_part(
                        Bucket=S3_BUCKET_NAME,
                        Key=f"{stream_sid}_combined_audio.raw",
                        PartNumber=part_number,
                        UploadId=upload_id,
                        Body=combined_audio_buffer,
                    )
                    parts.append({"PartNumber": part_number, "ETag": response["ETag"]})

                # Finalize the upload
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
            """Handle interruptions when the caller's speech starts."""
            nonlocal response_start_timestamp_twilio, last_assistant_item
            print("Handling speech started event.")
            try:
                if mark_queue and response_start_timestamp_twilio is not None:
                    # Calculate elapsed time since the AI's response started
                    elapsed_time = latest_media_timestamp - response_start_timestamp_twilio
                    if SHOW_TIMING_MATH:
                        print(f"Calculating elapsed time for truncation: {latest_media_timestamp} - {response_start_timestamp_twilio} = {elapsed_time}ms")

                    if last_assistant_item:
                        if SHOW_TIMING_MATH:
                            print(f"Truncating item with ID: {last_assistant_item}, Truncated at: {elapsed_time}ms")

                        # Send truncation event to OpenAI
                        truncate_event = {
                            "type": "conversation.item.truncate",
                            "item_id": last_assistant_item,
                            "content_index": 0,
                            "audio_end_ms": elapsed_time
                        }
                        await openai_ws.send(json.dumps(truncate_event))

                    # Clear any pending marks in Twilio's queue
                    await websocket.send_json({
                        "event": "clear",
                        "streamSid": stream_sid
                    })

                    # Reset internal state
                    mark_queue.clear()
                    last_assistant_item = None
                    response_start_timestamp_twilio = None

            except Exception as e:
                print(f"Error in handle_speech_started_event: {e}")

        
        
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
                            "text": "Greet the user with 'Fremont Park Golf Course AI Assistant on the line. How can I help you?'"
                        }
                    ]
                }
            }
            await openai_ws.send(json.dumps(initial_conversation_item))
            await openai_ws.send(json.dumps({"type": "response.create"}))

        # Uncomment the next line to have the AI speak first
        await send_initial_conversation_item(openai_ws)

        try:
            await asyncio.gather(receive_from_twilio(), send_to_twilio())
        finally:
            print ("came into finally before complete_s3_upload")
            await complete_s3_upload()



if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
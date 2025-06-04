from flask import Flask, render_template, request
from flask_socketio import SocketIO
from chat import DeepgramChat
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
    SpeakOptions,
)
import os
import json
from dotenv import load_dotenv
import threading
import time
import base64
import openai

load_dotenv()

chatbot = DeepgramChat()

# Simplified flags for cascaded approach
stt_connection = None
is_listening = False
connected_clients = set()  # Track connected clients to prevent duplicate greetings

# Add debug prints for environment variables
api_key = os.getenv("DEEPGRAM_API_KEY")
if not api_key:
    print("WARNING: DEEPGRAM_API_KEY not found in environment variables!")
    print("Please make sure your .env file exists and contains DEEPGRAM_API_KEY=your_key_here")
else:
    print("DEEPGRAM_API_KEY found in environment variables")
    print(f"API Key length: {len(api_key)} characters")

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", path='/socket.io')

# Initialize Deepgram clients
try:
    # Client for STT WebSocket
    deepgram_stt_client = DeepgramClient(api_key)
    # Client for TTS API calls
    deepgram_tts_client = DeepgramClient(api_key)
    print("Successfully initialized Deepgram clients (STT and TTS)")
except Exception as e:
    print(f"Error initializing Deepgram clients: {str(e)}")
    raise

@app.route('/')
def index():
    return render_template('index.html')

# Function to generate speech with Aura-2 and send to client
def generate_aura_speech_and_send_to_client(text):
    print(f"DEBUG: TTS FUNCTION CALLED with text length: {len(text)}")
    try:
        print(f"DEBUG: About to check text length: {len(text)} vs 1000")
        if len(text) > 1000:
            print(f"DEBUG: SUMMARIZATION TRIGGERED - Text too long ({len(text)} chars)")
            try:
                openai.api_key = os.getenv("OPENAI_API_KEY")
                response = openai.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "Summarize this text for text-to-speech in under 1000 characters. Keep it conversational and preserve key information. Always end on a complete sentence."},
                        {"role": "user", "content": text}
                    ],
                    max_tokens=200,
                    temperature=0.3
                )
                tts_text = response.choices[0].message.content.strip()
                print(f"DEBUG: Summarized from {len(text)} to {len(tts_text)} characters")
            except Exception as e:
                print(f"DEBUG: Summarization failed: {e}, truncating instead")
                tts_text = text[:1500] + "..."
        else:
            print(f"DEBUG: Text length OK ({len(text)} chars), no summarization needed")
            tts_text = text
            
        print(f"DEBUG: TTS Request - Text: '{tts_text}'")
        
        # Configure TTS options for Aura-2
        options = SpeakOptions(
            model="aura-2-odysseus-en",
            encoding="linear16",
            sample_rate=16000,
            container="wav"
        )
        
        print("DEBUG: Calling Deepgram TTS API with Aura-2...")
        response = deepgram_tts_client.speak.rest.v("1").stream_memory(
            {"text": tts_text}, 
            options
        )
        
        # Get the actual bytes from BytesIO object
        audio_bytes = response.stream_memory.getvalue()
        print(f"DEBUG: TTS successful. Audio size: {len(audio_bytes)} bytes.")
        
        # Convert audio bytes to base64 for transmission
        audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
        print("DEBUG: Emitting 'play_aura_audio' to frontend.")
        socketio.emit('play_aura_audio', {'audio': audio_base64})
        
    except Exception as e:
        print(f"DEBUG: Exception in generate_aura_speech_and_send_to_client: {str(e)}")
        import traceback
        traceback.print_exc()
        socketio.emit('error', {'data': {'message': f'TTS Generation Error: {str(e)}'}})

def generate_and_stream_response(user_message):
    """Generate response and TTS simultaneously for faster perceived response."""
    import threading
    
    print(f"DEBUG: Getting answer from chatbot for: '{user_message}'")
    chat_result = chatbot.get_answer(user_message)
    chatbot_answer = chat_result.get("answer", "I'm sorry, I could not find an answer.")
    sources = chat_result.get("sources", [])
    metadata = chat_result.get("metadata", {})
    
    # Send text response immediately
    print(f"DEBUG: Sending chatbot text response to frontend.")
    socketio.emit('conversation', {
        'data': {
            'text': chatbot_answer,
            'sources': sources,
            'metadata': metadata,
            'role': 'assistant',
            'replace_loading': True
        }
    })
    
    # Start TTS generation in parallel (non-blocking)
    print(f"DEBUG: Starting parallel TTS generation.")
    tts_thread = threading.Thread(
        target=generate_aura_speech_and_send_to_client, 
        args=(chatbot_answer,)
    )
    tts_thread.start()
    
    return chatbot_answer

def start_stt_connection():
    global stt_connection, is_listening
    
    try:
        print("DEBUG: Starting STT connection...")
        
        # Configure STT options
        options = LiveOptions(
            model="nova-2",
            language="en-US",
            smart_format=True,
            encoding="linear16",
            channels=1,
            sample_rate=16000,
            interim_results=True,
            utterance_end_ms="1000",
            vad_events=True,
            endpointing=300
        )
        
        stt_connection = deepgram_stt_client.listen.websocket.v("1")
        
        def on_open(self, open, **kwargs):
            print("DEBUG: STT connection opened")
            
        def on_message(self, result, **kwargs):
            try:
                sentence = result.channel.alternatives[0].transcript
                
                if len(sentence) == 0:
                    return
                    
                if result.is_final:
                    print(f"DEBUG: Final STT result: '{sentence}'")
                    
                    # Process with RAG chatbot
                    user_message = sentence.strip()
                    if user_message:
                        print(f"DEBUG: Sending user message to frontend: '{user_message}'")
                        socketio.emit('conversation', {
                            'data': {
                                'text': user_message,
                                'role': 'user'
                            }
                        })
                        
                        # Add loading indicator
                        print(f"DEBUG: Sending loading indicator to frontend.")
                        socketio.emit('conversation', {
                            'data': {
                                'text': '🤔 Thinking...',
                                'role': 'assistant',
                                'is_loading': True
                            }
                        })
                        
                        try:
                            generate_and_stream_response(user_message)
                            
                        except Exception as e:
                            error_msg = f"Error getting chatbot response: {str(e)}"
                            print(f"DEBUG: {error_msg}")
                            socketio.emit('error', {'data': {'message': error_msg}})
                            
                else:
                    print(f"DEBUG: Interim STT result: '{sentence}'")
                    
            except Exception as e:
                print(f"DEBUG: Error processing STT result: {str(e)}")
                
        def on_error(self, error, **kwargs):
            print(f"DEBUG: STT connection error: {error}")
            
        def on_close(self, close, **kwargs):
            print("DEBUG: STT connection closed")
            
        stt_connection.on(LiveTranscriptionEvents.Open, on_open)
        stt_connection.on(LiveTranscriptionEvents.Transcript, on_message)
        stt_connection.on(LiveTranscriptionEvents.Error, on_error)
        stt_connection.on(LiveTranscriptionEvents.Close, on_close)
        
        if stt_connection.start(options):
            print("DEBUG: STT connection started successfully")
            is_listening = True
            return True
        else:
            print("ERROR: Failed to start STT connection")
            return False
            
    except Exception as e:
        print(f"DEBUG: Exception starting STT connection: {str(e)}")
        return False

@socketio.on('connect')
def handle_connect():
    global connected_clients
    client_id = request.sid
    print(f"DEBUG: Client connected with ID: {client_id}")
    
    # Only send greeting if this is a new client
    if client_id not in connected_clients:
        connected_clients.add(client_id)
        socketio.emit('welcome', {'data': 'Connected to voice assistant'})
        
        # Send greeting message
        greeting_message = "Hello! I'm your Deepgram voice assistant, powered by Aura-2. How can I help you today?"
        print(f"DEBUG: Sending greeting: '{greeting_message}'")
        socketio.emit('conversation', {
            'data': {
                'text': greeting_message,
                'role': 'assistant'
            }
        })
        generate_aura_speech_and_send_to_client(greeting_message)

@socketio.on('start_recording')
def handle_start_recording():
    global is_listening
    print("DEBUG: Start recording requested")
    if not is_listening:
        if start_stt_connection():
            socketio.emit('recording_status', {'status': 'started'})
        else:
            socketio.emit('error', {'data': {'message': 'Failed to start recording'}})
    else:
        print("DEBUG: Already listening")
        socketio.emit('recording_status', {'status': 'started'})

@socketio.on('stop_recording')
def handle_stop_recording():
    global stt_connection, is_listening
    print("DEBUG: Stop recording requested")
    if stt_connection and is_listening:
        try:
            stt_connection.finish()
            is_listening = False
            socketio.emit('recording_status', {'status': 'stopped'})
        except Exception as e:
            print(f"DEBUG: Error stopping recording: {str(e)}")

@socketio.on('audio_data')
def handle_audio_data(data):
    global stt_connection, is_listening
    if stt_connection and is_listening:
        try:
            if isinstance(data, list):
                data = bytes(data)
            stt_connection.send(data)
        except Exception as e:
            print(f"DEBUG: Error sending audio data to STT: {str(e)}")
            socketio.emit('error', {'data': {'message': f'Error sending audio: {str(e)}'}})
    else:
        print("DEBUG: STT not ready or not listening for audio_data.")

@socketio.on('disconnect')
def handle_disconnect():
    global stt_connection, is_listening, connected_clients
    client_id = request.sid
    print(f"DEBUG: Client disconnected: {client_id}")
    
    # Remove from connected clients
    connected_clients.discard(client_id)
    
    if stt_connection and is_listening:
        try:
            stt_connection.finish()
            is_listening = False
        except:
            pass

if __name__ == '__main__':
    print("DEBUG: Starting Flask-SocketIO server with cascaded approach.")
    socketio.run(app, debug=True, port=3000, host='0.0.0.0')
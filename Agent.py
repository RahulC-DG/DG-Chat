from flask import Flask, render_template
from flask_socketio import SocketIO
from chat import DeepgramChat
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    AgentWebSocketEvents,
    SettingsOptions,
    FunctionCallRequest,
    FunctionCallResponse,
    Input,
    Output,
    AgentKeepAlive,
    SpeakOptions,
)
import os
import json
from dotenv import load_dotenv
import threading
import time
import base64
import sys
import openai

load_dotenv()

chatbot = DeepgramChat()

# Add a flag to indicate if the Deepgram connection is ready for audio
deepgram_ready = False

# Add tracking for agent's recent speech to prevent feedback loops
recent_agent_speech = []

# Add a flag to track when we're playing audio
agent_is_speaking = False

# Add this global variable
expecting_summary = False

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
agent_config = DeepgramClientOptions(
    options={
        "keepalive": "true",
        "microphone_record": "true",        
        "speaker_playback": "true",
    }
)

try:
    # Client for Agent WebSocket (STT)
    deepgram_agent_client = DeepgramClient(api_key, agent_config)
    # Client for direct TTS API calls
    deepgram_tts_client = DeepgramClient(api_key)
    print("Successfully initialized Deepgram clients (Agent and TTS)")
except Exception as e:
    print(f"Error initializing Deepgram clients: {str(e)}")
    raise

dg_connection = deepgram_agent_client.agent.websocket.v("1")

@app.route('/')
def index():
    return render_template('index.html')

# Function to generate speech with Aura-2 and send to client
def generate_aura_speech_and_send_to_client(text):
    print(f"DEBUG: TTS FUNCTION CALLED with text length: {len(text)}")
    global agent_is_speaking
    try:
        print(f"DEBUG: Setting agent_is_speaking = True")
        agent_is_speaking = True
        
        print(f"DEBUG: About to check text length: {len(text)} vs 1000")
        if len(text) > 1000:
            print(f"DEBUG: SUMMARIZATION TRIGGERED - Text too long ({len(text)} chars)")
            try:
                openai.api_key = os.getenv("OPENAI_API_KEY")
                response = openai.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "Summarize this text for text-to-speech in under 1500 characters. Keep it conversational and preserve key information."},
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
        
        print("DEBUG: Calling Deepgram TTS API with Aura-2 (SDK 4.x stream)...")
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
        
        # Reset flag after a brief delay to account for audio playback
        threading.Timer(len(audio_bytes) / 32000 + 1.0, lambda: setattr(sys.modules[__name__], 'agent_is_speaking', False)).start()
        
    except Exception as e:
        agent_is_speaking = False  # Reset flag on error
        print(f"DEBUG: Exception in generate_aura_speech_and_send_to_client: {str(e)}")
        import traceback
        traceback.print_exc()
        socketio.emit('error', {'data': {'message': f'TTS Generation Error: {str(e)}'}})

@socketio.on('connect')
def handle_connect():
    global deepgram_ready
    print("DEBUG: Client connected.")
    options = SettingsOptions()

    # Configure audio input settings
    options.audio.input = Input(
        encoding="linear16",
        sample_rate=16000
    )

    # Configure audio output settings
    options.audio.output = Output(
        encoding="linear16",
        sample_rate=16000,
        container="none"
    )

    # Keep LLM configuration but make it minimal/non-interfering
    options.agent.think.provider.type = "open_ai"
    options.agent.think.provider.model = "gpt-4o-mini"
    # Use a prompt that tells the agent to stay silent or minimal
    options.agent.think.prompt = (
        "You are a speech processing assistant. Your only job is to transcribe speech. "
        "Do not generate responses. If you must respond, say only '...' and nothing else."
    )

    # Keep speech recognition and synthesis
    options.agent.listen.provider.keyterms = ["hello", "goodbye"]
    options.agent.listen.provider.model = "nova-3"
    options.agent.listen.provider.type = "deepgram"
    options.agent.speak.provider.type = "deepgram"
    options.agent.greeting = ""

    def on_open(self, open, **kwargs):
        print(f"DEBUG: Agent WS Open event: {open}")
        socketio.emit('open', {'data': str(open)})

    def on_welcome(self, welcome, **kwargs):
        global deepgram_ready
        print(f"DEBUG: Agent WS Welcome event: {welcome}")
        deepgram_ready = True
        socketio.emit('welcome', {'data': str(welcome)})
        
        greeting_message = "Hello! I'm your Deepgram voice assistant, powered by Aura-2. How can I help you today?"
        print(f"DEBUG: Handling welcome - sending greeting text for display: '{greeting_message}'")
        socketio.emit('conversation', {
            'data': {
                'text': greeting_message,
                'role': 'assistant'
            }
        })
        print(f"DEBUG: Handling welcome - initiating Aura-2 speech for greeting.")
        generate_aura_speech_and_send_to_client(greeting_message)

    def on_conversation_text(self, conversation_text, **kwargs):
        global agent_is_speaking
        print(f"DEBUG: Agent WS on_conversation_text: {conversation_text.__dict__}")
        try:
            role = getattr(conversation_text, 'role', 'unknown')
            content = getattr(conversation_text, 'content', '')

            if role == 'user':
                # Skip processing if agent is currently speaking (to avoid feedback loop)
                if agent_is_speaking:
                    print(f"DEBUG: Ignoring user STT while agent is speaking (feedback prevention): '{content}'")
                    return
                
                user_message = content.strip()
                print(f"DEBUG: Processing user message for chatbot: '{user_message}'")

                if not user_message:
                    print("DEBUG: Empty user message from STT, ignoring.")
                    return

                print(f"DEBUG: Sending user message text to frontend for display: '{user_message}'")
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
                    print(f"DEBUG: Getting answer from chatbot for: '{user_message}'")
                    chat_result = chatbot.get_answer(user_message)
                    chatbot_answer = chat_result.get("answer", "I'm sorry, I could not find an answer.")
                    sources = chat_result.get("sources", [])
                    metadata = chat_result.get("metadata", {})
                    print(f"DEBUG: Chatbot responded with: '{chatbot_answer}'")

                    # Remove loading and send real response
                    print(f"DEBUG: Sending chatbot text response to frontend for display.")
                    socketio.emit('conversation', {
                        'data': {
                            'text': chatbot_answer,
                            'sources': sources,
                            'metadata': metadata,
                            'role': 'assistant',
                            'replace_loading': True  # Flag to replace loading message
                        }
                    })
                    
                    print(f"DEBUG: Initiating Aura-2 speech for chatbot answer.")
                    generate_aura_speech_and_send_to_client(chatbot_answer)
                    
                except Exception as e:
                    error_msg = f"Error getting chatbot response or processing TTS: {str(e)}"
                    print(f"DEBUG: {error_msg}")
                    import traceback
                    traceback.print_exc()
                    socketio.emit('error', {'data': {'message': error_msg}})
            
            elif role == 'assistant':
                print(f"DEBUG: Ignoring 'assistant' role message from Agent STT (likely agent's own minimal LLM): '{content}'")
            else:
                print(f"DEBUG: Ignoring message from Agent STT with unhandled role '{role}': '{content}'")

        except Exception as e:
            error_msg = f"Outer error in on_conversation_text: {str(e)}"
            print(f"DEBUG: {error_msg}")
            import traceback
            traceback.print_exc()
            socketio.emit('error', {'data': {'message': error_msg}})
    
    def on_agent_thinking(self, agent_thinking, **kwargs):
        print(f"DEBUG: Agent thinking: {agent_thinking.__dict__}")
        socketio.emit('thinking', {'data': agent_thinking.__dict__})

    def on_function_call_request(self, function_call_request: FunctionCallRequest, **kwargs):
        print(f"DEBUG: Function call request: {function_call_request.__dict__}")
        response = FunctionCallResponse(
            function_call_id=function_call_request.function_call_id,
            output="Function response here"
        )
        dg_connection.send_function_call_response(response)
        socketio.emit('function_call', {'data': function_call_request.__dict__})

    def on_agent_started_speaking(self, agent_started_speaking, **kwargs):
        print(f"DEBUG: Agent (internal) started speaking: {agent_started_speaking.__dict__}")

    def on_error(self, error, **kwargs):
        print(f"DEBUG: Agent WS Error event: {error}")
        error_data = {
            'message': str(error),
            'type': type(error).__name__,
            'details': str(error)
        }
        socketio.emit('error', {'data': error_data})

    dg_connection.on(AgentWebSocketEvents.Open, on_open)
    dg_connection.on(AgentWebSocketEvents.Welcome, on_welcome)
    dg_connection.on(AgentWebSocketEvents.ConversationText, on_conversation_text)
    dg_connection.on(AgentWebSocketEvents.AgentThinking, on_agent_thinking)
    dg_connection.on(AgentWebSocketEvents.FunctionCallRequest, on_function_call_request)
    dg_connection.on(AgentWebSocketEvents.AgentStartedSpeaking, on_agent_started_speaking)
    dg_connection.on(AgentWebSocketEvents.Error, on_error)

    print("DEBUG: Attempting to start Deepgram Agent WebSocket connection...")
    if not dg_connection.start(options):
        print("ERROR: Failed to start Deepgram Agent WebSocket connection.")
        socketio.emit('error', {'data': {'message': 'Failed to start Deepgram connection'}})
        return
    print("SUCCESS: Deepgram Agent WebSocket connection started.")

    def keep_alive():
        while True:
            try:
                print("DEBUG: Sending KeepAlive to Agent WS.")
                dg_connection.send(str(AgentKeepAlive()))
            except Exception as e:
                print(f"DEBUG: Error sending KeepAlive: {str(e)}")
            time.sleep(20)

    threading.Thread(target=keep_alive, daemon=True).start()

@socketio.on('audio_data')
def handle_audio_data(data):
    global deepgram_ready
    if dg_connection and deepgram_ready:
        try:
            if isinstance(data, list):
                data = bytes(data)
            dg_connection.send(data)
        except Exception as e:
            print(f"DEBUG: Error sending audio data to Agent WS: {str(e)}")
            socketio.emit('error', {'data': {'message': f'Error sending audio: {str(e)}'}})
    else:
        print("DEBUG: Agent WS not ready or not available for audio_data.")

@socketio.on('disconnect')
def handle_disconnect():
    print("DEBUG: Client disconnected from Socket.IO.")
    if dg_connection:
        print("DEBUG: Finishing Deepgram Agent WebSocket connection.")
        dg_connection.finish()

if __name__ == '__main__':
    print("DEBUG: Starting Flask-SocketIO server.")
    socketio.run(app, debug=True, port=3000, host='0.0.0.0')
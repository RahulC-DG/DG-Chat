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
)
import os
import json
from dotenv import load_dotenv
import threading
import time
load_dotenv()

chatbot = DeepgramChat()

# Add a flag to indicate if the Deepgram connection is ready for audio
deepgram_ready = False

# Add tracking for agent's recent speech to prevent feedback loops
recent_agent_speech = []

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

# Initialize Deepgram client
config = DeepgramClientOptions(
    options={
        "keepalive": "true",
        "microphone_record": "true",        
        "speaker_playback": "true",
    }
)

try:
    deepgram = DeepgramClient(api_key, config)
    print("Successfully initialized Deepgram client")
except Exception as e:
    print(f"Error initializing Deepgram client: {str(e)}")
    raise

dg_connection = deepgram.agent.websocket.v("1")

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    global deepgram_ready
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
        "Do not generate responses. If you must respond, say only 'Processing...' and nothing else."
    )

    # Keep speech recognition and synthesis
    options.agent.listen.provider.keyterms = ["hello", "goodbye"]
    options.agent.listen.provider.model = "nova-3"
    options.agent.listen.provider.type = "deepgram"
    options.agent.speak.provider.type = "deepgram"

    # Remove or minimize greeting
    options.agent.greeting = ""  # Empty greeting so it doesn't interfere

    # Event handlers
    def on_open(self, open, **kwargs):
        print("Open event received:", open.__dict__)
        socketio.emit('open', {'data': open.__dict__})

    def on_welcome(self, welcome, **kwargs):
        global deepgram_ready
        print("Welcome event received:", welcome.__dict__)
        deepgram_ready = True
        socketio.emit('welcome', {'data': welcome.__dict__})
        
        # Send our own greeting via TTS after connection is ready
        greeting_message = "Hello! I'm your Deepgram voice assistant. How can I help you today?"
        
        # Send greeting to frontend
        socketio.emit('conversation', {
            'data': {
                'text': greeting_message,
                'role': 'assistant'
            }
        })

    def on_conversation_text(self, conversation_text, **kwargs):
        print(f"on_conversation_text received: {conversation_text.__dict__}")

        try:
            role = getattr(conversation_text, 'role', 'unknown')
            content = getattr(conversation_text, 'content', '')

            # Only process user messages
            if role == 'user':
                user_message = content.strip()
                print(f"Processing user message: '{user_message}'")

                if not user_message:
                    print("Empty user message, ignoring")
                    return

                # Send user message to frontend immediately
                socketio.emit('conversation', {
                    'data': {
                        'text': user_message,
                        'role': 'user'
                    }
                })

                # Get chatbot response
                try:
                    chat_result = chatbot.get_answer(user_message)
                    chatbot_answer = chat_result.get("answer", "I'm sorry, I couldn't process your request.")
                    sources = chat_result.get("sources", [])
                    metadata = chat_result.get("metadata", {})

                    print(f"Chatbot response: {chatbot_answer}")

                    # Send chatbot response to frontend
                    socketio.emit('conversation', {
                        'data': {
                            'text': chatbot_answer,
                            'sources': sources,
                            'metadata': metadata,
                            'role': 'assistant'
                        }
                    })

                    # Convert text to speech and play it
                    socketio.emit('tts_response', {
                        'data': {
                            'text': chatbot_answer
                        }
                    })

                except Exception as e:
                    error_msg = f"Error getting chatbot response: {str(e)}"
                    print(error_msg)
                    socketio.emit('error', {'data': {'message': error_msg}})

            else:
                print(f"Ignoring message with role: {role}")

        except Exception as e:
            error_msg = f"Error in on_conversation_text: {str(e)}"
            print(error_msg)
            socketio.emit('error', {'data': {'message': error_msg}})

    def on_agent_thinking(self, agent_thinking, **kwargs):
        print("Thinking event received:", agent_thinking.__dict__)
        socketio.emit('thinking', {'data': agent_thinking.__dict__})

    def on_function_call_request(self, function_call_request: FunctionCallRequest, **kwargs):
        print("Function call event received:", function_call_request.__dict__)
        response = FunctionCallResponse(
            function_call_id=function_call_request.function_call_id,
            output="Function response here"
        )
        dg_connection.send_function_call_response(response)
        socketio.emit('function_call', {'data': function_call_request.__dict__})

    def on_agent_started_speaking(self, agent_started_speaking, **kwargs):
        print("Agent speaking event received:", agent_started_speaking.__dict__)
        socketio.emit('agent_speaking', {'data': agent_started_speaking.__dict__})

    def on_error(self, error, **kwargs):
        print("Error event received:", error.__dict__)
        error_data = {
            'message': str(error),
            'type': error.__class__.__name__,
            'details': error.__dict__
        }
        print("Sending error to client:", error_data)
        socketio.emit('error', {'data': error_data})

    # Register event handlers
    dg_connection.on(AgentWebSocketEvents.Open, on_open)
    dg_connection.on(AgentWebSocketEvents.Welcome, on_welcome)
    dg_connection.on(AgentWebSocketEvents.ConversationText, on_conversation_text)
    dg_connection.on(AgentWebSocketEvents.AgentThinking, on_agent_thinking)
    dg_connection.on(AgentWebSocketEvents.FunctionCallRequest, on_function_call_request)
    dg_connection.on(AgentWebSocketEvents.AgentStartedSpeaking, on_agent_started_speaking)
    dg_connection.on(AgentWebSocketEvents.Error, on_error)

    print("Starting Deepgram connection...")
    if not dg_connection.start(options):
        print("Failed to start Deepgram connection")
        socketio.emit('error', {'data': {'message': 'Failed to start connection'}})
        return
    print("Deepgram connection started successfully")

    # Start keep-alive thread
    def keep_alive():
        while True:
            try:
                dg_connection.send(str(AgentKeepAlive()))
                print("Sent keep-alive message")
            except Exception as e:
                print(f"Error sending keep-alive: {e}")
            time.sleep(5)

    threading.Thread(target=keep_alive, daemon=True).start()

@socketio.on('audio_data')
def handle_audio_data(data):
    global deepgram_ready # Access the global flag
    try:
        # Only send audio data if the deepgram connection is ready
        if dg_connection and deepgram_ready:
            print("Received audio data:", len(data), "bytes")
            # Convert to bytes if needed
            if isinstance(data, list):
                data = bytes(data)
            dg_connection.send(data)
        else:
            print("No Deepgram connection available")
            socketio.emit('error', {'data': {'message': 'No Deepgram connection available'}})
    except Exception as e:
        print("Error handling audio data:", str(e))
        socketio.emit('error', {'data': {'message': f'Error handling audio data: {str(e)}'}})

@socketio.on('disconnect')
def handle_disconnect():
    dg_connection.finish()

if __name__ == '__main__':
    socketio.run(app, debug=True, port=3000, host='0.0.0.0')
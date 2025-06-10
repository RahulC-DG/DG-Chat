# DeepgramChat

A RAG-based chat application for Deepgram documentation and SDKs, featuring both text-based and voice-enabled interfaces using a cascaded architecture.

## Components

### Chat Interface (chat.py)
A text-based interface that uses RAG (Retrieval Augmented Generation) to provide accurate, document-backed responses about Deepgram's APIs and SDKs.

### Voice Interface (Agent.py)
A voice-enabled interface using a cascaded approach that combines:
- **Deepgram STT API**: Direct speech-to-text transcription
- **RAG Chatbot**: Document-backed response generation
- **Deepgram TTS API**: High-quality text-to-speech synthesis

This architecture provides:
- Clear separation of concerns
- Better audio quality control
- Reduced latency and complexity
- No feedback loop issues
- Enhanced debugging capabilities

## Architecture

### Cascaded Voice Pipeline
```
Browser Audio → Deepgram STT → RAG Chatbot → Deepgram TTS → Browser Audio
```

**Key Benefits:**
- **Simple & Reliable**: Each component has a single responsibility
- **Better Control**: Full control over conversation flow and audio handling
- **No Echo Issues**: Clean audio pipeline prevents feedback loops
- **Easy Debugging**: Clear boundaries between each processing step
- **Scalable**: Easy to modify or replace individual components

### Components
- **STT (Speech-to-Text)**: Deepgram Nova-2 model for accurate transcription
- **RAG System**: Vector-based document retrieval with semantic search
- **TTS (Text-to-Speech)**: Deepgram Aura-2 for natural voice synthesis
- **Frontend**: Real-time WebSocket communication with optimized UI

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```
Or
```bash
pip3 install -r requirements.txt
```

2. Create a .env file with your API keys:
```bash
OPENAI_API_KEY=your_openai_key
DEEPGRAM_API_KEY=your_deepgram_key
EMBEDDING_MODEL=text-embedding-3-small
LLM_MODEL=gpt-4-turbo-preview
```

3. Set up Git LFS and pull vector stores:
```bash
# Install Git LFS if you haven't already
git lfs install

# Pull the vector store files
git lfs pull
```

4. Run the application:

For text-based chat:
```bash
python chat.py
```

For voice-enabled interface:
```bash
python Agent.py
```
Then open your browser to `http://localhost:3000`

## Voice Interface Features

### Real-time Interaction
- **Push-to-talk**: Click microphone to start/stop recording
- **Live transcription**: Real-time speech-to-text processing
- **Natural responses**: High-quality voice synthesis with Aura-2
- **Visual feedback**: Clear UI indicators for recording and playback states

### Smart Audio Handling
- **Echo cancellation**: Built-in audio processing prevents feedback
- **Noise suppression**: Clean audio input for better transcription
- **Automatic gain control**: Consistent audio levels
- **Graceful error handling**: Robust connection management

### Enhanced UI
- **Dynamic chat container**: Expands to show full conversation history
- **Auto-hiding welcome**: Clean interface during conversations
- **Message bubbles**: Chat-style conversation display
- **Source tracking**: Links to documentation and SDK examples

## Vector Stores

The application uses FAISS vector stores for efficient document retrieval. These are tracked using Git LFS:

```bash
# Track FAISS index files
git lfs track "*.index"
git lfs track "*.faiss"
git lfs track "data/vector_db/**/*"

# Make sure .gitattributes is tracked
git add .gitattributes
```

The vector stores contain:
- Documentation embeddings
- SDK code embeddings
- Semantic search indices

## Technical Implementation

### Backend (Agent.py)
- **Flask-SocketIO**: WebSocket server for real-time communication
- **Deepgram STT WebSocket**: Live speech transcription
- **RAG Integration**: Document retrieval and response generation
- **Deepgram TTS API**: Voice synthesis with Aura-2
- **Connection Management**: Robust WebSocket lifecycle handling

### Frontend (templates/index.html)
- **Web Audio API**: Browser audio capture and playback
- **Real-time UI**: Dynamic interface updates
- **Socket.IO Client**: WebSocket communication
- **Responsive Design**: Optimized for conversation flow

### Key Features
- **Document-backed responses**: All answers include source citations
- **Semantic caching**: Efficient handling of similar queries
- **Real-time transcription**: Live speech-to-text with interim results
- **Natural voice synthesis**: High-quality TTS with emotional expression
- **Clean conversation flow**: No audio interference or feedback loops

## File Structure

- `Agent.py`: Cascaded voice interface implementation
- `chat.py`: RAG-powered chatbot core
- `templates/index.html`: Optimized frontend interface
- `data/vector_db/`: FAISS vector stores for document retrieval
- `data/cache/`: Semantic cache for query optimization


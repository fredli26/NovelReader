# NovelReader 📖🔊

Convert ebooks to Cantonese audiobooks with neural voices. Upload an EPUB, MOBI, or AZW file, pick your chapters, choose a voice, and get high-quality MP3 or M4B audio.

## Features

- **Multiple TTS Engines**
  - **Azure Speech** — Professional neural voices (requires API key)
  - **Edge Neural** — Free Microsoft neural voices (requires internet)
  - **macOS Say** — Offline synthesis using system voices

- **Flexible Audio Output**
  - Single MP3 files per chapter
  - Combined M4B audiobook format
  - Adjustable speech rate and pitch

- **Ebook Support**
  - EPUB, MOBI, AZW, AZW3 formats
  - Automatic chapter detection
  - Text preview before synthesis
  - Book cover extraction

- **Local Web UI**
  - Upload books directly
  - Audition voices with sample text
  - Monitor synthesis jobs
  - Download results or open in Finder

## Installation

### Requirements
- Python 3.11+
- FFmpeg (for MP3/M4B encoding)

### Setup

1. Clone the repository:
```bash
git clone https://github.com/fredli26/NovelReader.git
cd NovelReader
```

2. Create a virtual environment:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Install FFmpeg (if not already installed):
```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt-get install ffmpeg

# Windows
choco install ffmpeg
```

## Configuration

### Azure Speech (Optional)
For Azure TTS, set environment variables before running:
```bash
export AZURE_SPEECH_KEY=your_api_key
export AZURE_SPEECH_REGION=southeastasia
```

### macOS System Voices
To add Cantonese voices to macOS:
1. System Settings → Accessibility → Spoken Content
2. Click "Manage Voices"
3. Add a Cantonese (zh_HK) voice

## Usage

Start the server:
```bash
python -m novelreader.server [--port 5678] [--host 127.0.0.1] [--no-browser]
```

The web UI will open automatically at `http://localhost:5678`

### Command-Line Arguments
- `--port` — Server port (default: 5678)
- `--host` — Server host (default: 127.0.0.1)
- `--no-browser` — Don't auto-open browser

## How It Works

1. **Upload** — Select an ebook file
2. **Preview** — See chapter titles and estimated duration
3. **Select** — Choose which chapters to convert
4. **Configure** — Pick TTS engine, voice, speech rate, and format
5. **Sample** — Audition the voice with sample text
6. **Synthesize** — Start the conversion job
7. **Download** — Get MP3 or M4B files

## Audio Output

Audio is saved to:
```
~/NovelReader/output/
```

Each book gets its own folder with:
- Individual chapter MP3 files
- Combined M4B audiobook (if selected)
- Metadata file

## Project Structure

```
novelreader/
├── server.py          # Flask web server and API
├── audio.py          # Audio processing (MP3, M4B encoding)
├── ebook.py          # Ebook parsing (EPUB, MOBI, etc.)
├── library.py        # Book management and storage
├── jobs.py           # Synthesis job scheduling
├── textprep.py       # Text preprocessing
├── tts/              # Text-to-speech engines
│   ├── base.py       # Base engine interface
│   ├── azure_engine.py
│   ├── edge_engine.py
│   └── macos_engine.py
└── templates/        # HTML/CSS/JS UI
```

## TTS Engines Comparison

| Engine | Cost | Quality | Internet | Setup |
|--------|------|---------|----------|-------|
| Azure | Paid | ⭐⭐⭐⭐⭐ | Required | API key |
| Edge | Free | ⭐⭐⭐⭐⭐ | Required | None |
| macOS | Free | ⭐⭐⭐ | Not needed | System voice |

## Limitations

- Maximum file upload: 300 MB
- Azure Speech: Billed per character synthesized
- Edge Neural: Free tier may throttle under load
- macOS voices: Non-neural synthesis (older quality)

## Troubleshooting

**"ffmpeg not found"**
- Install FFmpeg via your package manager

**"No Cantonese voice installed"** (macOS)
- Add a zh_HK voice in System Settings

**"Azure credentials not configured"**
- Set `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` environment variables

**Edge TTS failing with throttling errors**
- This is normal on free tier; the app automatically retries

## License

MIT

## Contributing

Feel free to open issues and submit pull requests!

---

Made for converting novels to Cantonese audiobooks. Enjoy! 🎧

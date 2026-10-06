# Local Buddy

A desktop AI companion that runs **entirely on your computer**. It chats through
[Ollama](https://ollama.com), changes its picture depending on what it is doing,
can listen through your microphone, speak its replies, and switch personality
when you describe a new one in a single sentence. No cloud, no API keys.

![Local Buddy screenshot](docs/screenshot.png)

## Features

- **Local chat** with any model you have in Ollama, streamed word by word
- **Sprite states** - `idle`, `listening`, `thinking`, `processing`, `talking`.
  Upload your own PNGs or GIFs for each state (several images become an animation)
- **Voice in** - optional mic mode detects when you speak, then transcribes it locally with Whisper
- **Voice out** - optional offline text-to-speech (basic voice, or a nicer neural voice with Piper)
- **Instant personalities** - type "a grumpy pirate who loves maths" and your local model writes the
  system prompt and applies it. Save favourites as presets
- **Dark and light themes** - change `THEME` at the top of `buddy.py`

## Quick start

1. Install [Ollama](https://ollama.com) and pull a model:
   ```
   ollama pull llama3.2
   ```
2. Install the Python packages (Python 3.9+):
   ```
   pip install -r requirements.txt
   ```
3. Run it:
   ```
   python buddy.py
   ```
4. Open **Settings**, upload sprites for each state, and pick your model.

If the chat says it can't reach Ollama, make sure Ollama is running (`ollama serve`).

## Sprites

No artwork is included. Use images you made or have the right to use - transparent PNGs
and GIFs work best. Uploaded sprites are copied to a `sprites/<state>/` folder next to
the script (ignored by git). States without sprites show an emoji.

| State | When it shows |
|-------|---------------|
| idle | resting |
| listening | the mic hears you speaking |
| thinking | waiting for the first words of a reply |
| processing | the model is slow or loading (over 4 seconds) |
| talking | the reply is streaming in or being spoken |

## Personalities

Click **Personality** in the top bar.

- **Describe one:** type a sentence and press *Generate & apply*. Your local model expands it into a
  full system prompt, which you can edit.
- **Presets:** a few are built in. *Save as preset* keeps your own in `buddy_config.json`.
- Switching personality starts a fresh chat so the old one doesn't leak in.

## Voice (optional)

**Speech recognition (your voice to text)**
```
pip install sounddevice numpy faster-whisper
```
Turn on the **Mic** button. The first time you speak, the Whisper model downloads once.
Adjust the threshold in Settings if it reacts to background noise.

**Spoken replies**

- Basic: `pip install pyttsx3`
- Nicer neural voice (offline):
  ```
  pip install piper-tts sounddevice numpy
  python -m piper.download_voices en_US-amy-medium --download-dir voices
  ```
  Then tick *Speak replies aloud* in Settings. `VOICE_PITCH` in `buddy.py` makes it squeakier or deeper.

## Configuration

Everything is at the top of `buddy.py`: `OLLAMA_URL`, `THEME`, `WHISPER_MODEL`,
`PIPER_MODEL`, `VOICE_PITCH`, `SLOW_AFTER`, `MAX_TURNS` (how many messages the model remembers).

## How it works

- Tkinter UI on the main thread. Network calls (Ollama, Whisper, speech) run in background
  threads and pass results back through a queue that the UI polls every 50 ms, so the window never freezes.
- A small state machine picks the sprite from what is happening: sent a message, got the first token,
  heard audio, and so on.
- The mic is ignored while Buddy is replying so it doesn't hear itself.

## Known limitations

- Single-file app with a Tkinter UI; chat text can't be selected or copied yet
- Speech recognition and neural voices need extra downloads and a reasonably fast computer
- Small models follow personalities less strictly than large ones

## License

MIT - see [LICENSE](LICENSE). Replace `[your name]` in the license with yours before publishing.

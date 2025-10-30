# PlayStory

A proof-of-concept web app for creating branching video stories with Sora. Generate a clip, choose from 3 AI-suggested actions, then generate the next clip using the last frame as visual reference. Repeat to build your story.

## Prerequisites

- Python 3.11+
- Node 18+
- ffmpeg (on PATH)
- OpenAI API key with Sora access

## Setup

Backend:
```powershell
cd backend
Copy-Item .env.example .env
# Edit .env and set OPENAI_API_KEY
python -m venv .venv
. .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

Frontend:
```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

## How it works

- FastAPI backend with endpoints for starting jobs, polling status, and branching stories
- Media stored in `media/videos` and `media/frames`
- Last frame extracted and used as `input_reference` for the next clip to maintain visual continuity
- Optional story context mode includes recent events in prompts for narrative continuity

## Tips

- Use one consistent video size for your project (e.g., `1280x720`)
- Sora 2 supports 4, 8, or 12 second clips
- Keep prompts focused on a single scene or beat

## License

MIT

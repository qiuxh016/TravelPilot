# TravelPilot

Context-aware multi-agent travel copilot for domestic free travel.

## Repository layout

- `apps/web`: Next.js web workspace
- `apps/api`: FastAPI HTTP API
- `apps/worker`: background task entry point
- `packages/core`: domain/application contracts
- `packages/infrastructure`: adapters for database and providers
- `infra`: local service configuration
- `tests`: integration, contract, E2E and evaluation tests
- `docs`: architecture and ADRs

## Quick start

```powershell
docker compose up -d postgres redis
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn apps.api.src.travelpilot_api.main:app --reload
```

The web app is intentionally a small shell until the first product slice is implemented.


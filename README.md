# PrimeFlix Backend

FastAPI backend application powered by `uv` Python package manager.

## Prerequisites

- [uv](https://github.com/astral-sh/uv) installed on your system.

## Setup & Running

1. **Install Dependencies**
   ```bash
   uv sync
   ```

2. **Run Development Server**
   ```bash
   uv run uvicorn app.main:app --reload --port 8000
   ```

3. **Interactive Documentation**
   - Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
   - ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)
   - Health check: [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)

## Project Structure

```
backend/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── endpoints/
│   │       │   └── health.py
│   │       └── router.py
│   ├── core/
│   │   └── config.py
│   └── main.py
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

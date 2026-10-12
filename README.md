# PrimeFlix Backend

Production-ready FastAPI backend application managed with the `uv` Python package manager.

## Prerequisites

- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv) (recommended) or standard `pip`

---

## Local Development

1. **Install Dependencies**
   ```bash
   uv sync
   ```

2. **Run Development Server**
   ```bash
   uv run python main.py
   # or
   uv run uvicorn app:app --reload --host 127.0.0.1 --port 8000
   ```

3. **API Documentation**
   - Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
   - ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)
   - Health Check: [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)

---

## Production Deployment

### 1. Cloud PaaS (Render / Railway / Fly.io / Heroku)
**Start Command:**
```bash
uv run uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 4
```
*(If the platform installs dependencies in a global virtualenv without `uv`:)*
```bash
uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 4
```

### 2. High-Concurrency Production (Gunicorn + Uvicorn Workers)
```bash
gunicorn app:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000}
```

### 3. Docker Container
```dockerfile
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

---

## Environment Configuration

Configure the following variables in `.env` or in your deployment dashboard:
- `DATABASE_URL`: PostgreSQL / Supabase connection URI
- `HOST`: Set to `0.0.0.0` for containers
- `PORT`: Server port (defaults to `8000`)
- `DEBUG`: Set to `False` in production
- `CORS_ORIGINS`: Allowed frontend URLs (e.g., `["https://primeflix.vercel.app"]`)
- `JWT_SECRET`: Secure random string for auth tokens
- `TMDB_API_KEY`: The Movie Database API key
- `OMDB_API_KEY`: Open Movie Database API key
- `GEMINI_API_KEY`: Google Gemini API key for AI Concierge
- `SMTP_*`: SMTP credentials for OTP and email notifications

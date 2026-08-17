# Smart Ticket Support System — Backend API

An enterprise-grade, high-performance customer support ticket management backend service built with **FastAPI**, **PostgreSQL** (native `asyncpg` connection pool with parameterized SQL), **Celery + Redis** for distributed background triage and scheduled reminders, and a swappable **LLM-assisted Auto-Triage Engine** supporting local **Ollama**, **OpenAI**, and deterministic mock adapters.

---

## 📑 Table of Contents
1. [Architecture Overview](#-architecture-overview)
2. [Key Technical Decisions & Tradeoffs](#-key-technical-decisions--tradeoffs)
   - [Zero-ORM Native Data Access (asyncpg)](#1-zero-orm-native-data-access-asyncpg)
   - [PostgreSQL Trigram Search vs. Elasticsearch](#2-postgresql-trigram-search-vs-elasticsearch)
   - [Dual-Token Authentication & Token Revocation](#3-dual-token-authentication--token-revocation)
   - [LLM Auto-Triage Adapter Pattern & Provider Tradeoffs](#4-llm-auto-triage-adapter-pattern--provider-tradeoffs)
   - [Scheduled Reminders & Proactive Auto-Cancellation](#5-scheduled-reminders--proactive-auto-cancellation)
   - [Observability, Middlewares & Rate Limiting](#6-observability-middlewares--rate-limiting)
3. [System Architecture Diagram](#-system-architecture-diagram)
4. [Getting Started (Docker Compose)](#-getting-started-docker-compose)
5. [Local Development Setup (without Docker)](#-local-development-setup-without-docker)
6. [API Contract & Standard Envelope](#-api-contract--standard-envelope)
7. [API Endpoints Reference](#-api-endpoints-reference)
8. [Automated Test Suite](#-automated-test-suite)
9. [What Works vs. Known Limitations](#-what-works-vs-known-limitations)

---

## 🏛 Architecture Overview

The system is designed with strict separation of concerns:
- **Presentation Layer (`app/api/`)**: Route handlers, dependency injection (`app/api/deps.py`), and global middleware pipeline (Request IDs, structured JSON access logs with duration in ms, IP brute-force rate limiters).
- **Service Layer (`app/services/`)**: Pure business logic, server-side RBAC enforcement, IDOR boundary protection, token management, and provider adapters.
- **Data Access Layer (`app/database.py`)**: `asyncpg` asynchronous connection pooling with parameterized SQL, schema definitions, and composite/GIN indexes.
- **Background Infrastructure (`app/tasks/`, `app/celery_app.py`)**: Celery distributed workers backed by Redis, non-blocking LLM triage with exponential backoff retries, and Celery Beat polling for due event reminders every 30s.

---

## ⚖️ Key Technical Decisions & Tradeoffs

### 1. Zero-ORM Native Data Access (`asyncpg`)
* **Decision**: We bypassed heavy ORMs (like SQLAlchemy Declarative/ORM or Tortoise) and used raw parameterized SQL with `asyncpg.Pool`.
* **Rationale**:
  * **Performance & Predictability**: `asyncpg` is the fastest PostgreSQL driver in the Python ecosystem, communicating directly via PostgreSQL binary wire protocol with zero ORM overhead.
  * **Explicit Queries**: Raw SQL ensures zero N+1 query surprises and full visibility into index utilization (`pg_trgm`, compound indexes).
  * **Parameterized Security**: All queries strictly bind parameters (`$1, $2, ...`), completely eliminating SQL injection vectors.
* **Tradeoff**: Schema migrations require explicit DDL updates rather than automatic reflection. We solved this with automated startup DDL execution and clean schema isolation.

---

### 2. PostgreSQL Trigram Search vs. Elasticsearch
* **Decision**: Implemented fuzzy keyword search directly inside PostgreSQL using the `pg_trgm` extension and GIN indexes over `title` and `description`.
* **Rationale**:
  * **Operational Simplicity**: Avoids the infrastructure overhead, dual-write consistency issues, and memory footprint of maintaining a separate Elasticsearch / OpenSearch cluster.
  * **Fuzzy & Substring Matching**: Trigram matching handles misspellings (e.g. searching `"invoce"` matches `"invoice"`) and partial substrings seamlessly via `similarity(title, $q)`.
* **Tradeoff**: For datasets exceeding tens of millions of documents with complex faceted search, Elasticsearch would scale horizontally better. For support ticket workloads (10k–1M tickets), `pg_trgm` GIN indexes deliver sub-millisecond query latency.

---

### 3. Dual-Token Authentication & Token Revocation
* **Security Design**:
  1. **Password Hashing**: Passwords hashed using **Argon2id** (memory cost 65536, time cost 3, parallelism 4), resilient against GPU and ASIC cracking.
  2. **Access Token (JWT, 15m Expiry)**: Stateless Bearer token carrying `sub` (user ID), `email`, `role`, `type`, and unique `jti`.
  3. **Refresh Token (7d Expiry)**: High-entropy cryptographic token (`secrets.token_urlsafe(48)`). Only its **SHA-256 digest** is stored in the `refresh_tokens` database table.
  4. **Token Rotation**: Calling `POST /api/v1/auth/refresh` revokes the submitted refresh token and atomically issues a fresh token pair.
  5. **Instant Logout & Session Invalidation**: Calling `POST /api/v1/auth/logout` sets `is_revoked = TRUE` in the database, immediately terminating the session.
  6. **Server-Side RBAC & IDOR Prevention**:
     * `admin`: Full visibility and modification rights across all tickets.
     * `agent`: Hard-restricted to viewing, updating, creating notes, or scheduling reminders **only on tickets assigned to them**. Direct URL manipulation with another ticket ID returns `403 Forbidden`.

---

### 4. LLM Auto-Triage Adapter Pattern & Provider Tradeoffs
All LLM integration is cleanly isolated in `app/services/llm_service.py` behind the `BaseLLMAdapter` interface:

| Provider | Pros | Cons / Tradeoffs | When to Use |
| :--- | :--- | :--- | :--- |
| **`OllamaAdapter`** *(Default)* (`http://ollama:11434`) | Completely private, zero API costs, runs 100% locally via Docker Compose with models like `llama3.2` or `mistral`. | Requires local compute; cold-start model load time. | Production local environment, privacy-focused ticket triage. |
| **`MockLLMAdapter`** | Zero external dependencies, instantaneous response, deterministic rules. | Heuristic keyword matching. | Unit/Integration testing, CI/CD pipelines, offline fallback. |

#### Resilient Asynchronous Dispatch & Fallback:
* When a ticket is created (`POST /api/v1/tickets`), the response is **immediately returned to the client** (sub-20ms latency).
* Celery executes `triage_ticket_task` with a 5-second timeout.
* **Exponential Backoff**: If the LLM call times out or errors, Celery retries up to 3 times ($2^1, 2^2, 2^3$ seconds delay).
* **Graceful Degradation**: If all retries fail, the task sets `manual_triage_required = TRUE` on the ticket. **The core ticket creation flow is never blocked or broken.**

---

### 5. Scheduled Reminders & Proactive Auto-Cancellation
* **Decoupled Architecture**: Agents attach reminders (`POST /api/v1/tickets/{id}/reminders`) with a target UTC timestamp `scheduled_for` and note message.
* **Celery Beat Polling**: A dedicated scheduler process triggers `check_due_reminders_task` every 30 seconds.
* **Smart Evaluation**:
  * If ticket status is `open` or `in_progress` $\rightarrow$ Marks reminder as `fired` and creates an in-app notification row in `notifications` table for the assigned agent.
  * If ticket was previously resolved/closed $\rightarrow$ Marks reminder as `cancelled`.
* **Proactive Invalidation**: When an agent updates a ticket's status to `resolved` or `closed` (`PATCH /api/v1/tickets/{id}`), all pending reminders for that ticket are **immediately cancelled** in the database.

---

### 6. Observability, Middlewares & Rate Limiting
1. **Correlation Request IDs (`req_<uuidv7>`)**:
   * Every request is assigned a time-ordered UUIDv7 (`req_019154a2-...`).
   * Attached to `request.state.request_id` and returned in the `X-Request-ID` response header and all JSON error/success envelopes.
2. **Structured Access Logging**:
   * High-precision latency tracking (`duration_ms`), HTTP status, client IP, route path, and correlation ID logged in JSON.
3. **Brute-Force Rate Limiting**:
   * IP-based sliding window rate limiter protects `/api/v1/auth/login` (default 5 requests/minute). Returns `429 Too Many Requests` with `Retry-After` headers.

---

## 📐 System Architecture Diagram

```mermaid
flowchart TD
    Client([Client / Frontend / Mobile]) -->|HTTP + Bearer JWT| MW_Req[Request ID Middleware\nGenerates req_UUIDv7]
    MW_Req --> MW_Log[Structured Logging Middleware\nTracks method, path, duration_ms]
    MW_Log --> MW_Rate[Rate Limiting Middleware\nLogin Brute-Force Bucket]
    
    MW_Rate --> Routers[FastAPI API Routers\n/api/v1/auth\n/api/v1/tickets\n/api/v1/tickets/{id}/notes\n/api/v1/tickets/{id}/reminders\n/api/v1/notifications]

    Routers --> Deps[Dependencies\n- get_db_pool\n- get_current_user\n- require_roles]
    Deps --> Services[Service Layer\n- AuthService (Argon2id + JWT)\n- TicketService (RBAC + pg_trgm)\n- ReminderService\n- LLM Triage Engine]

    Services --> DB[(PostgreSQL 16\n- asyncpg Connection Pool\n- pg_trgm & GIN Indexes\n- Persistent Volume)]
    Services -.->|triage_ticket_task.delay(id)| Redis[(Redis 7 Broker & Backend)]

    subgraph Celery Infrastructure
        Redis --> Worker[Celery Worker Process]
        Worker --> LLM[LLM Adapter Engine\n- Mock / Ollama / OpenAI\n- 3x Exponential Backoff\n- Fallback: manual_triage_required=True]
        LLM -.->|Update category, priority, summary| DB

        Beat[Celery Beat Scheduler] -->|Every 30s: check_due_reminders_task| Redis
        Redis --> RemWorker[Reminder Evaluator]
        RemWorker -->|Fires due reminders or cancels if resolved| DB
    end
```

---

## 🚀 Getting Started (Docker Compose)

The entire topology (FastAPI API, PostgreSQL, Redis, Celery Worker, Celery Beat, and Ollama) can be booted with a single command.

### 1. Clone & Configure Environment
```bash
git clone <repository_url>
cd dotkonnekt-smart-ticket-service

# Create local environment file from template
cp .env.example .env
```

### 2. Start Services with Docker Compose
```bash
docker compose up --build -d
```

### 3. Verify System Health
Check that all containers are healthy:
```bash
docker compose ps
```
Access the health check endpoint:
```bash
curl http://localhost:8000/health
```

### 4. Interactive API Documentation
Once running, open your browser:
* **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
* **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

## 💻 Local Development Setup (without Docker)

If running directly on your host machine:

### 1. Prerequisites
* Python 3.11+
* PostgreSQL running locally on port 5432 (`smart_ticket_db`)
* Redis running locally on port 6379

### 2. Setup Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Run FastAPI Web Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Run Celery Worker & Celery Beat
In separate terminal windows:
```bash
# Start Celery Worker
celery -A app.celery_app worker --loglevel=info

# Start Celery Beat Scheduler
celery -A app.celery_app beat --loglevel=info
```

---

## 📦 API Contract & Standard Envelope

Every API response follows a strict, standardized JSON contract:

### Success Response Envelope
```json
{
  "success": true,
  "data": {
    "id": "019154a2-7b3e-7a1b-9e45-123456789abc",
    "title": "Cannot download billing invoice",
    "status": "open",
    "priority": "high",
    "category": "billing",
    "ai_summary": "Summary: Cannot download billing invoice - The PDF invoice download gives 404.",
    "manual_triage_required": false,
    "created_at": "2026-08-17T12:00:00Z",
    "updated_at": "2026-08-17T12:00:00Z"
  },
  "meta": {
    "timestamp": "2026-08-17T12:00:00.123456Z",
    "requestId": "req_019154a2-7b3e-7a1b-9e45-fedcba987654"
  }
}
```

### Paginated Read Response Envelope
```json
{
  "success": true,
  "data": [ ... ],
  "meta": {
    "timestamp": "2026-08-17T12:00:00.123456Z",
    "requestId": "req_019154a2-7b3e-7a1b-9e45-fedcba987654",
    "pagination": {
      "page": 1,
      "pageSize": 20,
      "totalRecords": 85,
      "totalPages": 5,
      "hasNext": true,
      "hasPrev": false
    }
  }
}
```

### Standardized Error Envelope
```json
{
  "success": false,
  "error": {
    "code": "FORBIDDEN",
    "message": "Agents can only view tickets assigned to them.",
    "details": []
  },
  "meta": {
    "timestamp": "2026-08-17T12:00:00.123456Z",
    "requestId": "req_019154a2-7b3e-7a1b-9e45-fedcba987654"
  }
}
```

---

## 📡 API Endpoints Reference

### Authentication (`/api/v1/auth`)
* `POST /api/v1/auth/register` — Register a new user (`admin` or `agent`).
* `POST /api/v1/auth/login` — Authenticate and receive Access JWT + Refresh Token (rate-limited to 5 req/min).
* `POST /api/v1/auth/refresh` — Rotate refresh token and obtain new token pair.
* `POST /api/v1/auth/logout` — Revoke refresh token in database.
* `GET /api/v1/auth/me` — Retrieve current authenticated user profile.

### Tickets (`/api/v1/tickets`)
* `POST /api/v1/tickets` — Create a new support ticket (auto-enqueues Celery LLM triage).
* `GET /api/v1/tickets` — Search & list tickets with pagination (`q`, `status`, `priority`, `category`, `assigned_to_id`, `page`, `page_size`).
* `GET /api/v1/tickets/{id}` — Get ticket by ID (enforces agent ownership).
* `PATCH /api/v1/tickets/{id}` — Update status, priority, category, or assignee (auto-cancels reminders if resolved/closed).

### Internal Notes (`/api/v1/tickets/{id}/notes`)
* `POST /api/v1/tickets/{id}/notes` — Add internal note to a ticket.
* `GET /api/v1/tickets/{id}/notes` — List notes with author details.

### Reminders (`/api/v1/tickets/{id}/reminders`)
* `POST /api/v1/tickets/{id}/reminders` — Schedule a follow-up reminder at `scheduled_for`.
* `GET /api/v1/tickets/{id}/reminders` — List reminders on a ticket.

### Notifications (`/api/v1/notifications`)
* `GET /api/v1/notifications` — List event log notifications for current user.

---

## 🧪 Automated Test Suite

The repository includes a comprehensive, high-coverage **Pytest** suite covering:
* **Authentication**: Password hashing (Argon2id), JWT claims & expiry, token rotation, token revocation, invalid tokens.
* **Server-Side RBAC**: Agent vs Admin permission boundaries, IDOR prevention on ticket view/update/notes.
* **Ticket CRUD & Search**: Creation, pagination metadata, `pg_trgm` fuzzy keyword search, compound status/priority/category filters.
* **LLM Auto-Triage**: Mock adapter categorization & priority heuristics, Celery retry logic, graceful fallback to `manual_triage_required = True`.
* **Reminders & Scheduler**: Reminder firing, notification event logging, proactive auto-cancellation on ticket resolution.
* **Middlewares**: UUIDv7 Request ID propagation, structured access logging with latency, ContextVar logger correlation, brute-force rate limiting (429).

### Run Pytest
```bash
pytest -v
```

---

## 🔍 What Works vs. Known Limitations

### ✅ What Works
1. **Complete Zero-ORM Data Architecture**: High-speed parameterized SQL queries over `asyncpg.Pool`.
2. **PostgreSQL Trigram Search**: Fast keyword & fuzzy searching over ticket title/description with composite GIN indexes.
3. **Dual-Token Auth & DB Revocation**: Argon2id password hashing, 15m access token, 7d refresh token with database-backed revocation.
4. **Server-Side Role Access Control**: Enforced in the service layer to prevent IDOR attacks.
5. **Non-Blocking LLM Auto-Triage**: Celery background worker with 5s timeout, 3 exponential backoff retries, and graceful fallback to `manual_triage_required = True`.
6. **Decoupled Reminder Engine**: Celery Beat periodic task (every 30s) + proactive auto-cancellation on ticket resolution.
7. **Observability & Defense**: UUIDv7 Request IDs (`req_<uuidv7>`), structured access logging with duration in ms, and IP rate limiting on login.
8. **100% Dockerized**: Full topology with healthy checks configured in `docker-compose.yml`.

### ⚠️ Known Limitations & Production Enhancements
1. **Email / SMS Dispatch**: The notification system persists in-app notification rows in the `notifications` table; an external email service (e.g. AWS SES / SendGrid) can be plugged into `ReminderService.process_due_reminders`.
2. **Distributed WebSocket Push**: Notifications are currently polled or queried via REST API (`GET /api/v1/notifications`). A WebSocket router can be added for real-time notification push to connected browser clients.
3. **Ollama GPU Acceleration in Docker**: On macOS, Docker runs in a Linux VM without native Apple Metal GPU passthrough for Ollama; for maximum Ollama performance on Mac, run Ollama natively (`ollama serve`) and set `OLLAMA_BASE_URL=http://host.docker.internal:11434`.

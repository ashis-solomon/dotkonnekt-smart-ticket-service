# Smart Ticket Support System — Backend API

An enterprise-grade, asynchronous ticket management backend featuring **role-based access control (RBAC)**, **non-blocking LLM auto-triage**, **PostgreSQL trigram fuzzy search**, and **decoupled event reminder scheduling**.

---

## 1. System Overview

The **Smart Ticket Support System** is designed for internal support operations, ensuring high availability, resilient AI integration, and strict role-based data isolation.

### Core Capabilities
* **JWT Authentication & RBAC**: Dual-token flow (Access + Refresh token rotation and database-backed revocation) with Argon2id password hashing. Role boundaries (`admin` vs `agent`) are strictly enforced at the service layer to prevent Insecure Direct Object References (IDOR).
* **Isolated LLM Auto-Triage**: Asynchronously classifies ticket category (`billing`, `technical`, `account`, `other`), assigns priority, and generates an executive summary using a local **Ollama** instance (`llama3.2:1b`), with automatic fallback to manual triage upon failure.
* **Database-Level Fuzzy Search**: High-performance keyword search across ticket titles and descriptions using PostgreSQL's `pg_trgm` (trigram) extension and GIN indexes, eliminating the need for heavyweight external search infrastructure.
* **Scheduled Event Reminders**: Background scheduler powered by **Celery Beat** and **Redis** that evaluates due reminders every 30 seconds and auto-cancels pending alerts upon ticket resolution.
* **Production Observability**: UUIDv7 request correlation IDs (`req_<uuidv7>`), structured request/response logging with latency tracking, rate limiting, and standardized JSON API response envelopes.

---

## 2. Solution Architecture

The system follows a modular, asynchronous layered architecture. Incoming HTTP requests pass through a pipeline of global middlewares (UUIDv7 request correlation ID injection, structured request/response logging, and SlowAPI rate limiting) before routing to FastAPI endpoints. Business logic and role-based access controls are encapsulated in domain services (`AuthService`, `TicketService`, `ReminderService`), which execute parameterized SQL queries directly against a PostgreSQL 16 database using an asynchronous connection pool (`asyncpg.Pool`). 

Long-running and scheduled operations are decoupled from the HTTP request cycle using Celery backed by Redis. When a ticket is created, an asynchronous triage task is dispatched to a Celery worker, which invokes the isolated LLM service (`OllamaAdapter`) and updates the ticket record without blocking the API client. In parallel, Celery Beat runs a periodic 30-second scheduler that polls PostgreSQL for due reminders, generates notification records, and cleans up cancelled reminders for resolved tickets.

---

## 3. Deep-Dive Technical Design

### 3.1. Authentication & Role-Based Access Control (RBAC)
* **Dual-Token Lifecycle**:
  * **Access Token**: Stateless JWT (30-minute expiry) containing `sub` (User UUID), `email`, and `role`.
  * **Refresh Token**: High-entropy token (7-day expiry) stored as a SHA-256 digest in PostgreSQL (`refresh_tokens` table).
  * **Token Rotation & Revocation**: `/auth/refresh` atomically revokes the existing refresh token and issues a new pair. `/auth/logout` explicitly revokes the session in the database.
* **Role Matrix**:
  * `admin`: Full administrative access (can create, view, update, and reassign any ticket; view all notifications).
  * `agent`: Scoped access (can view and modify only tickets assigned to them; self-assign on creation).

### 3.2. LLM-Assisted Auto-Triage & Resilience
* **Isolated Adapter Pattern**: Encapsulated in `app/services/llm_service.py` behind `BaseLLMAdapter`. Switching between `OllamaAdapter`, `OpenAIAdapter`, or `MockLLMAdapter` requires only updating the `LLM_PROVIDER` environment variable without altering business logic.
* **Ollama Selection & Tradeoffs**:
  * *Why Ollama (`llama3.2:1b`)*: Zero external API costs, local data privacy (customer emails and internal tickets never leave the infrastructure), and fully offline operation for local Docker development.
  * *Tradeoffs*: Requires local compute (RAM/CPU/GPU) and higher inference latency (~1–3s) compared to cloud endpoints. Handled cleanly by running inference asynchronously in a Celery background worker.
* **Failure Handling & Graceful Degradation**:
  * Triage tasks execute with a configurable timeout (`LLM_TIMEOUT_SECONDS=300`).
  * On failure or transient timeout, Celery retries up to 3 times with **exponential backoff** ($2^{\text{retries}}$ seconds).
  * If retries are exhausted, the worker sets `manual_triage_required = TRUE` on the ticket. The ticket creation request was already completed successfully in sub-50ms.

### 3.3. PostgreSQL Data Layer & Schema Design
* **Native asyncpg Connection Pool**: Manages 5–20 persistent connections configured with timeout handling during FastAPI lifespan events.

![Database Schema ERD](docs/images/database_schema.png)

* **Trigram Fuzzy Search (`pg_trgm`)**:
  * PostgreSQL `pg_trgm` extension is enabled with GIN indexes on `title` and `description` (`gin_trgm_ops`).
  * Queries combine exact substring matching (`ILIKE`) with fuzzy word similarity (`word_similarity(q, column) > 0.25`), sorting results by similarity score and recency.
* **Alternative Approaches & Scalability Tradeoffs**:
  | Search Approach | Implementation | Pros | Cons / When to Choose |
  | :--- | :--- | :--- | :--- |
  | **PostgreSQL Trigram (Chosen)** | `pg_trgm` + GIN Indexes | Zero extra operational overhead; strong typo tolerance; ACID compliant; sub-10ms queries for <1M tickets. | Ideal for internal support desk scale (<1M records). |
  | **Vector Search (`pgvector`)** | Embeddings + HNSW Index | Semantic conceptual matching (e.g. "can't log in" matches "forgot password"). | Requires embedding computation step during ingest and higher memory for vector indexing. |
  | **Elasticsearch / Opensearch** | External Search Cluster | Horizontal scaling, BM25 text relevance, tokenization, high ingest throughput. | High infrastructure cost, operational complexity, and eventual consistency sync overhead. |

### 3.4. Event Reminder Scheduler
* **Decoupled Polling Loop**: Celery Beat invokes `check_due_reminders_task` every 30 seconds.
* **Lifecycle State Machine**:
  * **Fire**: If `scheduled_for <= NOW()` and ticket status is `open` or `in_progress`, the reminder transitions to `fired` and a notification record is inserted into the `notifications` table.
  * **Auto-Cancel**: If the ticket is resolved or closed before the due date, Celery Beat marks the reminder as `cancelled`. Furthermore, `TicketService.update_ticket` eagerly cancels pending reminders when a ticket is marked `resolved` or `closed`.

---

## 4. Tech Stack

**FastAPI**, **PostgreSQL**, **Celery + Redis**, **Ollama**, and **Docker**.

---

## 5. API Documentation & Endpoints

### 5.1. Interactive API Documentation
* **Swagger UI (Interactive API Explorer)**: [http://localhost:8000/docs](http://localhost:8000/docs)
* **ReDoc (Detailed Specifications)**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
* **OpenAPI JSON**: [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

### 5.2. Standardized Response Envelope
Every API response adheres to a uniform structure:
```json
{
  "success": true,
  "data": { ... },
  "meta": {
    "timestamp": "2026-08-18T00:30:00.000Z",
    "requestId": "req_019154a2-7b3e-7a1b-9e45-123456789abc"
  }
}
```

### 5.3. Route Overview

| Method | Endpoint | Access | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Public | System and database health status |
| `POST` | `/api/v1/auth/register` | Public | Register new agent/admin user |
| `POST` | `/api/v1/auth/login` | Public | Authenticate credentials & issue access/refresh tokens |
| `POST` | `/api/v1/auth/refresh` | Public | Rotate refresh token and issue new access token |
| `POST` | `/api/v1/auth/logout` | Authenticated | Revoke refresh token and terminate session |
| `GET` | `/api/v1/auth/me` | Authenticated | Get current authenticated user profile |
| `POST` | `/api/v1/tickets` | Authenticated | Create a ticket (dispatches async LLM triage) |
| `GET` | `/api/v1/tickets` | Authenticated | List tickets with **trigram search (`q`)** and filters |
| `GET` | `/api/v1/tickets/{id}` | Role Enforced | Get ticket details by ID (agents restricted to assigned) |
| `PATCH` | `/api/v1/tickets/{id}` | Role Enforced | Update status, priority, category, or assignee |
| `POST` | `/api/v1/tickets/{id}/notes` | Role Enforced | Add internal note to a ticket |
| `GET` | `/api/v1/tickets/{id}/notes` | Role Enforced | List internal notes for a ticket |
| `POST` | `/api/v1/tickets/{id}/reminders` | Role Enforced | Schedule a follow-up reminder on a ticket |
| `GET` | `/api/v1/tickets/{id}/reminders` | Role Enforced | List scheduled reminders for a ticket |
| `GET` | `/api/v1/notifications` | Authenticated | List reminder notification logs |

### 5.4. Keyword Search & Filtering (`GET /api/v1/tickets`)
The ticket listing route provides compound filtering and fuzzy search via query parameters:
* `q`: Keyword or phrase searched fuzzily across `title` and `description` (e.g. `?q=invoce` matches "Invoice payment issue").
* `status`: Filter by status (`open`, `in_progress`, `resolved`, `closed`).
* `priority`: Filter by priority (`low`, `medium`, `high`).
* `category`: Filter by category (`billing`, `technical`, `account`, `other`).
* `assigned_to_id`: Filter by assigned user UUID (Admins only; Agents are automatically scoped to their own ID).
* `page` & `page_size`: Pagination controls (defaults: `page=1`, `page_size=20`).

---

## 6. Getting Started & Setup

### 6.1. Prerequisites
* **Docker** (v24.0+) & **Docker Compose** (v2.20+)
* (Optional) **Python 3.11+** if running locally outside Docker

### 6.2. Step-by-Step Launch with Docker Compose

1. **Clone Repository & Enter Directory**:
   ```bash
   git clone https://github.com/ashis-solomon/dotkonnekt-smart-ticket-service.git
   cd dotkonnekt-smart-ticket-service
   ```

2. **Start All Services**:
   The application is pre-configured with zero-configuration defaults (no `.env` file required unless overriding settings):
   ```bash
   docker compose up --build -d
   ```
   *This starts the API, PostgreSQL 16, Redis 7, Celery Worker, Celery Beat, and Ollama.*

3. **Pull the Ollama LLM Model**:
   ```bash
   docker compose exec ollama ollama pull llama3.2:1b
   ```

4. **Verify Service Health**:
   ```bash
   curl http://localhost:8000/health
   ```
   Expected response: `{"success": true, "data": {"status": "healthy"}, ...}`

---

## 7. Future Improvements

1. **Unit & Integration Test Suite**: Implement comprehensive automated testing using `pytest` and testcontainers covering edge-case auth flows, Celery workers, and reminder polling routines.
2. **SSO / OAuth2 / OIDC Integration**: Integrate enterprise Identity Providers (Google Workspace, Okta, Azure AD, GitHub) via OpenID Connect alongside standard JWT auth.
3. **Versioned Database Migrations with Alembic**: Transition from automated startup DDL to versioned, linear Alembic migration scripts (`alembic upgrade head`).
4. **Distributed Caching Layer**: Implement Redis read-through caching for high-traffic read endpoints (`GET /tickets/{id}`) with automated cache invalidation upon ticket updates.
5. **Distributed Sliding Window Rate Limiting**: Move from in-memory SlowAPI limits to Redis-backed distributed token buckets per user and IP.
6. **Hybrid Vector & Semantic Search**: Incorporate `pgvector` alongside `pg_trgm` to support hybrid keyword + dense semantic similarity search.
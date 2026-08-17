"""Authentication, Argon2id password hashing, and Dual-Token JWT management."""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Any, Dict, Optional, Tuple
from uuid import UUID
import asyncpg
import jwt
from pwdlib import PasswordHash
import uuid6

from app.config import Settings
from app.schemas.auth import TokenResponse, UserLogin, UserRegister
from app.schemas.user import UserResponse, UserRole


class AuthService:
    def __init__(self, settings: Settings, pool: asyncpg.Pool):
        self.settings = settings
        self.pool = pool
        self.password_hasher = PasswordHash.recommended()

    def hash_password(self, password: str) -> str:
        """Hashes password using Argon2id."""
        return self.password_hasher.hash(password)

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verifies plain password against Argon2id hash."""
        return self.password_hasher.verify(plain_password, hashed_password)

    def _hash_token(self, token: str) -> str:
        """Computes SHA-256 digest of refresh token for safe database storage."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_access_token(self, user_id: str, email: str, role: str) -> str:
        """Creates a signed JWT Access Token with 30-minute expiry."""
        now = datetime.now(timezone.utc)
        expires_delta = timedelta(minutes=self.settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        payload = {
            "sub": str(user_id),
            "email": email,
            "role": role,
            "type": "access",
            "jti": str(uuid6.uuid7()),
            "iat": now,
            "exp": now + expires_delta,
        }
        return jwt.encode(payload, self.settings.JWT_SECRET_KEY, algorithm=self.settings.JWT_ALGORITHM)

    def decode_access_token(self, token: str) -> Dict[str, Any]:
        """Decodes and validates a signed JWT Access Token."""
        try:
            payload = jwt.decode(
                token,
                self.settings.JWT_SECRET_KEY,
                algorithms=[self.settings.JWT_ALGORITHM],
            )
            if payload.get("type") != "access":
                raise ValueError("Invalid token type")
            return payload
        except jwt.ExpiredSignatureError:
            raise ValueError("Token has expired")
        except jwt.PyJWTError as e:
            raise ValueError(f"Invalid token: {str(e)}")

    async def create_refresh_token(self, user_id: UUID) -> Tuple[str, datetime]:
        """Creates a secure high-entropy refresh token and persists its SHA-256 hash."""
        raw_token = secrets.token_urlsafe(48)
        token_hash = self._hash_token(raw_token)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=self.settings.REFRESH_TOKEN_EXPIRE_DAYS)
        token_id = uuid6.uuid7()

        query = """
            INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, is_revoked, created_at)
            VALUES ($1, $2, $3, $4, FALSE, $5)
        """
        async with self.pool.acquire() as conn:
            await conn.execute(query, token_id, user_id, token_hash, expires_at, now)

        return raw_token, expires_at

    async def register_user(self, data: UserRegister) -> UserResponse:
        """Registers a new user with Argon2id password hash."""
        async with self.pool.acquire() as conn:
            existing = await conn.fetchrow("SELECT id FROM users WHERE email = $1", data.email)
            if existing:
                raise ValueError("A user with this email already exists.")

            user_id = uuid6.uuid7()
            password_hash = self.hash_password(data.password)
            now = datetime.now(timezone.utc)
            role_val = data.role.value if data.role else UserRole.AGENT.value

            query = """
                INSERT INTO users (id, email, password_hash, full_name, role, is_active, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, TRUE, $6, $7)
                RETURNING id, email, full_name, role, is_active, created_at, updated_at
            """
            row = await conn.fetchrow(
                query, user_id, data.email, password_hash, data.full_name, role_val, now, now
            )
            return UserResponse(**dict(row))

    async def login_user(self, data: UserLogin) -> TokenResponse:
        """Authenticates user credentials and issues access + refresh tokens."""
        query = "SELECT id, email, password_hash, full_name, role, is_active, created_at, updated_at FROM users WHERE email = $1"
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, data.email)

        if not row or not self.verify_password(data.password, row["password_hash"]):
            raise ValueError("Invalid email or password.")

        if not row["is_active"]:
            raise ValueError("User account is deactivated.")

        user = UserResponse(**dict(row))
        access_token = self.create_access_token(str(user.id), user.email, user.role.value)
        refresh_token, _ = await self.create_refresh_token(user.id)

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=self.settings.access_token_expire_seconds,
            user=user,
        )

    async def rotate_refresh_token(self, refresh_token: str) -> Tuple[str, str, int]:
        """Rotates refresh token and issues a fresh access + refresh token pair."""
        token_hash = self._hash_token(refresh_token)
        now = datetime.now(timezone.utc)

        query = """
            SELECT r.id, r.user_id, r.expires_at, r.is_revoked, u.email, u.role, u.is_active
            FROM refresh_tokens r
            JOIN users u ON r.user_id = u.id
            WHERE r.token_hash = $1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, token_hash)
            if not row:
                raise ValueError("Invalid refresh token.")

            if row["is_revoked"]:
                raise ValueError("Refresh token has been revoked.")

            if row["expires_at"] <= now:
                raise ValueError("Refresh token has expired.")

            if not row["is_active"]:
                raise ValueError("User account is deactivated.")

            # Revoke current refresh token
            await conn.execute("UPDATE refresh_tokens SET is_revoked = TRUE WHERE id = $1", row["id"])

        # Issue new token pair
        access_token = self.create_access_token(str(row["user_id"]), row["email"], row["role"])
        new_refresh_token, _ = await self.create_refresh_token(row["user_id"])
        expires_in = self.settings.access_token_expire_seconds

        return access_token, new_refresh_token, expires_in

    async def revoke_refresh_token(self, refresh_token: str) -> None:
        """Revokes a refresh token on user logout."""
        token_hash = self._hash_token(refresh_token)
        query = "UPDATE refresh_tokens SET is_revoked = TRUE WHERE token_hash = $1"
        async with self.pool.acquire() as conn:
            await conn.execute(query, token_hash)

"""Authentication and token management endpoints."""

from typing import Dict
from fastapi import APIRouter, Depends, HTTPException, Request, status
import asyncpg

from app.api.deps import get_auth_service, get_current_user, get_db_pool
from app.config import Settings, get_settings
from app.schemas.auth import (
    LogoutRequest,
    TokenRefreshRequest,
    TokenRefreshResponse,
    TokenResponse,
    UserLogin,
    UserRegister,
)
from app.schemas.common import ApiResponse, MetaResponse, generate_request_id
from app.schemas.user import UserResponse, UserRole
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=ApiResponse[UserResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(
    request: Request,
    data: UserRegister,
    auth_service: AuthService = Depends(get_auth_service),
) -> ApiResponse[UserResponse]:
    request_id = getattr(request.state, "request_id", generate_request_id())
    try:
        user = await auth_service.register_user(data)
        return ApiResponse(
            data=user,
            meta=MetaResponse(requestId=request_id),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT
            if "already exists" in str(e)
            else status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post(
    "/login",
    response_model=ApiResponse[TokenResponse],
    status_code=status.HTTP_200_OK,
    summary="Authenticate credentials and issue JWT tokens",
)
async def login(
    request: Request,
    data: UserLogin,
    auth_service: AuthService = Depends(get_auth_service),
) -> ApiResponse[TokenResponse]:
    request_id = getattr(request.state, "request_id", generate_request_id())
    try:
        tokens = await auth_service.login_user(data)
        return ApiResponse(
            data=tokens,
            meta=MetaResponse(requestId=request_id),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )


@router.post(
    "/refresh",
    response_model=ApiResponse[TokenRefreshResponse],
    status_code=status.HTTP_200_OK,
    summary="Rotate refresh token and issue a fresh access token",
)
async def refresh_token(
    request: Request,
    data: TokenRefreshRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> ApiResponse[TokenRefreshResponse]:
    request_id = getattr(request.state, "request_id", generate_request_id())
    try:
        access_token, new_refresh_token, expires_in = await auth_service.rotate_refresh_token(
            data.refresh_token
        )
        return ApiResponse(
            data=TokenRefreshResponse(
                access_token=access_token,
                refresh_token=new_refresh_token,
                token_type="bearer",
                expires_in=expires_in,
            ),
            meta=MetaResponse(requestId=request_id),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )


@router.post(
    "/logout",
    response_model=ApiResponse[Dict[str, str]],
    status_code=status.HTTP_200_OK,
    summary="Revoke refresh token and invalidate session",
)
async def logout(
    request: Request,
    data: LogoutRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> ApiResponse[Dict[str, str]]:
    request_id = getattr(request.state, "request_id", generate_request_id())
    await auth_service.revoke_refresh_token(data.refresh_token)
    return ApiResponse(
        data={"message": "Logged out successfully. Token revoked."},
        meta=MetaResponse(requestId=request_id),
    )


@router.get(
    "/me",
    response_model=ApiResponse[UserResponse],
    status_code=status.HTTP_200_OK,
    summary="Get current user profile",
)
async def get_me(
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
) -> ApiResponse[UserResponse]:
    request_id = getattr(request.state, "request_id", generate_request_id())
    return ApiResponse(
        data=current_user,
        meta=MetaResponse(requestId=request_id),
    )

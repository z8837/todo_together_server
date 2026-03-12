from __future__ import annotations

import secrets
from typing import Optional

import jwt
from django.conf import settings
from django.utils import timezone

from .models import RefreshToken, User
from datetime import timedelta
from rest_framework import exceptions

ALGORITHM = 'HS256'

def issue_token_pair(user: User, request=None):
    refresh_obj, raw_refresh = create_refresh_token(user, request=request)
    access_token = build_access_token(user, refresh_obj)
    return access_token, refresh_obj


def create_refresh_token(user: User, request=None):
    raw_token = secrets.token_urlsafe(48)
    lifetime = settings.TOKEN_SETTINGS['REFRESH_TOKEN_LIFETIME']
    expires_at = timezone.now() + lifetime

    refresh = RefreshToken.objects.create(
        user=user,
        token=raw_token,
        expires_at=expires_at,
        user_agent=_extract_user_agent(request),
        client_ip=_extract_client_ip(request),
    )
    return refresh, raw_token


def build_access_token(user: User, refresh: RefreshToken):
    now = timezone.now()
    payload = {
        'sub': str(user.pk),
        'token_id': str(refresh.id),
        'type': 'access',
        'iat': now,
        'exp': now + settings.TOKEN_SETTINGS['ACCESS_TOKEN_LIFETIME'],
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str):
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])


def _extract_user_agent(request) -> str:
    if request is None:
        return ''
    return request.META.get('HTTP_USER_AGENT', '')[:255]


def _extract_client_ip(request) -> Optional[str]:
    if request is None:
        return None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')



def _now(): return timezone.now()

def refresh_with_sliding(raw_refresh_token: str):
    try:
        rt = RefreshToken.objects.select_related('user').get(token=raw_refresh_token)
    except RefreshToken.DoesNotExist as exc:
        raise exceptions.AuthenticationFailed("Invalid refresh token.") from exc

    if not rt.is_active:
        rt.revoke()
        raise exceptions.AuthenticationFailed("Refresh token expired. Please log in again.")

    rt.expires_at = _now() + settings.TOKEN_SETTINGS.get('REFRESH_TOKEN_LIFETIME', timedelta(days=45))   # 슬라이딩 연장
    rt.save(update_fields=['expires_at'])

    access = build_access_token(rt.user, rt)
    return rt.user, access, rt
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests
from django.conf import settings
from django.db import transaction
from django.db import models
from django.db.utils import IntegrityError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from rest_framework import exceptions

from .models import (
    RefreshToken, User, Project, Todo, Reminder, 
    Notification, TodoKind, Device, ProjectMember,
    NotificationPreference, UserNote, TodoUserState)

from .tokens import build_access_token, issue_token_pair

from firebase_admin import messaging, exceptions as fb_exceptions
from .firebase import get_firebase_app
from django.db.models import Q  

# from pyfcm import FCMNotification  # pip install pyfcm

logger = logging.getLogger(__name__)


REQUEST_TIMEOUT = 5


class SocialProvider(models.TextChoices):
    GOOGLE = 'google', 'Google'
    NAVER = 'naver', 'Naver'
    KAKAO = 'kakao', 'Kakao'
    APPLE = "apple", 'Apple'


@dataclass(slots=True)
class SocialProfile:
    provider: str
    provider_user_id: str
    email: Optional[str] = None
    nickname: Optional[str] = None
    name: Optional[str] = None


def normalize_provider(provider: str) -> str:
    value = (provider or '').strip().lower()
    if value not in SocialProvider.values:
        raise exceptions.ValidationError({'provider': _('Unsupported social login provider.')})
    return value


def handle_social_login(*, provider: str, token: str, nickname: Optional[str],
                        request=None, redirect_uri=None, identity_token=None):
    provider = normalize_provider(provider)

    # ✅ Apple: identity_token으로 먼저 sub/aud 확인 → 신규면 nickname 요구(422) → 그 다음에만 code 교환
    if provider == SocialProvider.APPLE:
        if not identity_token:
            raise exceptions.ValidationError({"identity_token": ["Apple identity_token is required."]})

        apple_client_id, id_claims = _parse_and_verify_apple_identity_token(identity_token)
        sub = id_claims.get("sub")
        if not sub:
            raise exceptions.AuthenticationFailed("Apple sub missing.")

        # (선택) Android 웹 플로우면 redirect_uri 꼭 필요하니 기본값 처리하고 싶으면 여기서:
        # if not redirect_uri:
        #     redirect_uri = settings.APPLE_REDIRECT_URI_DEFAULT

        # ✅ 신규 여부 먼저 판단 (여기서는 code 교환 X)
        exists = User.objects.filter(
            provider=SocialProvider.APPLE,
            provider_user_id=str(sub),
        ).exists()

        provided_nickname = _clean_nickname(nickname)
        if not exists and not provided_nickname:
            # ⭐ 여기서 422를 내면 authorizationCode를 아직 안 써서 재시도 가능
            raise UnprocessableEntity({"nickname": ["닉네임을 입력해주세요."]})

        # ✅ nickname이 있거나(신규) / 기존 유저면 이제 code 교환 (1회)
        token_payload = _exchange_apple_code(
            code=token.strip(),
            client_id=apple_client_id,
            redirect_uri=redirect_uri,
        )
        exchanged_id_token = token_payload.get("id_token")
        if not exchanged_id_token:
            raise exceptions.AuthenticationFailed("Apple id_token missing from token exchange.")

        exchanged_claims = _verify_apple_id_token(
            id_token=exchanged_id_token,
            client_id=apple_client_id,
        )

        # 안전장치: identity_token sub와 교환된 id_token sub가 동일해야 함
        if str(exchanged_claims.get("sub")) != str(sub):
            raise exceptions.AuthenticationFailed("Apple sub mismatch between identity_token and exchanged id_token.")

        email = exchanged_claims.get("email") or id_claims.get("email")

        profile = SocialProfile(
            provider=SocialProvider.APPLE,
            provider_user_id=str(sub),
            email=email,
            nickname=None,
            name=None,
        )

    else:
        # ✅ 기존 로직 유지 (Google/Naver/Kakao)
        profile = get_social_profile(provider, token, redirect_uri=redirect_uri)

    # ✅ 이후는 기존 그대로
    with transaction.atomic():
        user, created = sync_social_user(profile, nickname)
        now = timezone.now()
        User.objects.filter(pk=user.pk).update(last_login=now)

        access_token, refresh_token = issue_token_pair(user, request=request)

    return {
        "user": user,
        "created": created,
        "profile": profile,
        "access_token": access_token,
        "refresh_token": refresh_token,
    }


def refresh_access_token(raw_refresh_token: str):
    try:
        refresh = RefreshToken.objects.select_related('user').get(token=raw_refresh_token)
    except RefreshToken.DoesNotExist as exc:
        raise exceptions.AuthenticationFailed(_('Invalid refresh token. Please log in again.')) from exc

    if not refresh.is_active:
        refresh.revoke()
        raise exceptions.AuthenticationFailed(_('Refresh token expired. Please log in again.'))

    access_token = build_access_token(refresh.user, refresh)
    return refresh.user, access_token


def _clean_nickname(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    trimmed = raw.strip()
    if not trimmed:
        return None
    return trimmed


from rest_framework.exceptions import APIException

class UnprocessableEntity(APIException):
    status_code = 422
    default_code = 'unprocessable_entity'

    def __init__(self, detail=None):
        if detail is None:
            detail = {'nickname': ['닉네임을 입력해주세요.']}
        super().__init__(detail)


def sync_social_user(profile: SocialProfile, nickname: Optional[str]):
    provided_nickname = _clean_nickname(nickname)

    # 1) 기존 유저 찾기: provider + provider_user_id
    user = User.objects.filter(
        provider=profile.provider,
        provider_user_id=profile.provider_user_id,
    ).first()

    # 2) 없으면 신규: 닉네임 필수
    if user is None:
        if not provided_nickname:
            raise UnprocessableEntity({"nickname": ["닉네임을 입력해주세요."]})

        email = profile.email or f"{profile.provider}-{profile.provider_user_id}@no-email.local"

        user = User.objects.create_user(
            email=email,
            provider=profile.provider,
            provider_user_id=profile.provider_user_id,
            nickname=provided_nickname,
        )
        return user, True

    # 3) 있으면 업데이트(선택)
    updated_fields = []

    if profile.email and user.email != profile.email:
        user.email = profile.email
        updated_fields.append("email")

    if provided_nickname and provided_nickname != user.nickname:
        user.nickname = provided_nickname
        updated_fields.append("nickname")

    if not user.nickname and profile.nickname:
        fb = _clean_nickname(profile.nickname)
        if fb:
            user.nickname = fb
            updated_fields.append("nickname")

    if updated_fields:
        user.save(update_fields=list(set(updated_fields)))

    return user, False



def get_social_profile(provider: str, token: str, redirect_uri=None) -> SocialProfile:
    token = token.strip()
    if provider == SocialProvider.GOOGLE:
        return _fetch_google_profile(token)
    if provider == SocialProvider.NAVER:
        return _fetch_naver_profile(token)
    if provider == SocialProvider.KAKAO:
        return _fetch_kakao_profile(token)
    if provider == SocialProvider.APPLE:
        return _fetch_apple_profile(token, redirect_uri=redirect_uri)  # ✅ token에 authCode를 담아 보냄
    raise exceptions.ValidationError({'provider': _('Unsupported social login provider.')})

WEB_CLIENT_ID  = settings.WEB_CLIENT_ID
def _fetch_google_profile(token: str) -> SocialProfile:
    request_adapter = google_requests.Request()
    # client_id = settings.SOCIAL_LOGIN.get('GOOGLE_CLIENT_ID', '').strip()
    client_id = WEB_CLIENT_ID
    try:
        id_info = google_id_token.verify_oauth2_token(
            token,
            request_adapter,
            audience=client_id or None,
        )
    except (ValueError, google_exceptions.GoogleAuthError) as exc:
        logger.warning('Google token validation failed: %s', exc)
        raise exceptions.AuthenticationFailed(_('Failed to validate Google token. Please try again.')) from exc

    subject = id_info.get('sub')
    if not subject:
        raise exceptions.AuthenticationFailed(_('Google user information is missing.'))

    email = id_info.get('email')
    # For privacy, some responses might not include email unless requested scopes are present.
    nickname = id_info.get('name') or id_info.get('given_name')

    return SocialProfile(
        provider=SocialProvider.GOOGLE,
        provider_user_id=str(subject),
        email=email,
        nickname=nickname,
        name=id_info.get('name'),
    )


def _fetch_naver_profile(token: str) -> SocialProfile:
    headers = {'Authorization': f'Bearer {token}'}
    try:
        response = requests.get(
            'https://openapi.naver.com/v1/nid/me',
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.exception('Naver token validation failed: %s', exc)
        raise exceptions.AuthenticationFailed(_('Failed to validate Naver token. Please try again.')) from exc

    data = response.json()
    if response.status_code != 200 or data.get('resultcode') != '00':
        raise exceptions.AuthenticationFailed(_('Invalid Naver token.'))

    profile = data.get('response', {})
    user_id = profile.get('id')
    if not user_id:
        raise exceptions.AuthenticationFailed(_('Naver user information is missing.'))

    nickname = profile.get('nickname') or profile.get('name')

    return SocialProfile(
        provider=SocialProvider.NAVER,
        provider_user_id=str(user_id),
        email=profile.get('email'),
        nickname=nickname,
        name=profile.get('name'),
    )


def _fetch_kakao_profile(token: str) -> SocialProfile:
    headers = {"Authorization": f"Bearer {token.strip()}"}

    try:
        response = requests.get(
            "https://kapi.kakao.com/v2/user/me",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.exception("Kakao request failed: %s", exc)
        raise exceptions.AuthenticationFailed(_("Failed to validate Kakao token.")) from exc

    if response.status_code != 200:
        logger.warning("Kakao token invalid: %s %s", response.status_code, response.text[:200])
        raise exceptions.AuthenticationFailed(_("Invalid Kakao token."))

    data = response.json()
    user_id = data.get("id")
    if not user_id:
        raise exceptions.AuthenticationFailed(_("Kakao user information is missing."))

    kakao_account = data.get("kakao_account") or {}
    profile = kakao_account.get("profile") or {}

    email = kakao_account.get("email")  # 없을 수 있음
    nickname = profile.get("nickname")  # 없을 수 있음

    return SocialProfile(
        provider=SocialProvider.KAKAO,
        provider_user_id=str(user_id),
        email=email,
        nickname=nickname,
        name=None,
    )



import jwt  # PyJWT

APPLE_ISSUER = "https://appleid.apple.com"
APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"

def _build_apple_client_secret(*, client_id: str) -> str:
    now = int(time.time())
    exp = now + 10 * 60

    headers = {"kid": settings.APPLE_KEY_ID, "alg": "ES256"}
    payload = {
        "iss": settings.APPLE_TEAM_ID,
        "iat": now,
        "exp": exp,
        "aud": APPLE_ISSUER,
        "sub": client_id,
    }

    # ✅ 민감값(키)은 절대 로그 X / TEAM_ID, KEY_ID, iat/exp 정도만
    logger.info(
        "[APPLE] build_client_secret kid=%s team=%s sub(client_id)=%s iat=%s exp=%s",
        settings.APPLE_KEY_ID,
        settings.APPLE_TEAM_ID,
        client_id,
        now,
        exp,
    )

    private_key = settings.APPLE_PRIVATE_KEY_P8.replace("\\n", "\n")
    return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)





REQUEST_TIMEOUT = 10

def _exchange_apple_code(*, code: str, client_id: str, redirect_uri: str | None = None) -> dict:
    client_secret = _build_apple_client_secret(client_id=client_id)

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if redirect_uri:
        data["redirect_uri"] = redirect_uri  # ✅ Android web 플로우면 거의 필수

    headers = {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "yourapp/1.0"}

    # ✅ code는 민감하니 앞 6자만
    logger.info(
        "[APPLE_TOKEN] request client_id=%s code_prefix=%s redirect_uri=%s",
        client_id,
        (code[:6] + "...") if code else None,
        redirect_uri,
    )

    try:
        resp = requests.post(APPLE_TOKEN_URL, data=data, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.exception("[APPLE_TOKEN] request failed client_id=%s", client_id)
        raise exceptions.AuthenticationFailed("Apple token exchange failed.") from exc

    # ✅ 응답 파싱 (JSON 실패 대비)
    raw_text = (resp.text or "")[:800]  # 너무 길면 잘라
    try:
        payload = resp.json() if resp.content else {}
    except Exception:
        payload = {"_non_json": raw_text}

    logger.warning(
        "[APPLE_TOKEN] response client_id=%s status=%s body=%s",
        client_id,
        resp.status_code,
        payload,
    )

    if resp.status_code != 200 or "error" in payload:
        # Apple이 주는 키: error, error_description 등
        err = payload.get("error") if isinstance(payload, dict) else None
        desc = payload.get("error_description") if isinstance(payload, dict) else None
        raise exceptions.AuthenticationFailed(f"Apple token exchange error: {err} {desc or ''}".strip())

    return payload




from jwt import PyJWKClient


def _verify_apple_id_token(*, id_token: str, client_id: str) -> dict:
    try:
        hdr = jwt.get_unverified_header(id_token)
        logger.info("[APPLE_ID_TOKEN] header kid=%s alg=%s client_id=%s", hdr.get("kid"), hdr.get("alg"), client_id)
    except Exception:
        logger.warning("[APPLE_ID_TOKEN] failed to read token header client_id=%s", client_id)

    jwk_client = PyJWKClient(APPLE_JWKS_URL)
    signing_key = jwk_client.get_signing_key_from_jwt(id_token)

    try:
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=APPLE_ISSUER,
            options={"verify_exp": True},
        )
        # ✅ 핵심 claim만
        logger.info(
            "[APPLE_ID_TOKEN] verified iss=%s aud=%s sub_prefix=%s email_present=%s",
            claims.get("iss"),
            claims.get("aud"),
            (str(claims.get("sub"))[:6] + "...") if claims.get("sub") else None,
            bool(claims.get("email")),
        )
        return claims
    except jwt.PyJWTError as exc:
        # 디버깅용으로 서명검증 없이 aud/iss만 훑어보기(신뢰X, 로그용)
        try:
            unverified = jwt.decode(id_token, options={"verify_signature": False})
            logger.warning(
                "[APPLE_ID_TOKEN] verify failed client_id=%s iss=%s aud=%s err=%s",
                client_id, unverified.get("iss"), unverified.get("aud"), exc
            )
        except Exception:
            logger.warning("[APPLE_ID_TOKEN] verify failed client_id=%s err=%s", client_id, exc)

        raise exceptions.AuthenticationFailed("Invalid Apple id_token.") from exc

def _parse_and_verify_apple_identity_token(identity_token: str) -> tuple[str, dict]:
    """
    return: (client_id(aud), verified_claims)
    - aud가 bundle_id일 수도, service_id일 수도 있어서 여기서 자동 결정
    """
    try:
        unverified = jwt.decode(identity_token, options={"verify_signature": False})
    except Exception as exc:
        raise exceptions.AuthenticationFailed("Invalid Apple identity_token format.") from exc

    client_id = unverified.get("aud")
    if not client_id or client_id not in settings.APPLE_CLIENT_IDS:
        raise exceptions.AuthenticationFailed("Apple aud(client_id) is not allowed.")

    claims = _verify_apple_id_token(id_token=identity_token, client_id=client_id)
    return client_id, claims



def _fetch_apple_profile(auth_code: str, redirect_uri: str | None = None) -> SocialProfile:
    code = auth_code.strip()
    last_error = None

    for client_id in [cid for cid in settings.APPLE_CLIENT_IDS if cid]:
        logger.info("[APPLE] try client_id=%s redirect_uri=%s", client_id, redirect_uri)
        try:
            token_payload = _exchange_apple_code(code=code, client_id=client_id, redirect_uri=redirect_uri)
            id_token = token_payload.get("id_token")
            if not id_token:
                raise exceptions.AuthenticationFailed("Apple id_token missing.")

            claims = _verify_apple_id_token(id_token=id_token, client_id=client_id)

            sub = claims.get("sub")
            if not sub:
                raise exceptions.AuthenticationFailed("Apple sub missing.")

            email = claims.get("email")
            logger.info("[APPLE] success client_id=%s sub_prefix=%s", client_id, str(sub)[:6] + "...")
            return SocialProfile(
                provider=SocialProvider.APPLE,
                provider_user_id=str(sub),
                email=email,
                nickname=None,
                name=None,
            )
        except exceptions.APIException as exc:
            logger.warning("[APPLE] failed client_id=%s err=%s", client_id, exc)
            last_error = exc
            continue

    raise last_error or exceptions.AuthenticationFailed("Apple login failed.")









import time
IOS_BUNDLE_ID = "kr.co.heeyun.todotogether"  # ← 너 앱 번들ID로

def build_ios_alert_apns(title: str, body: str):
    return messaging.APNSConfig(
        headers={
            "apns-push-type": "alert",
            "apns-priority": "10",
            "apns-topic": IOS_BUNDLE_ID,
            # 1시간 뒤 만료(원하면 조정/삭제)
            "apns-expiration": str(int(time.time()) + 3600),
        },
        payload=messaging.APNSPayload(
            aps=messaging.Aps(
                alert=messaging.ApsAlert(title=title, body=body),
                sound="default",
                # badge=1,  # 필요하면
            )
        ),
    )



def send_push_to_user(
    user,
    title: str,
    body: str,
    data: dict | None = None,
    *,
    force_data_only: bool = False,
):
    """
    유저의 활성 디바이스에 FCM 발송.
    - notification_pref 에 따라 data-only vs notification+data 분기
    - force_data_only=True 이면 무조건 data-only로 전송
    - FCM 에러는 위로 안 올리고 로그만 남김

    ✅ 개선점
    - 토큰을 한번에 모아서 멀티캐스트 형태로 전송
    - devices.exists() 같은 추가 쿼리 없이 1번만 조회
    - 폐기 토큰 처리(registration-token-not-registered)
    """

    # 1) FCM 초기화
    try:
        get_firebase_app()
    except Exception:
        logger.exception("FCM 초기화 실패 (user=%s)", getattr(user, "id", None))
        return

    # 2) 활성 디바이스 + 토큰 로드 (쿼리 1번)
    devices = list(user.devices.filter(is_active=True).only("id", "fcm_token"))
    if not devices:
        return

    # ⚠️ devices와 tokens의 인덱스를 맞추기 위해 (토큰 없는 디바이스는 제외한) paired 리스트를 만든다
    paired = [(d, d.fcm_token) for d in devices if getattr(d, "fcm_token", None)]
    if not paired:
        return

    devices2 = [p[0] for p in paired]
    tokens = [p[1] for p in paired]

    # 3) payload 문자열 강제
    data = data or {}
    msg_type = data.get("type")

    payload: dict[str, str] = {str(k): ("" if v is None else str(v)) for k, v in data.items()}

    # 4) 알림 설정(pref)에 따라 data-only 여부 결정
    pref = getattr(user, "notification_pref", None)
    data_only = bool(force_data_only)

    if not force_data_only and pref and msg_type:
        if msg_type == "project_invite" and not getattr(pref, "project_invite_enabled", True):
            data_only = True
        elif msg_type == "project_updated" and not getattr(pref, "project_activity_enabled", True):
            data_only = True
        elif msg_type in ("todo_created", "todo_updated") and not getattr(pref, "todo_activity_enabled", True):
            data_only = True
        elif msg_type == "todo_reminder" and not getattr(pref, "todo_reminder_enabled", True):
            return  # 리마인더 OFF면 아예 안 보냄

    android_cfg = messaging.AndroidConfig(priority="high")

    # 5) 메시지 구성
    if data_only:
        message = messaging.MulticastMessage(
            data=payload,
            tokens=tokens,
        )
    elif msg_type == "todo_reminder":
        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data=payload,
            tokens=tokens,
            android=android_cfg,
            apns=build_ios_alert_apns(title, body),
        )
    else:
        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data=payload,
            tokens=tokens,
        )

    # 6) 전송
    try:
        # ✅ send_multicast 가 없는 환경을 대비해 send_each_for_multicast 사용
        if hasattr(messaging, "send_each_for_multicast"):
            resp = messaging.send_each_for_multicast(message)
        else:
            # fallback: 아주 옛 버전이면 단건 전송으로 degrade
            logger.warning("firebase_admin.messaging.send_each_for_multicast 미지원 → 단건 전송 fallback")
            class _Resp:
                responses = []
            resp = _Resp()
            for t in tokens:
                try:
                    r = messaging.send(
                        messaging.Message(
                            notification=None if data_only else messaging.Notification(title=title, body=body),
                            data=payload,
                            token=t,
                            android=android_cfg if (not data_only and msg_type == "todo_reminder") else None,
                            apns=build_ios_alert_apns(title, body) if (not data_only and msg_type == "todo_reminder") else None,
                        )
                    )
                    resp.responses.append(type("R", (), {"success": True, "exception": None})())
                except Exception as e:
                    resp.responses.append(type("R", (), {"success": False, "exception": e})())
    except fb_exceptions.FirebaseError as e:
        code = getattr(e, "code", "")
        logger.warning(
            "FCM 전송 실패 (user=%s, code=%s, msg=%s)",
            getattr(user, "id", None),
            code,
            e,
        )
        return
    except Exception:
        logger.exception("FCM 전송 중 알 수 없는 에러 (user=%s)", getattr(user, "id", None))
        return

    # 7) 실패 토큰 처리
    for i, r in enumerate(resp.responses):
        if getattr(r, "success", False):
            continue

        exc = getattr(r, "exception", None)
        code = getattr(exc, "code", "") if exc else ""
        logger.warning(
            "FCM 전송 실패 (user=%s, device=%s, code=%s, msg=%s)",
            getattr(user, "id", None),
            getattr(devices2[i], "id", None) if i < len(devices2) else None,
            code,
            exc,
        )

        try:
            d = devices2[i]
            d.is_active = False
            d.save(update_fields=["is_active"])
        except Exception:
            logger.exception("FCM 실패 토큰 처리 중 에러 (user=%s, idx=%s)", getattr(user, "id", None), i)





def send_profile_updated_notifications(user: User):
    projects = Project.objects.filter(Q(owner=user) | Q(membership__user=user)).distinct()
    if not projects.exists():
        return

    user_ids = set()
    for p in projects:
        user_ids.add(p.owner_id)
        user_ids.update(p.members.values_list("id", flat=True))

    user_ids.discard(user.id)
    if not user_ids:
        return

    data = {
        "type": "user_profile_updated",
        "userId": str(user.id),
        "nickname": user.nickname or "",
    }

    def _enqueue():
        from .tasks import push_to_users_task
        push_to_users_task.delay(list(user_ids), "", "", data, True)

    transaction.on_commit(_enqueue)



def send_project_invite_notification(invite):
    invited_user = invite.invited_user
    if not invited_user:
        return

    title = f"'{invite.project.name}' 프로젝트에 초대되었습니다"
    body = f"{invite.invited_by.nickname or invite.invited_by.email} 님이 초대했습니다."
    data = {
        "type": "project_invite",
        "projectId": str(invite.project.id),
        "inviteToken": invite.token,
    }

    def _enqueue():
        from .tasks import push_to_user_task  # ✅ 함수 안 import
        push_to_user_task.delay(invited_user.id, title, body, data, False)

    transaction.on_commit(_enqueue)


def _user_payload(user):
    return {
        "email": user.email,
        "nickname": user.nickname,
        # 필요하면 더 추가 (프로필이미지 등)
        # "avatarUrl": user.avatar_url,
    }

def send_project_updated_notifications(
    project: Project,
    editor,
    changes: list[dict] | None = None,
    event: str = "updated",
    members_snapshot=None,
):
    changes = changes or []

    if members_snapshot is None:
        members = list(project.members.all()) + [project.owner]
    else:
        members = list(members_snapshot)

    uniq = {}
    for u in members:
        uniq[u.id] = u
    members = list(uniq.values())

    if event == "destroy":
        title = f"'{project.name}' 프로젝트가 삭제되었습니다"
        body = f"{editor.nickname or editor.email} 님이 '{project.name}' 프로젝트를 삭제했습니다."
        data = {"type": "project_destroy", "projectId": str(project.id)}
        kind = Notification.Kind.PROJECT_UPDATED
    else:
        title = f"'{project.name}' 프로젝트가 수정되었습니다"
        body = f"{editor.nickname or editor.email} 님이 '{project.name}'을 수정했습니다."
        data = {"type": "project_updated", "projectId": str(project.id)}
        kind = Notification.Kind.PROJECT_UPDATED

    # ✅ DB Notification은 동기(빠름), FCM만 비동기로
    target_ids = []
    for user in members:
        is_editor = (user.id == editor.id)
        target_ids.append(user.id)

        Notification.objects.create(
            user=user,
            kind=kind,
            project=project,
            message=body,
            is_read=is_editor,
            read_at=timezone.now() if is_editor else None,
            extra={
                "project_id": str(project.id),
                "editor_id": str(editor.id),
                "from": _user_payload(editor),
                "event": event,
                "changes": changes,
            },
        )

    def _enqueue():
        from .tasks import push_to_users_task  # ✅ 함수 안 import
        push_to_users_task.delay(target_ids, title, body, data, False)

    transaction.on_commit(_enqueue)


def send_todo_activity_notifications(todo: Todo, actor, event: str, changes: list[dict] | None = None):
    """
    event: "created" | "updated"
    """
    changes = changes or []

    project = todo.project
    members = list(project.members.all()) + [project.owner]

    for user in members:
        
        is_actor = user.id == actor.id
        if event == "created":
            title = f"[{project.name}] 새 TODO가 생성되었습니다"
            body = f"{actor.nickname or actor.email} 님이 '{todo.title}'를 추가했습니다."
            kind = Notification.Kind.TODO_CREATED
        elif event == "destroy":
            title = f"[{project.name}] TODO가 삭제되었습니다"
            body = f"{actor.nickname or actor.email} 님이 '{todo.title}'를 삭제했습니다."
            kind = Notification.Kind.TODO_UPDATED
        else:
            title = f"[{project.name}] TODO가 수정되었습니다"
            body = f"{actor.nickname or actor.email} 님이 '{todo.title}'를 수정했습니다."
            kind = Notification.Kind.TODO_UPDATED

        data = {
            "type": f"todo_{event}",
            "projectId": str(project.id),
            "todoId": str(todo.id),
        }

        force_data_only = todo.kind == TodoKind.CHECKLIST
        if not force_data_only:
            Notification.objects.create( #TODO
                user=user,
                kind=kind,
                project=project,
                message=body,  # 앱에서 리스트에 보여줄 문구
                is_read=is_actor,
                read_at= timezone.now() if is_actor else None,
                extra={
                    "todo_id": str(todo.id),
                    "project_id": str(project.id),
                    "actor_id": str(actor.id),
                    "event": event,
                    "from" : _user_payload(actor),
                    "changes": changes,
                },
            )
        
        send_push_to_user(user, title, body, data, force_data_only= force_data_only) 

        



def send_todo_reminder_notification(user: User, reminder: Reminder):
    todo = reminder.todo
    project = todo.project

    if todo.kind == TodoKind.CHECKLIST:
        title = f"[{project.name}] TODO 알림"
        body = f"곧 마감될 TODO: {todo.title}"
    else : 
        title = f"[{project.name}] 일정 알림"
        body = f"곧 시작할 일정: {todo.title}"

    data = {
        "type": "todo_reminder",
        "projectId": str(project.id),
        "todoId": str(todo.id),
        "reminderId": str(reminder.id),
    }

    send_push_to_user(user, title, body, data)




def schedule_next_reminders_for_todo(todo: Todo):
    """
    이 TODO에 대해 '다음 알림' Reminder 1건만 생성.
    - 기존 미발송 Reminder는 삭제
    - todo.next_alarm_at()이 None이면 생성 안 함
    """
    alarm_at = todo.next_alarm_at()

    # 기존 미발송 예약 삭제
    Reminder.objects.filter(todo=todo, sent=False).delete()

    if alarm_at is None:
        return

    Reminder.objects.create(
        todo=todo,
        scheduled_at=alarm_at,
    )




# services.py (맨 아래 쪽 아무 데나)


def _get_project_user_ids(project: Project, onlyOwner : bool) -> set:
    """
    해당 프로젝트에 속한 유저 ids (owner + members)
    """
    ids = {project.owner_id}
    if onlyOwner : return ids
    member_ids = project.members.values_list("id", flat=True)
    ids.update(member_ids)
    return ids


def send_project_membership_changed_data_only(
    *,
    project: Project,
    event_type: str,
    actor: User,
    subject_user: User,
    role: Optional[str] = None,
    extra: Optional[dict] = None,
    onlyOwner: bool = False,
):
    user_ids = _get_project_user_ids(project, onlyOwner)
    user_ids.discard(subject_user.id)

    if not user_ids:
        return

    payload = {
        "type": event_type,
        "projectId": str(project.id),
        "actorId": str(actor.id),
        "actorNickname": actor.nickname or "",
        "userId": str(subject_user.id),
        "nickname": subject_user.nickname or "",
        "role": role or "",
        "ownerId": str(project.owner_id),
    }
    if extra:
        for k, v in extra.items():
            payload[str(k)] = "" if v is None else str(v)

    def _enqueue():
        from .tasks import push_to_users_task
        push_to_users_task.delay(list(user_ids), "", "", payload, True)

    transaction.on_commit(_enqueue)






ROLE_PRIORITY = [
    ProjectMember.Role.MANAGER,
    ProjectMember.Role.WRITER,
    ProjectMember.Role.READER,
]

def pick_new_owner(project):
    """
    project: Project instance (membership prefetch 되어있으면 쿼리 절약)
    return: User | None
    """
    # membership 관계명: project.membership (너 코드 기준)
    qs = project.membership.filter(is_deleted=False).select_related("user")

    for role in ROLE_PRIORITY:
        pm = qs.filter(role=role).first()
        if not pm:
            continue
        u = pm.user
        if not u:
            continue
        # 탈퇴/비활성 유저는 제외
        if getattr(u, "is_deleted", False):
            continue
        if not getattr(u, "is_active", True):
            continue
        return u

    return None





from rest_framework.exceptions import ValidationError

def leave_project(
    *,
    project,
    user,
    new_owner=None,
    bump_version=True,
    notify_fn=None,   # send_project_membership_changed_data_only 같은 함수 주입 (선택)
):
    """
    프로젝트 나가기 공용 로직.
    - 일반 멤버: ProjectMember 삭제
    - owner: new_owner에게 owner 위임 + new_owner 멤버십 삭제 + 기존 owner 멤버십 삭제
    """

    with transaction.atomic():
        # ✅ 일반 멤버
        if project.owner_id != user.id:
            try:
                membership = ProjectMember.objects.get(project=project, user=user)
            except ProjectMember.DoesNotExist:
                raise ValidationError({"detail": "이 프로젝트의 멤버가 아닙니다."})

            membership_role = membership.role
            membership.delete()

            if bump_version:
                project.bump_version(save=True)

            if notify_fn:
                notify_fn(
                    project=project,
                    event_type="project_member_left",
                    actor=user,
                    subject_user=user,
                    role=membership_role,
                )
            return

        # ✅ owner인 경우: new_owner 필수
        if new_owner is None:
            raise ValidationError({"detail": "owner가 나갈 때는 new_owner가 필요합니다."})
        if new_owner.id == user.id:
            raise ValidationError({"detail": "본인을 새 owner로 지정할 수 없습니다."})

        if not ProjectMember.objects.filter(project=project, user=new_owner).exists():
            raise ValidationError({"detail": "new_owner는 이 프로젝트의 멤버가 아닙니다."})

        # owner 위임
        project.owner = new_owner
        project.save(update_fields=["owner", "updated_at"])

        # owner는 membership에 포함되지 않는 정책이므로 new_owner 멤버십 삭제
        ProjectMember.objects.filter(project=project, user=new_owner).delete()

        # 기존 owner 멤버십 삭제(있다면)
        ProjectMember.objects.filter(project=project, user=user).delete()

        if bump_version:
            project.bump_version(save=True)

        if notify_fn:
            notify_fn(
                project=project,
                event_type="project_member_left",
                actor=user,
                subject_user=user,
                role="owner",
                extra={"newOwnerId": str(new_owner.id)},
            )


@transaction.atomic
def withdraw_user(*, user, notify_fn=None):
    # ✅ 내가 속한 프로젝트들(owned + member)
    projects = Project.all_objects.filter(
        Q(owner=user) | Q(membership__user=user),
        is_deleted=False
    ).distinct().prefetch_related("membership__user")

    for project in projects:
        if project.owner_id == user.id:
            new_owner = pick_new_owner(project)
            if new_owner:
                leave_project(
                    project=project,
                    user=user,
                    new_owner=new_owner,
                    notify_fn=notify_fn,
                )
            else:
                project.soft_delete(save=True)
        else:
            leave_project(
                project=project,
                user=user,
                notify_fn=notify_fn,
            )

    # 기존 탈퇴 처리(디바이스/토큰/익명화 등)
    Device.objects.filter(user=user).update(is_active=False)
    RefreshToken.objects.filter(user=user).delete()
    NotificationPreference.objects.filter(user=user).delete()
    UserNote.objects.filter(user=user).delete()
    TodoUserState.objects.filter(user=user).delete()

    user.withdraw()
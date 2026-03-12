# mysite/apps/todo_together/authentication.py
from typing import Optional, Tuple
import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework import status, mixins

UserModel = get_user_model()  # 런타임 용도 (DB 조회 등에 사용)

class AccessTokenAuthentication(BaseAuthentication):
    keyword = b"Bearer"
    algorithms = getattr(settings, "JWT_ALGORITHMS", ["HS256"])
    secret_key = getattr(settings, "JWT_SECRET_KEY", settings.SECRET_KEY)

    def authenticate(self, request) -> Optional[Tuple[AbstractBaseUser, str]]:
        auth = get_authorization_header(request)
        if not auth:
            return None

        parts = auth.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            return None

        token = parts[1].decode("utf-8")

        try:
            payload = jwt.decode(token, self.secret_key, algorithms=self.algorithms)
        except jwt.ExpiredSignatureError:
            print('여기!!!!')
            raise exceptions.AuthenticationFailed("토큰이 만료되었습니다.")
        except jwt.InvalidTokenError:
            raise exceptions.AuthenticationFailed("유효하지 않은 토큰입니다.")

        # access 토큰만 허용
        token_type = (payload.get("token_type") or payload.get("type") or "").lower()
        if token_type != "access":
            raise exceptions.AuthenticationFailed("access 토큰이 아닙니다.")

        # ✅ 여러 키 fallback 지원
        user_id = (
            payload.get("user_id")
            or payload.get("uid")
            or payload.get("sub")
            or payload.get("id")
            or payload.get("userId")
        )

        # 일부 구현은 payload["user"] = { "id": ... } 구조일 수 있음
        if not user_id and isinstance(payload.get("user"), dict):
            user_id = payload["user"].get("id")

        # 또는 email로만 넣었을 수도 있음
        email = payload.get("email")

        if not user_id and not email:
            raise exceptions.AuthenticationFailed("user_id 클레임이 없습니다.")

        try:
            if user_id is not None:
                # 문자열 숫자면 int 캐스팅 시도
                try:
                    user_id_cast = int(user_id)
                except (TypeError, ValueError):
                    user_id_cast = user_id  # UUID 등 다른 PK 형태면 그대로 사용
                user = UserModel.objects.get(pk=user_id_cast, is_active=True)
            else:
                user = UserModel.objects.get(email=email, is_active=True)
        except UserModel.DoesNotExist:
            raise exceptions.AuthenticationFailed("사용자를 찾을 수 없습니다.")

        return (user, token)

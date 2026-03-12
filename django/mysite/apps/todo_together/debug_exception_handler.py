from rest_framework.views import exception_handler
from rest_framework import exceptions, status

def custom_exception_handler(exc, context):
    # DRF 기본 핸들러로 Response 먼저 생성
    response = exception_handler(exc, context)

    # access_token 인증 실패 관련은 항상 401로 고정
    if isinstance(exc, exceptions.AuthenticationFailed):
        if response is not None:
            response.status_code = status.HTTP_401_UNAUTHORIZED

            # 필요하면 code 붙여주기 (클라에서 구분할 수 있게)
            if isinstance(response.data, dict) and "code" not in response.data:
                # detail만 있는 경우
                detail = response.data.get("detail", "")
                if "만료" in str(detail):
                    response.data = {
                        "code": "token_expired",
                        "detail": detail,
                    }
                else:
                    response.data = {
                        "code": "auth_failed",
                        "detail": detail,
                    }

    return response
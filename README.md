# todo_together_server

Todo Together 앱을 위한 Django 백엔드 서버입니다. 협업 프로젝트, 일정형 할 일, 체크리스트, 초대/알림, 디바이스 푸시 등록, 계정 삭제 흐름을 REST API로 제공합니다.

## 주요 기능

- 소셜 로그인: Google, Naver, Kakao, Apple 로그인과 자체 Access/Refresh 토큰 발급
- 사용자 기능: 내 정보 조회, 닉네임 수정, 로그아웃, 회원 탈퇴, 개인 메모 저장
- 프로젝트 협업: 프로젝트 생성/수정/삭제, 멤버 역할 관리, 초대/수락/거절
- Todo 관리: 일정형 Todo와 체크리스트 분리, 반복 일정, 알림 시간, 숨김 처리, 동기화 API
- 알림: 앱 내 알림, FCM 디바이스 등록, 알림 설정, Celery 기반 리마인더 작업
- 부가 데이터: 공휴일 동기화, 개인정보처리방침/지원/마케팅 페이지, 계정 삭제 확인 페이지

## 디렉터리 구조

```text
todo_together_server/
├─ django/
│  ├─ manage.py
│  └─ mysite/
│     ├─ settings.py
│     ├─ urls.py
│     ├─ celery.py
│     ├─ apps/
│     │  ├─ todo_together/
│     │  └─ firstapp/
│     └─ core/
├─ .gitignore
├─ requirements.txt
└─ README.md
```

## 기술 스택

- Python / Django 5
- Django REST Framework
- Celery + Redis
- MySQL
- Firebase Admin SDK
- Google OAuth 검증, PyJWT

## 프로젝트 색상 API

`Project.color`는 `VARCHAR(7)`에 대문자 `#RRGGBB` 문자열로 저장합니다.
미지정 상태는 빈 문자열(`""`)이며, 기존 프로젝트에도 이 값이 적용됩니다.
문자열의 미지정 상태를 빈 문자열로 두는 것은 [Django의 권장 방식](https://docs.djangoproject.com/en/5.2/ref/models/fields/#null)입니다.
ORM 기본값과 DB 기본값을 함께 설정하여 배포 중 구버전 서버가 색상 컬럼을
포함하지 않고 INSERT하더라도 동작합니다.

- 생성: `POST /toto/projects/`
- 수정: `PATCH /toto/projects/{id}/` 또는 `PUT /toto/projects/{id}/`
- 조회: 프로젝트 목록, 상세, `/toto/projects/sync/` 응답에 `color`가 포함됩니다.
- `"#1a56ff"` 또는 `"1a56ff"` 입력은 `"#1A56FF"`로 정규화합니다.
- 생성 시 `color` 생략은 미지정을 의미합니다.
- 수정 시 `color` 생략은 기존 색상 유지를 의미합니다. 구버전 앱의 요청도 허용합니다.
- 수정 시 `"color": ""` 또는 `"color": null`은 색상을 해제합니다.
- 6자리 RGB 외의 형식, 잘못된 hex, 문자열이 아닌 입력은 HTTP 400입니다.
- 수정 권한은 기존과 동일하게 owner/manager이며, 색상 변경에도 프로젝트 버전이 증가합니다.

```json
{"color": "#1A56FF"}
```

색상 필드는 추가 필드이며 기존 필드나 응답 구조를 바꾸지 않습니다.
기존 앱은 새 필드를 무시하고 기존 색상 표시를 계속 사용합니다.
앱의 색상 선택/달력 표시는 별도 작업입니다.

적용: `python manage.py migrate todo_together --database=toto_db`

회귀 검사: `python manage.py test mysite.apps.todo_together --settings=mysite.test_settings`
이 설정은 운영 DB 이름을 사용하지 않고 별도 임시 MySQL 테스트 DB를 생성/제거합니다.
MySQL 계정에 테스트 DB 생성 권한이 필요합니다.
과거 마이그레이션 중 새 DB에서 재실행할 수 없는 변경이 있어 테스트에서는 현재
스키마로 시작하며, 별도 회귀 검사에서 `0034 -> 0035`의 실제 컬럼 추가와
구버전 모델의 INSERT 호환성을 확인합니다. 운영 마이그레이션 이력은 변경하지 않습니다.

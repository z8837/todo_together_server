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
앱은 기본 색과 개인 색을 선택할 수 있으며 일반·반복 일정에 같은 프로젝트 색을 사용합니다.

## 프로젝트별 개인 색

`Project.color`는 공통 기본 색입니다. 개인 색은 별도의 `ProjectUserPreference`에
`(project, user)` 유일 제약과 `color_override VARCHAR(7) DEFAULT ''`로 저장합니다.
소유자와 reader/writer/manager 모두 본인 설정을 변경할 수 있습니다.
다른 사용자 설정은 응답에 노출되지 않으며, 사용자 ID는 인증 정보에서 결정합니다.

- 조회/수정: `GET`, `PATCH`, `PUT /toto/projects/{id}/my-settings/`
- 입력: `{"color_override": "#1A56FF", "version": 0}`. 색 형식은 기본 색과 동일합니다.
- 빈 문자열 또는 null은 기본 색 사용으로 복원합니다. 해당 행은 유지하고 개인 버전을 증가시킵니다.
- `version`은 선택 사항입니다. 조회 시 받은 버전을 보내면 충돌 시 HTTP 409와 최신 `preference`를 반환합니다.
- 동일한 색의 재저장은 버전을 증가시키지 않습니다.
- 개인 색 변경은 프로젝트 공통 버전을 바꾸거나 다른 멤버에게 알림을 보내지 않습니다.
- 프로젝트 목록/상세/sync 응답에 읽기 전용 `my_color`, `display_color`, `preference_version`을 추가합니다.
  기존 `color`는 계속 공통 색을 의미합니다. 표시 우선순위는 개인 색 → 기본 색 → 프로젝트 ID 기반 자동 색입니다.
- `/toto/projects/sync/` 요청에 `preferences: [{"project": "프로젝트 UUID", "version": 1}]`를
  추가하면 응답의 `preferences`에서 본인 설정의 변경분을 받습니다.
  프로젝트 버전이 같아도 개인 색 변경과 복원이 전달됩니다. 기존 앱의 요청은 그대로 허용합니다.
- 위젯은 앱 실행 없이 전체 프로젝트 sync를 조회하여 `display_color`와 일정 데이터를 함께 갱신합니다.

적용: `python manage.py migrate todo_together --database=toto_db`

회귀 검사: `python manage.py test mysite.apps.todo_together --settings=mysite.test_settings`
이 설정은 운영 DB 이름을 사용하지 않고 별도 임시 MySQL 테스트 DB를 생성/제거합니다.
MySQL 계정에 테스트 DB 생성 권한이 필요합니다.
과거 마이그레이션 중 새 DB에서 재실행할 수 없는 변경이 있어 테스트에서는 현재
스키마로 시작하며, 별도 회귀 검사에서 `0034 -> 0035`의 실제 컬럼 추가와
구버전 모델의 INSERT 호환성을 확인합니다. 운영 마이그레이션 이력은 변경하지 않습니다.

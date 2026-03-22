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
- yt-dlp, faster-whisper, ffmpeg 기반 미디어 실험 기능

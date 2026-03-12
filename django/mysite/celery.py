# mysite/celery.py
import os
from celery import Celery

# Django settings 모듈 지정
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")

app = Celery("mysite")

# settings.py 에서 CELERY_ 로 시작하는 값들 읽어오기
app.config_from_object("django.conf:settings", namespace="CELERY")

# INSTALLED_APPS 에서 tasks.py 자동 검색
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")

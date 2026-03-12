# app/tasks.py
from celery import shared_task
from django.utils import timezone


import requests
from django.conf import settings
from .models import Reminder, Holiday, TodoUserState, Todo, User
import datetime as dt
import xml.etree.ElementTree as ET

from .services import send_todo_reminder_notification

from .services import (
    send_todo_activity_notifications,
    schedule_next_reminders_for_todo,
)

@shared_task
def send_due_reminders():
    now = timezone.now()

    qs = (
        Reminder.objects
        .select_related("todo", "todo__project")
        .filter(sent=False, scheduled_at__lte=now)[:500]
    )

    reminders = list(qs)

    for reminder in reminders:
        # 동시성 방어: 내가 먼저 sent=True 처리 성공한 경우만 진행
        updated = (
            Reminder.objects
            .filter(pk=reminder.pk, sent=False)
            .update(sent=True)
        )
        if not updated:
            continue

        todo = reminder.todo

        project = todo.project

        # todo가 삭제되었거나 완료면 스킵
        if todo.is_deleted or todo.status == Todo.Status.DONE or project.is_deleted:
            continue

        # ✅ 프로젝트 전체 멤버(멤버 + owner)
        member_ids = set(project.members.values_list("id", flat=True))
        member_ids.add(project.owner_id)

        # ✅ 이 todo를 숨긴 유저 제외
        hidden_ids = set(
            TodoUserState.objects
            .filter(todo=todo, is_hidden=True)
            .values_list("user_id", flat=True)
        )

        target_ids = member_ids - hidden_ids
        if not target_ids:
            # 전원이 hidden이면 이번 알림은 아무도 안 보냄(그래도 sent 처리 완료)
            pass
        else:
            targets = (
                User.objects
                .filter(id__in=target_ids)
                .prefetch_related("devices")
                .select_related("notification_pref")
            )

            for user in targets:
                send_todo_reminder_notification(user, reminder)

        # 🔁 반복 TODO라면 다음 알림 1번만 재생성
        if todo.is_recurring:
            schedule_next_reminders_for_todo(todo)






API_BASE = "http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getHoliDeInfo"



def _yyyymmdd_to_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y%m%d").date()


HOLIDAY_NAME_MAP = {
    "1월1일": "신정",
    "전국동시지방선거": "지방선거",
    "기독탄신일": "성탄절",
}

def normalize_holiday_name(name: str) -> str:
    """
    공공데이터 dateName을 우리 앱 표기 규칙으로 통일
    - 완전 일치도 바꾸고
    - 문자열 안에 포함된 경우도 바꿈 (예: 대체공휴일(기독탄신일) -> 대체공휴일(성탄절))
    """
    name = (name or "").strip()
    if not name:
        return name

    # ✅ 1) 완전 일치 우선 치환 (빠름 + 의도 명확)
    if name in HOLIDAY_NAME_MAP:
        return HOLIDAY_NAME_MAP[name]

    # ✅ 2) 부분 포함 치환
    # 긴 키부터 치환되게 정렬(안전)
    for src, dst in sorted(HOLIDAY_NAME_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        if src in name:
            name = name.replace(src, dst)

    return name

def _parse_items(xml_text: str):
    root = ET.fromstring(xml_text)
    items = root.findall(".//item")
    parsed = []

    for item in items:
        date_kind = (item.findtext("dateKind") or "").strip()

        date_name = (item.findtext("dateName") or "").strip()
        date_name = normalize_holiday_name(date_name)  # ✅ 여기서 치환

        is_holiday_raw = (item.findtext("isHoliday") or "").strip().upper()
        locdate_raw = (item.findtext("locdate") or "").strip()
        seq_raw = (item.findtext("seq") or "0").strip()

        if not locdate_raw or not date_name:
            continue

        try:
            locdate = _yyyymmdd_to_date(locdate_raw)
        except ValueError:
            continue

        parsed.append(
            {
                "locdate": locdate,
                "date_kind": date_kind,
                "date_name": date_name,
                "is_holiday": is_holiday_raw == "Y",
                "seq": int(seq_raw) if seq_raw.isdigit() else 0,
            }
        )
    return parsed


def sync_holidays_for_year_month(year: int, month: int) -> int:
    """
    year/month 공휴일을 받아 DB에 업서트.
    반환: 저장(업데이트 포함) 시도한 아이템 수
    """
    # service_key = getattr(settings, "PUBLIC_DATA_SERVICE_KEY", None)
    service_key = settings.PUBLIC_DATA_SERVICE_KEY
    if not service_key:
        raise RuntimeError("settings.PUBLIC_DATA_SERVICE_KEY 가 설정되어 있어야 합니다.")

    params = {
        "solYear": f"{year}",
        "solMonth": f"{month:02d}",
        "ServiceKey": service_key,  # requests가 인코딩 처리
        "_type": "xml",
        "numOfRows": 100,  # 한 달 공휴일은 매우 적지만 넉넉히
        "pageNo": 1,
    }

    resp = requests.get(API_BASE, params=params, timeout=15)
    resp.raise_for_status()

    items = _parse_items(resp.text)

    for it in items:
        Holiday.objects.update_or_create(
            locdate=it["locdate"],
            date_name=it["date_name"],
            defaults={
                "date_kind": it["date_kind"],
                "is_holiday": it["is_holiday"],
                "seq": it["seq"],
                "source": "KTO_SPCDE",
            },
        )

    return len(items)


def sync_holidays_between(start_date: dt.date, end_date: dt.date) -> int:
    """
    start_date ~ end_date 범위를 월 단위로 순회하며 동기화
    """
    if end_date < start_date:
        return 0

    total = 0
    y, m = start_date.year, start_date.month
    end_y, end_m = end_date.year, end_date.month

    while (y, m) <= (end_y, end_m):
        total += sync_holidays_for_year_month(y, m)
        # 다음 달로 이동
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1

    return total


@shared_task(name="mysite.apps.todo_together.tasks.sync_holidays_past_5_years")
def sync_holidays_past_5_years():
    """
    ✅ 수동 실행용: 지금 기준 과거 5년 ~ 현재까지 저장
    예) 2025-12-20이면 2020-01 ~ 2025-12
    """
    today = timezone.localdate()
    start = dt.date(today.year - 5, 1, 1)
    end = dt.date(today.year, 12, 31)
    return sync_holidays_between(start, end)


@shared_task(name="mysite.apps.todo_together.tasks.sync_holidays_until_2_years_later")
def sync_holidays_until_2_years_later():
    """
    ✅ 주기 실행용(1시간마다): 현재 ~ 2년 후까지 저장
    '2년 후까지'를 월 단위로 커버하기 위해 end를 (today + 2년)의 12월까지 잡는 방식 추천
    """
    today = timezone.localdate()
    start = dt.date(today.year, today.month, 1)  # 이번 달부터
    end_year = today.year + 2
    end = dt.date(end_year, 12, 31)
    return sync_holidays_between(start, end)




import logging
logger = logging.getLogger(__name__)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def todo_post_create_task(self, todo_id: str, actor_id: int):
    
    todo = Todo.objects.select_related("project").get(id=todo_id)
    actor = User.objects.get(id=actor_id)

    # 알림/푸시
    send_todo_activity_notifications(todo, actor=actor, event="created")

    # 리마인더 예약
    schedule_next_reminders_for_todo(todo)

@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def todo_post_update_task(self, todo_id: str, actor_id: int, event: str, changes: list | None = None):
    logger.warning("todo_post_update_task!!")
    todo = Todo.objects.select_related("project").get(id=todo_id)
    actor = User.objects.get(id=actor_id)

    send_todo_activity_notifications(todo, actor=actor, event=event, changes=changes or [])
    schedule_next_reminders_for_todo(todo)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def todo_post_destroy_task(self, todo_id: str, actor_id: int):
    # ✅ 삭제(soft delete)된 todo도 조회되어야 하므로 all_objects 사용
    todo = Todo.all_objects.select_related("project").get(id=todo_id)
    actor = User.objects.get(id=actor_id)

    # ✅ 알림/FCM 백그라운드 발송
    send_todo_activity_notifications(todo, actor=actor, event="destroy")







@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def push_to_user_task(self, user_id: int, title: str, body: str, data: dict, force_data_only: bool = False):
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return

    # ✅ 여기서만 import (순환 import 방지)
    from .services import send_push_to_user
    send_push_to_user(
        user,
        title=title or "",
        body=body or "",
        data=data or {},
        force_data_only=force_data_only,
    )


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def push_to_users_task(self, user_ids: list[int], title: str, body: str, data: dict, force_data_only: bool = False):
    if not user_ids:
        return

    # 존재하는 유저만
    qs = User.objects.filter(id__in=user_ids).only("id")
    from .services import send_push_to_user  # ✅ 여기서만 import

    for u in qs:
        send_push_to_user(
            u,
            title=title or "",
            body=body or "",
            data=data or {},
            force_data_only=force_data_only,
        )
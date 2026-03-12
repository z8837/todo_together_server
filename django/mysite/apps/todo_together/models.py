from django.db import models
# from django.contrib.auth.models import AbstractUser
import uuid
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.utils import timezone

from datetime import timedelta, datetime, time as dtime

from django.conf import settings
from django.db.models import Q
from django.core.exceptions import ValidationError

class TOTOTest(models.Model):
	name = models.CharField(default='', max_length=2048)
	memo = models.CharField(default='', max_length=2048)
	is_super = models.BooleanField(default=False)
	age = models.IntegerField(default=0)




class UserManager(BaseUserManager):
    def create_user(self, email, provider, provider_user_id, password=None, **extra_fields):
        # email은 DB에서 unique 아님. 그래도 저장은 하니까 normalize만.
        email = (email or "").strip()
        if email:
            email = self.normalize_email(email)
        else:
            # email 없는 provider도 있을 수 있으니 placeholder 허용
            # (소셜에서 이메일 안 내려오는 케이스 대비)
            email = f"{provider}-{provider_user_id}@no-email.local"

        extra_fields.setdefault("is_active", True)

        user = self.model(
            email=email,
            provider=provider,
            provider_user_id=provider_user_id,
            **extra_fields,
        )
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()

        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        if not email:
            raise ValueError("Email is required for superuser.")

        # 관리자 계정은 local provider로 생성
        return self.create_user(
            email=email,
            provider="local",
            provider_user_id=email,   # local은 email로 통일
            password=password,
            **extra_fields,
        )


class User(AbstractBaseUser, PermissionsMixin):
    # 로그인 식별자(인증키) = UUID
    username = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # 이메일은 중복 허용 (unique=False)
    email = models.EmailField(blank=True)

    provider = models.CharField(max_length=20)
    provider_user_id = models.CharField(max_length=255)

    nickname = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    
    # ✅ 탈퇴 상태
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)


    date_joined = models.DateTimeField(default=timezone.now)

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email"]  # createsuperuser 시 email 입력받게

    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_user_id"],
                name="uniq_provider_provider_user_id",
            )
        ]

    def __str__(self):
        return f"{self.email} ({self.provider})"
    
    def withdraw(self):
        """
        탈퇴 처리:
        - 로그인 불가
        - 개인정보 익명화
        - provider_user_id 변경(재가입 가능하게 유니크 해제)
        """
        self.is_active = False
        self.is_deleted = True
        self.deleted_at = timezone.now()

        # 개인정보 익명화
        anon = uuid.uuid4().hex
        self.email = f"deleted-{anon}@deleted.local"
        self.nickname = "탈퇴한 사용자"

        # 소셜 고유키도 익명화(유니크 제약 해제 목적)
        self.provider_user_id = f"deleted-{anon}"

        self.set_unusable_password()
        self.save(update_fields=[
            "is_active", "is_deleted", "deleted_at",
            "email", "nickname", "provider_user_id",
            "password",
        ])



class AccountDeletionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        PROCESSED = "processed", "Processed"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField()
    provider = models.CharField(max_length=20)  # google/naver/kakao
    token = models.CharField(max_length=128, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    requested_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    requested_ip = models.CharField(max_length=64, blank=True, default="")
    user_agent = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["email", "provider", "status"]),
            models.Index(fields=["expires_at"]),
        ]



class RefreshToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, related_name='refresh_tokens', on_delete=models.CASCADE)
    token = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    client_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.user.email} - {self.token}'

    @property
    def is_active(self):
        return self.revoked_at is None and self.expires_at > timezone.now()

    def revoke(self):
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=['revoked_at'])



# Create your models here.










# -------------------------
# 공통: 타임스탬프 + 소프트 삭제
# -------------------------
class TimeStampedSoftDelete(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def soft_delete(self, save: bool = True):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        if save:
            self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])


class AliveManager(models.Manager):
    """소프트 삭제된 항목은 숨기는 기본 매니저"""
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


# -------------------------
# Project & ProjectMember (권한)
# -------------------------
class Project(TimeStampedSoftDelete):
    """
    프로젝트:
      - owner: 최고관리자(권한 부여 가능)
      - members: reader / writer (through=ProjectMember)
      - 최근 업데이트 설명/시각
    """
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_projects",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)

    version = models.PositiveIntegerField(default=1)
    recent_update_at = models.DateTimeField(null=True, blank=True)

    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="ProjectMember",
        through_fields=("project", "user"),
        related_name="projects",
        blank=True,
    )

    objects = AliveManager()
    all_objects = models.Manager()

    class Meta:
        indexes = [
            models.Index(fields=["is_deleted"]),
            models.Index(fields=["updated_at"]),
            models.Index(fields=["owner", "is_deleted"]),
        ]

    def __str__(self):
        return self.name

    # -------- 권한 헬퍼 --------
    def role_of(self, user) -> str | None:
        """'owner' | 'manager' | 'writer' | 'reader' | None"""
        if not user:
            return None
        if user == self.owner:
            return "owner"
        pm = self.membership.filter(user=user).only("role").first()
        return pm.role if pm else None

    def has_read(self, user) -> bool:
        """읽기 권한: owner / manager / writer / reader"""
        return self.role_of(user) in {"owner", "manager", "writer", "reader"}

    def has_write(self, user) -> bool:
        """쓰기 권한(할 일/체크리스트 등): owner / manager / writer"""
        return self.role_of(user) in {"owner", "manager", "writer"}

    def has_manage_project(self, user) -> bool:
        """프로젝트 수정 권한(이름/설명 등): owner / manager"""
        return self.role_of(user) in {"owner", "manager"}

    def can_delete_project(self, user) -> bool:
        """프로젝트 삭제 권한: owner만"""
        return user == self.owner

    def can_grant_roles(self, user) -> bool:
        """권한 부여/변경 권한: owner만 (정책 유지)"""
        return user == self.owner

    def set_member_role(self, actor, user, role: str):
        """
        actor(수행자)가 owner인지 확인 후 role 부여/변경.
        role: 'reader' | 'writer' | 'manager'
        """
        if not self.can_grant_roles(actor):
            raise PermissionError("권한을 변경할 수 있는 사용자가 아닙니다.")
        if user == self.owner:
            raise ValidationError("owner의 역할은 변경할 수 없습니다.")
        if role not in ("reader", "writer", "manager"):
            raise ValidationError("role은 'reader'/'writer'/'manager' 중 하나여야 합니다.")

        obj, _ = ProjectMember.objects.update_or_create(
            project=self, user=user, defaults={"role": role}
        )
        return obj
    
    def bump_version(self, save=True):
        self.version += 1
        self.recent_update_at = timezone.now()
        if save:
            self.save(update_fields=["version", "recent_update_at", "updated_at"])

class ProjectMember(TimeStampedSoftDelete):
    """
    프로젝트 멤버십: reader(읽기만), writer(읽기+쓰기)
    ※ owner는 Project.owner로 관리하며 여기엔 넣지 않는 것을 원칙으로 함.
    """
    class Role(models.TextChoices):
        READER = "reader", "Reader"
        WRITER = "writer", "Writer"
        MANAGER = "manager", "Manager"  # ✅ 추가

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="membership"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="project_memberships"
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.READER)

    objects = AliveManager()
    all_objects = models.Manager()

    class Meta:
        unique_together = (("project", "user"),)
        indexes = [
            models.Index(fields=["project", "user"]),
            models.Index(fields=["project", "role"]),
        ]

    def __str__(self):
        return f"{self.project.name} - {self.user} ({self.role})"




class ProjectChecklistOrder(TimeStampedSoftDelete):
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="checklist_orders"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_checklist_orders",
    )

    ordered_checklist_ids = models.JSONField(default=list, blank=True)
    version = models.PositiveIntegerField(default=1)

    objects = AliveManager()
    all_objects = models.Manager()

    class Meta:
        unique_together = (("project", "user"),)
        indexes = [
            models.Index(fields=["project", "user"]),
            models.Index(fields=["updated_at"]),
        ]

    def bump_version(self, save=True, extra_update_fields=None):
        self.version += 1
        if save:
            fields = {"version", "updated_at"}
            if extra_update_fields:
                fields.update(extra_update_fields)
            self.save(update_fields=list(fields))







class TodoKind(models.TextChoices):
    SCHEDULE = "schedule", "Schedule"
    CHECKLIST = "checklist", "Checklist"

# -------------------------
# Todo
# -------------------------
class Todo(TimeStampedSoftDelete):
    """
    Todo
      - created_by: 생성자(본인만 수정/삭제 가능)
      - 생성은 '프로젝트 쓰기 권한'이 있는 사용자만 가능
      - 일회성/반복 스케줄 + 알림
    """
    class Status(models.TextChoices):
        TODO = "todo", "To Do"
        DOING = "doing", "Doing"
        DONE = "done", "Done"

    kind = models.CharField(max_length=20, choices=TodoKind.choices, default=TodoKind.SCHEDULE, db_index=True)

    version = models.PositiveIntegerField(default=1)

    WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="todos")
    title = models.CharField(max_length=200)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.TODO)
    assignees = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="assigned_todos", blank=True
    )

    created_by = models.ForeignKey(  # 생성자
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_todos",
    )

    # 일회성 / 반복
    is_recurring = models.BooleanField(default=False)

    # --- 일회성 ---
    # due_date = models.DateField(null=True, blank=True)
    # due_time = models.TimeField(null=True, blank=True)

    # --- 반복 ---
    # 요일 비트마스크: 월=1<<0, 화=1<<1, ..., 일=1<<6
    weekday_mask = models.PositiveSmallIntegerField(default=0)
    # recur_time = models.TimeField(null=True, blank=True)  # 반복 시각(필수)

    # 🔹 반복 시작/종료 날짜 (옵션)
    #    - recur_start_date: 이 날짜부터 반복 시작 (없으면 "오늘" 기준)
    #    - recur_end_date: 이 날짜까지만 반복 (없으면 무기한)
    # recur_start_date = models.DateField(null=True, blank=True)
    # recur_end_date = models.DateField(null=True, blank=True)


    start_date = models.DateField(null=True, blank=True)  # null=False (필수)
    end_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    # --- 알림 ---
    alarm_offset_minutes = models.PositiveIntegerField(null=True, blank=True)  # null 이면 알림 없음

    # 정렬/완료
    # sort_index = models.FloatField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = AliveManager()
    all_objects = models.Manager()
    
    class Meta:
        indexes = [
            models.Index(fields=["project", "is_deleted"]),
            models.Index(fields=["project", "status"]),
            # models.Index(fields=["project", "sort_index"]),
            models.Index(fields=["start_date"]),
            models.Index(fields=["end_date"]),
            models.Index(fields=["weekday_mask"]),
            models.Index(fields=["project", "is_deleted", "status"]),
            models.Index(fields=["created_by"]),
        ]
        constraints = [
            # # (1) 일회성: is_recurring=False 이면 due_date 필요
            # models.CheckConstraint(
            #     name="todo_oneoff_requires_date_or_recurring",
            #     check=Q(is_recurring=True) | Q(is_recurring=False, due_date__isnull=False),
            # ),
            # # (2) due_time이 있으면 due_date도 있어야 함
            # models.CheckConstraint(
            #     name="todo_due_time_requires_due_date",
            #     check=Q(due_time__isnull=True) | Q(due_date__isnull=False),
            # ),
            # # (3) 반복: 요일+시간 필수
            # models.CheckConstraint(
            #     name="todo_recurring_requires_mask_and_time",
            #     check=Q(is_recurring=False) | Q(is_recurring=True, weekday_mask__gt=0, recur_time__isnull=False),
            # ),
            # # (4) 반복일 때는 일회성 필드 비우기(혼합 금지)
            # models.CheckConstraint(
            #     name="todo_recurring_disallows_due_fields",
            #     check=Q(is_recurring=False) | (Q(is_recurring=True) & Q(due_date__isnull=True) & Q(due_time__isnull=True)),
            # ),
            # # (5) 알림은 음수 불가
            # models.CheckConstraint(
            #     name="todo_alarm_offset_nonnegative",
            #     check=Q(alarm_offset_minutes__gte=0),
            # ),
            #  # 🔹 (6) 반복 종료일 >= 시작일
            # models.CheckConstraint(
            #     name="todo_recur_end_after_start",
            #     check=(
            #         Q(recur_end_date__isnull=True) |
            #         Q(recur_start_date__isnull=True) |
            #         Q(recur_end_date__gte=models.F("recur_start_date"))
            #     ),
            # ),
            models.CheckConstraint(
                name="todo_checklist_requires_start_fields_null",
                check=Q(kind=TodoKind.SCHEDULE) | (Q(start_date__isnull=True) & Q(start_time__isnull=True)),
            ),
            models.CheckConstraint(
                name="todo_checklist_disallows_recurring",
                check=Q(kind=TodoKind.SCHEDULE) | Q(is_recurring=False),
            ),
            models.CheckConstraint(
                name="todo_checklist_disallows_weekday_mask",
                check=Q(kind=TodoKind.SCHEDULE) | Q(weekday_mask=0),
            ),
            models.CheckConstraint(
                name="todo_schedule_requires_start_date",
                check=Q(kind=TodoKind.CHECKLIST) | Q(start_date__isnull=False),
            ),
            models.CheckConstraint(
                name="todo_end_date_gte_start_date_for_schedule",
                check=(
                    Q(kind=TodoKind.CHECKLIST)
                    | Q(start_date__isnull=True)     # 방어
                    | Q(end_date__isnull=True)
                    | Q(end_date__gte=models.F("start_date"))
                ),
            ),
            models.CheckConstraint(
                name="todo_checklist_end_time_requires_end_date",
                check=Q(kind=TodoKind.SCHEDULE) | Q(end_time__isnull=True) | Q(end_date__isnull=False),
            ),

            # (2) 반복: 요일 + start_time 필수
            # models.CheckConstraint(
            #     name="todo_recurring_requires_mask_and_start_time",
            #     check=Q(is_recurring=False) | Q(is_recurring=True, weekday_mask__gt=0, start_time__isnull=False),
            # ),

        ]
        ordering = ["start_date", "start_time", "-updated_at"]

    def __str__(self):
        return f"[{self.project.name}] {self.title}"

    # ---------- 권한 헬퍼 ----------
    @staticmethod
    def can_create(project: Project, user) -> bool:
        """프로젝트 쓰기 권한이 있으면 TODO 생성 가능"""
        return project.has_write(user)

    def can_edit_content(self, user) -> bool:
        if not user:
            return False
        return (user == self.created_by) or self.project.has_manage_project(user)

    def can_change_status(self, user) -> bool:
        if not user:
            return False

        if self.kind == TodoKind.CHECKLIST:
            # ✅ 프로젝트 인원(=읽기 가능한 멤버 + owner) 전부 허용
            return self.project.has_read(user)

        # ✅ 일정은 기존 정책
        return self.can_edit_content(user)

    def can_update(self, user) -> bool:
        # 기존 코드와 호환 유지용(=내용 수정 권한)
        return self.can_edit_content(user)

    def can_delete(self, user) -> bool:
        return self.can_edit_content(user)

    # ---------- 요일 비트마스크 헬퍼 ----------
    @staticmethod
    def encode_weekdays(days: list[int]) -> int:
        mask = 0
        for d in days:
            if 0 <= d <= 6:
                mask |= (1 << d)
        return mask

    def save(self, *args, **kwargs):
        if self.start_time:
            self.start_time = self.start_time.replace(second=0, microsecond=0)
        if self.end_time:
            self.end_time = self.end_time.replace(second=0, microsecond=0)
        super().save(*args, **kwargs)
        
    @staticmethod
    def decode_weekdays(mask: int) -> list[int]:
        return [d for d in range(7) if mask & (1 << d)]

    def set_weekdays(self, days: list[int], save: bool = True):
        self.weekday_mask = self.encode_weekdays(days)
        if save:
            self.save(update_fields=["weekday_mask", "updated_at"])

    def get_weekdays(self) -> list[int]:
        return self.decode_weekdays(self.weekday_mask)

    # ---------- 알림 편의 ----------
    def set_alarm_hours_before(self, hours: int, save: bool = True):
        self.alarm_offset_minutes = max(0, int(hours) * 60)
        if save:
            self.save(update_fields=["alarm_offset_minutes", "updated_at"])



    def deadline_at(self, now=None):
        if now is None:
            now = timezone.now()
        if self.kind != TodoKind.CHECKLIST:
            return None
        if not self.end_date or not self.end_time:
            return None

        base_time = self.end_time
        dt = timezone.make_aware(
            datetime.combine(self.end_date, base_time),
            timezone.get_current_timezone(),
        )
        return dt if dt > now else None
    
    # ---------- 발생/알림 시각 계산 ----------
    def next_occurrence(self, now=None):
        if now is None:
            now = timezone.now()

        if self.kind == TodoKind.CHECKLIST:
            return None
         # ✅ start_date 없으면 계산 불가
        if not self.start_date:
            return None
        
        # 1) 일회성
        if not self.is_recurring:
            base_time = self.start_time or dtime(0, 0)
            dt = timezone.make_aware(
                datetime.combine(self.start_date, base_time),
                timezone.get_current_timezone(),
            )
            return dt if dt > now else None

        # 2) 반복
        if not self.weekday_mask or not self.start_time:
            return None

        base_date = now.date()

        # 반복 시작일은 start_date
        start_date = self.start_date
        if start_date < base_date:
            start_date = base_date

        search_end = start_date + timedelta(days=7)
        if self.end_date and self.end_date < search_end:
            search_end = self.end_date

        # 반복이 이미 끝난 경우
        if self.end_date and self.end_date < start_date:
            return None

        d = start_date
        while d <= search_end:
            weekday_bit = 1 << d.weekday()
            if self.weekday_mask & weekday_bit:
                dt = timezone.make_aware(
                    datetime.combine(d, self.start_time),
                    timezone.get_current_timezone(),
                )
                if dt > now:
                    return dt
            d += timedelta(days=1)

        return None

    def next_alarm_at(self, now=None):
        if self.alarm_offset_minutes is None:
            return None

        now = now or timezone.now()
        offset = timedelta(minutes=self.alarm_offset_minutes)

        if self.kind == TodoKind.CHECKLIST:
            occ = self.deadline_at(now=now)
            if not occ:
                return None

            alarm_at = occ - offset
            return alarm_at if alarm_at > now else None
    
        occ = self.next_occurrence(now=now)
        if not occ:
            return None

        while True:
            alarm_at = occ - offset

            # ✅ "지금 이후"에 울릴 알람이면 OK
            if alarm_at > now:
                return alarm_at

            # ✅ 일회성이면 더 이상 다음 알람 없음
            if not self.is_recurring:
                return None

            # ✅ 현재 occ의 알람은 이미 지났으니, 다음 occ로 넘김
            occ = self.next_occurrence(now=occ + timedelta(seconds=1))
            if not occ:
                return None
            
    

    # ---------- 유효성(폼/어드민 친절 메시지) ----------
    def clean(self):
        super().clean()

        if self.kind == TodoKind.SCHEDULE:
            if not self.start_date:
                raise ValidationError("start_date는 필수입니다.")
            if self.end_date and self.end_date < self.start_date:
                raise ValidationError("end_date는 start_date보다 빠를 수 없습니다.")
            if self.is_recurring and not self.weekday_mask:
                raise ValidationError("반복 일정은 최소 1개 이상의 요일을 선택해야 합니다.")

        elif self.kind == TodoKind.CHECKLIST:
            # ✅ 반복 금지
            if self.is_recurring:
                raise ValidationError("CHECKLIST는 반복을 사용할 수 없습니다.")
            if self.weekday_mask:
                raise ValidationError("CHECKLIST는 weekday_mask를 사용할 수 없습니다.")

            # ✅ start_date/time는 "값이 들어온 경우만" 막기
            if self.start_date:
                raise ValidationError("CHECKLIST는 start_date를 사용할 수 없습니다. end_date만 사용하세요.")
            if self.start_time:
                raise ValidationError("CHECKLIST는 start_time을 사용할 수 없습니다. end_time만 사용하세요.")

            if self.end_time and not self.end_date:
                raise ValidationError("CHECKLIST는 end_time만 단독으로 설정할 수 없습니다. end_date가 필요합니다.")


    def bump_version(self, save=True):
        self.version += 1
        if save:
            self.save(update_fields=["version", "updated_at"])

    def soft_delete(self, save: bool = True):
        """
        Todo 소프트삭제 시, 연결된 Reminder도 같이 삭제
        """
        # 1) 예약된/발송여부 상관 없이 전부 삭제 (원하면 sent=False만 지우게 바꿔도 됨)
        Reminder.objects.filter(todo=self, sent=False).delete()

        # 2) Todo 자체 소프트 삭제
        super().soft_delete(save=save)




class TodoUserState(models.Model):
    """
    유저별 Todo 개인화 상태
    - is_hidden=True면 앱에서만 해당 Todo를 숨김(무시)
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="todo_states",
    )
    todo = models.ForeignKey(
        "Todo",
        on_delete=models.CASCADE,
        related_name="user_states",
    )

    # sort_index = models.FloatField(default=0)  # 유저별 우선순위

    is_hidden = models.BooleanField(default=False)
    hidden_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "todo"], name="uq_todo_user_state"),
        ]
        indexes = [
            models.Index(fields=["user", "is_hidden"]),
            models.Index(fields=["user", "updated_at"]),
            models.Index(fields=["todo", "is_hidden"]),
        ]

    def __str__(self):
        return f"TodoUserState(user={self.user_id}, todo={self.todo_id}, hidden={self.is_hidden})"


class Notification(TimeStampedSoftDelete):
    class Kind(models.TextChoices):
        PROJECT_INVITE = "project_invite", "Project Invite"
        PROJECT_INVITE_ACCEPTED = "project_invite_accepted", "Project Invite Accepted"
        PROJECT_INVITE_DECLINED = "project_invite_declined", "Project Invite Declined"
        PROJECT_ADDED = "project_added", "Project Added"
        PROJECT_UPDATED = "project_updated", "Project Updated"
        # 🔹 TODO 관련 알림 타입 추가
        TODO_CREATED = "todo_created", "Todo Created"
        TODO_UPDATED = "todo_updated", "Todo Updated"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    kind = models.CharField(max_length=50, choices=Kind.choices)
    message = models.CharField(max_length=255, blank=True)
    project = models.ForeignKey(
        "Project",
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name="notifications",
    )
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    extra = models.JSONField(default=dict, blank=True)

    objects = AliveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-created_at"]

    def mark_read(self, save: bool = True):
        self.is_read = True
        self.read_at = timezone.now()
        if save:
            self.save(update_fields=["is_read", "read_at", "updated_at"])




class ProjectInvite(TimeStampedSoftDelete):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="invites",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_project_invites",
    )

    # 이메일 기준 초대 (아직 회원이 아닐 수도 있음)
    email = models.EmailField()

    # 이미 가입된 유저면 연결
    invited_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="received_project_invites",
    )

    role = models.CharField(
        max_length=10,
        choices=ProjectMember.Role.choices,
        default=ProjectMember.Role.READER,
    )

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )

    token = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    objects = AliveManager()
    all_objects = models.Manager()

    class Meta:
        indexes = [
            models.Index(fields=["email", "status"]),
            models.Index(fields=["project", "status"]),
            models.Index(fields=["token"]),
        ]

    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at < timezone.now())



class Device(models.Model):
    """
    유저의 FCM 디바이스 토큰 보관용
    - 한 유저가 여러 기기(Android, iOS)를 쓸 수 있으니 다수 row 허용
    """
    class Platform(models.TextChoices):
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="devices",
    )
    fcm_token = models.CharField(max_length=255, unique=True)
    platform = models.CharField(
        max_length=20,
        choices=Platform.choices,
        default=Platform.ANDROID,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)
    
    # ✅ 추가
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    
    def __str__(self):
        return f"{self.user.email} - {self.platform} - {self.fcm_token[:10]}..."
    

class NotificationPreference(models.Model):
    """
    유저별 알림 설정
      - 프로젝트 초대
      - PROJECT 활동(수정)
      - TODO 활동(생성/수정)
      - TODO 시작 전 리마인더
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_pref",
    )
    project_invite_enabled = models.BooleanField(default=True)
    project_activity_enabled = models.BooleanField(default=True)
    todo_activity_enabled = models.BooleanField(default=True)
    todo_reminder_enabled = models.BooleanField(default=True)

    def __str__(self):
        return f"NotificationPreference({self.user.email})"
    





class Reminder(models.Model):
    """
    TODO 알림 예약 레코드.
    scheduled_at 시각이 되면 Celery가 읽어서 프로젝트 멤버들에게 FCM 발송.
    """
    class Type(models.TextChoices):
        TODO_REMINDER = "todo_reminder", "Todo Reminder"

    todo = models.ForeignKey(
        Todo,
        on_delete=models.CASCADE,
        related_name="reminders",
    )
    type = models.CharField(
        max_length=50,
        choices=Type.choices,
        default=Type.TODO_REMINDER,
    )
    scheduled_at = models.DateTimeField()
    sent = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["scheduled_at", "sent"]),
            models.Index(fields=["todo", "sent"]),
        ]

    def __str__(self):
        return f"[{self.type}] todo={self.todo_id} @ {self.scheduled_at}"

    


class Holiday(models.Model):
    # API: locdate=20260505 형태 → DateField로 저장
    locdate = models.DateField(db_index=True)

    # API: dateName, dateKind, isHoliday, seq
    date_name = models.CharField(max_length=100, db_index=True)
    date_kind = models.CharField(max_length=2, blank=True, default="")  # "01" 등
    is_holiday = models.BooleanField(default=False)
    seq = models.IntegerField(default=0)

    # 추적용
    source = models.CharField(max_length=50, default="KTO_SPCDE", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # 같은 날짜에 다른 이름 공휴일이 있을 수도 있어서 (date+name) 유니크 추천
        constraints = [
            models.UniqueConstraint(fields=["locdate", "date_name"], name="uq_holiday_locdate_name")
        ]
        ordering = ["locdate", "date_name"]

    def __str__(self):
        return f"{self.locdate} {self.date_name}"
    

class UserNote(models.Model):
    """
    유저당 1개 메모(텍스트만)
    - 줄바꿈/띄어쓰기/이모지 포함 그대로 저장
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="note",
    )
    content = models.TextField(default="", blank=True)  # ✅ 줄바꿈 포함 텍스트
    version = models.PositiveIntegerField(default=1)

    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"UserNote({self.user_id})"
from rest_framework import serializers
from .models import *
from django.contrib.auth import get_user_model
from .services import normalize_provider


class TOTOTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = TOTOTest
        fields = "__all__"





User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    # 항상 포함되게 하고 싶으면 SerializerMethodField로 get_or_create
    notification_pref = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "nickname",
            "provider",
            "provider_user_id",
            "last_login",
            "date_joined",
            "notification_pref",   # ✅ 여기 추가
        )
        read_only_fields = fields

    def get_notification_pref(self, obj):
        # 순환 import 피하려고 여기서 import
        from .models import NotificationPreference

        pref, _ = NotificationPreference.objects.get_or_create(user=obj)
        return NotificationPreferenceSerializer(pref).data




class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = [
            "project_invite_enabled",
            "project_activity_enabled",
            "todo_activity_enabled",
            "todo_reminder_enabled",
        ]


class SocialLoginSerializer(serializers.Serializer):
    provider = serializers.CharField()
    token = serializers.CharField()
    nickname = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    # ✅ 추가
    public_id = serializers.UUIDField(required=False, allow_null=True)
    fcm_token = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    redirect_uri = serializers.URLField(required=False, allow_blank=True, allow_null=True)
    identity_token = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate_redirect_uri(self, value):
        if not value:
            return None
        v = value.strip()
        return v or None
    def validate_identity_token(self, value):
        if not value:
            return None
        v = value.strip()
        return v or None



    def validate_provider(self, value):
        return normalize_provider(value)

    def validate_token(self, value):
        trimmed = value.strip()
        if not trimmed:
            raise serializers.ValidationError('Social access token is required.')
        return trimmed

    def validate_nickname(self, value):
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            return None
        if len(trimmed) < 2:
            raise serializers.ValidationError('Nickname must be at least 2 characters long.')
        return trimmed
    
    # ✅ 추가: fcm_token 공백/빈값 -> None 처리
    def validate_fcm_token(self, value):
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class RefreshTokenSerializer(serializers.Serializer):
    refresh_token = serializers.CharField()

    def validate_refresh_token(self, value):
        trimmed = value.strip()
        if not trimmed:
            raise serializers.ValidationError('Refresh token is required.')
        return trimmed


class NicknameSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('nickname',)

    def validate_nickname(self, value):
        value = value.strip()
        if len(value) < 2:
            raise serializers.ValidationError('Nickname must be at least 2 characters long.')
        return value

    def update(self, instance, validated_data):
        instance.nickname = validated_data['nickname']
        instance.save(update_fields=['nickname'])
        return instance




from .models import Project, ProjectMember, Todo

# ---------- User (간단 노출용) ----------
class SimpleUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "nickname", "provider"]
        read_only_fields = fields


# ---------- ProjectMember ----------
class ProjectMemberSerializer(serializers.ModelSerializer):
    user = SimpleUserSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), write_only=True, source="user"
    )

    class Meta:
        model = ProjectMember
        fields = ["id", "user", "user_id", "role", "created_at", "updated_at"]
        read_only_fields = ["id", "user", "created_at", "updated_at"]


# ---------- Project ----------
class ProjectSerializer(serializers.ModelSerializer):
    owner = SimpleUserSerializer(read_only=True)
    # 멤버십 read-only로 내려주고, 변경은 별도 액션에서 처리
    membership = ProjectMemberSerializer(many=True, read_only=True)

    class Meta:
        model = Project
        fields = [
            "id",
            "name",
            "description",
            "version",
            "recent_update_at",
            "owner",
            "membership",  # reader/writer 목록(역할 포함)
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "owner", "created_at", "updated_at"]

    def create(self, validated_data):
        request = self.context["request"]
        project = Project.objects.create(owner=request.user, **validated_data)
        return project


class ProjectSyncSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = [
            "id",
            "name",
            "description",
            "version",
            "recent_update_at",
            "updated_at",
            "created_at",
            # 필요하면 여기에 최소한의 추가 필드만 더 넣기
        ]
        read_only_fields = fields


class ProjectChecklistOrderSyncSerializer(serializers.ModelSerializer):
    project = serializers.CharField(source="project_id", read_only=True)

    class Meta:
        model = ProjectChecklistOrder
        fields = ["project", "ordered_checklist_ids", "version", "updated_at"]


class ProjectChecklistOrderUpsertSerializer(serializers.Serializer):
    ordered_checklist_ids = serializers.ListField(
        child=serializers.CharField(),
        required=True
    )
    # 낙관적 락(동시편집)용: 클라가 마지막으로 가진 버전
    version = serializers.IntegerField(required=False)





# ---------- Todo ----------
class TodoSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    created_by = SimpleUserSerializer(read_only=True)
    assignees = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), many=True, required=False
    )
    is_hidden = serializers.BooleanField(read_only=True)
    class Meta:
        model = Todo
        fields = [
            "id",
            "project",
            "title",
            "status",
            "assignees",
            "created_by",
            "is_recurring",

            # ✅ new
            "start_date",
            "start_time",
            "end_date",
            "end_time",

            "weekday_mask",
            "alarm_offset_minutes",
            # "sort_index",
            "completed_at",
            "created_at",
            "updated_at",
            "version",

            "is_hidden",
            "kind",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]

    def validate(self, attrs):
        request = self.context["request"]

        # 생성 권한(기존 유지)
        if request.method == "POST":
            project = attrs.get("project")
            if not project or not project.has_write(request.user):
                raise serializers.ValidationError("이 프로젝트에 TODO를 생성할 권한이 없습니다.")

        # kind 결정(부분수정 대비)
        kind = attrs.get("kind") or getattr(self.instance, "kind", TodoKind.SCHEDULE)

        start_date = attrs.get("start_date", getattr(self.instance, "start_date", None))
        start_time = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end_date   = attrs.get("end_date", getattr(self.instance, "end_date", None))
        end_time   = attrs.get("end_time", getattr(self.instance, "end_time", None))

        is_recurring = attrs.get("is_recurring")
        if is_recurring is None and self.instance is not None:
            is_recurring = self.instance.is_recurring
        is_recurring = bool(is_recurring)

        weekday_mask = attrs.get("weekday_mask")
        if weekday_mask is None and self.instance is not None:
            weekday_mask = self.instance.weekday_mask

        if kind == TodoKind.SCHEDULE:
            # ✅ 일정은 start_date 필수
            if not start_date:
                raise serializers.ValidationError({"start_date": "start_date는 필수입니다."})

            if end_date and start_date and end_date < start_date:
                raise serializers.ValidationError({"end_date": "end_date는 start_date보다 빠를 수 없습니다."})

            if is_recurring:
                if not weekday_mask or int(weekday_mask) <= 0:
                    raise serializers.ValidationError({"weekday_mask": "반복 일정은 weekday_mask가 필요합니다."})

        elif kind == TodoKind.CHECKLIST:
            # ✅ 반복 금지
            if is_recurring:
                raise serializers.ValidationError({"is_recurring": "CHECKLIST는 반복을 사용할 수 없습니다."})
            if weekday_mask and int(weekday_mask) > 0:
                raise serializers.ValidationError({"weekday_mask": "CHECKLIST는 weekday_mask를 사용할 수 없습니다."})

            # ✅ start_date/time 금지
            if "start_date" in attrs and attrs["start_date"] is not None:
                raise serializers.ValidationError({"start_date": "CHECKLIST는 start_date를 사용할 수 없습니다. end_date만 사용하세요."})
            if "start_time" in attrs and attrs["start_time"] is not None:
                raise serializers.ValidationError({"start_time": "CHECKLIST는 start_time을 사용할 수 없습니다. end_time만 사용하세요."})

            # ✅ end_time 단독 금지
            if (("end_time" in attrs and attrs.get("end_time") is not None) or end_time) and not end_date:
                raise serializers.ValidationError({"end_date": "CHECKLIST는 end_time을 쓰려면 end_date가 필요합니다."})

        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        assignees = validated_data.pop("assignees", [])
        todo = Todo.objects.create(created_by=request.user, **validated_data)
        if assignees:
            todo.assignees.set(assignees)
        return todo

    def update(self, instance, validated_data):
        request = self.context["request"]

        # ✅ status만 변경인지 판단
        only_status = set(validated_data.keys()).issubset({"status"})

        if only_status:
            if not instance.can_change_status(request.user):
                raise serializers.ValidationError("status 변경 권한이 없습니다.")
        else:
            if not instance.can_edit_content(request.user):
                raise serializers.ValidationError("생성자 또는 프로젝트 owner만 수정할 수 있습니다.")

        # ✅ CHECKLIST는 start_date/start_time 강제로 None 유지 + 반복 강제 해제
        target_kind = validated_data.get("kind", instance.kind)

        if target_kind == TodoKind.CHECKLIST:
            validated_data["is_recurring"] = False
            validated_data["weekday_mask"] = 0
            validated_data["start_date"] = None
            validated_data["start_time"] = None

        assignees = validated_data.pop("assignees", None)
        obj = super().update(instance, validated_data)
        if assignees is not None:
            obj.assignees.set(assignees)
        return obj


class TodoSyncSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    assignees = serializers.PrimaryKeyRelatedField(many=True, read_only=True)

    is_hidden = serializers.BooleanField(read_only=True)
    class Meta:
        model = Todo
        fields = [
            "id",
            "project",
            "title",
            "status",
            "assignees",
            "created_by",
            "is_recurring",

            # ✅ new
            "start_date",
            "start_time",
            "end_date",
            "end_time",

            "weekday_mask",
            "alarm_offset_minutes",
            # "sort_index",
            "completed_at",
            "version",
            "created_at",
            "updated_at",

            "is_hidden",
            "kind",
        ]
        read_only_fields = fields


class TodoUserStateSerializer(serializers.ModelSerializer):
    todo_id = serializers.UUIDField(source="todo.id", read_only=True)

    class Meta:
        model = TodoUserState
        fields = ["todo_id", "is_hidden", "hidden_at", "updated_at"]

# 입력 전용 (project는 URL에서 받으므로 불필요)
class MemberRoleInputSerializer(serializers.Serializer):
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        source="user"
    )
    role = serializers.ChoiceField(choices=ProjectMember.Role.choices)







class ProjectInviteSerializer(serializers.ModelSerializer):
    project = ProjectSerializer(read_only=True)
    invited_by = UserSerializer(read_only=True)
    invited_user = UserSerializer(read_only=True)

    class Meta:
        model = ProjectInvite
        fields = [
            "id",
            "project",
            "invited_by",
            "email",
            "invited_user",
            "role",
            "status",
            "token",
            "expires_at",
            "created_at",
        ]


class ProjectInviteCreateSerializer(serializers.Serializer):
    # email = serializers.EmailField()
    id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        source="invited_user",
    )
    role = serializers.ChoiceField(choices=ProjectMember.Role.choices)


class ProjectInviteTokenSerializer(serializers.Serializer):
    token = serializers.CharField()


class NotificationSerializer(serializers.ModelSerializer):
    project = ProjectSerializer(read_only=True)
    extra = serializers.JSONField(read_only=True)
    invite_status = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "kind",
            "message",
            "project",
            "is_read",
            "created_at",
            "extra",
            "invite_status",  # 프로젝트 초대 알림일 때만 값이 들어감
        ]

    def get_invite_status(self, obj):
        # 프로젝트 초대 알림이 아니면 None
        if obj.kind != Notification.Kind.PROJECT_INVITE:
            return None

        token = (obj.extra or {}).get("invite_token")
        if not token:
            return None

        try:
            inv = ProjectInvite.objects.get(token=token)
        except ProjectInvite.DoesNotExist:
            return None

        return inv.status  # "pending" | "accepted" | "declined" | "expired"
    

class NotificationSyncRequestSerializer(serializers.Serializer):
    # 마지막으로 동기화한 시각 (옵션)
    last_synced_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
    )
    


class ProjectLeaveSerializer(serializers.Serializer):
    # User PK 타입에 맞춰 사용
    # - PK가 UUID면: PrimaryKeyRelatedField 그대로 ok
    # - int PK여도 ok (User queryset 기반이라 pk 타입 자동 맞음)
    new_owner = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.all(),
        required=False,
        allow_null=True,
    )



class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = [
            "id",
            "public_id",   # ✅ 추가
            "platform",
            "fcm_token",
            "is_active",
            "created_at",
            "last_used_at",
        ]
        read_only_fields = ["id", "public_id", "created_at", "last_used_at"]


class DeviceRegisterSerializer(serializers.Serializer):
    public_id = serializers.UUIDField(required=False, allow_null=True)
    fcm_token = serializers.CharField(max_length=255)
    platform = serializers.ChoiceField(choices=Device.Platform.choices)


class DeviceUnregisterSerializer(serializers.Serializer):
    # ✅ token 대신 public_id, 없으면 스킵
    public_id = serializers.UUIDField(required=False, allow_null=True)

class HolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Holiday
        fields = [
            "id",
            "locdate",
            "date_name",
            "date_kind",
            "is_holiday",
            "seq",
            "source",
            "created_at",
            "updated_at",
        ]



class UserNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserNote
        fields = ["id", "content", "version", "created_at", "updated_at"]
        read_only_fields = ["id", "version", "created_at", "updated_at"]



class SignOutSerializer(serializers.Serializer):
    refresh_token = serializers.CharField(required=False, allow_blank=True)
    public_id = serializers.UUIDField(required=False, allow_null=True)
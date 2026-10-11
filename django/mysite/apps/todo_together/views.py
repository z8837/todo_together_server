from django.http import HttpResponse, JsonResponse
from django.core.serializers.json import DjangoJSONEncoder

from django.db.models import Q, Exists, OuterRef, Subquery, Value, F, Case, When, FloatField, Prefetch
from django.db.models.functions import Coalesce
from django.db import transaction, router
import json
import os
from wakeonlan import send_magic_packet
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponseForbidden
from .models import *
from .serializers import *
from rest_framework.views import APIView

from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .serializers import *
from django.contrib.auth import get_user_model
from datetime import timedelta
import secrets
from django.utils import timezone

from .services import (
    handle_social_login, refresh_access_token, pick_new_owner, leave_project,
    withdraw_user)
from rest_framework import status, mixins

from rest_framework import viewsets, permissions, status, filters
from rest_framework.decorators import action

from .models import Project, ProjectMember, Todo, UserNote, TodoKind

from django_filters.rest_framework import DjangoFilterBackend
from .tokens import refresh_with_sliding

from rest_framework.exceptions import PermissionDenied

from .auth import AccessTokenAuthentication
    
from .services import (
    send_project_invite_notification,
    send_project_updated_notifications,
    send_profile_updated_notifications,
    send_project_membership_changed_data_only
)

from .tasks import todo_post_create_task, todo_post_update_task, todo_post_destroy_task
from django.shortcuts import get_object_or_404


from django.shortcuts import render
from django.urls import reverse
from django.core.mail import send_mail

from django.views.decorators.http import require_http_methods

from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from urllib.parse import urlencode

class IntentResponseRedirect(HttpResponseRedirect):
    allowed_schemes = ["http", "https", "intent"]  # ✅ intent 허용

@csrf_exempt
@require_POST
def apple_callback(request):
    code = request.POST.get("code")
    id_token = request.POST.get("id_token")
    state = request.POST.get("state")

    if not code:
        return HttpResponseBadRequest("Missing code")

    params = {"code": code}
    if id_token:
        params["id_token"] = id_token
    if state:
        params["state"] = state

    package = "kr.co.heeyun.todotogether"
    intent_url = (
        f"intent://callback?{urlencode(params)}"
        f"#Intent;package={package};scheme=signinwithapple;end"
    )

    return IntentResponseRedirect(intent_url)

@require_http_methods(["GET"])
def account_deletion_page(request):
    return render(request, "legal/account_deletion.html", {"success": False})


@require_http_methods(["POST"])
def account_deletion_submit(request):
    email = (request.POST.get("email") or "").strip().lower()
    provider = (request.POST.get("provider") or "").strip().lower()
    reason = (request.POST.get("reason") or "").strip()

    if provider not in ("google", "naver", "kakao"):
        # 계정 유무/오류를 사용자에게 노출하지 않도록 성공 화면으로 통일
        return render(request, "legal/account_deletion.html", {"success": True})

    token = secrets.token_urlsafe(32)
    expires_at = timezone.now() + timedelta(minutes=30)

    AccountDeletionRequest.objects.create(
        email=email,
        provider=provider,
        token=token,
        expires_at=expires_at,
        requested_ip=request.META.get("REMOTE_ADDR", ""),
        user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:255],
    )

    confirm_url = request.build_absolute_uri(
        reverse("account_deletion_confirm", args=[token])
    )

    subject = "[Todo Together] 계정 삭제 확인"
    body = (
        "Todo Together 계정 삭제 요청을 확인하려면 아래 링크를 눌러 주세요.\n\n"
        f"{confirm_url}\n\n"
        "이 링크는 일정 시간 후 만료됩니다.\n"
        "본인이 요청하지 않았다면 이 메일을 무시해 주세요."
    )

    send_mail(
        subject,
        body,
        getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@toto.example.com"),
        [email],
        fail_silently=True,
    )

    return render(request, "legal/account_deletion.html", {"success": True})


@require_http_methods(["GET", "POST"])
def account_deletion_confirm(request, token: str):
    # ✅ 이 모델이 실제로 사용하는 DB를 라우터로 결정
    db = router.db_for_write(AccountDeletionRequest)

    if request.method == "GET":
        now = timezone.now()
        req = AccountDeletionRequest.objects.using(db).filter(token=token).first()

        valid = bool(
            req
            and req.expires_at >= now
            and req.status == AccountDeletionRequest.Status.PENDING
        )

        # confirm 화면(버튼 있는 화면) / invalid 화면 분기
        return render(
            request,
            "legal/account_deletion_confirm.html",
            {"state": "confirm" if valid else "invalid"}
        )

    # ✅ POST에서만 트랜잭션 + row lock + 탈퇴 처리
    with transaction.atomic(using=db):
        now = timezone.now()
        req = (
            AccountDeletionRequest.objects.using(db)
            .select_for_update()
            .filter(token=token)
            .first()
        )

        if (not req) or (req.expires_at < now) or (req.status != AccountDeletionRequest.Status.PENDING):
            return render(request, "legal/account_deletion_confirm.html", {"state": "invalid"})

        # ✅ 1회성 처리
        req.status = AccountDeletionRequest.Status.CONFIRMED
        req.confirmed_at = now
        req.save(update_fields=["status", "confirmed_at"])

        # ✅ 실제 유저 찾기 (같은 DB로)
        users_qs = User.objects.using(db).filter(
            provider=req.provider,
            email__iexact=req.email,
            is_deleted=False,
            is_active=True,
        )

        if users_qs.count() == 1:
            user = users_qs.first()
            withdraw_user(
                user=user,
                notify_fn=send_project_membership_changed_data_only,
            )

        # 계정 존재 여부를 노출하지 않기 위해 결과는 동일하게 완료 처리
        req.status = AccountDeletionRequest.Status.PROCESSED
        req.processed_at = now
        req.save(update_fields=["status", "processed_at"])

    return render(request, "legal/account_deletion_confirm.html", {"state": "done"})






class SocialLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        s = SocialLoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        result = handle_social_login(
            provider=s.validated_data['provider'],
            token=s.validated_data['token'],
            nickname=s.validated_data.get('nickname'),
            request=request,
            redirect_uri=s.validated_data.get('redirect_uri'),  # ✅ 추가
            identity_token=s.validated_data.get('identity_token'),  # ✅ 추가
        )
        # result 안에 refresh 모델(or exp)이 없다면, 발급 함수에서 반환하도록 살짝 수정하세요.
        # 예: result = {'user': user, 'access_token': access, 'refresh_token': rt.token, 'refresh_obj': rt, 'created': created}

        refresh_obj = result['refresh_token']
        refresh_token = refresh_obj.token
        refresh_exp = int(refresh_obj.expires_at.timestamp())

        user = result['user']
         # ✅ 디바이스 활성화: public_id 우선, 없으면 fcm_token, 둘 다 없으면 스킵
        public_id = s.validated_data.get("public_id")
        fcm_token = s.validated_data.get("fcm_token")
        
        activated_public_id = None

        if public_id:
            device = Device.objects.filter(public_id=public_id).first()
            if device:
                device.user = user            # (추천) 로그인 유저로 소유권 맞춤
                device.is_active = True
                device.save(update_fields=["user", "is_active", "last_used_at"])
                activated_public_id = device.public_id

        elif fcm_token:
            device = Device.objects.filter(fcm_token=fcm_token).first()
            if device:
                device.user = user            # (추천) 로그인 유저로 소유권 맞춤
                device.is_active = True
                device.save(update_fields=["user", "is_active", "last_used_at"])
                activated_public_id = device.public_id

        return Response({
            'user': UserSerializer(user).data,
            'access_token': result['access_token'],
            'refresh_token': refresh_token,
            'refresh_exp': refresh_exp,
            'is_new_user': result['created'],
            # ✅ 추가: 활성화된 디바이스가 있으면 그 public_id, 아니면 null
            'public_id': str(activated_public_id) if activated_public_id else None,
        }, status=status.HTTP_200_OK)


class TokenRefreshView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        s = RefreshTokenSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        # 슬라이딩 연장 + access 재발급
        user, access_token, rt = refresh_with_sliding(s.validated_data['refresh_token'])
        refresh_exp = int(rt.expires_at.timestamp())

        return Response({
            'user': UserSerializer(user).data,
            'access_token': access_token,
            'refresh_token': rt.token,     # B안(슬라이딩): 문자열 동일
            'refresh_exp': refresh_exp,    # 갱신된 만료시각
        }, status=status.HTTP_200_OK)


class MeView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class NicknameView(APIView):

    def patch(self, request):
        user = request.user
        old_nickname = user.nickname  # ✅ 변경 전 닉네임 저장

        serializer = NicknameSerializer(
            instance=user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # DB에서 다시 읽어올 필요까진 없지만 안전하게 하고 싶으면:
        # user.refresh_from_db()

        # ✅ 닉네임이 실제로 바뀐 경우에만 알림 전송
        if old_nickname != user.nickname:
            send_profile_updated_notifications(user)

        return Response(
            UserSerializer(user).data,
            status=status.HTTP_200_OK,
        )






def _user_payload(user):
    return {
        "email": user.email,
        "nickname": user.nickname,
        # 필요하면 더 추가 (프로필이미지 등)
        # "avatarUrl": user.avatar_url,
    }



class IsAuthenticated(permissions.IsAuthenticated):
    pass


User = get_user_model()

# ---------- Project ----------
class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]

    # 간단 필터/검색/정렬
    filterset_fields = {
        "id": ["exact", "in"],
        "name": ["exact", "icontains"],
        "updated_at": ["gte", "lte"],
    }
    search_fields = ["name", "description", "version"]
    ordering_fields = ["updated_at", "name", "created_at"]
    ordering = ["created_at"]

    def get_queryset(self):
        user = self.request.user
        # owner 이거나 멤버십에 포함된 프로젝트만
        return Project.objects.filter(
            Q(owner=user) | Q(membership__user=user, membership__is_deleted=False)
        ).distinct().prefetch_related(Prefetch(
            "user_preferences", queryset=ProjectUserPreference.objects.filter(user=user),
            to_attr="_current_user_preferences",
        ))

    def perform_destroy(self, instance):
        # 소프트 삭제
        instance.soft_delete()

    # ----- 멤버십 목록 -----
    @action(detail=True, methods=["get"], url_path="members")
    def list_members(self, request, pk=None):
        project = self.get_object()
        if not project.has_read(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        data = ProjectMemberSerializer(project.membership.all(), many=True).data
        return Response(data)

    # ----- 멤버 역할 부여/변경 (owner만) -----
    @action(detail=True, methods=["post"], url_path="set-member-role")
    def set_member_role(self, request, pk=None):
        project = self.get_object()
        if not project.can_grant_roles(request.user):
            raise PermissionDenied("owner만 멤버 권한을 변경할 수 있습니다.")

        # ⬇️ 여기서 ModelSerializer 대신 입력 전용 Serializer 사용
        s = MemberRoleInputSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        target_user = s.validated_data["user"]
        role = s.validated_data["role"]

        pm = project.set_member_role(actor=request.user, user=target_user, role=role)
        project.bump_version(save=True)

        user = request.user
        send_project_membership_changed_data_only(
            project=project,
            event_type="project_member_role_changed", 
            actor=user,                          
            subject_user=user,                   
            role=role,
        )

        return Response(ProjectMemberSerializer(pm).data, status=status.HTTP_200_OK)
    

    # ----- 유저 검색 (프로젝트에 추가할 유저 찾기) -----
    @action(detail=True, methods=["get"], url_path="search-users")
    def search_users(self, request, pk=None):
        """
        GET /api/projects/{id}/search-users?q=키워드&limit=20

        - q: 이메일/닉네임 부분일치 검색
        - limit: 최대 몇 명까지 (기본 20, 최대 50 정도로 제한)
        - 이미 이 프로젝트의 멤버 + owner 는 결과에서 제외
        """
        project = self.get_object()

        # 읽기 권한 정도는 있어야 검색 가능 (원하면 has_write로 바꿔도 됨)
        if not project.has_read(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)

        q = (request.query_params.get("q") or "").strip()
        try:
            limit = int(request.query_params.get("limit", 20))
        except ValueError:
            limit = 20
        limit = max(1, min(limit, 50))  # 1~50 사이로 제한

        qs = User.objects.all()

        if q:
            qs = qs.filter(
                Q(email__icontains=q) |
                Q(nickname__icontains=q)
            )

        # 이미 프로젝트에 있는 멤버 + owner 제외
        member_ids = project.members.values_list("id", flat=True)
        qs = qs.exclude(id__in=member_ids).exclude(id=project.owner_id)

        # 필요하면 자기 자신도 제외하고 싶다면 아래 주석 해제
        qs = qs.exclude(id=request.user.id)

        qs = qs.order_by("nickname", "email")[:limit]

        # 이미 있는 UserSerializer 재활용
        data = UserSerializer(qs, many=True).data
        return Response(data, status=status.HTTP_200_OK)
    


    @action(detail=True, methods=["post"], url_path="invites")
    def create_invite(self, request, pk=None):
        project = self.get_object()

        if not project.can_grant_roles(request.user):
            raise PermissionDenied("owner만 초대할 수 있습니다.")

        s = ProjectInviteCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        invited_user = s.validated_data["invited_user"]
        role = s.validated_data["role"]

        # ✅ pending 중복이면 "새로 생성/알림 전송 X"
        existing = ProjectInvite.objects.filter(
            project=project,
            invited_user=invited_user,
            status=ProjectInvite.Status.PENDING,
        ).order_by("-created_at").first()

        if existing:
            return Response(ProjectInviteSerializer(existing).data, status=status.HTTP_200_OK)
            # 또는 정말 아무 응답 느낌이면:
            # return Response(status=status.HTTP_204_NO_CONTENT)

        token = secrets.token_urlsafe(32)
        expires_at = timezone.now() + timedelta(days=7)

        invite = ProjectInvite.objects.create(
            project=project,
            invited_by=request.user,
            invited_user=invited_user,
            # (옵션) 모델에 email 필드가 남아있다면 기록용으로 채워두기
            email=invited_user.email,
            role=role,
            token=token,
            expires_at=expires_at,
            status=ProjectInvite.Status.PENDING,
        )

        Notification.objects.create(
            user=invited_user,
            kind=Notification.Kind.PROJECT_INVITE,
            project=project,
            message=f"'{project.name}' 프로젝트에 초대되었습니다.",
            extra={"invite_token": token, "from": _user_payload(request.user)},
        )
        send_project_invite_notification(invite)

        return Response(ProjectInviteSerializer(invite).data, status=status.HTTP_201_CREATED)
        

    @action(detail=True, methods=["post"], url_path="leave")
    def leave(self, request, pk=None):
        project = self.get_object()
        user = request.user

        # owner가 아니면 serializer 필요 없음
        if project.owner_id != user.id:
            leave_project(
                project=project,
                user=user,
                notify_fn=send_project_membership_changed_data_only,
            )
            return Response(status=status.HTTP_204_NO_CONTENT)

        # owner면 new_owner 입력 받기
        s = ProjectLeaveSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        new_owner = s.validated_data.get("new_owner")

        leave_project(
            project=project,
            user=user,
            new_owner=new_owner,
            notify_fn=send_project_membership_changed_data_only,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
    

    def perform_update(self, serializer):
        project = serializer.instance  # 업데이트 전 instance

        # ✅ owner + manager만 프로젝트 수정 가능
        if not project.has_manage_project(self.request.user):
            raise PermissionDenied("owner 또는 manager만 프로젝트를 수정할 수 있습니다.")
        
        # ✅ 변경 전 스냅샷 (원하는 필드만)
        before = {
            "name": project.name,
            "description": project.description,
        }

        # 저장
        updated_project = serializer.save()

        # ✅ 변경 후 스냅샷
        after = {
            "name": updated_project.name,
            "description": updated_project.description,
        }

        # ✅ diff 만들기
        changes = []
        for k in before.keys():
            if before[k] != after[k]:
                changes.append({
                    "field": k,
                    "before": before[k],
                    "after": after[k],
                })

        updated_project.bump_version(save=True)

        # ✅ 변경점 함께 전달
        send_project_updated_notifications(
            updated_project,
            editor=self.request.user,
            event="updated",
            changes=changes,
        )


    def destroy(self, request, *args, **kwargs):
        project = self.get_object()

        # ✅ 삭제는 owner만
        if not project.can_delete_project(request.user):
            raise PermissionDenied("owner만 프로젝트를 삭제할 수 있습니다.")

        # ✅ 삭제 전에 멤버 스냅샷 확보
        members_snapshot = list(project.members.all()) + [project.owner]

        # ✅ 소프트 삭제
        self.perform_destroy(project)
        project.bump_version(save=True)

        # ✅ 같은 함수로 "삭제" 알림 발송
        send_project_updated_notifications(
            project=project,
            editor=request.user,
            changes=[],
            event="destroy",
            members_snapshot=members_snapshot,
        )

        return Response(status=status.HTTP_204_NO_CONTENT)
    

    @action(detail=True, methods=["get", "put", "patch"], url_path="my-settings")
    def my_settings(self, request, pk=None):
        project = self.get_object()
        if not project.has_read(request.user):
            raise PermissionDenied("프로젝트에 접근할 수 없습니다.")

        if request.method == "GET":
            preference = ProjectUserPreference.objects.filter(project=project, user=request.user).first()
            if preference is None:
                return Response({"project": str(project.id), "color_override": "", "version": 0, "updated_at": None})
            return Response(ProjectUserPreferenceSerializer(preference).data)

        incoming = ProjectUserPreferenceInputSerializer(data=request.data)
        incoming.is_valid(raise_exception=True)
        database = router.db_for_write(ProjectUserPreference)
        with transaction.atomic(using=database):
            preference, _ = ProjectUserPreference.objects.get_or_create(
                project=project, user=request.user, defaults={"version": 0},
            )
            preference = ProjectUserPreference.objects.select_for_update().get(pk=preference.pk)
            client_version = incoming.validated_data.get("version")
            if client_version is not None and client_version != preference.version:
                return Response({
                    "detail": "다른 기기에서 색을 변경했습니다. 새로고침 후 다시 선택해주세요.",
                    "preference": ProjectUserPreferenceSerializer(preference).data,
                }, status=status.HTTP_409_CONFLICT)
            color = incoming.validated_data["color_override"]
            if color != preference.color_override or preference.version == 0:
                preference.color_override = color
                preference.version += 1
                preference.save(update_fields=["color_override", "version", "updated_at"])
        return Response(ProjectUserPreferenceSerializer(preference).data)

    @action(detail=True, methods=["get", "put"], url_path="checklist-order")
    def checklist_order(self, request, pk=None):
        project = self.get_object()

        # 개인 설정이라 read 권한만 있어도 저장 허용하는 게 보통 더 UX 좋음
        # (원하면 has_write로 바꿔도 됨)
        if not project.has_read(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)

        # --- 현재 프로젝트의 "체크리스트" id 목록 구하기 ---
        # 네 구조상 체크리스트는 Todo(kind=CHECKLIST)로 보임
        checklist_qs = Todo.all_objects.filter(
            project=project,
            kind=TodoKind.CHECKLIST,
            is_deleted=False,
        ).only("id", "created_at").order_by("created_at")

        current_ids = [str(x.id) for x in checklist_qs]
        current_set = set(current_ids)

        # --- 유저의 order row 가져오기/생성 ---
        order_obj, created = ProjectChecklistOrder.all_objects.get_or_create(
            project=project,
            user=request.user,
            defaults={"ordered_checklist_ids": [], "version": 1, "is_deleted": False},
        )
        if order_obj.is_deleted:
            # soft delete를 쓰는 경우 되살리기
            order_obj.is_deleted = False
            order_obj.save(update_fields=["is_deleted", "updated_at"])

        def _normalize(ids: list[str]) -> list[str]:
            # 1) 중복 제거(순서 유지)
            seen = set()
            ids = [i for i in ids if not (i in seen or seen.add(i))]
            # 2) 현재 존재하는 checklist만 남김
            ids = [i for i in ids if i in current_set]
            # 3) 누락된 새 checklist는 뒤에 붙이기(안 넣으면 저장 순간에 “사라진 것처럼” 될 수 있음)
            missing = [i for i in current_ids if i not in set(ids)]
            return ids + missing

        if request.method == "GET":
            # 저장된 order가 있으면 보정해서 내려주기
            saved = list(order_obj.ordered_checklist_ids or [])
            normalized = _normalize(saved)

            # (선택) 읽기 시 보정된 값을 DB에도 반영하고 싶으면 아래 2줄 사용
            # if normalized != saved:
            #     order_obj.ordered_checklist_ids = normalized; order_obj.save(update_fields=["ordered_checklist_ids","updated_at"])

            return Response(
                {
                    "project": str(project.id),
                    "ordered_checklist_ids": normalized,
                    "version": order_obj.version,
                    "updated_at": order_obj.updated_at,
                },
                status=status.HTTP_200_OK,
            )

        # --- PUT: 저장 ---
        s = ProjectChecklistOrderUpsertSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        # client_version = s.validated_data.get("version")
        incoming = s.validated_data["ordered_checklist_ids"]

        normalized = _normalize([str(x) for x in incoming])

        order_obj.ordered_checklist_ids = normalized
        order_obj.bump_version(save=True, extra_update_fields={"ordered_checklist_ids"})



        return Response(
            {
                "project": str(project.id),
                "ordered_checklist_ids": order_obj.ordered_checklist_ids,
                "version": order_obj.version,
                "updated_at": order_obj.updated_at,
            },
            status=status.HTTP_200_OK,
        )





# views.py
class ProjectSyncView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        # ---- 1) 기존 Project sync ----
        client_projects = {
            p["id"]: p["version"]
            for p in request.data.get("projects", [])
            if "id" in p and "version" in p
        }

        qs = Project.all_objects.filter(
            Q(owner=user) | Q(membership__user=user, membership__is_deleted=False)
        ).distinct().order_by("created_at").prefetch_related(Prefetch(
            "user_preferences", queryset=ProjectUserPreference.objects.filter(user=user),
            to_attr="_current_user_preferences",
        ))

        to_send = []
        deleted_ids = []

        readable_project_ids = []  # 아래 order sync에서 재사용
        for p in qs:
            pid = str(p.id)
            readable_project_ids.append(pid)

            client_version = client_projects.get(pid, None)

            if client_version is None:
                if not p.is_deleted:
                    to_send.append(p)
                continue

            if p.is_deleted:
                deleted_ids.append(pid)
                continue

            if p.version > client_version:
                to_send.append(p)

        # ---- 2) ProjectChecklistOrder sync 추가 ----
        client_orders = {
            str(o["project"]): o["version"]
            for o in request.data.get("checklist_orders", [])
            if "project" in o and "version" in o
        }

        orders_qs = ProjectChecklistOrder.all_objects.filter(
            user=user,
            project_id__in=readable_project_ids,
        ).select_related("project")

        orders_to_send = []
        orders_deleted_project_ids = []

        for o in orders_qs:
            pid = str(o.project_id)
            client_version = client_orders.get(pid, None)

            if client_version is None:
                if not o.is_deleted:
                    orders_to_send.append(o)
                continue

            if o.is_deleted:
                orders_deleted_project_ids.append(pid)
                continue

            if o.version > client_version:
                orders_to_send.append(o)

        data = {
            "projects": ProjectSerializer(to_send, many=True, context={"request": request}).data,
            "deleted": deleted_ids,

            # ✅ 추가
            "checklist_orders": ProjectChecklistOrderSyncSerializer(orders_to_send, many=True).data,
            "checklist_orders_deleted": orders_deleted_project_ids,
        }
        # Private changes have their own versions; do not bump the shared project
        # version or notify other members when somebody changes their display color.
        client_preferences = {
            str(item["project"]): item["version"]
            for item in request.data.get("preferences", [])
            if "project" in item and "version" in item
        }
        preferences = ProjectUserPreference.objects.filter(
            user=user, project_id__in=readable_project_ids, project__is_deleted=False,
        )
        data["preferences"] = ProjectUserPreferenceSerializer([
            preference for preference in preferences
            if preference.version > client_preferences.get(str(preference.project_id), -1)
        ], many=True).data
        return Response(data, status=status.HTTP_200_OK)






class ProjectInviteViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectInviteSerializer

    def get_queryset(self):
        user = self.request.user
        # 내 이메일로 온, 아직 처리 안 된 초대
        return ProjectInvite.objects.filter(
            status=ProjectInvite.Status.PENDING
        ).filter(
            Q(invited_user=user) | Q(email__iexact=user.email)
        )

    @action(detail=False, methods=["get"], url_path="me")
    def my_invites(self, request):
        qs = self.get_queryset()
        data = ProjectInviteSerializer(qs, many=True).data
        return Response(data)

    # @action(detail=False, methods=["post"], url_path="accept")
    # def accept(self, request):
    #     s = ProjectInviteTokenSerializer(data=request.data)
    #     s.is_valid(raise_exception=True)
    #     token = s.validated_data["token"]

    #     try:
    #         invite = ProjectInvite.objects.get(token=token)
    #     except ProjectInvite.DoesNotExist:
    #         return Response({"detail": "초대를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

    #     if invite.status != ProjectInvite.Status.PENDING:
    #         return Response({"detail": "이미 처리된 초대입니다."}, status=status.HTTP_400_BAD_REQUEST)

    #     if invite.is_expired():
    #         invite.status = ProjectInvite.Status.EXPIRED
    #         invite.save(update_fields=["status", "updated_at"])
    #         return Response({"detail": "초대가 만료되었습니다."}, status=status.HTTP_400_BAD_REQUEST)

    #     user = request.user
    #     if invite.email.lower() != user.email.lower():
    #         return Response({"detail": "초대된 이메일과 로그인한 계정이 다릅니다."}, status=status.HTTP_403_FORBIDDEN)

    #     invite.invited_user = user
    #     invite.status = ProjectInvite.Status.ACCEPTED
    #     invite.save(update_fields=["invited_user", "status", "updated_at"])

    #     # 멤버십 생성/업데이트
    #     pm, _ = ProjectMember.objects.update_or_create(
    #         project=invite.project,
    #         user=user,
    #         defaults={"role": invite.role},
    #     )

    #     # owner에게 알림
    #     Notification.objects.create( #초대 수락
    #         user=invite.project.owner,
    #         kind=Notification.Kind.PROJECT_INVITE_ACCEPTED,
    #         project=invite.project,
    #         message=f"{user.nickname or user.email} 님이 초대를 수락했습니다.",
    #         extra={"invited_user_id": str(user.id)},
    #     )
        
    #     return Response(ProjectMemberSerializer(pm).data, status=status.HTTP_200_OK)

    # @action(detail=False, methods=["post"], url_path="decline")
    # def decline(self, request):
    #     s = ProjectInviteTokenSerializer(data=request.data)
    #     s.is_valid(raise_exception=True)
    #     token = s.validated_data["token"]

    #     try:
    #         invite = ProjectInvite.objects.get(token=token)
    #     except ProjectInvite.DoesNotExist:
    #         return Response({"detail": "초대를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

    #     if invite.status != ProjectInvite.Status.PENDING:
    #         return Response({"detail": "이미 처리된 초대입니다."}, status=status.HTTP_400_BAD_REQUEST)

    #     user = request.user
    #     if invite.email.lower() != user.email.lower():
    #         return Response({"detail": "초대된 이메일과 로그인한 계정이 다릅니다."}, status=status.HTTP_403_FORBIDDEN)

    #     invite.invited_user = user
    #     invite.status = ProjectInvite.Status.DECLINED
    #     invite.save(update_fields=["invited_user", "status", "updated_at"])

    #     Notification.objects.create( #초대 거절
    #         user=invite.project.owner,
    #         kind=Notification.Kind.PROJECT_INVITE_DECLINED,
    #         project=invite.project,
    #         message=f"{user.nickname or user.email} 님이 초대를 거절했습니다.",
    #     )

    #     return Response(status=status.HTTP_204_NO_CONTENT)





# ---------- Todo ----------


def _todo_snapshot(todo):
    """
    변경점 비교를 위한 스냅샷(필요한 필드만, 비교하기 쉬운 형태로 정규화)
    """
    return {
        "title": todo.title,
        "status": todo.status,
        "is_recurring": todo.is_recurring,

        # 반복 요일은 mask 그대로보다 decode한 리스트가 diff에 유용
        "weekdays": sorted(todo.get_weekdays()) if todo.is_recurring else [],

        "start_date": todo.start_date.isoformat() if todo.start_date else None,
        "end_date": todo.end_date.isoformat() if todo.end_date else None,
        "start_time": todo.start_time.isoformat() if todo.start_time else None,
        "end_time": todo.end_time.isoformat() if todo.end_time else None,

        "alarm_offset_minutes": todo.alarm_offset_minutes,

        # M2M: assignees는 id 리스트로 저장(순서 안정화)
        # "assignee_ids": list(
        #     todo.assignees.order_by("id").values_list("id", flat=True)
        # ),

        # 완료시각도 보고 싶으면 포함(선택)
        # "completed_at": todo.completed_at.isoformat() if todo.completed_at else None,
    }


def _make_changes(before: dict, after: dict):
    changes = []
    for k in before.keys():
        if before[k] != after[k]:
            changes.append({
                "field": k,
                "before": before[k],
                "after": after[k],
            })
    return changes

import logging
logger = logging.getLogger(__name__)

class TodoViewSet(viewsets.ModelViewSet):
    authentication_classes = [AccessTokenAuthentication]
    serializer_class = TodoSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]

    filterset_fields = {
        "id": ["exact", "in"],
        "project": ["exact", "in"],
        "kind": ["exact", "in"],  # ✅ 추가
        "status": ["exact", "in"],
        "is_recurring": ["exact"],
        "start_date": ["exact", "gte", "lte"],
        "end_date": ["exact", "gte", "lte"],  # ✅ checklist 마감 검색용(추천)
        "weekday_mask": ["exact"],
        "created_by": ["exact"],
    }
    search_fields = ["title"]
    ordering_fields = ["project", "status", "start_date", "end_date", "updated_at", "created_at"]
    ordering = ["project", "start_date", "-updated_at"]

    def get_queryset(self):
        user = self.request.user
        readable_projects = Project.objects.filter(
            Q(owner=user) | Q(membership__user=user)
        ).values_list("id", flat=True)

        qs = Todo.objects.filter(project_id__in=readable_projects)

        # 숨김
        hidden_exists = TodoUserState.objects.filter(
            user=user, todo_id=OuterRef("pk"), is_hidden=True
        )
        qs = qs.annotate(is_hidden=Exists(hidden_exists))  # ✅ serializer의 is_hidden과 맞춤

        
        return qs

    def perform_create(self, serializer):
        todo = serializer.save()
        actor_id = self.request.user.id
        todo_id = str(todo.id)
        transaction.on_commit(lambda: todo_post_create_task.delay(todo_id, actor_id))
        # todo = serializer.save()
        # send_todo_activity_notifications(todo, actor=self.request.user, event="created")
        # schedule_next_reminders_for_todo(todo)

    def perform_update(self, serializer):
        todo_before = serializer.instance
        before = _todo_snapshot(todo_before)
        old_status = todo_before.status

        todo = serializer.save()
        # completed_at 처리(그대로)
        if old_status != todo.status:
            if todo.status == Todo.Status.DONE:
                todo.completed_at = timezone.now()
                todo.save(update_fields=["completed_at", "updated_at"])
            elif old_status == Todo.Status.DONE:
                todo.completed_at = None
                todo.save(update_fields=["completed_at", "updated_at"])

        todo.bump_version(save=True)
        # ✅ CHECKLIST는 업데이트 활동알림 스킵
        changes = []
        if todo.kind != TodoKind.CHECKLIST:
            after = _todo_snapshot(todo)
            changes = _make_changes(before, after)
            # send_todo_activity_notifications(
            #     todo, actor=self.request.user, event="updated", changes=changes
            # )
        actor_id = self.request.user.id
        todo_id = str(todo.id)

        def _enqueue():
            logger.warning("[on_commit] fired update todo=%s", todo_id)
            try:
                r = todo_post_update_task.delay(todo_id, actor_id, "updated", changes)
                logger.warning("[on_commit] enqueued task=%s todo=%s", r.id, todo_id)
            except Exception:
                logger.exception("[on_commit] enqueue failed update todo=%s", todo_id)

        transaction.on_commit(_enqueue)
        # transaction.on_commit(lambda: todo_post_update_task.delay(todo_id, actor_id, "updated", changes))
        # schedule_next_reminders_for_todo(todo)

    def _only_status(self, request):
        return set(request.data.keys()).issubset({"status"})

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
    
        if self._only_status(request):
            if not instance.can_change_status(request.user):
                return Response({"detail": "status 변경 권한이 없습니다."}, status=403)
        else:
            if not instance.can_edit_content(request.user):
                return Response({"detail": "생성자 또는 프로젝트 owner만 수정할 수 있습니다."}, status=403)

        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()

        if self._only_status(request):
            if not instance.can_change_status(request.user):
                return Response({"detail": "status 변경 권한이 없습니다."}, status=403)
        else:
            if not instance.can_edit_content(request.user):
                return Response({"detail": "생성자 또는 프로젝트 owner만 수정할 수 있습니다."}, status=403)

        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if not instance.can_delete(request.user):
            return Response({"detail": "생성자만 삭제할 수 있습니다."}, status=status.HTTP_403_FORBIDDEN)
        todo_id = str(instance.id)
        actor_id = request.user.id

        # ✅ 삭제 + 버전 bump를 한 트랜잭션으로 묶고, 커밋 후 task enqueue
        with transaction.atomic():
            instance.soft_delete()
            instance.bump_version(save=True)

            transaction.on_commit(lambda: todo_post_destroy_task.delay(todo_id, actor_id))
        return Response(status=status.HTTP_204_NO_CONTENT)
    

    @action(detail=True, methods=["post"], url_path="hide")
    def hide(self, request, pk=None):
        todo = self.get_object()

        state, _ = TodoUserState.objects.update_or_create(
            user=request.user,
            todo=todo,
            defaults={
                "is_hidden": True,
                "hidden_at": timezone.now(),
            },
        )
        return Response(TodoUserStateSerializer(state).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="unhide")
    def unhide(self, request, pk=None):
        todo = self.get_object()

        state, _ = TodoUserState.objects.update_or_create(
            user=request.user,
            todo=todo,
            defaults={
                "is_hidden": False,
                "hidden_at": None,
            },
        )
        return Response(TodoUserStateSerializer(state).data, status=status.HTTP_200_OK)
    


class TODOSyncView(APIView):
    authentication_classes = [AccessTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        client_todos = {
            t["id"]: t["version"]
            for t in request.data.get("todos", [])
            if "id" in t and "version" in t
        }

        readable_projects = Project.objects.filter(
            Q(owner=user) | Q(membership__user=user)
        ).values_list("id", flat=True)

        qs = Todo.all_objects.filter(project_id__in=readable_projects)

        # ✅ 여기 추가
        hidden_exists = TodoUserState.objects.filter(
            user=user,
            todo_id=OuterRef("pk"),
            is_hidden=True,
        )
        qs = qs.annotate(is_hidden=Exists(hidden_exists))

        to_send = []
        deleted_ids = []

        for todo in qs:
            todo_id_str = str(todo.id)
            client_version = client_todos.get(todo_id_str, None)

            if client_version is None:
                if not todo.is_deleted:
                    to_send.append(todo)
                continue

            if todo.is_deleted:
                deleted_ids.append(todo_id_str)
                continue

            if todo.version > client_version:
                to_send.append(todo)

        data = {
            "todos": TodoSyncSerializer(to_send, many=True).data,
            "deleted": deleted_ids,
        }
        return Response(data, status=status.HTTP_200_OK)














class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    내 알림 목록 + 읽음 처리 + 알림에서 바로 초대 수락/거절
    """
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Notification.objects.filter(user=user)

        # ?is_read=false 같은 필터 지원
        is_read = self.request.query_params.get("is_read")
        if is_read is not None:
            val = is_read.lower()
            if val in ("true", "1"):
                qs = qs.filter(is_read=True)
            elif val in ("false", "0"):
                qs = qs.filter(is_read=False)

        kind = self.request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind)

        return qs

    # ----- 단일 알림 읽음 처리 -----
    @action(detail=True, methods=["post"], url_path="read")
    def mark_read(self, request, pk=None):
        notif = self.get_object()
        if notif.user != request.user:
            return Response(status=status.HTTP_403_FORBIDDEN)

        notif.mark_read()
        return Response(NotificationSerializer(notif).data, status=status.HTTP_200_OK)

    # ----- 모든 알림 읽음 처리 -----
    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        user = request.user
        qs = Notification.objects.filter(user=user, is_read=False)
        updated = qs.update(is_read=True, read_at=timezone.now())
        return Response({"updated": updated}, status=status.HTTP_200_OK)

    # ----- 알림에서 바로 초대 수락 -----
    @action(detail=True, methods=["post"], url_path="accept-invite")
    def accept_invite(self, request, pk=None):
        notif = self.get_object()
        user = request.user

        if notif.user != user:
            return Response(status=status.HTTP_403_FORBIDDEN)

        if notif.kind != Notification.Kind.PROJECT_INVITE:
            return Response({"detail": "프로젝트 초대 알림이 아닙니다."},
                            status=status.HTTP_400_BAD_REQUEST)

        token = (notif.extra or {}).get("invite_token")
        if not token:
            return Response({"detail": "초대 토큰이 없습니다."},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            invite = ProjectInvite.objects.get(token=token)
        except ProjectInvite.DoesNotExist:
            notif.mark_read()
            return Response({"detail": "초대를 찾을 수 없습니다."},
                            status=status.HTTP_404_NOT_FOUND)

        # 이미 처리된 초대
        if invite.status != ProjectInvite.Status.PENDING:
            notif.mark_read()
            return Response({"detail": "이미 처리된 초대입니다."},
                            status=status.HTTP_400_BAD_REQUEST)

        # 만료 체크
        if invite.is_expired():
            invite.status = ProjectInvite.Status.EXPIRED
            invite.save(update_fields=["status", "updated_at"])
            notif.mark_read()
            return Response({"detail": "초대가 만료되었습니다."},
                            status=status.HTTP_400_BAD_REQUEST)

        # 이메일 매칭 체크
        if invite.email.lower() != user.email.lower():
            return Response({"detail": "초대된 이메일과 로그인한 계정이 다릅니다."},
                            status=status.HTTP_403_FORBIDDEN)

        # 초대 수락 처리
        invite.invited_user = user
        invite.status = ProjectInvite.Status.ACCEPTED
        invite.save(update_fields=["invited_user", "status", "updated_at"])

        pm, _ = ProjectMember.objects.update_or_create(
            project=invite.project,
            user=user,
            defaults={"role": invite.role},
        )

        # 이 알림은 읽음 처리
        notif.mark_read()

        # owner에게 "수락됨" 알림
        Notification.objects.create( #초대 수락
            user=invite.project.owner,
            kind=Notification.Kind.PROJECT_INVITE_ACCEPTED,
            project=invite.project,
            message=f"{user.nickname or user.email} 님이 초대를 수락했습니다.",
            extra={"invited_user_id": str(user.id), "from": _user_payload(request.user)},
        )
        
        invite.project.bump_version(save=True)  # ✅ 동기화/변경 감지용 (추천)
        
        send_project_membership_changed_data_only(
            project=invite.project,
            event_type="project_invite_accepted",   # 또는 "project_invite_accepted"
            actor=user,                           # 수락한 사람
            subject_user=user,                    # 들어온 사람
            role=invite.role,
            extra={
                "inviteToken": invite.token,      # 필요하면
            },
            onlyOwner=True,
        )
        return Response(ProjectMemberSerializer(pm).data,
                        status=status.HTTP_200_OK)

    # ----- 알림에서 바로 초대 거절 -----
    @action(detail=True, methods=["post"], url_path="decline-invite")
    def decline_invite(self, request, pk=None):
        notif = self.get_object()
        user = request.user

        if notif.user != user:
            return Response(status=status.HTTP_403_FORBIDDEN)

        if notif.kind != Notification.Kind.PROJECT_INVITE:
            return Response({"detail": "프로젝트 초대 알림이 아닙니다."},
                            status=status.HTTP_400_BAD_REQUEST)

        token = (notif.extra or {}).get("invite_token")
        if not token:
            return Response({"detail": "초대 토큰이 없습니다."},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            invite = ProjectInvite.objects.get(token=token)
        except ProjectInvite.DoesNotExist:
            notif.mark_read()
            return Response({"detail": "초대를 찾을 수 없습니다."},
                            status=status.HTTP_404_NOT_FOUND)

        if invite.status != ProjectInvite.Status.PENDING:
            notif.mark_read()
            return Response({"detail": "이미 처리된 초대입니다."},
                            status=status.HTTP_400_BAD_REQUEST)

        if invite.email.lower() != user.email.lower():
            return Response({"detail": "초대된 이메일과 로그인한 계정이 다릅니다."},
                            status=status.HTTP_403_FORBIDDEN)

        invite.invited_user = user
        invite.status = ProjectInvite.Status.DECLINED
        invite.save(update_fields=["invited_user", "status", "updated_at"])

        notif.mark_read()

        Notification.objects.create( #초대 거절
            user=invite.project.owner,
            kind=Notification.Kind.PROJECT_INVITE_DECLINED,
            project=invite.project,
            message=f"{user.nickname or user.email} 님이 초대를 거절했습니다.",
            extra = {
                "from": _user_payload(request.user)
            }
        )

        send_project_membership_changed_data_only(
            project=invite.project,
            event_type="project_invite_declined",   # 또는 "project_invite_accepted"
            actor=user,                           # 수락한 사람
            subject_user=user,                    # 들어온 사람
            role=invite.role,
            extra={
                "inviteToken": invite.token,      # 필요하면
            },
            onlyOwner=True,
        )

        return Response(status=status.HTTP_204_NO_CONTENT)


class NotificationSyncView(APIView):
    """
    POST /api/notifications/sync/

    body:
    {
      "last_synced_at": "2025-12-10T12:34:56Z"  // optional
    }

    응답:
    {
      "notifications": [...],  // is_deleted=False 이면서 updated_at > last_synced_at
      "deleted": ["id1", "id2"],  // is_deleted=True 이면서 updated_at > last_synced_at
      "server_time": "..."
    }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        s = NotificationSyncRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        last_synced_at = s.validated_data.get("last_synced_at")

        # 삭제 포함 전체(Notification.all_objects)에서 내 것만
        qs = Notification.all_objects.filter(user=user)

        if last_synced_at is not None:
            qs = qs.filter(updated_at__gt=last_synced_at)

        # 살아 있는 알림 / 삭제된 알림 분리
        changed_alive = qs.filter(is_deleted=False)
        changed_deleted = qs.filter(is_deleted=True)

        data = {
            "notifications": NotificationSerializer(changed_alive, many=True).data,
            "deleted": [str(n.id) for n in changed_deleted],
            "server_time": timezone.now(),  # 클라가 이걸 next last_synced_at로 쓰면 됨
        }
        return Response(data, status=status.HTTP_200_OK)


class UserSearchView(APIView):
    """
    GET /api/users/search?q=키워드&limit=20

    - q: 이메일/닉네임 부분검색
    - limit: 최대 몇 명까지 (기본 20, 최대 50)
    """
    permission_classes = [IsAuthenticated]   # 너가 정의한 래퍼 사용

    def get(self, request):
        user_id = request.query_params.get("id")

        if user_id:
            user = get_object_or_404(User, id=user_id)
            if user.id == request.user.id:
                return Response(UserSerializer(user).data)  # 원하면 허용/차단 선택
            return Response(UserSerializer(user).data)
        
        q = (request.query_params.get("q") or "").strip()

        # limit 파라미터 처리
        try:
            limit = int(request.query_params.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 50))  # 1~50 사이로 클램핑

        qs = User.objects.filter(is_active=True, is_deleted=False)

        if q:
            qs = qs.filter(
                Q(email__icontains=q) |
                Q(nickname__icontains=q)
            )

        # 자기 자신은 굳이 안 보고 싶다면 제외 (원하면 지워도 됨)
        qs = qs.exclude(id=request.user.id)

        qs = qs.order_by("nickname", "email")[:limit]

        data = UserSerializer(qs, many=True).data
        return Response(data)








class DeviceViewSet(viewsets.GenericViewSet):
    """
    FCM 디바이스 토큰 등록/해제용
    - POST /api/devices/register      : 토큰 등록/갱신
    - POST /api/devices/unregister    : 토큰 비활성화 (로그아웃 시)
    - GET  /api/devices/              : 내 디바이스 목록 (옵션)
    """
    authentication_classes = [AccessTokenAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = DeviceSerializer

    def get_queryset(self):
        # 내 디바이스만
        return Device.objects.filter(user=self.request.user)

    # (옵션) 내 디바이스 목록 조회
    def list(self, request, *args, **kwargs):
        qs = self.get_queryset().order_by("-last_used_at")
        data = DeviceSerializer(qs, many=True).data
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="register")
    @transaction.atomic
    def register(self, request):
        """
        body:
        {
          "public_id": "uuid(optional)",
          "fcm_token": "...",
          "platform": "android" | "ios"
        }
        """
        s = DeviceRegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        public_id = s.validated_data.get("public_id")
        token = s.validated_data["fcm_token"]
        platform = s.validated_data["platform"]
        user = request.user

        created = False

        # 0) 토큰이 이미 DB에 있으면 우선 확보(토큰 unique 충돌 방지)
        token_device = Device.objects.filter(fcm_token=token).first()

        if public_id:
            # 1) public_id로 내 디바이스를 찾는다(없으면 생성)
            device = Device.objects.filter(user=user, public_id=public_id).first()

            if device is None:
                # 없으면 생성하되, 토큰이 다른 row에 묶여있던 경우는 그 row를 재활용/이동
                if token_device:
                    device = token_device
                    created = False
                else:
                    device = Device(public_id=public_id)
                    created = True
            # 업데이트(소유권/토큰/플랫폼/활성화)
            device.user = user
            device.fcm_token = token
            device.platform = platform
            device.is_active = True
            device.save()
        else:
            # 2) public_id가 없으면: 토큰 기준으로 upsert (기존 동작 + public_id 자동 생성)
            device, created = Device.objects.get_or_create(
                fcm_token=token,
                defaults={
                    "user": user,
                    "platform": platform,
                    "is_active": True,
                },
            )
            if not created:
                if device.user_id != user.id:
                    device.user = user
                device.platform = platform
                device.is_active = True
                device.save(update_fields=["user", "platform", "is_active", "last_used_at"])

        return Response(
            DeviceSerializer(device).data,   # ✅ public_id 포함되어 내려감
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="unregister")
    def unregister(self, request):
        """
        body:
        {
          "public_id": "uuid(optional)"
        }

        - public_id 없으면 스킵(204)
        - 있으면 해당 디바이스 비활성화(없어도 스킵)
        """
        s = DeviceUnregisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        public_id = s.validated_data.get("public_id")
        if not public_id:
            return Response(status=status.HTTP_204_NO_CONTENT)

        Device.objects.filter(
            user=request.user,
            public_id=public_id,
        ).update(is_active=False)

        return Response(status=status.HTTP_204_NO_CONTENT)









class NotificationPreferenceView(APIView):
    """
    GET  /users/me/notification-pref/   -> 내 알림 설정 조회
    PATCH /users/me/notification-pref/  -> 내 알림 설정 수정
    """
    authentication_classes = [AccessTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get_object(self, user):
        # 없으면 기본값으로 생성
        pref, _ = NotificationPreference.objects.get_or_create(user=user)
        return pref

    def get(self, request):
        pref = self.get_object(request.user)
        s = NotificationPreferenceSerializer(pref)
        return Response(s.data, status=status.HTTP_200_OK)

    def patch(self, request):
        pref = self.get_object(request.user)
        s = NotificationPreferenceSerializer(
            instance=pref,
            data=request.data,
            partial=True
        )
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data, status=status.HTTP_200_OK)





class HolidayViewSet(viewsets.ReadOnlyModelViewSet):
    authentication_classes = [AccessTokenAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = HolidaySerializer
    pagination_class = None
    queryset = Holiday.objects.all()

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        "id": ["exact", "in"],
        "locdate": ["exact", "gte", "lte"],
        "is_holiday": ["exact"],
        "date_kind": ["exact", "in"],
        "source": ["exact", "in"],
    }
    search_fields = ["date_name"]
    ordering_fields = ["locdate", "date_name", "updated_at", "created_at"]
    ordering = ["locdate", "date_name"]






class MyNoteView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        note, _ = UserNote.objects.get_or_create(user=request.user)
        return Response(UserNoteSerializer(note).data)

    def put(self, request):
        note, _ = UserNote.objects.get_or_create(user=request.user)
        serializer = UserNoteSerializer(note, data=request.data)
        serializer.is_valid(raise_exception=True)

        note.content = serializer.validated_data.get("content", note.content)
        note.version = note.version + 1
        note.save(update_fields=["content", "version", "updated_at"])

        return Response(UserNoteSerializer(note).data)

    def patch(self, request):
        note, _ = UserNote.objects.get_or_create(user=request.user)
        serializer = UserNoteSerializer(note, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        if "content" in serializer.validated_data:
            note.content = serializer.validated_data["content"]
            note.version = note.version + 1
            note.save(update_fields=["content", "version", "updated_at"])

        return Response(UserNoteSerializer(note).data)




class NoteSyncView(APIView):
    authentication_classes = [AccessTokenAuthentication]
    permission_classes = [IsAuthenticated]

    """
    POST /users/me/note/sync/

    body:
    {
      "version": 3   // optional
    }

    response:
    - 변경 있음: { "note": {...} }
    - 변경 없음: { "note": null }
    """

    def post(self, request):
        user = request.user

        client_version = request.data.get("version", None)
        try:
            client_version = int(client_version) if client_version is not None else None
        except (TypeError, ValueError):
            client_version = None

        note, _ = UserNote.objects.get_or_create(user=user)

        # 클라 버전이 없거나, 버전이 다르면 note 내려줌
        if client_version is None or note.version != client_version:
            return Response(
                {"note": UserNoteSerializer(note).data},
                status=status.HTTP_200_OK,
            )

        # 버전 같으면 보낼 거 없음
        return Response({"note": None}, status=status.HTTP_200_OK)




class SignOutView(APIView):
    authentication_classes = [AccessTokenAuthentication]
    permission_classes = [IsAuthenticated]

    # @transaction.atomic
    def post(self, request):
        s = SignOutSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        user = request.user
        refresh_token_str = (s.validated_data.get("refresh_token") or "").strip()
        public_id = s.validated_data.get("public_id")  # ✅ UUID or None

        # 1) Device 비활성화 (내 디바이스만)
        if public_id:
            Device.objects.filter(
                user=user,
                public_id=public_id,
            ).update(is_active=False)

        # 2) RefreshToken 삭제 (내 토큰만)
        if refresh_token_str:
            RefreshToken.objects.filter(
                user=user,
                token=refresh_token_str,
            ).delete()

            # ✅ 삭제 대신 revoke를 선호하면 아래로 바꿔도 됨
            # RefreshToken.objects.filter(user=user, token=refresh_token_str).update(revoked_at=timezone.now())

        # 멱등 응답
        return Response(status=status.HTTP_204_NO_CONTENT)




class WithdrawView(APIView):
    authentication_classes = [AccessTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # ✅ request.user가 로드된 DB alias 사용 (멀티 DB에서 가장 안전)
        db = getattr(request.user._state, "db", None) or "default"

        with transaction.atomic(using=db):
            withdraw_user(
                user=request.user,
                notify_fn=send_project_membership_changed_data_only,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)






















class TOTOTestViewSet(viewsets.ModelViewSet):
    serializer_class = TOTOTestSerializer
    permission_classes = [AllowAny]
    queryset = TOTOTest.objects.all()

	# filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
		'id' : ['gte','lte'],
		'name': ['exact', 'icontains'],
		'memo': ['exact', 'icontains'],
		'age' : ['gte','lte'],
	}      # 정확 일치 필터
    search_fields = ['name','memo']               # 부분 검색: ?search=키워드
    ordering_fields = ['id', 'name']       # 정렬: ?ordering=-created_at


def test(request):
    return returnJson({"message": "테스트 입니다 ^_^"})

def returnJson(result, beautify=False):
	if beautify:# or settings.DEBUG:
		content = json.dumps(result, ensure_ascii=False, cls=DjangoJSONEncoder, indent=4)
	else:
		content = json.dumps(result, ensure_ascii=False, cls=DjangoJSONEncoder)
	response = HttpResponse(content, content_type="application/json; encoding=utf-8")
	response["Access-Control-Allow-Origin"] = "*"
	# response["Access-Control-Allow-Origin"] = "http://localhost:3000"
	return response




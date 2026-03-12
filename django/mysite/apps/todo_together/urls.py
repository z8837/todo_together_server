"""
URL configuration for mysite project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    MeView, NicknameView, SocialLoginView, TokenRefreshView,
    ProjectViewSet, TodoViewSet, TOTOTestViewSet, 
    ProjectInviteViewSet, NotificationViewSet, UserSearchView,
    DeviceViewSet, NotificationPreferenceView, ProjectSyncView, 
    TODOSyncView, NotificationSyncView, HolidayViewSet, MyNoteView, NoteSyncView,
    SignOutView, WithdrawView, apple_callback

)
from django.views.generic import TemplateView

from . import views

router = DefaultRouter()

router.register("toto_test", TOTOTestViewSet, basename="totoTest")
router.register("projects", ProjectViewSet, basename="project")
router.register("todos", TodoViewSet, basename="todo")
router.register("project-invites", ProjectInviteViewSet, basename="project-invite")
router.register("notifications", NotificationViewSet, basename="notification")
router.register("devices", DeviceViewSet, basename="device")  
router.register("holidays", HolidayViewSet, basename="holiday")  
# router.register("social-login/", SocialLoginView, basename="social-login")

urlpatterns = [
    path("auth/social-login/", SocialLoginView.as_view(), name="social-login"),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path("auth/signout/", SignOutView.as_view(), name="signout"),
    path("auth/withdraw/", WithdrawView.as_view(), name="withdraw"), 
    path("auth/apple/callback", apple_callback, name="apple_callback"),

    path('users/me/', MeView.as_view(), name='me'),
    path('users/me/nickname/', NicknameView.as_view(), name='nickname'),
    path("users/me/notification-pref/", NotificationPreferenceView.as_view(), name="notification-pref"), 
    path("users/me/note/", MyNoteView.as_view(), name="note"), 
    path("users/me/note/sync/", NoteSyncView.as_view(), name="note-sync"), 

    path("users/search/", UserSearchView.as_view(), name="user-search"),

    path("projects/sync/", ProjectSyncView.as_view(), name="project-sync"),
    path("todos/sync/", TODOSyncView.as_view(), name="todo-sync"),
    path("notifications/sync/", NotificationSyncView.as_view(), name="notification-sync"),

    path(
        "privacy-policy/",
        TemplateView.as_view(template_name="legal/privacy.html"),
        name="privacy-policy",
    ),
    path(
        "marketing/",
        TemplateView.as_view(template_name="legal/marketing.html"),
        name="marketing",
    ),
    path(
        "support/",
        TemplateView.as_view(template_name="legal/support.html"),
        name="support",
    ),
    
    path("account-deletion/", views.account_deletion_page, name="account_deletion_page"),
    path("account-deletion/submit/", views.account_deletion_submit, name="account_deletion_submit"),
    path("account-deletion/confirm/<str:token>/", views.account_deletion_confirm, name="account_deletion_confirm"),



    path("", include(router.urls)),
]

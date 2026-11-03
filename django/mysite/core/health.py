"""Small, unauthenticated probes without account or infrastructure details."""

from django.db import connections
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe


@never_cache
@require_safe
def live(request):
    return JsonResponse({"status": "ok"})


@never_cache
@require_safe
def ready(request):
    try:
        for alias in connections:
            with connections[alias].cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    raise ValueError("Unexpected database probe result")
    except Exception:
        # Do not expose database aliases, addresses, or driver exceptions.
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})

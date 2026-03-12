class CleanForwardedHost:
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        xfh = request.META.get("HTTP_X_FORWARDED_HOST")
        if xfh and "," in xfh:
            request.META["HTTP_X_FORWARDED_HOST"] = xfh.split(",")[0].strip()
        return self.get_response(request)
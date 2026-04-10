"""
Common middleware for the application.
"""


class NoCacheAuthenticatedMiddleware:
    """
    Add Cache-Control: no-store to HTML responses for authenticated users.
    Prevents browsers and CDNs from caching user-specific pages (entries,
    profile, recordings, etc.), avoiding stale content when permissions
    or data change.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        content_type = response.headers.get('Content-Type', '')
        if request.user.is_authenticated and 'text/html' in content_type:
            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
        return response

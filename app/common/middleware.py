import logging
import uuid

from .logging import request_id_context

logger = logging.getLogger(__name__)


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = str(uuid.uuid4())
        token = request_id_context.set(request.request_id)
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request.request_id
            if not request.path.startswith("/static/"):
                response["Cache-Control"] = "no-store"
            logger.info(
                "request", extra={"event_code": "http_request", "status_code": response.status_code}
            )
            return response
        finally:
            request_id_context.reset(token)

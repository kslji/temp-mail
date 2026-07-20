import os
import time
import httpx
import asyncio
import logging
import traceback
from functools import wraps
from typing import Any, Callable, Dict, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("central-logger")

# Load environment configurations directly for maximum compatibility across different projects
LOGGER_SERVICE_URL = os.environ.get("LOGGER_SERVICE_URL") or os.environ.get("ATS_LOGGER_SERVICE_URL") or "http://localhost:8013"
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY") or os.environ.get("ATS_INTERNAL_API_KEY") or "ts_internal_secret_key_change_me"

def send_log_to_central(
    service_name: str,
    function_name: str,
    level: str,
    message: str,
    duration_ms: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Sends a log entry asynchronously using a background task / non-blocking fire-and-forget.
    """
    payload = {
        "serviceName": service_name,
        "functionName": function_name,
        "level": level,
        "message": message,
        "durationMs": int(duration_ms) if duration_ms is not None else None,
        "metadata": metadata
    }

    async def _send():
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.post(
                    f"{LOGGER_SERVICE_URL}/logs",
                    json=payload,
                    headers={"x-internal-key": INTERNAL_API_KEY}
                )
        except Exception as e:
            logger.warning(f"Failed to send log to central logger-service: {e}")

    try:
        # Schedule the coroutine in the running event loop if possible, otherwise run synchronously in background thread
        loop = asyncio.get_running_loop()
        loop.create_task(_send())
    except RuntimeError:
        # Fallback if no event loop is running (e.g. startup/shutdown scripts)
        import threading
        def run_in_thread():
            asyncio.run(_send())
        threading.Thread(target=run_in_thread, daemon=True).start()

def log_action(service_name: str, function_name: Optional[str] = None):
    """
    Decorator to log function execution time, inputs, outputs, and errors.
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func_name = function_name or func.__name__

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000
                send_log_to_central(
                    service_name=service_name,
                    function_name=func_name,
                    level="info",
                    message=f"Successfully executed {func_name}",
                    duration_ms=duration_ms,
                    metadata={"args_count": len(args), "kwargs_keys": list(kwargs.keys())}
                )
                return result
            except Exception as e:
                duration_ms = (time.perf_counter() - start) * 1000
                send_log_to_central(
                    service_name=service_name,
                    function_name=func_name,
                    level="error",
                    message=f"Error executing {func_name}: {str(e)}",
                    duration_ms=duration_ms,
                    metadata={"error": str(e), "args_count": len(args)}
                )
                raise e

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000
                send_log_to_central(
                    service_name=service_name,
                    function_name=func_name,
                    level="info",
                    message=f"Successfully executed {func_name}",
                    duration_ms=duration_ms,
                    metadata={"args_count": len(args), "kwargs_keys": list(kwargs.keys())}
                )
                return result
            except Exception as e:
                duration_ms = (time.perf_counter() - start) * 1000
                send_log_to_central(
                    service_name=service_name,
                    function_name=func_name,
                    level="error",
                    message=f"Error executing {func_name}: {str(e)}",
                    duration_ms=duration_ms,
                    metadata={"error": str(e), "args_count": len(args)}
                )
                raise e

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator

class CentralLoggerMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, service_name: str):
        super().__init__(app)
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in ("/health", "/", "/docs", "/openapi.json"):
            return await call_next(request)

        start_time = time.perf_counter()
        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start_time) * 1000
            
            send_log_to_central(
                service_name=self.service_name,
                function_name=f"{request.method} {request.url.path}",
                level="error" if response.status_code >= 400 else "info",
                message=f"{request.method} {request.url.path} responded with status {response.status_code}",
                duration_ms=duration_ms,
                metadata={
                    "method": request.method,
                    "url": str(request.url),
                    "statusCode": response.status_code,
                    "client_host": request.client.host if request.client else None
                }
            )
            return response
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            stack_trace = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            
            send_log_to_central(
                service_name=self.service_name,
                function_name=f"{request.method} {request.url.path}",
                level="error",
                message=f"Unhandled error in {request.method} {request.url.path}: {str(e)}",
                duration_ms=duration_ms,
                metadata={
                    "method": request.method,
                    "url": str(request.url),
                    "client_host": request.client.host if request.client else None,
                    "error": str(e),
                    "stack": stack_trace
                }
            )
            raise e

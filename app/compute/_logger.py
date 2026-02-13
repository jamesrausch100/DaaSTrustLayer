"""Resilient logger — uses structlog if available, stdlib logging fallback."""
try:
    import structlog
    logger = structlog.get_logger()
except ImportError:
    import logging as _logging
    _log = _logging.getLogger("m2a.compute")

    class _FallbackLogger:
        def _fmt(self, msg, kw):
            extra = " ".join(f"{k}={v}" for k, v in kw.items())
            return f"{msg} {extra}" if extra else msg

        def info(self, msg, **kw):
            _log.info(self._fmt(msg, kw))

        def warning(self, msg, **kw):
            _log.warning(self._fmt(msg, kw))

        def error(self, msg, **kw):
            _log.error(self._fmt(msg, kw))

        def debug(self, msg, **kw):
            _log.debug(self._fmt(msg, kw))

    logger = _FallbackLogger()

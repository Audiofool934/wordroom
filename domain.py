"""Shared error type and input checks, independent of the web server."""


class AppError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def text(value, label, limit=1600, optional=False):
    if not isinstance(value, str) or len(value) > limit or (not optional and not value.strip()):
        raise AppError(f"{label} must be text of at most {limit} characters.")
    return value.strip()

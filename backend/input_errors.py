"""Structured locations accompany the existing human-readable validation text."""


class InputError(ValueError):
    def __init__(self, message, location=None, issues=None):
        super().__init__(message)
        self.issues = issues or [dict(location or {}, message=message)]


def error_response(error):
    return {'ok': False, 'error': str(error), 'issues': getattr(error, 'issues', [])}

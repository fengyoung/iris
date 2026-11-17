"""Exceptions shared by the standalone application."""


class InterviewError(RuntimeError):
    """An actionable application error."""


class ValidationError(InterviewError, ValueError):
    """Invalid input or untrusted model output."""

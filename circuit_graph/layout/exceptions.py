"""Layout-specific exceptions."""


class LayoutError(Exception):
    """Base class for all layout errors."""


class LayoutConfigError(LayoutError):
    """Invalid or missing layout JSON configuration."""


class LayoutPageOverflowError(LayoutError):
    """Components do not fit on the configured page."""


class LayoutOverlapError(LayoutError):
    """Could not resolve label overlap."""

class MapsProviderError(Exception):
    pass


class MapsConfigurationError(MapsProviderError):
    pass


class MapsUpstreamError(MapsProviderError):
    pass


class MapsRetryableError(MapsUpstreamError):
    """A transient transport or upstream status failure safe to retry."""

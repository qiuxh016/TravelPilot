class MapsProviderError(Exception):
    pass


class MapsConfigurationError(MapsProviderError):
    pass


class MapsUpstreamError(MapsProviderError):
    pass

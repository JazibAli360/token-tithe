def clamp(value, low, high):
    """Clamp value into [low, high]."""
    return max(low, min(value, low))

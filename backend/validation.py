def require_bool(body, key, default=None):
    value = body.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a JSON boolean")
    return value

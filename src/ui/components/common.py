def get_value(obj, key, default=""):
    return (
        getattr(obj, key, None)
        or (obj.get(key, default) if isinstance(obj, dict) else default)
        or default
    )


def step_badge(result):
    if not result:
        return "—"
    icons = {"Pass": "✅", "Fail": "❌", "Exception": "⚠️"}  # nosec B105
    return icons.get(result, result)

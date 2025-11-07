def is_payable(event, config):
    data = event.data
    severity = data.get("severity", "").lower()
    tags = data.get("tags", [])
    return (
        severity in ["high", "critical"] and
        any(tag in config["payable_tags"] for tag in tags)
    )
def classify_stock(actual_stock, forecast):
    """
    Returns risk level based on demand vs supply
    """

    if actual_stock <= 0:
        return "CRITICAL"

    ratio = forecast / actual_stock if actual_stock > 0 else 999

    if ratio >= 1.2:
        return "CRITICAL"
    elif ratio >= 0.8:
        return "LOW"
    else:
        return "NORMAL"


def recommend_supply(forecast, current_stock):
    """
    Suggest how much to distribute
    """

    buffer = forecast * 1.2  # safety stock
    recommendation = max(int(buffer - current_stock), 0)

    return recommendation
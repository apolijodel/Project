import pandas as pd
import numpy as np
from statsmodels.tsa.arima.model import ARIMA


def forecast_medicine_usage(data, steps=7):
    """
    THESIS-LEVEL ARIMA FORECASTING (NO SARIMA)
    - stable preprocessing
    - trend correction
    - missing date handling
    - safe fallback forecasting
    """

    # ---------------- VALIDATION ----------------
    if not data or len(data) < 8:
        return [0] * steps

    # ---------------- LOAD DATA ----------------
    df = pd.DataFrame(data, columns=["date", "qty"])

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")

    df = df.dropna()

    if df.empty:
        return [0] * steps

    # ---------------- AGGREGATE BY DATE ----------------
    df = df.groupby("date")["qty"].sum().sort_index()

    # ---------------- FIX: MAKE TIME SERIES CONTINUOUS ----------------
    df = df.asfreq("D")

    # fill missing values (important for ARIMA stability)
    df = df.ffill().fillna(0)

    series = df

    # ---------------- OPTIONAL: LIGHT SMOOTHING ----------------
    series = series.rolling(window=2, min_periods=1).mean()

    # ---------------- ARIMA MODEL ----------------
    try:
        model = ARIMA(series, order=(2, 1, 2))
        model_fit = model.fit()

        forecast = model_fit.forecast(steps=steps)

        # ensure no negative or NaN values
        forecast = forecast.replace([np.inf, -np.inf], 0).fillna(0)
        forecast = np.maximum(forecast, 0)

        return forecast.tolist()

    except Exception as e:
        print("ARIMA ERROR:", e)

        # ---------------- FALLBACK TREND MODEL ----------------
        diff = series.diff().mean()
        last = series.iloc[-1]

        return [max(last + diff * i, 0) for i in range(1, steps + 1)]
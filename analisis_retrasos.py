"""Análisis de retrasos: ¿qué parte del mercado explica el precio de la gasolina 95 en Alicante?

Con el histórico del Ministerio (data/historico_ministerio.csv, lo crea backtest.py) y con
precios de mercado de Yahoo Finance (gasolina NY, Brent, gasóleo NY, EUR/USD) mide:
  1. Con cuántos días de retraso y con qué fuerza cada referencia se refleja en el surtidor.
  2. Si un modelo calibrado con esos datos PREDICE de verdad el cambio de precio de los
     próximos 7 días, probándolo siempre con datos que no vio al ajustarse (walk-forward).
  3. Cuánto dinero habría ahorrado un conductor siguiéndolo, frente a repostar hoy y frente
     al modelo actual del bot.
Guarda data/analisis_retrasos.md y data/modelo.json (el modelo ajustado, por si compensa usarlo).
"""
import html
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import backtest as bt
import gasolina_bot as gb

HIST = Path("data/historico_ministerio.csv")
INFORME = Path("data/analisis_retrasos.md")
MODELO = Path("data/modelo.json")
H = 7                                   # horizonte: cambio de precio de los próximos 7 días
BUCKETS = [(0, 3), (3, 7), (7, 14), (14, 21), (21, 35)]   # tramos de días pasados del mercado
VARS = {"gasolina": "gasolina NY", "brent": "Brent", "gasoil": "gasóleo NY"}
ALFAS = [10, 100, 1000]
UMBRAL = 0.3                            # cts/L: espera si se prevé bajada mayor, compra si se prevé subida


# ---------- Datos ----------
def mercado_ext():
    import yfinance as yf
    d = yf.download(["RB=F", "BZ=F", "HO=F", "EURUSD=X"], period="2y", interval="1d",
                    auto_adjust=True, progress=False)["Close"].ffill().dropna()
    return pd.DataFrame({
        "gasolina": d["RB=F"] / 3.78541 / d["EURUSD=X"],     # USD/galón -> EUR/litro
        "brent": d["BZ=F"] / 158.987 / d["EURUSD=X"],        # USD/barril -> EUR/litro
        "gasoil": d["HO=F"] / 3.78541 / d["EURUSD=X"],
    }, index=d.index)


def preparar(hist, mkt):
    h = hist.copy()
    h["fecha"] = pd.to_datetime(h["fecha"])
    for c in ["media", "n"]:
        h[c] = pd.to_numeric(h[c], errors="coerce")
    h = h.dropna(subset=["media"]).set_index("fecha").sort_index()
    if len(h) < 200:
        raise RuntimeError(f"Solo hay {len(h)} días de histórico; hacen falta al menos 200 (lanza antes el backtest)")
    cal = pd.date_range(h.index.min(), h.index.max(), freq="D")
    P = h["media"].reindex(cal).interpolate(limit=3)
    idx = pd.DatetimeIndex(mkt.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    mkt = mkt.copy()
    mkt.index = idx.normalize()
    mkt = mkt[~mkt.index.duplicated()].sort_index()
    if mkt.index.min() > cal[0] - pd.Timedelta(days=40):
        raise RuntimeError(f"El mercado empieza el {mkt.index.min().date()}: no cubre el histórico de precios")
    return P, mkt


# ---------- 1. Retraso y fuerza de cada referencia ----------
def retrasos(P, mkt):
    out = {}
    cal = P.index
    ext = pd.date_range(cal[0] - pd.Timedelta(days=60), cal[-1])
    dP = (P - P.shift(H)) * 100
    for v in VARS:
        X = mkt[v].reindex(ext, method="ffill")
        res = []
        for L in range(0, 29):
            dX = ((X.shift(L) - X.shift(L + H)) * 100).reindex(cal)
            m = dP.notna() & dX.notna()
            if m.sum() > 60 and dX[m].std() > 0:
                res.append((L, float(np.corrcoef(dP[m], dX[m])[0, 1]),
                            float(np.cov(dP[m], dX[m])[0, 1] / dX[m].var())))
        out[v] = max(res, key=lambda t: t[1]) if res else (np.nan, np.nan, np.nan)
    return out


# ---------- 2. Variables del modelo ----------
def construir(P, mkt):
    cal = P.index
    ext = pd.date_range(cal[0] - pd.Timedelta(days=60), cal[-1])
    Pe = P.reindex(ext)
    F = pd.DataFrame(index=ext)
    for v in VARS:
        X = mkt[v].reindex(ext, method="ffill").shift(1)   # a primera hora solo se conoce el cierre de ayer
        for a, b in BUCKETS:
            F[f"{v}_{a}_{b}"] = (X.shift(a) - X.shift(b)) * 100
        if v in ("gasolina", "brent"):
            margen = Pe - gb.IVA * X.rolling(14).mean()
            F[f"margen_{v}"] = (margen - margen.rolling(90, min_periods=30).mean()) * 100
    F["surt_0_3"] = (Pe - Pe.shift(3)) * 100
    F["surt_3_7"] = (Pe.shift(3) - Pe.shift(7)) * 100
    F["surt_7_14"] = (Pe.shift(7) - Pe.shift(14)) * 100
    y = (Pe.shift(-H) - Pe) * 100
    return F, y, Pe


def ajustar(X, y, alfa):
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1
    Z = (X - mu) / sd
    ym = y.mean()
    w = np.linalg.solve(Z.T @ Z + alfa * np.eye(Z.shape[1]), Z.T @ (y - ym))
    return mu, sd, w, ym


def predecir(mod, X):
    mu, sd, w, ym = mod
    return ((X - mu) / sd) @ w + ym


def walk_forward(F, y, cols, alfa, inicio):
    """Cada 7 días reajusta con lo que ya se sabía (objetivos ya cumplidos) y predice los 7 siguientes."""
    valid = F[cols].notna().all(axis=1) & y.notna()
    preds = pd.Series(np.nan, index=F.index)
    T = inicio
    while T <= F.index[-1]:
        ent = valid & (F.index <= T - pd.Timedelta(days=H))
        if ent.sum() >= 100:
            mod = ajustar(F.loc[ent, cols].values, y[ent].values, alfa)
            sl = (F.index >= T) & (F.index < T + pd.Timedelta(days=7)) & F[cols].notna().all(axis=1)
            if sl.any():
                preds[sl] = predecir(mod, F.loc[sl, cols].values)
        T += pd.Timedelta(days=7)
    return preds


def evaluar(preds, y):
    m = preds.notna() & y.notna()
    e0, e1 = y[m] ** 2, (y[m] - preds[m]) ** 2
    r2 = 1 - e1.sum() / e0.sum()
    corr = float(np.corrcoef(preds[m], y[m])[0, 1]) if preds[m].std() > 0 else np.nan
    lo, hi = bt.ic95((e0 - e1).values)
    grande = m & (y.abs() >= 0.2)
    dirac = float((np.sign(preds[grande]) == np.sign(y[grande])).mean()) if grande.sum() else np.nan
    return {"r2": float(r2), "corr": corr, "n": int(m.sum()), "dir": dirac,
            "mejora_ic": (float(lo), float(hi)), "m": m}


# ---------- Análisis completo ----------
def analizar(hist, mkt):
    P, mkt = preparar(hist, mkt)
    ret = retrasos(P, mkt)
    F, y, Pe = construir(P, mkt)
    todas = list(F.columns)
    grupos = {
        "A) solo precios del surtidor": [c for c in todas if c.startswith("surt")],
        "B) solo mercado": [c for c in todas if not c.startswith("surt")],
        "C) mercado + surtidor": todas,
    }
    base = F[todas].notna().all(axis=1) & y.notna()
    inicio = base[base].index.min() + pd.Timedelta(days=150)
    if inicio >= F.index[-1] - pd.Timedelta(days=30):
        raise RuntimeError("Histórico demasiado corto para evaluar fuera de muestra")

    resultados = {}
    for nombre, cols in grupos.items():
        mejor = None
        for alfa in ALFAS:
            preds = walk_forward(F, y, cols, alfa, inicio)
            ev = evaluar(preds, y)
            if mejor is None or ev["r2"] > mejor[1]["r2"]:
                mejor = (alfa, ev, preds)
        resultados[nombre] = mejor

    # Mejor variante -> simulación del conductor (periodo fuera de muestra) y modelo actual del bot
    nombre_b = max(resultados, key=lambda k: resultados[k][1]["r2"])
    alfa_b, ev_b, preds_b = resultados[nombre_b]
    oos = preds_b.notna() & Pe.notna()
    ini_o = oos[oos].index.min()
    fin_o = Pe.dropna().index.max()
    rng = pd.date_range(ini_o, fin_o)
    Po = Pe.reindex(rng).interpolate(limit=3).values
    sig_m = np.zeros(len(rng), dtype=int)
    pv = preds_b.reindex(rng).fillna(0).values
    sig_m[pv >= UMBRAL] = 1
    sig_m[pv <= -UMBRAL] = -1
    ah_m, az_m, opt, esp_m = bt.simular(Po, sig_m)
    r_old = mkt["gasolina"]
    e_old = bt.exp_cts(r_old, 3, 4).reindex(rng, method="ffill").fillna(0).values
    ah_o, _, _, esp_o = bt.simular(Po, bt.a_senal(e_old, gb.UMBRAL_SUBE, gb.UMBRAL_BAJA))
    vm, vo = bt.veredicto(ah_m), bt.veredicto(ah_o)

    # Modelo final con todos los datos (por si compensa usarlo)
    cols_b = grupos[nombre_b]
    ent = F[cols_b].notna().all(axis=1) & y.notna()
    mu, sd, w, ym = ajustar(F.loc[ent, cols_b].values, y[ent].values, alfa_b)
    MODELO.parent.mkdir(exist_ok=True)
    MODELO.write_text(json.dumps({
        "variante": nombre_b, "alfa": alfa_b, "columnas": cols_b, "media": mu.tolist(),
        "desv": sd.tolist(), "coef": w.tolist(), "intercepto": float(ym), "umbral_cts": UMBRAL,
        "entrenado_hasta": str(F.index[ent][-1].date()), "r2_fuera_de_muestra": ev_b["r2"],
        "util": bool(ev_b["r2"] > 0.05 and vm[1] > 0)}, indent=1), encoding="utf-8")

    L = ["# Análisis de retrasos · gasolina 95 · provincia de Alicante\n",
         f"Datos: {len(P.dropna())} días de precios ({P.index[0].date()} a {P.index[-1].date()}). "
         f"Evaluación fuera de muestra: {ini_o.date()} a {fin_o.date()} ({ev_b['n']} días).\n",
         "## 1. Cuánto y con qué retraso se refleja cada referencia (cambios de 7 días)"]
    for v, (lag, corr, pend) in ret.items():
        L.append(f"- {VARS[v]}: mayor relación con {lag:.0f} días de retraso (correlación {corr:.2f}; "
                 f"cuando sube 1 cts/L en el mercado, el surtidor se mueve {pend:+.2f} cts/L)")
    L.append("\n## 2. ¿Predice el cambio de los próximos 7 días? (probado con datos no vistos al ajustar)")
    L.append("R² frente a suponer 'no cambia' (0 = no mejora nada; negativo = empeora):")
    for nombre, (alfa, ev, _) in resultados.items():
        lo, hi = ev["mejora_ic"]
        sig = "mejora real" if lo > 0 else "no distinguible de cero"
        L.append(f"- {nombre}: R² {ev['r2']:+.3f}, correlación {ev['corr']:+.2f}, "
                 f"acierto de dirección {ev['dir']:.0%} ({sig})")
    L.append(f"\nMejor variante: **{nombre_b}**.\n")
    L.append("## 3. Dinero que habría ahorrado un conductor (repostar en los próximos 7 días)")
    L.append(f"- Modelo nuevo ({nombre_b}, umbral ±{UMBRAL} cts): {vm[0]:+.2f} cts/L frente a repostar hoy "
             f"(IC95 {vm[1]:+.2f} a {vm[2]:+.2f}) → {vm[3]}; espera el {esp_m:.0%} de los días")
    L.append(f"- Modelo actual del bot (mismo periodo): {vo[0]:+.2f} cts/L (IC95 {vo[1]:+.2f} a {vo[2]:+.2f}) "
             f"→ {vo[3]}; espera el {esp_o:.0%} de los días")
    L.append(f"- Techo teórico (mejor día de cada semana): {opt.mean():+.2f} cts/L "
             f"(≈ {opt.mean() * gb.LITROS / 100:.2f} € por depósito de {gb.LITROS} L)\n")
    util = ev_b["r2"] > 0.05 and vm[1] > 0
    L.append("## Conclusión")
    if util:
        L.append("El modelo nuevo predice mejor que 'no cambia' y su ahorro es medible. Compensa sustituir el "
                 "cálculo del bot por este (data/modelo.json).")
    elif ev_b["r2"] > 0.02:
        L.append("Hay algo de señal, pero el ahorro no se distingue de cero. Se puede usar como orientación, "
                 "sin prometer céntimos concretos.")
    else:
        L.append("El mercado no permite predecir el precio del surtidor a 7 días con estos datos. Lo honesto es "
                 "convertir el bot en una alerta simple de movimientos fuertes y dejar de dar cifras.")
    texto = "\n".join(L)

    d_old = f"{ret['gasolina'][0]:.0f}" if not np.isnan(ret["gasolina"][0]) else "?"
    resumen = html.escape(
        "🔬 Análisis de retrasos (gasolina 95, Alicante)\n\n"
        + "\n".join(f"{n[:2]} R² {e['r2']:+.3f}" for n, (_, e, _) in resultados.items())
        + f"\n\nMejor: {nombre_b}. Predicción 7 días: correlación {ev_b['corr']:+.2f}, "
          f"dirección acertada {ev_b['dir']:.0%}.\n"
          f"Ahorro del conductor: {vm[0]:+.2f} cts/L vs repostar hoy "
          f"(IC95 {vm[1]:+.2f} a {vm[2]:+.2f}) → {vm[3]}.\n"
          f"Modelo actual del bot: {vo[0]:+.2f} cts/L.\n"
          f"Retraso típico gasolina NY: {d_old} días.\n\n"
        + L[-1] + "\nInforme completo: data/analisis_retrasos.md")
    return texto, resumen


def main():
    try:
        hist = pd.read_csv(HIST)
        texto, resumen = analizar(hist, mercado_ext())
        INFORME.write_text(texto, encoding="utf-8")
        print(texto)
        gb.enviar(resumen)
    except Exception as e:
        try:
            gb.enviar(f"⚠️ El análisis de retrasos falló: {html.escape(str(e))}")
        finally:
            raise


if __name__ == "__main__":
    main()

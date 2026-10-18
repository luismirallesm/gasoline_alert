"""Backtest del bot de gasolina 95 (provincia de Alicante).

1. Baja del Ministerio el precio medio diario de los últimos DIAS días (y lo guarda en
   data/historico_ministerio.csv; las siguientes veces solo baja lo que falta).
2. Reproduce lo que habría dicho el bot cada día usando SOLO datos de mercado anteriores
   (no hay histórico de noticias gratuito más allá de ~3 meses, así que aquí no entran).
3. Simula a un conductor que necesita repostar en los próximos 7 días y compara:
   repostar hoy  vs  seguir al bot  vs  un día cualquiera  vs  el mejor día posible.
4. Prueba varios ajustes (umbrales, ventanas) con el 65 % más antiguo de los datos y los
   evalúa en el 35 % más reciente, para no engañarnos con ajustes que solo valen "de memoria".
"""
import html
import itertools
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import gasolina_bot as gb

HIST = Path("data/historico_ministerio.csv")
INFORME = Path("data/backtest.md")
DIAS = int(os.environ.get("DIAS", "365"))
VENTANA = 7                      # días que el conductor puede esperar como máximo
URL = ("https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/"
       "EstacionesTerrestresHist/FiltroProvincia/{fecha}/{prov}")
FORMATOS = ["%d-%m-%Y", "%Y-%m-%d"]   # el Ministerio no documenta el formato: probamos ambos
COLS = ["fecha", "media", "mediana", "minimo", "n"]


# ---------- 1. Histórico del Ministerio ----------
def valores_dia(d, fmt):
    r = gb.descargar(URL.format(fecha=d.strftime(fmt), prov=gb.PROVINCIA), intentos=3)
    est = json.loads(r.content.decode("utf-8-sig")).get("ListaEESSPrecio") or []
    vals = []
    for e in est:
        txt = e.get("Precio Gasolina 95 E5") or e.get("Precio_x0020_Gasolina_x0020_95_x0020_E5") or ""
        try:
            vals.append(float(txt.replace(",", ".")))
        except ValueError:
            pass
    return vals


def detectar_formato(d):
    errores = []
    for fmt in FORMATOS:
        try:
            if len(valores_dia(d, fmt)) >= 50:
                print("Formato de fecha del Ministerio:", fmt)
                return fmt
            errores.append(f"{fmt}: sin datos")
        except Exception as e:
            errores.append(f"{fmt}: {e}")
    raise RuntimeError("El Ministerio no devolvió histórico con ningún formato de fecha (" +
                       "; ".join(errores) + ")")


def descargar_historico(hoy):
    df = pd.read_csv(HIST) if HIST.exists() else pd.DataFrame(columns=COLS)
    hechas = set(df["fecha"])
    pendientes = [hoy - timedelta(days=k) for k in range(DIAS, 0, -1)
                  if (hoy - timedelta(days=k)).isoformat() not in hechas]
    print(f"Histórico: {len(hechas)} días ya guardados, {len(pendientes)} por descargar")
    if not pendientes:
        return df
    fmt = detectar_formato(pendientes[-1])
    nuevas, fallos = [], 0

    def volcar():
        out = pd.concat([df, pd.DataFrame(nuevas)], ignore_index=True)
        out = out.drop_duplicates("fecha").sort_values("fecha")
        HIST.parent.mkdir(exist_ok=True)
        out[COLS].to_csv(HIST, index=False)
        return out

    for k, d in enumerate(pendientes):
        try:
            vals = valores_dia(d, fmt)
            fallos = 0
        except Exception as e:
            fallos += 1
            print(f"{d}: fallo ({e})")
            if fallos >= 10:
                print("Demasiados fallos seguidos: sigo con lo descargado hasta ahora.")
                break
            continue
        if vals:
            sr = pd.Series(vals)
            nuevas.append({"fecha": d.isoformat(), "media": round(sr.mean(), 4),
                           "mediana": round(sr.median(), 4), "minimo": sr.min(), "n": len(sr)})
        else:
            nuevas.append({"fecha": d.isoformat(), "media": np.nan, "mediana": np.nan,
                           "minimo": np.nan, "n": 0})
        if k % 25 == 24:
            volcar()
            print(f"  ...{k + 1}/{len(pendientes)}")
        time.sleep(1)
    return volcar()


# ---------- 2. Señales del bot, reproducidas día a día ----------
def exp_cts(r, n_nuevo, lag):
    """Misma fórmula que el bot en vivo, en vectorial. shift(1): a primera hora solo se conoce
    el cierre del día anterior, así que no hay información del futuro."""
    nuevo = r.rolling(n_nuevo).mean()
    reflejado = r.shift(lag).rolling(7).mean()
    return ((nuevo - reflejado) * gb.IVA * 100 * gb.PASO).shift(1)


def a_senal(exp, sube, baja):
    s = np.zeros(len(exp), dtype=int)
    s[exp >= sube] = 1       # REPOSTA
    s[exp <= baja] = -1      # ESPERA
    return s


# ---------- 3. Conductor simulado ----------
def simular(P, sig, W=VENTANA):
    """Para cada día de inicio, un conductor que necesita repostar en los próximos W días:
    compra el primer día en que el bot no dice ESPERA (o el último día si siempre dice ESPERA)."""
    n = len(P) - W
    ah_hoy, ah_azar, optimo = np.empty(n), np.empty(n), np.empty(n)
    esperas = 0
    for i in range(n):
        d = W - 1
        for k in range(W):
            if sig[i + k] != -1:
                d = k
                break
        compra = P[i + d]
        ah_hoy[i] = (P[i] - compra) * 100
        ah_azar[i] = (P[i:i + W].mean() - compra) * 100
        optimo[i] = (P[i] - P[i:i + W].min()) * 100
        esperas += d > 0
    return ah_hoy, ah_azar, optimo, esperas / n


def ic95(x, bloque=7, B=2000, seed=0):
    """Intervalo de confianza del 95 % de la media (bootstrap por bloques: días seguidos se parecen)."""
    x = np.asarray(x, dtype=float)
    if len(x) < 2 * bloque:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    k = int(np.ceil(len(x) / bloque))
    medias = [np.concatenate([x[s:s + bloque] for s in rng.integers(0, len(x) - bloque + 1, k)])
              [:len(x)].mean() for _ in range(B)]
    return tuple(np.percentile(medias, [2.5, 97.5]))


def veredicto(x):
    lo, hi = ic95(x)
    m = float(np.mean(x))
    if np.isnan(lo):
        return m, lo, hi, "pocos datos"
    if lo > 0:
        return m, lo, hi, "MEJORA a repostar hoy"
    if hi < 0:
        return m, lo, hi, "EMPEORA respecto a repostar hoy"
    return m, lo, hi, "no concluyente (no se distingue de repostar hoy)"


# ---------- 4. Análisis completo ----------
def analizar(hist, mkt):
    h = hist.copy()
    h["fecha"] = pd.to_datetime(h["fecha"])
    for c in ["media", "mediana", "minimo", "n"]:
        h[c] = pd.to_numeric(h[c], errors="coerce")
    h = h.dropna(subset=["media"]).set_index("fecha").sort_index()
    cobertura = len(h)
    if cobertura < 60:
        raise RuntimeError(f"Solo hay {cobertura} días de histórico: no basta para un backtest fiable")
    cal = pd.date_range(h.index.min(), h.index.max(), freq="D")
    P = h["media"].reindex(cal).interpolate(limit=3).dropna()
    cal = P.index
    r = mkt["rbob_eur_l"]
    Pa = P.values
    n = len(Pa) - VENTANA
    corte = int(0.65 * n)

    def senal_para(n_nuevo, lag, sube, baja):
        e = exp_cts(r, n_nuevo, lag).reindex(cal, method="ffill").fillna(0.0).values
        return a_senal(e, sube, baja), e

    # Ajuste actual del bot
    sig0, e0 = senal_para(3, 4, gb.UMBRAL_SUBE, gb.UMBRAL_BAJA)
    a_hoy, a_azar, opt, p_espera = simular(Pa, sig0)
    # Calibración: ¿el precio de surtidor se mueve lo que el modelo predice?
    fut = np.array([(Pa[i + 7] - Pa[i]) * 100 if i + 7 < len(Pa) else np.nan for i in range(len(Pa))])
    ok = ~np.isnan(fut) & (np.arange(len(Pa)) >= 40)
    corr = float(np.corrcoef(e0[ok], fut[ok])[0, 1]) if ok.sum() > 10 and np.std(e0[ok]) > 0 else np.nan
    pend = float(np.cov(e0[ok], fut[ok])[0, 1] / np.var(e0[ok])) if ok.sum() > 10 and np.var(e0[ok]) > 0 else np.nan
    # Acierto direccional de los avisos con cambio real relevante
    aciertos = tot = 0
    for i in range(len(Pa) - 7):
        if sig0[i] != 0 and abs(Pa[i + 7] - Pa[i]) >= 0.002:
            tot += 1
            aciertos += (Pa[i + 7] > Pa[i]) == (sig0[i] == 1)

    m_all = veredicto(a_hoy)
    m_test = veredicto(a_hoy[corte:])

    # Búsqueda de ajustes: se elige con el tramo antiguo y se evalúa en el reciente
    mejor, mejor_val = None, -1e9
    for n_nuevo, lag, sube, baja in itertools.product([2, 3, 5], [3, 4, 6],
                                                      [0.3, 0.5, 0.7, 1.0, 1.5],
                                                      [-0.5, -1.0, -1.5, -2.0, -3.0]):
        sg, _ = senal_para(n_nuevo, lag, sube, baja)
        ah, _, _, _ = simular(Pa, sg)
        v = ah[:corte].mean()
        if v > mejor_val:
            mejor_val, mejor = v, (n_nuevo, lag, sube, baja, sg, ah)
    n_nuevo, lag, sube, baja, sg_b, ah_b = mejor
    mb_test = veredicto(ah_b[corte:])

    L = []
    L.append("# Backtest del bot de gasolina 95 · provincia de Alicante\n")
    L.append(f"Datos: {cobertura} días de precios del Ministerio ({cal[0].date()} a {cal[-1].date()}), "
             f"{int(h['n'].min())}-{int(h['n'].max())} gasolineras por día.")
    L.append("Sin noticias (no hay histórico gratuito largo): se prueba solo el modelo de mercado.")
    L.append(f"Conductor simulado: necesita repostar en los próximos {VENTANA} días. "
             f"Ahorro en céntimos por litro (≈ ×{gb.LITROS / 100:.2f} € por depósito de {gb.LITROS} L).\n")
    L.append("## Ajuste actual del bot (umbrales +0,7 / -1,0)")
    L.append(f"- Todo el periodo: {m_all[0]:+.2f} cts/L frente a repostar hoy "
             f"(IC95 {m_all[1]:+.2f} a {m_all[2]:+.2f}) → {m_all[3]}")
    L.append(f"- Tramo reciente (35 %): {m_test[0]:+.2f} cts/L (IC95 {m_test[1]:+.2f} a {m_test[2]:+.2f}) → {m_test[3]}")
    L.append(f"- Frente a repostar un día cualquiera de la semana: {a_azar.mean():+.2f} cts/L")
    L.append(f"- Techo teórico (el mejor día de cada semana, imposible de saber): {opt.mean():+.2f} cts/L")
    L.append(f"- Días en que el bot hace esperar: {p_espera:.0%}")
    if tot:
        L.append(f"- Acierto direccional de avisos (precio 7 días después): {aciertos / tot:.0%} de {tot} avisos")
    L.append(f"- Calibración: correlación {corr:.2f}; pendiente {pend:.2f} "
             "(1 = el surtidor se mueve justo lo que predice el modelo; 0 = no hay relación)\n")
    L.append("## Mejor ajuste encontrado (elegido con el tramo antiguo)")
    L.append(f"- Ventana reciente {n_nuevo} días, retraso {lag} días, umbral REPOSTA {sube:+.1f}, ESPERA {baja:+.1f}")
    L.append(f"- En el tramo antiguo (donde se eligió): {mejor_val:+.2f} cts/L")
    L.append(f"- En el tramo reciente (no visto al elegir): {mb_test[0]:+.2f} cts/L "
             f"(IC95 {mb_test[1]:+.2f} a {mb_test[2]:+.2f}) → {mb_test[3]}\n")
    if m_test[3].startswith("MEJORA"):
        concl = "El modelo actual aporta ahorro medible frente a repostar hoy."
    elif m_test[3].startswith("EMPEORA"):
        concl = "El modelo actual cuesta dinero frente a repostar hoy: no conviene seguirlo tal cual."
    else:
        concl = "No se puede demostrar que el modelo actual mejore a repostar hoy con estos datos."
    L.append("## Conclusión\n" + concl)
    texto = "\n".join(L)

    resumen = html.escape(
        "📊 Backtest gasolina 95 (Alicante)\n"
        f"{cobertura} días de datos, conductor que reposta en los próximos {VENTANA} días.\n\n"
        f"Ajuste actual: {m_test[0]:+.2f} cts/L vs repostar hoy en el tramo reciente "
        f"(IC95 {m_test[1]:+.2f} a {m_test[2]:+.2f}) → {m_test[3]}.\n"
        f"Todo el periodo: {m_all[0]:+.2f} cts/L. Techo teórico: {opt.mean():+.2f} cts/L.\n"
        f"Calibración (pendiente): {pend:.2f}. Hace esperar el {p_espera:.0%} de los días.\n"
        f"Mejor ajuste (fuera de muestra): {mb_test[0]:+.2f} cts/L → {mb_test[3]}.\n\n" + concl +
        "\nInforme completo: data/backtest.md en el repositorio.")
    return texto, resumen


def main():
    try:
        hist = descargar_historico(date.today())
        mkt = gb.mercado("3y")
        texto, resumen = analizar(hist, mkt)
        INFORME.parent.mkdir(exist_ok=True)
        INFORME.write_text(texto, encoding="utf-8")
        print(texto)
        gb.enviar(resumen)
    except Exception as e:
        try:
            gb.enviar(f"⚠️ El backtest falló: {html.escape(str(e))}")
        finally:
            raise


if __name__ == "__main__":
    main()

"""Bot de Telegram: ¿repostar gasolina 95 hoy o esperar? (provincia de Alicante)

Cada día:
  1. Baja los precios reales de todas las gasolineras de la provincia (API del Ministerio).
  2. Mide la gasolina mayorista (RBOB), el Brent y el EUR/USD (Yahoo Finance).
  3. Mide el tono de las noticias geopolíticas sobre petróleo (GDELT, gratis).
  4. Decide REPOSTA / ESPERA / NEUTRO y lo manda por Telegram.
  5. Guarda el dato del día y, con el tiempo, mide su propio acierto real.

Idea clave: el precio del surtidor tarda 1-2 semanas en copiar al mayorista. Si el
mayorista de los últimos días ya se ha movido y el surtidor aún no, se puede anticipar.
"""
import html
import json
import os
import time
from pathlib import Path

import pandas as pd
import requests

# ---------- Ajustes ----------
PROVINCIA = "03"  # Alicante
URL_MINETUR = (
    "https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/"
    f"PreciosCarburantes/EstacionesTerrestres/FiltroProvincia/{PROVINCIA}"
)
CSV = Path("data/precios_alicante.csv")
LITROS = 40          # tamaño de un repostaje típico
IVA = 1.21
PASO = 0.8           # fracción del cambio mayorista que acaba llegando al surtidor
UMBRAL_SUBE = 0.7    # céntimos/L: por encima -> REPOSTA
UMBRAL_BAJA = -1.0   # céntimos/L: por debajo -> ESPERA (el surtidor baja lento, exige más)
COLUMNAS = ["fecha", "media", "mediana", "minimo", "n", "senal", "exp_cts", "tono_z"]


# ---------- Datos ----------
CABECERAS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
}


def descargar(url, intentos=5):
    """GET con reintentos. La web del Ministerio a veces corta la conexión o tiene mal el certificado."""
    import urllib3
    urllib3.disable_warnings()
    ultimo = None
    for i in range(intentos):
        try:
            r = requests.get(url, headers=CABECERAS, timeout=90, verify=(i < 2))
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException as e:
            ultimo = e
            print(f"Intento {i + 1}/{intentos} fallido: {e}")
            time.sleep(5 * (i + 1))
    raise ultimo


def precios_provincia():
    r = descargar(URL_MINETUR)
    estaciones = json.loads(r.content.decode("utf-8-sig"))["ListaEESSPrecio"]
    valores = []
    for e in estaciones:
        txt = (e.get("Precio Gasolina 95 E5") or "").replace(",", ".")
        try:
            valores.append(float(txt))
        except ValueError:
            pass
    s = pd.Series(valores)
    if s.empty:
        raise RuntimeError("El Ministerio no devolvió precios de gasolina 95")
    return {"media": s.mean(), "mediana": s.median(), "minimo": s.min(), "n": len(s)}


def mercado():
    import yfinance as yf

    d = yf.download(["RB=F", "BZ=F", "EURUSD=X"], period="1y", interval="1d",
                    auto_adjust=True, progress=False)["Close"].dropna()
    d["rbob_eur_l"] = d["RB=F"] / 3.78541 / d["EURUSD=X"]   # USD/galón -> EUR/litro
    d["brent_eur"] = d["BZ=F"] / d["EURUSD=X"]
    return d


GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
CONSULTA_GDELT = ('(oil OR crude OR OPEC OR Hormuz) '
                  '(Iran OR Russia OR "Saudi Arabia" OR Israel OR Ukraine OR Libya OR Venezuela)')


def gdelt(params, intentos=3):
    """Petición a GDELT con reintentos. Si falla, imprime qué respondió realmente (para diagnosticar)."""
    for i in range(intentos):
        r = None
        try:
            r = requests.get(GDELT, params=params, headers=CABECERAS, timeout=60)
            return r.json()
        except Exception as e:
            estado = r.status_code if r is not None else None
            cuerpo = r.text[:300] if r is not None else ""
            print(f"GDELT intento {i + 1}/{intentos}: {e} | estado={estado} | respuesta={cuerpo!r}")
            time.sleep(10)  # GDELT pide ~1 petición cada 5 s
    return None


def geopolitica():
    """Devuelve (z del tono de noticias, titulares). Tono muy negativo = tensión = riesgo de subida."""
    tono_z, titulares = None, []
    js = gdelt({"query": CONSULTA_GDELT, "mode": "timelinetone", "format": "json", "timespan": "30d"})
    try:
        datos = js["timeline"][0]["data"]
        idx = pd.to_datetime([p["date"] for p in datos], format="%Y%m%dT%H%M%SZ")
        s = pd.Series([p["value"] for p in datos], index=idx).resample("D").mean().dropna()
        if len(s) >= 10 and s.std() > 0:
            tono_z = float((s.iloc[-3:].mean() - s.mean()) / s.std())
    except Exception as e:
        print("GDELT tono no utilizable:", repr(e))
    time.sleep(10)
    js = gdelt({"query": CONSULTA_GDELT, "mode": "artlist", "format": "json",
                "maxrecords": 3, "timespan": "2d", "sort": "datedesc"})
    try:
        titulares = [a["title"] for a in js.get("articles", [])][:3]
    except Exception as e:
        print("GDELT titulares no utilizables:", repr(e))
    return tono_z, titulares


# ---------- Decisión ----------
def decidir(mkt, tono_z):
    r = mkt["rbob_eur_l"]
    if len(r) < 40:
        raise RuntimeError("Pocos datos de mercado")
    nuevo = r.iloc[-3:].mean()                 # lo que cuesta ahora la gasolina mayorista
    reflejado = r.iloc[-11:-4].mean()          # lo que el surtidor ya está copiando
    gap = nuevo - reflejado
    hist = (r.rolling(3).mean() - r.shift(4).rolling(7).mean()).dropna()
    sigma = hist.std() * IVA * 100 * PASO      # lo "normal" que se mueve esto, en céntimos
    exp_cts = gap * IVA * 100 * PASO
    ajuste_geo = 0.0 if tono_z is None else max(-1.0, min(1.0, -0.4 * tono_z))
    exp_cts += ajuste_geo
    if exp_cts >= UMBRAL_SUBE:
        senal = "REPOSTA"
    elif exp_cts <= UMBRAL_BAJA:
        senal = "ESPERA"
    else:
        senal = "NEUTRO"
    fuerza = abs(exp_cts) / sigma if sigma > 0 else 0
    conf = "alta" if fuerza >= 1.5 else "media" if fuerza >= 0.75 else "baja"
    cambio_may = (nuevo / reflejado - 1) * 100
    brent_7d = (mkt["brent_eur"].iloc[-1] / mkt["brent_eur"].iloc[-6] - 1) * 100
    return {"senal": senal, "exp_cts": exp_cts, "conf": conf, "cambio_may": cambio_may,
            "brent_7d": brent_7d, "ajuste_geo": ajuste_geo}


# ---------- Histórico y acierto ----------
def cargar():
    if CSV.exists():
        return pd.read_csv(CSV)
    return pd.DataFrame(columns=COLUMNAS)


def guardar(df, fila):
    df = df[df["fecha"] != fila["fecha"]]
    df = pd.concat([df, pd.DataFrame([fila])], ignore_index=True)
    CSV.parent.mkdir(exist_ok=True)
    df[COLUMNAS].to_csv(CSV, index=False)


def acierto(df):
    """Compara cada aviso con el precio real 7-9 días después. Si el precio no cambió, no cuenta."""
    d = df.copy()
    d["fecha"] = pd.to_datetime(d["fecha"])
    d = d.set_index("fecha").sort_index()
    ok = tot = 0
    for f, row in d.iterrows():
        if row["senal"] not in ("REPOSTA", "ESPERA"):
            continue
        fut = d.loc[(d.index >= f + pd.Timedelta(days=7)) &
                    (d.index <= f + pd.Timedelta(days=9)), "media"]
        if fut.empty:
            continue
        dif = fut.iloc[0] - row["media"]
        if abs(dif) < 0.002:
            continue
        tot += 1
        ok += (dif > 0) == (row["senal"] == "REPOSTA")
    return ok, tot


# ---------- Mensaje ----------
def mensaje(p, dec, titulares, ok, tot, tendencia, aviso_precios=None):
    emoji = {"REPOSTA": "🟢", "ESPERA": "🟡", "NEUTRO": "⚪"}[dec["senal"]]
    e = dec["exp_cts"]
    euros = abs(e) / 100 * LITROS
    if dec["senal"] == "REPOSTA":
        titulo = f"REPOSTA HOY: se espera subida de ~{e:.1f} cts/L (≈{euros:.2f} € en {LITROS} L)"
    elif dec["senal"] == "ESPERA":
        titulo = f"ESPERA unos días: se espera bajada de ~{abs(e):.1f} cts/L (≈{euros:.2f} € en {LITROS} L)"
    else:
        titulo = "DA IGUAL: no se espera un cambio relevante, reposta cuando te toque"
    t = [f"⛽ <b>Gasolina 95 · Provincia de Alicante</b>",
         (f"Hoy: media {p['media']:.3f} €/L · mínimo {p['minimo']:.3f} ({p['n']} gasolineras)"
          if p else f"⚠️ No pude leer los precios del surtidor hoy ({html.escape(str(aviso_precios))}). "
                    "El aviso se basa solo en el mercado mayorista."),
         "", f"{emoji} <b>{titulo}</b>", f"Confianza: {dec['conf']}", "",
         f"Mayorista (gasolina) últimos 3 días vs. lo que ya refleja el surtidor: {dec['cambio_may']:+.1f}%",
         f"Brent en 5 días: {dec['brent_7d']:+.1f}%"]
    if tendencia is not None:
        t.append(f"Surtidor en la última semana: {tendencia:+.1f} cts/L")
    if dec["ajuste_geo"] > 0.2:
        t.append("Noticias: tensión geopolítica por encima de lo normal (sesgo al alza)")
    elif dec["ajuste_geo"] < -0.2:
        t.append("Noticias: tono más calmado de lo normal (sesgo a la baja)")
    if titulares:
        t += ["", "📰 " + "\n📰 ".join(html.escape(x) for x in titulares)]
    t.append("")
    if tot >= 10:
        t.append(f"Acierto real hasta ahora: {ok / tot:.0%} ({ok}/{tot} avisos comprobados)")
    else:
        t.append(f"Acierto real: aún pocos datos ({tot} avisos comprobados, hacen falta 10)")
    return "\n".join(t)


def enviar(texto):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print(texto)  # modo prueba: sin Telegram, solo imprime
        return
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": texto, "parse_mode": "HTML"}, timeout=30)
    r.raise_for_status()


def paso(nombre, fn):
    try:
        return fn()
    except Exception as e:
        raise RuntimeError(f"{nombre}: {e}") from e


def main():
    try:
        try:
            p, aviso = paso("Precios del Ministerio", precios_provincia), None
        except Exception as e:  # sin precios aún se puede avisar con el mercado
            p, aviso = None, e
        mkt = paso("Mercado (Yahoo Finance)", mercado)
        tono_z, titulares = geopolitica()
        dec = decidir(mkt, tono_z)
        df = cargar()
        tendencia = None
        if p:
            hoy = pd.Timestamp.today().strftime("%Y-%m-%d")
            guardar(df, {"fecha": hoy, "media": round(p["media"], 4),
                         "mediana": round(p["mediana"], 4), "minimo": p["minimo"], "n": p["n"],
                         "senal": dec["senal"], "exp_cts": round(dec["exp_cts"], 2),
                         "tono_z": None if tono_z is None else round(tono_z, 2)})
            df = cargar()
            d = df.copy()
            d["fecha"] = pd.to_datetime(d["fecha"])
            hace7 = d[d["fecha"] <= pd.Timestamp(hoy) - pd.Timedelta(days=7)]
            tendencia = None if hace7.empty else (p["media"] - hace7.iloc[-1]["media"]) * 100
        ok, tot = acierto(df) if len(df) else (0, 0)
        enviar(mensaje(p, dec, titulares, ok, tot, tendencia, aviso))
    except Exception as e:
        try:
            enviar(f"⚠️ El bot de gasolina falló hoy: {html.escape(str(e))}")
        finally:
            raise


if __name__ == "__main__":
    main()

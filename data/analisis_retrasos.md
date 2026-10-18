# Análisis de retrasos · gasolina 95 · provincia de Alicante

Datos: 365 días de precios (2025-10-18 a 2026-10-17). Evaluación fuera de muestra: 2026-04-15 a 2026-10-17 (179 días).

## 1. Cuánto y con qué retraso se refleja cada referencia (cambios de 7 días)
- gasolina NY: mayor relación con 3 días de retraso (correlación 0.38; cuando sube 1 cts/L en el mercado, el surtidor se mueve +0.47 cts/L)
- Brent: mayor relación con 2 días de retraso (correlación 0.40; cuando sube 1 cts/L en el mercado, el surtidor se mueve +0.58 cts/L)
- gasóleo NY: mayor relación con 2 días de retraso (correlación 0.41; cuando sube 1 cts/L en el mercado, el surtidor se mueve +0.34 cts/L)

## 2. ¿Predice el cambio de los próximos 7 días? (probado con datos no vistos al ajustar)
R² frente a suponer 'no cambia' (0 = no mejora nada; negativo = empeora):
- A) solo precios del surtidor: R² +0.003, correlación -0.17, acierto de dirección 60% (no distinguible de cero)
- B) solo mercado: R² +0.047, correlación +0.10, acierto de dirección 67% (no distinguible de cero)
- C) mercado + surtidor: R² +0.047, correlación +0.11, acierto de dirección 68% (no distinguible de cero)

Mejor variante: **B) solo mercado**.

## 3. Dinero que habría ahorrado un conductor (repostar en los próximos 7 días)
- Modelo nuevo (B) solo mercado, umbral ±0.3 cts): -0.05 cts/L frente a repostar hoy (IC95 -0.34 a +0.14) → no concluyente (no se distingue de repostar hoy); espera el 16% de los días
- Modelo actual del bot (mismo periodo): +0.24 cts/L (IC95 -0.16 a +0.60) → no concluyente (no se distingue de repostar hoy); espera el 40% de los días
- Techo teórico (mejor día de cada semana): +1.07 cts/L (≈ 0.43 € por depósito de 40 L)

## Conclusión
Hay algo de señal, pero el ahorro no se distingue de cero. Se puede usar como orientación, sin prometer céntimos concretos.
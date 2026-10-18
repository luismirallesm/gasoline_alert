# Backtest del bot de gasolina 95 · provincia de Alicante

Datos: 365 días de precios del Ministerio (2025-10-18 a 2026-10-17), 447-479 gasolineras por día.
Mercado: 501 días de gasolina mayorista (2024-10-18 a 2026-10-16).
Sin noticias (no hay histórico gratuito largo): se prueba solo el modelo de mercado.
Conductor simulado: necesita repostar en los próximos 7 días. Ahorro en céntimos por litro (≈ ×0.40 € por depósito de 40 L).

## Ajuste actual del bot (umbrales +0,7 / -1,0)
- Todo el periodo: +0.26 cts/L frente a repostar hoy (IC95 +0.04 a +0.51) → MEJORA a repostar hoy
- Tramo reciente (35 %): +0.11 cts/L (IC95 -0.35 a +0.61) → no concluyente (no se distingue de repostar hoy)
- Frente a repostar un día cualquiera de la semana: +0.53 cts/L
- Techo teórico (el mejor día de cada semana, imposible de saber): +1.16 cts/L
- Días en que el bot hace esperar: 30%
- Acierto direccional de avisos (precio 7 días después): 79% de 256 avisos
- Calibración: correlación 0.08; pendiente 0.11 (1 = el surtidor se mueve justo lo que predice el modelo; 0 = no hay relación)

## Mejor ajuste encontrado (elegido con el tramo antiguo)
- Ventana reciente 2 días, retraso 3 días, umbral REPOSTA +0.3, ESPERA -0.5
- En el tramo antiguo (donde se eligió): +0.40 cts/L
- En el tramo reciente (no visto al elegir): +0.26 cts/L (IC95 -0.31 a +0.99) → no concluyente (no se distingue de repostar hoy)

## Conclusión
No se puede demostrar que el modelo actual mejore a repostar hoy con estos datos.
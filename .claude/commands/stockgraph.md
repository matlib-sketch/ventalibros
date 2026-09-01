---
name: stockgraph
description: Genera un gráfico de banda de PE que proyecta el precio futuro de una acción bajo distintos escenarios de múltiplo. Úsalo siempre que el usuario pida proyectar el precio futuro de una acción, una banda de PE, escenarios de valoración futura, o un gráfico de EPS vs PE de cualquier ticker.
---

Proyecta el precio futuro de la acción indicada (el ticker viene como argumento: $ARGUMENTS) bajo tres escenarios de múltiplo PE. Sigue estos pasos:

1. **Búsqueda de datos reales**: Busca en la web datos actuales del ticker `$ARGUMENTS`:
   - EPS diluido trailing (últimos 12 meses / TTM)
   - Estimaciones de consenso de EPS para los próximos 3 años fiscales
   - Precio actual de la acción
   - Rango histórico de PE de la empresa como compañía madura: techo reciente y piso reciente. NO uses el máximo histórico absoluto si ocurrió cuando el EPS era diminuto o negativo, porque distorsiona. Usa el rango de los últimos 3-5 años cuando la empresa ya era madura.
   - Cuándo termina el año fiscal de la empresa (para etiquetar correctamente los ejes).
   - Verifica la coherencia: precio actual ÷ EPS trailing ≈ PE actual. Usa una sola fuente coherente para EPS.

2. **Genera un archivo HTML standalone** llamado `stockgraph_$ARGUMENTS.html` con las siguientes características:
   - Usa Chart.js desde CDN (sin dependencias locales).
   - Gráfico de líneas con el precio implícito (precio = EPS × PE) para cada año fiscal.
   - **Tres líneas**:
     - Verde: PE en el techo reciente (escenario optimista)
     - Morado: PE actual (escenario base)
     - Naranja: PE en el piso reciente (escenario pesimista)
   - El punto "Hoy" debe ser el primero en el eje X y anclar exactamente en el precio real actual, usando el PE actual, para que el gráfico sea honesto visualmente.
   - Eje Y en dólares (formato `$X`), eje X en años fiscales (ej: FY2024, FY2025, FY2026, FY2027).
   - Título del gráfico: `$ARGUMENTS – Proyección de precio por escenario de PE`
   - Tooltips que muestren: año, escenario, EPS estimado, PE usado, precio implícito.
   - Pie de página dentro del HTML aclarando: *"Las cifras futuras son estimaciones de consenso con incertidumbre creciente. Este gráfico es ilustrativo, no una predicción ni recomendación de inversión."*
   - Diseño limpio: fondo blanco o gris muy claro, fuente sans-serif, leyenda visible.
   - Incluye una tabla HTML debajo del gráfico con los valores numéricos: año | EPS est. | precio (techo PE) | precio (PE actual) | precio (piso PE).

3. **Análisis de texto** tras generar el archivo:
   - Indica el precio actual y el PE actual calculado.
   - Para cada escenario (techo, base, piso) muestra el precio proyectado al año más lejano y el porcentaje de subida o bajada desde hoy.
   - Subraya que la diferencia entre escenarios viene del múltiplo (PE), no de las ganancias estimadas — ambos escenarios comparten el mismo EPS.
   - Cierra con: *"Esta información es solo ilustrativa y no constituye una recomendación de inversión."*

4. **Entrega**: Usa la herramienta SendUserFile para enviarle el archivo HTML generado al usuario.

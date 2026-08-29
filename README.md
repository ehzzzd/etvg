# 📺 TiVo XMLTV EPG Generator para GitHub Actions

Generador automático de guía de programación electrónica (EPG) en formato estándar **XMLTV** (`.xml` y `.xml.gz`), extrayendo datos directamente de la API pública de **TiVo** (`online.tivo.com`).

Está diseñado para ejecutarse automáticamente mediante **GitHub Actions** cada 6 horas (o con la frecuencia que elijas) y generar una guía continua de **~24 horas** con ventanas deslizantes, logotipos de canales y carátulas de programas.

---

## 🚀 Características Principales

- **Sin necesidad de inicio de sesión:** Inicializa la sesión y asocia el `bodyId` automáticamente mediante llamadas HTTP limpias.
- **Ventana Deslizante (~24h continuous):** Encadena bloques horarios (ej. bloques de 8h) para cubrir 24 horas completas sin cortes ni saltos de programación.
- **Soporte Multi-Proveedor:** Puedes configurar uno o varios operadores (Spectrum, Liberty, DirecTV, Comcast, etc.) en un solo archivo de configuración.
- **Filtro de Canales Flexible:** Selecciona canales por número (`"700"`), por distintivo (`"GAME-1"`) o con personalización de ID (`"xmltv_id": "GAME-1.us"`).
- **Logotipos y Carátulas Oficiales:** Extrae automáticamente los logos de los canales y pósters de los eventos desde la CDN de TiVo (`i.tivo.com`).
- **Compresión GZIP Automática:** Genera tanto `epg.xml` como `epg.xml.gz` (reducción de tamaño de ~95% para carga instantánea en IPTV).
- **Herramientas de Búsqueda Integradas:** Comandos para descubrir proveedores por código postal y listar canales disponibles.

---

## 📁 Estructura del Proyecto

```text
├── .github/
│   └── workflows/
│       └── epg.yml          # Flujo programado de GitHub Actions (Cron)
├── config.json              # Configuración de proveedores y canales
├── generator.py             # Script principal en Python
├── requirements.txt         # Dependencias (requests)
└── README.md                # Esta guía
```

---

## ⚙️ Configuración (`config.json`)

El archivo `config.json` controla qué proveedores y canales se extraen:

```json
{
  "settings": {
    "output_xml": "epg.xml",
    "output_gzip": true,
    "hours_ahead": 24,
    "hours_backward": 6,
    "window_hours": 8,
    "timezone_mode": "utc",
    "time_shift_hours": 0,
    "lang": "en"
  },
  "providers": [
    {
      "id": "spectrum_manhattan",
      "name": "Spectrum HD Manhattan Cable",
      "postalCode": "10001",
      "headend": "341765326",
      "lineupType": 83,
      "gmtOffsetSeconds": -14400,
      "channels": [
        "700", "701", "702", "703", "704", "705", "706",
        "707", "708", "709", "710", "711", "712", "713"
      ]
    }
  ]
}
```

### Zona horaria: cómo funciona (IMPORTANTE)

> **Verificado contra la API real:** TiVo devuelve los `startTime` en **UTC (GMT+0)**. Por eso el generador escribe cada hora como `YYYYMMDDHHMMSS +0000`, que es el estándar XMLTV y **no necesita ninguna corrección**.

Kodi (y cualquier reproductor compatible con XMLTV) toma ese instante UTC y lo **convierte automáticamente a la zona horaria del dispositivo**. Como Puerto Rico es **UTC-4 fijo, sin horario de verano**, la guía se ve siempre en hora correcta, de forma **permanente**, sin tocar nada:

- `20260829170000 +0000` → **1:00 PM** en Puerto Rico.

**Regla de oro (la RAE de las EPG):** *si el archivo XMLTV lleva offsets explícitos y correctos, NO apliques ningún ajuste manual en el reproductor.* El desfase casi siempre proviene de un ajuste manual o de un caché de EPG obsoleto, no del archivo.

#### `time_shift_hours` → dejarlo en `0` (valor correcto)

`"time_shift_hours": 0` es el valor correcto y **no debe cambiarse**. Un valor distinto (`1`, `-1`, etc.) es lo que en esta fase de desarrollo desfasaba la guía una hora (p. ej., "los de la 1 PM salen a las 2 PM"). Con la fuente en UTC, cualquier `time_shift_hours != 0` desfasa el archivo.

#### Lo único que has de revisar en Kodi (no es el Time Shift)

1. **Zona horaria del sistema Kodi** → configúrala a tu zona real (para Puerto Rico, `America/Puerto_Rico` / **UTC-4**). No es lo mismo que el *EPG Time Shift*: es la hora del dispositivo, y es la clave para que Kodi convierta el UTC correctamente.
2. **Limpia el caché de EPG** (una sola vez, tras cargar el archivo nuevo): en Kodi ve a **Settings → PVR & Live TV → Guide → Clear data** (o "Borrar datos de la guía") y deja que Kodi vuelva a importar la lista. Esto es esencial: Kodi cachea la guía y si no se limpia, sigue mostrando horas viejas aunque el archivo haya cambiado.
3. **Deja el EPG Time Shift en `0`** (por defecto). Como el archivo es UTC correcto, no necesitas desplazar nada, y así **no afectas a tus otras fuentes de EPG**.

> 💡 Puerto Rico no cambia de hora (no tiene DST), así que, con la zona del dispositivo bien configurada, esta solución es **estable para siempre**: ya no tendrás que andar ajustando el shift cada vez que regeneres la guía.

---

### ¿Cómo agregar más canales o proveedores?

Para agregar otro proveedor, simplemente añade otro objeto al arreglo `"providers"`:

```json
{
  "id": "liberty_pr",
  "name": "Liberty Cablevision Puerto Rico",
  "postalCode": "00901",
  "headend": "7177373627",
  "lineupType": 83,
  "gmtOffsetSeconds": -14400,
  "channels": [
    "WAPA",
    "TELEMUNDO",
    "UNIVISION"
  ]
}
```

### Formatos aceptados en `"channels"`:
1. **Por número de canal:** `["700", "701", "702"]`
2. **Por CallSign:** `["GAME-1", "GAME-2", "ESPNHD"]`
3. **Personalizado con ID XMLTV:**
   ```json
   {
     "channelNumber": "700",
     "callSign": "GAME-1",
     "xmltv_id": "GAME1.us",
     "name": "MLB Extra Innings 1"
   }
   ```
4. **Todos los canales del proveedor:** `["*"]`

---

## 🔍 Comandos para Descubrir Nuevos Proveedores y Canales

El script incluye funciones para consultar TiVo desde tu terminal local o entorno de pruebas:

### 1. Buscar proveedores por Código Postal (ZIP)
```bash
python generator.py --search-providers 10001
```
*Muestra los nombres, `headend` y `lineupType` de todos los operadores disponibles en esa zona.*

### 2. Listar todos los canales de un proveedor
```bash
python generator.py --list-channels 10001 341765326 83
```
*Muestra la lista de números, siglas (`callSign`) y nombres oficiales para añadirlos a tu `config.json`.*

---

## 🛠️ Instalación en GitHub

1. **Crea un repositorio en GitHub** (público o privado).
2. **Sube los archivos del proyecto:**
   - `.github/workflows/epg.yml`
   - `config.json`
   - `generator.py`
   - `requirements.txt`
3. **Habilita permisos de escritura para GitHub Actions:**
   - En tu repositorio de GitHub ve a: **Settings** → **Actions** → **General**.
   - En la sección **Workflow permissions**, selecciona:
     - ✅ **Read and write permissions**.
   - Haz clic en **Save**.
4. **Ejecución de prueba:**
   - Ve a la pestaña **Actions** en GitHub.
   - Selecciona el flujo **Update TiVo EPG**.
   - Haz clic en el botón **Run workflow**.

En un par de minutos, GitHub Actions generará y guardará en tu repositorio `epg.xml` y `epg.xml.gz`.

---

## 📡 Cómo Usar la Guía en tus Reproductores IPTV

Una vez que GitHub Actions genere el archivo, puedes usar la URL directa en **TiviMate**, **OTT Navigator**, **Kodi (IPTV Simple Client)**, **Jellyfin**, **Plex** o cualquier aplicación compatible con XMLTV:

### Opción 1: Enlace Raw directo de GitHub
```text
https://raw.githubusercontent.com/<TU_USUARIO>/<TU_REPOSITORIO>/main/epg.xml.gz
```
*(O `epg.xml` si prefieres sin comprimir, aunque `.gz` carga mucho más rápido).*

### Opción 2: Usando GitHub Pages (Recomendado para mayor velocidad de CDN)
1. Ve a **Settings** → **Pages** en tu repositorio.
2. En *Branch*, elige `main` y guarda.
3. Tu enlace EPG será:
```text
https://<TU_USUARIO>.github.io/<TU_REPOSITORIO>/epg.xml.gz
```

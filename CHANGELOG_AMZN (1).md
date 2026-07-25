# AMZN Service — Changelog

---

## 🇪🇸 Español

### Modo HYBRID (DV + HDR10)

Se implementó soporte para el modo `HYBRID`, que combina Dolby Vision y HDR10 en manifests separados. Cuando se solicita `HYBRID`, `DV_HYBRID` o `HybridLog`, el servicio obtiene el manifest principal en DV y, si tiene éxito, solicita un segundo manifest con `HDR10` como base layer. Los tracks de video del segundo manifest se etiquetan con `hdr10=True, dv=False` y se añaden a la lista de pistas disponibles. Se corrigió un bug crítico por el cual la condición que activaba el bloque DV fallaba cuando no se pasaba el argumento `-p`, porque `self.profile` era `None` y `.get(None)` devolvía `None` en lugar de buscar el perfil `default`.

### Soporte AV1

Se añadió soporte para el codec AV1 extrayendo los cambios necesarios de una implementación de referencia. Los cambios incluyen: detección de codec con prioridad `AV1 > H265 > H264`, forzar el modo de bitrate `CVBR` cuando se selecciona AV1, filtrado de tracks por codec tras parsear el manifest (`av01`, `avc`, `hev1`), manejo gracioso del error cuando AV1 no está disponible en el manifest (devuelve `{}` en lugar de lanzar excepción), y adición de `SeparateFile` a `fragmentRepresentations` en los tres payloads de la solicitud.

### Migración de endpoints y device

**config.yaml:** Se actualizó el device de Hisense a LG OLED55C3PUA (`device_type: A71I8788P1ZV8`). Se añadió el diccionario `dtid_dict` con los `device_type` aprobados por Amazon. Se eliminó el endpoint `browse`, se renombraron `licence` → `license_wv` y `licence_pr` → `license_pr`, y se añadieron los endpoints `configuration`, `refreshplayback`, `opensession`, `updatesession` y `closesession`.

**`__init__.py`:** Se actualizó `prepare_endpoint()` con el nuevo enrutamiento. Se actualizó `_get_license()` para usar `license_wv` y `license_pr`. Se añadió en `configure()` una verificación del `dtid_dict` y una llamada al endpoint `configuration` para verificar el `marketplace_id` real de la región. Se corrigió un bug por el que el endpoint `configuration` intentaba enrutar a `www.primevideo.com` en lugar de al host del manifest (`atv-ps-eu.primevideo.com`), lo que causaba errores 404.

### Audio Atmos 576 kb/s desde manifest DV

Se implementó la obtención del audio Atmos de 576 kb/s DD+ desde el manifest DV/UHD. El bloque de audio DV se ejecuta siempre que haya un device configurado, independientemente del flag `--atmos`. Se corrigieron múltiples bugs: la condición `and not self.atmos` que impedía el fetch, la variable `need_uhd_audio` que bloqueaba la ejecución, el filtrado incorrecto por string `"atmos"` en el codec, el fallo silencioso de `.get(None)` cuando `self.profile` era `None`, y el umbral de log que excluía tracks de 448 kb/s. El log resultante informa del número de tracks Atmos encontrados y del mejor bitrate disponible.

### Language map para audio (todos los manifests)

Se implementaron las funciones `_build_ordered_lang_map_from_mpd()` y `_apply_ordered_lang_map()`. Amazon usa códigos de idioma cortos (`es`, `en`, `pt`) en el atributo `lang` de los `AdaptationSet` del MPD, pero los IDs de cada `Representation` contienen el tag completo (`audio_es-ES_*`, `audio_es-419_*`, `audio_en-US_*`). Estas funciones leen los IDs para construir un mapa ordenado y lo aplican a los tracks de audio tras parsearlos, corrigiendo todos los idiomas al tag BCP-47 completo. El language map se aplica al manifest DASH principal, al manifest de audio CVBR y al manifest DV/UHD.

### Resolución de idioma en subtítulos

Se añadió la función `_resolve_subtitle_language()`. Amazon a veces devuelve códigos de idioma cortos (`es`, `pt`, `fr`) en el campo `languageCode` de la API de subtítulos, cuando el contenido real es específico de una región. La función examina la URL del subtítulo, que habitualmente contiene el tag completo (`/es-ES/`, `/es_419/`, `/pt-BR/`), y devuelve el código completo. Si ya tiene subtag regional, lo retorna sin cambios. Se aplica en el loop de subtítulos para todos los tracks.

---

## 🇬🇧 English

### HYBRID Mode (DV + HDR10)

Added support for `HYBRID` mode, combining Dolby Vision and HDR10 from separate manifests. When `HYBRID`, `DV_HYBRID`, or `HybridLog` is requested, the service fetches the main manifest in DV and, on success, requests a second manifest with `HDR10` as the base layer. Video tracks from the second manifest are tagged `hdr10=True, dv=False` and added to the available track list. Fixed a critical bug where the condition activating the DV block would silently fail when `-p` was not passed, because `self.profile` was `None` and `.get(None)` returned `None` instead of looking up the `default` profile.

### AV1 Support

Added AV1 codec support by extracting the required changes from a reference implementation. Changes include: codec detection with `AV1 > H265 > H264` priority, forcing `CVBR` bitrate mode when AV1 is selected, filtering tracks by codec after parsing the manifest (`av01`, `avc`, `hev1`), graceful handling when AV1 is unavailable in the manifest (returns `{}` instead of raising), and adding `SeparateFile` to `fragmentRepresentations` in all three request payloads.

### Endpoint and Device Migration

**config.yaml:** Updated device from Hisense to LG OLED55C3PUA (`device_type: A71I8788P1ZV8`). Added `dtid_dict` with Amazon-approved `device_type` values. Removed the `browse` endpoint, renamed `licence` → `license_wv` and `licence_pr` → `license_pr`, and added `configuration`, `refreshplayback`, `opensession`, `updatesession`, and `closesession` endpoints.

**`__init__.py`:** Updated `prepare_endpoint()` with the new routing. Updated `_get_license()` to use `license_wv` and `license_pr`. Added a `dtid_dict` check and a call to the `configuration` endpoint in `configure()` to verify the actual region `marketplace_id`. Fixed a bug where the `configuration` endpoint was incorrectly routing to `www.primevideo.com` instead of the manifest host (`atv-ps-eu.primevideo.com`), causing 404 errors.

### Atmos 576 kb/s Audio from DV Manifest

Implemented fetching of 576 kb/s DD+ Atmos audio from the DV/UHD manifest. The DV audio block now runs whenever a device is configured, regardless of the `--atmos` flag. Fixed multiple bugs: the `and not self.atmos` condition that blocked fetching, the `need_uhd_audio` variable that gated execution, incorrect filtering by the `"atmos"` codec string, silent failure of `.get(None)` when `self.profile` was `None`, and a log threshold that excluded 448 kb/s tracks. The resulting log reports the number of Atmos tracks found and the best bitrate available.

### Language Map for Audio (All Manifests)

Implemented `_build_ordered_lang_map_from_mpd()` and `_apply_ordered_lang_map()`. Amazon uses short language codes (`es`, `en`, `pt`) in the `lang` attribute of MPD `AdaptationSet` elements, but each `Representation` ID contains the full tag (`audio_es-ES_*`, `audio_es-419_*`, `audio_en-US_*`). These functions read the IDs to build an ordered map and apply it to audio tracks after parsing, correcting all languages to full BCP-47 tags. The language map is applied to the main DASH manifest, the CVBR audio manifest, and the DV/UHD manifest.

### Subtitle Language Resolution

Added the `_resolve_subtitle_language()` function. Amazon sometimes returns short language codes (`es`, `pt`, `fr`) in the `languageCode` field of the subtitle API, even when the content is region-specific. The function examines the subtitle URL, which typically contains the full tag (`/es-ES/`, `/es_419/`, `/pt-BR/`), and returns the complete code. If the code already has a regional subtag, it is returned unchanged. Applied in the subtitle loop for all tracks.

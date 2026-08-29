#!/usr/bin/env python3
"""
TiVo XMLTV EPG Generator
Genera una guía de programación electrónica (EPG) en formato XMLTV estándar
a partir de la API pública de online.tivo.com, compatible con GitHub Actions.
"""

import os
import sys
import json
import gzip
import argparse
import requests
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET
from xml.dom import minidom


class TiVoAPI:
    BODY_ID = "tsn:A8F000001F29A37"
    BASE_URL = "https://online.tivo.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://online.tivo.com/tve/start"
        })

    def init_session(self):
        """Crea cookies de sesión y asocia el bodyId de TiVo."""
        r1 = self.session.get("http://online.tivo.com/start", timeout=15)
        r1.raise_for_status()
        r2 = self.session.get(f"http://online.tivo.com/body/selector/static/{self.BODY_ID}", timeout=15)
        r2.raise_for_status()

    def get_providers_for_zip(self, postal_code):
        """Busca todos los proveedores disponibles para un código postal."""
        url = f"{self.BASE_URL}/tve/guide/lineups/{postal_code}"
        res = self.session.get(url, timeout=15)
        res.raise_for_status()
        data = res.json()
        if isinstance(data, list):
            return data
        return data.get("lineups", [])

    def set_lineup(self, postal_code, headend, lineup_type=83, gmt_offset_seconds=-14400):
        """Fija el proveedor en la sesión activa y retorna la lista completa de canales."""
        params = {
            "bodyId": self.BODY_ID,
            "postalCode": str(postal_code),
            "headend": str(headend),
            "lineupType": str(lineup_type),
            "gmtOffsetSeconds": str(gmt_offset_seconds)
        }
        res = self.session.get(f"{self.BASE_URL}/tve/guide/lineupChannels", params=params, timeout=15)
        res.raise_for_status()
        data = res.json()
        return data.get("channels", [])

    def get_controller_data(self):
        """Obtiene estado de la sesión, hora actual de TiVo y offset GMT."""
        res = self.session.get(f"{self.BASE_URL}/tve/guide/controllerData", params={"bodyId": self.BODY_ID}, timeout=15)
        res.raise_for_status()
        return res.json()

    def get_guide_grid(self, min_end_time, max_start_time, count=100, offset=0):
        """Obtiene la parrilla de programación para un rango horario y lote de canales."""
        params = {
            "minEndTime": min_end_time.strftime("%Y-%m-%d %H:%M:00"),
            "maxStartTime": max_start_time.strftime("%Y-%m-%d %H:%M:00"),
            "count": count,
            "offset": offset,
            "isReceived": "true"
        }
        res = self.session.get(f"{self.BASE_URL}/tve/guide/guideGrid", params=params, timeout=25)
        res.raise_for_status()
        return res.json().get("gridRow", [])


def get_channel_logo_url(logo_index):
    """Calcula la URL pública del logo del canal en la CDN de TiVo."""
    if logo_index and isinstance(logo_index, int) and logo_index > 65536:
        adj = logo_index - 65536
        return f"https://i.tivo.com/images-static/logos/112x32/{adj}.png"
    return None


def get_program_poster_url(collection_id):
    """Calcula la URL pública de la carátula/banner del programa en la CDN de TiVo."""
    if not collection_id or not isinstance(collection_id, str):
        return None
    parts = collection_id.split(".")
    if len(parts) >= 2 and parts[-1].isdigit():
        num_str = parts[-1]
        padded = num_str.zfill(6)
        last6 = padded[-6:]
        first3 = last6[:3]
        last3 = last6[3:]
        return f"https://i.tivo.com/images-production/collection/{first3}/{last3}/{num_str}/showcaseBanner_560x419.jpg"
    return None


def format_xmltv_date(dt_utc, gmt_offset_seconds=0, mode="utc"):
    """
    Formatea la fecha al estándar XMLTV.

    IMPORTANTE (verificado contra la API real): TiVo entrega los `startTime`
    directamente en TIEMPO UNIVERSAL UTC (GMT+0). Por eso el modo por defecto,
    'utc', escribe el instante tal cual con el offset ' +0000'.

    - Modo 'utc' (RECOMENDADO, estándar XMLTV / Kodi):
        YYYYMMDDHHMMSS +0000
        Kodi convierte ese instante a la zona horaria del dispositivo. Con el
        dispositivo en Puerto Rico (UTC-4 fijo, sin horario de verano) se muestra
        la hora correcta de forma PERMANENTE y NO hay que tocar el EPG Time Shift.

    - Modo 'local':
        Escribe la hora local del proveedor con su offset (+/-HHMM).
        Matemáticamente es el mismo instante; solo cambia la etiqueta.

    NOTA TÉCNICA: no uses `time_shift_hours` para "corregir" aquí. El archivo es
    UTC real; un shift manual es lo que durante días provocó que la guía se viera
    adelantada/atrasada de forma inconsistente en Kodi.
    """
    if mode == "local" and gmt_offset_seconds:
        local_dt = dt_utc + timedelta(seconds=gmt_offset_seconds)
        sign = "+" if gmt_offset_seconds >= 0 else "-"
        total_min = abs(gmt_offset_seconds) // 60
        hours = total_min // 60
        mins = total_min % 60
        tz_suffix = f"{sign}{hours:02d}{mins:02d}"
        return local_dt.strftime("%Y%m%d%H%M%S") + f" {tz_suffix}"
    return dt_utc.strftime("%Y%m%d%H%M%S +0000")



def parse_iso_datetime(dt_str):
    return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")


def now_utc_naive():
    """Hora UTC actual sin tzinfo, para mantener compatibilidad con el resto del script."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_guide_time_mode(provider_cfg, channel_filter=None):
    """
    Define cómo interpreta este lineup los horarios que devuelve TiVo.

    - utc: TiVo devuelve `startTime` como instante UTC. Es el comportamiento que
      vemos en Spectrum / GAME, por eso se escribe directo como +0000.
    - local: TiVo devuelve `startTime` en hora local del proveedor. Es lo que está
      pasando con Liberty PR; aquí convertimos esa hora local a UTC antes de
      escribir XMLTV para que Kodi/PVR la muestre en la hora correcta.

    Se puede poner por proveedor con `guide_time_mode`, o por canal si el filtro
    de `channels` es un objeto.
    """
    raw = None
    if isinstance(channel_filter, dict):
        raw = channel_filter.get("guide_time_mode") or channel_filter.get("time_mode")
    raw = raw or provider_cfg.get("guide_time_mode") or provider_cfg.get("time_mode") or "utc"
    mode = str(raw).strip().lower().replace("-", "_")
    aliases = {
        "provider_local": "local",
        "lineup_local": "local",
        "local_time": "local",
        "utc_time": "utc",
    }
    mode = aliases.get(mode, mode)
    if mode not in ("utc", "local"):
        print(f"    [!] guide_time_mode inválido: {raw!r}; usando 'utc'.")
        mode = "utc"
    return mode


def tivo_time_to_utc(dt_raw, gmt_offset_seconds, guide_time_mode):
    """Convierte el startTime devuelto por TiVo a un datetime UTC interno."""
    if guide_time_mode == "local":
        return dt_raw - timedelta(seconds=gmt_offset_seconds)
    return dt_raw


def process_provider(api, provider_cfg, hours_ahead=24, hours_backward=6, window_hours=8):
    """Procesa un proveedor, filtra sus canales y obtiene programación continua (pasada y futura)."""
    postal_code = provider_cfg.get("postalCode")
    headend = provider_cfg.get("headend")
    lineup_type = provider_cfg.get("lineupType", 83)
    gmt_offset = provider_cfg.get("gmtOffsetSeconds", -14400)
    provider_name = provider_cfg.get("name", f"Headend {headend}")
    channel_filters = provider_cfg.get("channels", [])
    guide_time_mode = get_guide_time_mode(provider_cfg)

    print(f"\n[+] Configurando proveedor: {provider_name} (ZIP: {postal_code}, Headend: {headend})")
    
    # 1. Establecer lineup
    all_channels = api.set_lineup(postal_code, headend, lineup_type, gmt_offset)
    print(f"    Lineup cargado con {len(all_channels)} canales totales.")

    # 2. Consultar controllerData para hora real y gmtOffset
    try:
        ctrl = api.get_controller_data()
        cur_time_str = ctrl.get("curDateTime")
        if ctrl.get("selectedLineup", {}).get("gmtOffsetSeconds") is not None:
            gmt_offset = ctrl["selectedLineup"]["gmtOffsetSeconds"]
        # curDateTime viene en hora local del proveedor. Calculamos ambas bases
        # porque algunos lineups de TiVo esperan/entregan UTC (Spectrum/GAME),
        # mientras otros entregan hora local del proveedor (Liberty PR).
        local_time = datetime.strptime(cur_time_str, "%Y-%m-%d %H:%M")
        utc_time = local_time - timedelta(seconds=gmt_offset)
    except Exception as e:
        print(f"    [!] Aviso: No se pudo obtener controllerData ({e}), usando UTC.")
        utc_time = now_utc_naive()
        local_time = utc_time + timedelta(seconds=gmt_offset)

    base_dt = local_time if guide_time_mode == "local" else utc_time
    print(f"    Modo horario TiVo: {guide_time_mode.upper()} "
          f"(local={local_time.strftime('%Y-%m-%d %H:%M')}, "
          f"UTC={utc_time.strftime('%Y-%m-%d %H:%M')}, offset={gmt_offset // 3600:+d}h).")

    # 3. Filtrar canales deseados
    # channel_filters puede ser:
    # - ["700", "701", ...]
    # - ["GAME-1", "GAME-2", ...]
    # - [{"channelNumber": "700", "xmltv_id": "GAME1.us", ...}]
    matched = []
    seen_keys = set()

    for idx, ch in enumerate(all_channels):
        ch_num = str(ch.get("channelNumber", "")).strip()
        call_sign = str(ch.get("callSign", "")).strip()

        # Si no hay filtros especificados o incluye "*", se incluyen todos
        if not channel_filters or "*" in channel_filters:
            matched.append((idx, ch, {}))
            continue

        for f in channel_filters:
            matched_filter = None
            if isinstance(f, str):
                target = f.strip().upper()
                if target == ch_num.upper() or target == call_sign.upper():
                    matched_filter = {}
            elif isinstance(f, dict):
                target_num = str(f.get("channelNumber", "")).strip().upper()
                target_call = str(f.get("callSign", "")).strip().upper()
                if (target_num and target_num == ch_num.upper()) or (target_call and target_call == call_sign.upper()):
                    matched_filter = f

            if matched_filter is not None:
                uniq_key = ch_num or call_sign
                if uniq_key not in seen_keys:
                    seen_keys.add(uniq_key)
                    matched.append((idx, ch, matched_filter))
                break

    if not matched:
        print(f"    [-] No se encontraron canales coincidentes con los filtros especificados.")
        return [], [], gmt_offset

    print(f"    -> Canales filtrados a extraer: {len(matched)}")
    for idx, ch, mf in matched:
        print(f"       • Canal #{ch.get('channelNumber')} [{ch.get('callSign')}]: {ch.get('name')}")

    # 4. Obtener programación mediante ventanas deslizantes y lotes inteligentes
    # Agrupamos canales cercanos en clusters para no descargar cientos de canales intermedios no deseados
    indices = sorted(list(set([idx for idx, ch, mf in matched])))
    clusters = []
    if indices:
        curr_start = indices[0]
        curr_end = indices[0]
        for i in indices[1:]:
            if i - curr_end <= 25:
                curr_end = i
            else:
                clusters.append((curr_start, curr_end - curr_start + 1))
                curr_start = i
                curr_end = i
        clusters.append((curr_start, curr_end - curr_start + 1))

    # Estructura: callSign/number -> dict de offerId -> offer
    programmes_map = {}
    channel_obj_map = {}

    for idx, ch, mf in matched:
        cid = mf.get("xmltv_id") or f"{ch.get('callSign')}.us"
        programmes_map[cid] = {}
        channel_obj_map[cid] = (ch, mf)

    # Ventana deslizante que incluye horas pasadas (-hours_backward) para garantizar que el programa
    # que se está emitiendo AHORA MISMO esté 100% capturado con su hora de inicio real sin saltos
    current_start = base_dt - timedelta(hours=hours_backward)
    end_limit = base_dt + timedelta(hours=hours_ahead)
    total_hours = hours_ahead + hours_backward

    print(f"    -> Descargando {total_hours}h de programación (-{hours_backward}h pasadas a +{hours_ahead}h futuras) en {len(clusters)} lotes de canales...")
    while current_start < end_limit:
        win_end = min(current_start + timedelta(hours=window_hours), end_limit)
        win_label = f"{current_start.strftime('%Y-%m-%d %H:%M')} a {win_end.strftime('%H:%M')} {guide_time_mode.upper()}"
        print(f"       - Ventana [{win_label}]...")

        for c_offset, c_count in clusters:
            try:
                grid_rows = api.get_guide_grid(current_start, win_end, count=c_count, offset=c_offset)
                for row in grid_rows:
                    r_ch = row.get("channel", {})
                    r_num = str(r_ch.get("channelNumber", "")).strip().upper()
                    r_call = str(r_ch.get("callSign", "")).strip().upper()

                    # Buscar a qué canal_id corresponde
                    for cid, (orig_ch, mf) in channel_obj_map.items():
                        if r_num == str(orig_ch.get("channelNumber", "")).strip().upper() and \
                           r_call == str(orig_ch.get("callSign", "")).strip().upper():
                            for offer in row.get("offer", []):
                                offer_key = offer.get("offerId") or (offer.get("startTime"), offer.get("title"))
                                programmes_map[cid][offer_key] = offer
                            break
            except Exception as e:
                print(f"       [!] Error al descargar lote (offset={c_offset}, count={c_count}): {e}")

        current_start = win_end

    # Retornar info formateada
    return channel_obj_map, programmes_map, gmt_offset, provider_cfg


def build_xmltv(all_provider_results, settings):
    """Construye el documento XML estándar XMLTV."""
    lang = settings.get("lang", "en")
    tz_mode = settings.get("timezone_mode", "utc") # "utc" (recomendado para Kodi) o "local"
    root = ET.Element("tv", {
        "generator-info-name": "TiVo-EPG-Generator",
        "source-info-url": "https://online.tivo.com"
    })

    added_channel_ids = set()

    # 1. Tags <channel>
    for channel_obj_map, programmes_map, gmt_offset, prov_cfg in all_provider_results:
        for cid, (ch, mf) in channel_obj_map.items():
            if cid in added_channel_ids:
                continue
            added_channel_ids.add(cid)

            ch_el = ET.SubElement(root, "channel", {"id": cid})

            # Nombre para mostrar
            custom_name = mf.get("name")
            call_sign = ch.get("callSign", "")
            ch_num = ch.get("channelNumber", "")
            tivo_name = ch.get("name", "")

            if custom_name:
                ET.SubElement(ch_el, "display-name").text = custom_name
            if call_sign:
                ET.SubElement(ch_el, "display-name").text = call_sign
            if ch_num and call_sign:
                ET.SubElement(ch_el, "display-name").text = f"{ch_num} {call_sign}"
            if tivo_name and tivo_name not in (call_sign, custom_name):
                ET.SubElement(ch_el, "display-name").text = tivo_name

            # Logo del canal
            logo_url = get_channel_logo_url(ch.get("logoIndex"))
            if logo_url:
                ET.SubElement(ch_el, "icon", {"src": logo_url})

    # 2. Tags <programme>
    for channel_obj_map, programmes_map, gmt_offset, prov_cfg in all_provider_results:
        provider_time_shift = prov_cfg.get("time_shift_hours", settings.get("time_shift_hours", 0))
        if provider_time_shift:
            print(f"    [i] time_shift_hours={provider_time_shift:+d} aplicado a {prov_cfg.get('name', prov_cfg.get('id', 'proveedor'))}.")
            print("        • Úsalo solo como último recurso; primero ajusta guide_time_mode ('utc' o 'local').")
        for cid, offers_dict in programmes_map.items():
            offers = list(offers_dict.values())
            offers.sort(key=lambda x: x.get("startTime", ""))

            for offer in offers:
                st_str = offer.get("startTime")
                duration = offer.get("duration", 0)
                if not st_str or not duration:
                    continue

                raw_st_dt = parse_iso_datetime(st_str)
                ch, mf = channel_obj_map.get(cid, ({}, {}))
                guide_time_mode = get_guide_time_mode(prov_cfg, mf)
                time_shift = mf.get("time_shift_hours", provider_time_shift) if isinstance(mf, dict) else provider_time_shift

                # Normalizamos TODO internamente a UTC.
                # Si TiVo entrega hora local (Liberty PR), convertimos local -> UTC.
                # Si TiVo entrega UTC (Spectrum/GAME), se deja tal cual.
                st_dt = tivo_time_to_utc(raw_st_dt, gmt_offset, guide_time_mode)

                # Ajuste manual opcional por proveedor/canal, después de normalizar.
                if time_shift:
                    st_dt += timedelta(hours=time_shift)

                end_dt = st_dt + timedelta(seconds=duration)

                prog_el = ET.SubElement(root, "programme", {
                    "start": format_xmltv_date(st_dt, gmt_offset, mode=tz_mode),
                    "stop": format_xmltv_date(end_dt, gmt_offset, mode=tz_mode),
                    "channel": cid
                })

                # Título
                title = offer.get("title") or "Sin título"
                ET.SubElement(prog_el, "title", {"lang": lang}).text = title

                # Subtítulo (ej. equipos que juegan)
                subtitle = offer.get("subtitle")
                if subtitle:
                    ET.SubElement(prog_el, "sub-title", {"lang": lang}).text = subtitle

                # Descripción
                desc = subtitle or title
                if offer.get("movieYear"):
                    desc += f" ({offer['movieYear']})"
                ET.SubElement(prog_el, "desc", {"lang": lang}).text = desc

                # Carátula del programa / evento
                poster_url = get_program_poster_url(offer.get("collectionId"))
                if poster_url:
                    ET.SubElement(prog_el, "icon", {"src": poster_url})

                # Calidad HDTV
                if offer.get("hdtv"):
                    video_el = ET.SubElement(prog_el, "video")
                    ET.SubElement(video_el, "quality").text = "HDTV"

                # Rating MPAA
                if offer.get("mpaaRating"):
                    rating_el = ET.SubElement(prog_el, "rating", {"system": "MPAA"})
                    ET.SubElement(rating_el, "value").text = offer["mpaaRating"]

                # Episodio / Fecha original
                if offer.get("originalAirdate"):
                    air_dt = offer["originalAirdate"].split(" ")[0].replace("-", "")
                    ET.SubElement(prog_el, "episode-num", {"system": "original-air-date"}).text = air_dt

    return root


def search_providers(postal_code):
    """Comando auxiliar para buscar headends disponibles en un código postal."""
    print(f"\n[+] Consultando proveedores disponibles para el código postal: {postal_code} ...")
    api = TiVoAPI()
    api.init_session()
    providers = api.get_providers_for_zip(postal_code)
    if not providers:
        print("[-] No se encontraron proveedores para ese código postal.")
        return
    print(f"\nSe encontraron {len(providers)} proveedores:")
    print(f"{'HEADEND':<14} | {'TIPO':<6} | {'NOMBRE DEL PROVEEDOR'}")
    print("-" * 75)
    for p in providers:
        headend = str(p.get("headend", ""))
        lineup_type = str(p.get("lineupType", ""))
        label = p.get("displayLabel") or p.get("headendName") or ""
        print(f"{headend:<14} | {lineup_type:<6} | {label}")
    print("-" * 75)


def list_channels(postal_code, headend, lineup_type=83):
    """Comando auxiliar para listar todos los canales de un proveedor."""
    print(f"\n[+] Obteniendo canales para ZIP: {postal_code}, Headend: {headend}, Tipo: {lineup_type} ...")
    api = TiVoAPI()
    api.init_session()
    channels = api.set_lineup(postal_code, headend, lineup_type)
    if not channels:
        print("[-] No se devolvieron canales.")
        return
    print(f"\nSe encontraron {len(channels)} canales en el proveedor:")
    print(f"{'CANAL':<8} | {'CALLSIGN':<14} | {'NOMBRE'}")
    print("-" * 65)
    for ch in channels:
        num = str(ch.get("channelNumber", ""))
        call = str(ch.get("callSign", ""))
        name = str(ch.get("name", ""))
        print(f"#{num:<7} | {call:<14} | {name}")
    print("-" * 65)


def main():
    parser = argparse.ArgumentParser(description="TiVo XMLTV EPG Generator")
    parser.add_argument("--config", "-c", default="config.json", help="Ruta al archivo de configuración JSON")
    parser.add_argument("--search-providers", metavar="ZIP", help="Busca proveedores disponibles para un código postal")
    parser.add_argument("--list-channels", nargs="+", metavar=("ZIP", "HEADEND"), help="Lista los canales de un proveedor: <ZIP> <HEADEND> [TIPO]")
    args = parser.parse_args()

    if args.search_providers:
        search_providers(args.search_providers)
        return

    if args.list_channels:
        zip_code = args.list_channels[0]
        headend = args.list_channels[1]
        lineup_type = int(args.list_channels[2]) if len(args.list_channels) > 2 else 83
        list_channels(zip_code, headend, lineup_type)
        return

    if not os.path.exists(args.config):
        print(f"[-] Error: Archivo de configuración no encontrado: {args.config}")
        sys.exit(1)

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    settings = config.get("settings", {})
    output_xml = settings.get("output_xml", "epg.xml")
    output_gzip = settings.get("output_gzip", True)
    hours_ahead = settings.get("hours_ahead", 24)
    hours_backward = settings.get("hours_backward", 6)
    window_hours = settings.get("window_hours", 8)

    print("=" * 65)
    print("           TiVo XMLTV EPG Generator")
    print("=" * 65)
    print(f"• Ventana futura: {hours_ahead} horas | Pasada: {hours_backward} horas")
    print(f"• Bloques deslizantes: {window_hours} horas")
    print(f"• Salida XML: {output_xml}")

    api = TiVoAPI()
    print("\n[+] Inicializando sesión anónima en online.tivo.com...")
    api.init_session()
    print("[+] Sesión establecida exitosamente.")

    all_results = []
    for prov in config.get("providers", []):
        res = process_provider(api, prov, hours_ahead=hours_ahead, hours_backward=hours_backward, window_hours=window_hours)
        if res[0]:
            all_results.append(res)

    print("\n[+] Compilando documento XMLTV...")
    root = build_xmltv(all_results, settings)

    xml_raw = minidom.parseString(ET.tostring(root, "utf-8")).toprettyxml(indent="  ", encoding="utf-8")

    # Guardar .xml
    with open(output_xml, "wb") as f:
        f.write(xml_raw)
    file_size_kb = os.path.getsize(output_xml) / 1024
    print(f"[✔] Archivo XML guardado: {output_xml} ({file_size_kb:.1f} KB)")

    # Guardar .xml.gz
    if output_gzip:
        gz_file = f"{output_xml}.gz"
        with gzip.open(gz_file, "wb") as f:
            f.write(xml_raw)
        gz_size_kb = os.path.getsize(gz_file) / 1024
        print(f"[✔] Archivo GZ guardado: {gz_file} ({gz_size_kb:.1f} KB)")

    print("\n[✔] Proceso finalizado con éxito.")
    print("-" * 65)
    print("ZONA HORARIA: TiVo entrega UTC y el archivo se guarda como UTC (+0000).")
    now_utc = now_utc_naive()
    print(f"  • Hora UTC ahora: {now_utc.strftime('%Y-%m-%d %H:%M')}")
    print(f"  • Hora en Puerto Rico (UTC-4): {(now_utc + timedelta(hours=-4)).strftime('%Y-%m-%d %H:%M')}")
    print("  • Spectrum/GAME usa guide_time_mode='utc'.")
    print("  • Liberty PR usa guide_time_mode='local': se consulta en hora local y se convierte a UTC en XMLTV.")
    print("  • Deja time_shift_hours en 0; úsalo solo para ajustes manuales por proveedor/canal.")
    print("  • En Kodi: Settings → PVR & Live TV → Guide → Clear data tras cargar el archivo,")
    print("    y verifica la zona horaria del sistema (America/Puerto_Rico).")
    print("-" * 65)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass

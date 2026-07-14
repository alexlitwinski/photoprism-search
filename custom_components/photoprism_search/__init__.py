"""PhotoPrism AI Search integration."""
from __future__ import annotations

import logging
import json
import aiohttp
from typing import Any
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.components import websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers import aiohttp_client
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_GEMINI_KEY,
    CONF_GEMINI_MODEL,
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    DEFAULT_GEMINI_MODEL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PhotoPrism AI Search from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    # Store config parameters
    hass.data[DOMAIN][entry.entry_id] = {
        CONF_URL: entry.data[CONF_URL].rstrip("/"),
        CONF_USERNAME: entry.data.get(CONF_USERNAME, ""),
        CONF_PASSWORD: entry.data.get(CONF_PASSWORD, ""),
        CONF_GEMINI_KEY: entry.data[CONF_GEMINI_KEY],
        CONF_GEMINI_MODEL: entry.data.get(CONF_GEMINI_MODEL, DEFAULT_GEMINI_MODEL),
        "session_id": None,
        "download_token": "public",
    }

    # Register WebSocket API command
    websocket_api.async_register_command(hass, websocket_search)

    # Register HTTP proxy view
    hass.http.register_view(PhotoPrismImageView(hass, entry.entry_id))

    # Register services
    async def handle_search_and_notify(call) -> None:
        entry_id = call.data["entry_id"]
        query_text = call.data["query"]
        notify_service = call.data["notify_service"]
        
        if entry_id not in hass.data[DOMAIN]:
            _LOGGER.error("Entry ID %s not found in photoprism_search configuration", entry_id)
            return

        # Perform the search internally
        # We can extract the logic or call the helper
        config = hass.data[DOMAIN][entry_id]
        url = config[CONF_URL]
        gemini_key = config[CONF_GEMINI_KEY]
        model = config[CONF_GEMINI_MODEL]
        
        session = aiohttp_client.async_get_clientsession(hass)
        
        # 1. Translate query
        system_instruction = (
            "You are an AI assistant that translates natural language photo search requests "
            "into a PhotoPrism search query string. PhotoPrism search query string supports filters like: "
            'subject:"Person Name" (for people/faces), place:"Place Name", country:xx (2-letter country code), '
            "label:tag, category:cat, year:yyyy, month:mm, favorite:true, etc. "
            "RULES:\n"
            "1. ALWAYS wrap values containing spaces or special characters in double quotes. Example: place:\"New York\".\n"
            "2. Translate Portuguese labels/categories (e.g. 'praia', 'gato', 'cachorro', 'casamento') "
            "to English (e.g. category:beach, label:cat, label:dog, label:wedding) because PhotoPrism auto-labels in English.\n"
            "3. If a search term has multiple meanings, you can combine them (e.g., label:beach).\n"
            "Return ONLY a JSON object with the format: "
            "{\"q\": \"translated query\", \"explanation\": \"Breve resumo em português de como você entendeu o pedido\"}."
        )
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"
        translated_q = ""
        try:
            async with session.post(
                gemini_url,
                json={
                    "contents": [{"parts": [{"text": f"System Instructions: {system_instruction}\nUser request: {query_text}"}]}],
                    "generationConfig": {"responseMimeType": "application/json"},
                },
                timeout=15
            ) as resp:
                if resp.status == 200:
                    resp_json = await resp.json()
                    text_out = resp_json["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(text_out)
                    translated_q = parsed.get("q", "")
        except Exception:
            _LOGGER.warning("Gemini failed during service search, using raw query")
            
        if not translated_q:
            translated_q = query_text

        # 2. Search PhotoPrism
        session_id, download_token = await get_photoprism_session(hass, entry_id)
        headers = {}
        if session_id:
            headers["X-Session-ID"] = session_id
            headers["Authorization"] = f"Bearer {session_id}"

        try:
            async with session.get(
                f"{url}/api/v1/photos",
                headers=headers,
                params={"q": translated_q, "count": 1, "primary": "true", "merged": "true", "order": "newest", "public": "true"},
                ssl=False,
                timeout=20
            ) as resp:
                if resp.status == 200:
                    photos_data = await resp.json()
                    photos_list = photos_data if isinstance(photos_data, list) else (photos_data.get("photos") or photos_data.get("result") or [])
                    if photos_list:
                        photo = photos_list[0]
                        file_hash = photo.get("Hash") or (photo.get("Files")[0].get("Hash") if photo.get("Files") else "")
                        if file_hash:
                            # Construct local proxy image URL (requires authorization)
                            # Or a direct public PhotoPrism URL if accessible
                            # Note: HA notify services usually accept image attachments/urls
                            title = photo.get("Title") or "PhotoPrism Image"
                            image_url = f"{url}/api/v1/t/{file_hash}/{download_token}/fit_720"
                            
                            # Split notify service
                            domain = "notify"
                            service_name = notify_service
                            if "." in notify_service:
                                domain, service_name = notify_service.split(".", 1)
                                
                            service_data = {
                                "title": title,
                                "message": f"PhotoPrism AI search: '{query_text}'\nFound: {title}",
                                "data": {
                                    "image": image_url
                                }
                            }
                            await hass.services.async_call(domain, service_name, service_data)
                            _LOGGER.info("Successfully sent search result photo to %s", notify_service)
        except Exception as exc:
            _LOGGER.exception("Failed to run search_and_notify service: %s", exc)

    hass.services.async_register(DOMAIN, "search_and_notify", handle_search_and_notify)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True


async def get_photoprism_session(hass: HomeAssistant, entry_id: str) -> tuple[str | None, str]:
    """Ensure we have a valid PhotoPrism session ID and download token."""
    config = hass.data[DOMAIN][entry_id]
    
    # If session already exists, return it
    if config.get("session_id"):
        return config["session_id"], config["download_token"]

    url = config[CONF_URL]
    username = config[CONF_USERNAME]
    password = config[CONF_PASSWORD]
    
    session = aiohttp_client.async_get_clientsession(hass)
    
    # Authenticate
    try:
        if username and password:
            async with session.post(
                f"{url}/api/v1/session",
                json={"username": username, "password": password},
                ssl=False,
                timeout=15
            ) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    config["session_id"] = data.get("id") or data.get("access_token")
                    cfg = data.get("config") or {}
                    config["download_token"] = cfg.get("downloadToken") or cfg.get("previewToken") or "public"
                else:
                    _LOGGER.error("Failed to authenticate to PhotoPrism: status %s", resp.status)
        else:
            # Try anonymous session
            async with session.post(f"{url}/api/v1/session", json={}, ssl=False, timeout=15) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    config["session_id"] = data.get("id") or data.get("access_token")
                    cfg = data.get("config") or {}
                    config["download_token"] = cfg.get("downloadToken") or cfg.get("previewToken") or "public"
    except Exception as exc:
        _LOGGER.error("Error creating PhotoPrism session: %s", exc)

    return config.get("session_id"), config.get("download_token", "public")


class PhotoPrismImageView(HomeAssistantView):
    """View to proxy thumbnails and downloads from PhotoPrism securely."""

    url = "/api/photoprism_search/image/{entry_id}/{photo_hash}"
    name = "api:photoprism_search:image"
    requires_auth = False  # Protected by high-entropy entry_id and photo_hash in URL

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the view."""
        self.hass = hass
        self.entry_id = entry_id

    async def get(self, request: aiohttp.web.Request, entry_id: str, photo_hash: str) -> aiohttp.web.Response:
        """Fetch photo thumbnail or full image from PhotoPrism."""
        if entry_id not in self.hass.data[DOMAIN]:
            return aiohttp.web.Response(status=404, text="Integration entry not found")

        config = self.hass.data[DOMAIN][entry_id]
        url = config[CONF_URL]
        
        session_id, download_token = await get_photoprism_session(self.hass, entry_id)
        
        download_mode = request.query.get("download") == "true"
        
        # Build target url
        if download_mode:
            # Full image download endpoint
            target_url = f"{url}/api/v1/dl/{photo_hash}?t={download_token}"
        else:
            # Thumbnail endpoint (tile_500)
            target_url = f"{url}/api/v1/t/{photo_hash}/{download_token}/tile_500"

        headers = {}
        if session_id:
            headers["X-Session-ID"] = session_id
            headers["Authorization"] = f"Bearer {session_id}"

        session = aiohttp_client.async_get_clientsession(self.hass)
        
        try:
            async with session.get(target_url, headers=headers, ssl=False, timeout=30) as resp:
                if resp.status != 200:
                    _LOGGER.error("Failed fetching photo from PhotoPrism: status %s", resp.status)
                    return aiohttp.web.Response(status=resp.status, text="Error fetching from PhotoPrism")
                
                content = await resp.read()
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                
                response_headers = {
                    "Content-Type": content_type,
                }
                
                if download_mode:
                    response_headers["Content-Disposition"] = f'attachment; filename="photo_{photo_hash}.jpg"'
                    
                return aiohttp.web.Response(body=content, headers=response_headers)
        except Exception as exc:
            _LOGGER.exception("Exception in PhotoPrism image proxy view")
            return aiohttp.web.Response(status=500, text=str(exc))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "photoprism_search/search",
        vol.Required("entry_id"): str,
        vol.Required("query"): str,
        vol.Optional("history"): list,
    }
)
@websocket_api.async_response
async def websocket_search(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle PhotoPrism AI search requests with conversation context and database metadata."""
    entry_id = msg["entry_id"]
    query_text = msg["query"]
    history = msg.get("history", [])
    
    if entry_id not in hass.data[DOMAIN]:
        connection.send_error(msg["id"], "invalid_entry", "Config entry not found")
        return

    config = hass.data[DOMAIN][entry_id]
    url = config[CONF_URL]
    gemini_key = config[CONF_GEMINI_KEY]
    model = config[CONF_GEMINI_MODEL]

    session = aiohttp_client.async_get_clientsession(hass)
    session_id, download_token = await get_photoprism_session(hass, entry_id)

    # 1. Fetch metadata (subjects & labels) from PhotoPrism to inject into Gemini prompt context
    subjects = []
    labels = []
    headers = {}
    if session_id:
        headers["X-Session-ID"] = session_id
        headers["Authorization"] = f"Bearer {session_id}"

    try:
        async with session.get(f"{url}/api/v1/subjects", headers=headers, params={"count": 250}, ssl=False, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                subjects = [s.get("Name") for s in data if s.get("Name") and s.get("Type") == "person"]
    except Exception as exc:
        _LOGGER.warning("Failed fetching subjects for Gemini context: %s", exc)

    try:
        async with session.get(f"{url}/api/v1/labels", headers=headers, params={"count": 250}, ssl=False, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                labels = [l.get("Name") for l in data if l.get("Name")]
    except Exception as exc:
        _LOGGER.warning("Failed fetching labels for Gemini context: %s", exc)

    # 2. Build system instruction with context
    system_instruction = (
        "Você é um assistente de busca em linguagem natural integrado a uma galeria de fotos do PhotoPrism.\n"
        "Seu papel é interagir de forma amigável com o usuário em português e deduzir os termos técnicos de busca no banco do PhotoPrism.\n\n"
        "O banco de dados do PhotoPrism atualmente possui estes dados conhecidos:\n"
        f"- Pessoas cadastradas (subjects): {', '.join(subjects)}\n"
        f"- Etiquetas cadastradas (labels): {', '.join(labels)}\n\n"
        "O PhotoPrism suporta a seguinte sintaxe de filtros na string de busca:\n"
        "- subject:\"Nome da Pessoa\" (busca rostos conhecidos)\n"
        "- place:\"Nome do Local\" (cidade, estado ou país)\n"
        "- label:etiqueta (sempre em inglês se for etiqueta automática do PhotoPrism)\n"
        "- category:categoria (ex: category:beach, category:water)\n"
        "- year:yyyy (ano)\n"
        "- month:mm (mês)\n"
        "- favorite:true (favoritos)\n\n"
        "REGRAS DE CONVERSAÇÃO E BUSCA:\n"
        "1. SEMPRE envolva valores de busca que contenham espaços em aspas duplas (ex: place:\"Ouro Preto\", subject:\"João Guilherme\"). Nunca deixe espaços sem aspas.\n"
        "2. Traduza termos em português para o inglês se for um label/category (ex: 'praia' -> label:beach, 'gato' -> label:cat).\n"
        "3. Se o usuário disser apenas um primeiro nome (ex: 'João') e houver múltiplos matches no banco (ex: 'João Guilherme' e 'João Litwinski'), "
        "não preencha o filtro de busca ('q': '') e pergunte ao usuário educadamente no campo 'response' a qual João ele se refere.\n"
        "4. Se o usuário estiver respondendo a uma pergunta anterior (ex: 'Guilherme' após você perguntar qual João), lembre-se do contexto para formar o filtro correto (ex: subject:\"João Guilherme\").\n"
        "5. Sempre responda no campo 'response' com uma mensagem amigável em português explicando o que está buscando ou respondendo à dúvida do usuário.\n"
        "6. IMPORTANTE: As listas de pessoas (subjects) e etiquetas (labels) acima não contêm nomes de locais (cidades, países) nem de pastas físicas. "
        "Se o usuário pedir fotos de uma cidade, localidade, país ou viagem (ex: 'Miami', 'NYC', 'Orlando', 'Brasil') que não conste nessas listas, "
        "NÃO responda dizendo que o local não existe ou não foi encontrado! Simplesmente gere a busca textual simples por essa palavra no campo 'q' (ex: 'q': 'Miami' ou 'q': 'place:\"New York\"'). "
        "O PhotoPrism pesquisará pelos nomes das pastas e caminhos de arquivos correspondentes.\n\n"
        "Retorne APENAS um objeto JSON válido no formato:\n"
        "{\n"
        "  \"q\": \"string de busca traduzida para o PhotoPrism (deixe vazio se houver ambiguidade não resolvida)\",\n"
        "  \"response\": \"Sua resposta amigável ao usuário em português\"\n"
        "}"
    )

    # 3. Format contents list with chat history
    contents_list = []
    for turn in history:
        role = "user" if turn.get("sender") == "user" else "model"
        contents_list.append({
            "role": role,
            "parts": [{"text": turn.get("text", "")}]
        })
    # Add current query
    contents_list.append({
        "role": "user",
        "parts": [{"text": query_text}]
    })

    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"
    translated_q = ""
    chat_response = ""
    
    try:
        async with session.post(
            gemini_url,
            json={
                "contents": contents_list,
                "systemInstruction": {
                    "parts": [{"text": system_instruction}]
                },
                "generationConfig": {"responseMimeType": "application/json"},
            },
            timeout=15
        ) as resp:
            if resp.status == 200:
                resp_json = await resp.json()
                try:
                    text_out = resp_json["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(text_out)
                    translated_q = parsed.get("q", "")
                    chat_response = parsed.get("response", "")
                except (KeyError, IndexError, ValueError) as err:
                    _LOGGER.error("Failed to parse Gemini response: %s (Raw: %s)", err, resp_json)
            else:
                body = await resp.text()
                _LOGGER.error("Gemini API call failed: status %d, body: %s", resp.status, body)
    except Exception as exc:
        _LOGGER.exception("Error calling Gemini API")

    if not chat_response:
        chat_response = f"Buscando fotos por: {query_text}"
        translated_q = query_text

    # If the AI concluded that query is ambiguous (empty search string), return empty results immediately
    if not translated_q:
        connection.send_result(
            msg["id"],
            {
                "translated_query": "",
                "explanation": chat_response,
                "response": chat_response,
                "photos": []
            }
        )
        return

    _LOGGER.info("Translated search query: '%s' -> '%s' (Response: %s)", query_text, translated_q, chat_response)

    # 4. Search PhotoPrism using the translated query
    photos_endpoint = f"{url}/api/v1/photos"
    params = {
        "q": translated_q,
        "count": 10,
        "primary": "true",
        "merged": "true",
        "order": "newest",
        "public": "true",
    }

    try:
        async with session.get(photos_endpoint, headers=headers, params=params, ssl=False, timeout=20) as resp:
            if resp.status != 200:
                connection.send_error(
                    msg["id"], 
                    "photoprism_error", 
                    f"PhotoPrism search failed with status {resp.status}"
                )
                return
            
            photos_data = await resp.json()
            results = []
            photos_list = photos_data if isinstance(photos_data, list) else (photos_data.get("photos") or photos_data.get("result") or [])
            
            for item in photos_list:
                file_hash = item.get("Hash") or ""
                if not file_hash and item.get("Files"):
                    file_hash = item["Files"][0].get("Hash") or ""
                
                if not file_hash:
                    continue
                    
                results.append({
                    "hash": file_hash,
                    "title": item.get("Title") or "Untitled",
                    "description": item.get("Description") or "",
                    "taken_at": item.get("TakenAt") or "",
                    "place": item.get("PlaceName") or item.get("PlaceCountry") or "",
                    "labels": [label.get("Name") for label in item.get("Labels", [])] if item.get("Labels") else [],
                    "thumb_url": f"/api/photoprism_search/image/{entry_id}/{file_hash}",
                    "download_url": f"/api/photoprism_search/image/{entry_id}/{file_hash}?download=true",
                })
            
            connection.send_result(
                msg["id"],
                {
                    "translated_query": translated_q,
                    "explanation": chat_response,
                    "response": chat_response,
                    "photos": results
                }
            )
            
    except Exception as exc:
        _LOGGER.exception("Error searching PhotoPrism")
        connection.send_error(msg["id"], "search_failed", str(exc))

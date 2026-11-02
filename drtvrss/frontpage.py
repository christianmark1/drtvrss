"""Mirror the rows shown on the DRTV front page.

DRTV's front page is served by the "Massive" platform API. The page endpoint
returns the rows ("entries"), but only prefetches items for the first 15 lists.
The remaining lists are fetched separately through the lists endpoint.
"""

from collections import namedtuple
from time import time
import asyncio
import ssl

import certifi
from aiohttp import ClientSession, TCPConnector

from .drtv import is_geo_restricted, CUSTOM_FIELDS, TITLE

ssl_context = ssl.create_default_context(cafile=certifi.where())

API = "https://prod95.dr-massive.com/api"
# The query parameters the DRTV web client sends along on every API call
API_PARAMS = {
    "device": "web_browser",
    "ff": "idp,ldp,rpt",
    "geoLocation": "dk",
    "isDeviceAbroad": "false",
    "lang": "da",
    "segments": "drtv,optedout",
    "sub": "Anonymous",
}
ITEMS_PER_ROW = 24
# The API refuses anything above 15
MAX_LIST_PREFETCH = 15
CACHE_SECONDS = 30 * 60

FrontpageItem = namedtuple(
    "FrontpageItem", ["title", "wallpaper", "description", "geo_restricted", "url"]
)
FrontpageRow = namedtuple("FrontpageRow", ["title", "items"])

frontpage_rows: list[FrontpageRow] = []
frontpage_age: float = 0

# Maps a DRTV item id (show, season or episode) to its DRTV path, e.g.
# "627993" -> "/serie/brutale-boern_627993". Used to resolve the show a
# season or episode belongs to, since the front page only gives us the id.
item_paths: dict[str, str] = {}


async def api_get(session: ClientSession, endpoint: str, **params) -> dict:
    print("Fetching DRTV API", endpoint)
    async with session.get(
        API + endpoint, params={**API_PARAMS, **params}, timeout=10
    ) as r:
        r.raise_for_status()
        return await r.json()


def slug(path: str) -> str:
    return path.split("/")[-1]


def item_url(item: dict):
    """Translate a DRTV item into a link on this site.
    Returns None for item types we have no page for."""
    kind = item.get("type")
    path = item.get("path", "")
    if kind == "show":
        return "/" + slug(path) + "/"
    if kind == "season":
        return "/saeson/" + slug(path)
    if kind == "episode":
        return "/episode/" + slug(path)
    if kind == "program":
        return "/program/" + slug(path)
    if kind == "channel" and item.get("channelShortCode"):
        return "/live/" + item["channelShortCode"]
    return None


def parse_row(entry: dict, items: list[dict]):
    row_items = []
    for item in items:
        url = item_url(item)
        if url is None:
            continue
        images = item.get("images", {})
        row_items.append(
            FrontpageItem(
                item.get(TITLE, ""),
                images.get("wallpaper") or images.get("tile"),
                item.get("shortDescription", ""),
                is_geo_restricted(item.get(CUSTOM_FIELDS, {})),
                url,
            )
        )
    if len(row_items) == 0:
        return None
    # The hero row at the top has no public title, only DRTV's internal list name
    title = entry.get(TITLE) or "Fremhævet"
    return FrontpageRow(title, row_items)


async def fetch_frontpage() -> list[FrontpageRow]:
    async with ClientSession(connector=TCPConnector(ssl=ssl_context)) as session:
        page = await api_get(
            session,
            "/page",
            path="/",
            list_page_size=ITEMS_PER_ROW,
            max_list_prefetch=MAX_LIST_PREFETCH,
            text_entry_format="html",
        )
        entries = [
            e
            for e in page["entries"]
            if e.get("type") == "ListEntry" and e.get("list") is not None
        ]

        # Lists past the prefetch limit come back empty, fetch those separately
        async def fetch_items(entry):
            lst = entry["list"]
            if lst.get("items") or not lst.get("size"):
                return lst.get("items", [])
            try:
                fetched = await api_get(
                    session,
                    "/lists/" + str(lst["id"]),
                    page=1,
                    page_size=ITEMS_PER_ROW,
                )
                return fetched.get("items", [])
            except Exception as e:
                print("Could not fetch list", lst.get("id"), e)
                return []

        all_items = await asyncio.gather(*[fetch_items(e) for e in entries])

    rows = []
    for entry, items in zip(entries, all_items):
        for item in items:
            if "id" in item and "path" in item:
                item_paths[str(item["id"])] = item["path"]
        row = parse_row(entry, items)
        if row is not None:
            rows.append(row)
    return rows


async def get_frontpage() -> list[FrontpageRow]:
    global frontpage_rows, frontpage_age
    if frontpage_age + CACHE_SECONDS < time():
        try:
            frontpage_rows = await fetch_frontpage()
            frontpage_age = time()
        except Exception as e:
            print("Could not fetch DRTV frontpage", e)
            # Retry in a minute rather than hammering DRTV on every request
            frontpage_age = time() - CACHE_SECONDS + 60
    return frontpage_rows


async def get_show_slug(itemid: str) -> str:
    """Given the id of a season or episode, find the slug of its show,
    e.g. "627994" -> "brutale-boern_627993"."""
    async with ClientSession(connector=TCPConnector(ssl=ssl_context)) as session:
        item = await api_get(session, "/items/" + itemid)
        if item.get("type") == "show":
            return slug(item["path"])
        show_id = str(item["showId"])
        if show_id not in item_paths:
            show = await api_get(session, "/items/" + show_id)
            item_paths[show_id] = show["path"]
        return slug(item_paths[show_id])

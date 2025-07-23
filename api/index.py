import asyncio
     import aiohttp
     import re
     import xml.etree.ElementTree as ET
     from datetime import datetime
     import json
     import logging
     from typing import List, Dict, Optional
     import openai
     from flask import Flask, request, jsonify
     import os
     from urllib.parse import quote

     # Configure logging
     logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
     logger = logging.getLogger(__name__)

     # Initialize Flask app
     app = Flask(__name__)

     # Configuration
     OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "your-openai-api-key")  # Use environment variable
     EPG_URL = "https://iptv-org.github.io/epg/guide.xml"  # Example EPG source
     LOGO_BASE_URL = "https://iptv-org.github.io/logos/"  # Fallback logo source
     WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
     PLAYLIST_STORAGE = "/tmp/playlists.json"  # Temporary storage for Vercel

     # In-memory storage for playlists
     combined_playlist = []
     channel_status_cache = {}
     last_sync_time = None

     def save_playlist_urls(urls: List[str]):
         """Save playlist URLs to a JSON file."""
         try:
             with open(PLAYLIST_STORAGE, "w") as f:
                 json.dump(urls, f)
         except Exception as e:
             logger.error(f"Error saving playlist URLs: {e}")

     def load_playlist_urls() -> List[str]:
         """Load playlist URLs from a JSON file."""
         try:
             if os.path.exists(PLAYLIST_STORAGE):
                 with open(PLAYLIST_STORAGE, "r") as f:
                     return json.load(f)
             return []
         except Exception as e:
             logger.error(f"Error loading playlist URLs: {e}")
             return []

     async def fetch_playlist(url: str) -> Optional[str]:
         """Fetch an M3U playlist from a given URL."""
         try:
             async with aiohttp.ClientSession() as session:
                 async with session.get(url, timeout=10) as response:
                     if response.status == 200:
                         return await response.text()
                     else:
                         logger.error(f"Failed to fetch playlist {url}: Status {response.status}")
                         return None
         except Exception as e:
             logger.error(f"Error fetching playlist {url}: {e}")
             return None

     def parse_m3u(content: str) -> List[Dict]:
         """Parse an M3U playlist into a list of channel dictionaries."""
         channels = []
         lines = content.splitlines()
         current_channel = {}
         
         for line in lines:
             line = line.strip()
             if line.startswith("#EXTINF"):
                 match = re.match(r'#EXTINF:-?1\s*,?\s*(tvg-id="([^"]*)")?\s*(tvg-name="([^"]*)")?\s*(tvg-logo="([^"]*)")?\s*(group-title="([^"]*)")?\s*,(.+)', line)
                 if match:
                     current_channel = {
                         "tvg-id": match.group(2) or "",
                         "tvg-name": match.group(4) or "",
                         "tvg-logo": match.group(6) or "",
                         "group-title": match.group(8) or "Uncategorized",
                         "name": match.group(9).strip(),
                         "url": ""
                     }
             elif line and not line.startswith("#") and current_channel:
                 current_channel["url"] = line
                 channels.append(current_channel)
                 current_channel = {}
         
         return channels

     async def check_channel_status(url: str) -> bool:
         """Check if a channel is live."""
         try:
             async with aiohttp.ClientSession() as session:
                 async with session.head(url, timeout=5) as response:
                     return response.status in (200, 206)
         except Exception:
             return False

     async def categorize_channel(channel_name: str) -> str:
         """Use OpenAI API to categorize a channel."""
         try:
             openai.api_key = OPENAI_API_KEY
             prompt = f"Classify the TV channel '{channel_name}' into one of these categories: Sports, Music, News, Entertainment, Kids, or Other. Return only the category name."
             response = await asyncio.get_event_loop().run_in_executor(
                 None,
                 lambda: openai.Completion.create(
                     model="text-davinci-003",
                     prompt=prompt,
                     max_tokens=10,
                     temperature=0.5
                 )
             )
             category = response.choices[0].text.strip()
             return category if category in ["Sports", "Music", "News", "Entertainment", "Kids", "Other"] else "Other"
         except Exception as e:
             logger.error(f"Error categorizing channel {channel_name}: {e}")
             return "Other"

     async def is_english_channel(channel_name: str) -> bool:
         """Use OpenAI API to determine if a channel is in English."""
         try:
             prompt = f"Is the TV channel '{channel_name}' primarily in English? Return 'Yes' or 'No'."
             response = await asyncio.get_event_loop().run_in_executor(
                 None,
                 lambda: openai.Completion.create(
                     model="text-davinci-003",
                     prompt=prompt,
                     max_tokens=5,
                     temperature=0.5
                 )
             )
             return response.choices[0].text.strip().lower() == "yes"
         except Exception as e:
             logger.error(f"Error checking language for {channel_name}: {e}")
             return False

     async def fetch_epg() -> Dict:
         """Fetch and parse EPG data."""
         try:
             async with aiohttp.ClientSession() as session:
                 async with session.get(EPG_URL, timeout=10) as response:
                     if response.status == 200:
                         epg_data = await response.text()
                         tree = ET.fromstring(epg_data)
                         epg = {}
                         for channel in tree.findall(".//channel"):
                             channel_id = channel.get("id")
                             epg[channel_id] = []
                             for programme in tree.findall(f".//programme[@channel='{channel_id}']"):
                                 epg[channel_id].append({
                                     "start": programme.get("start"),
                                     "stop": programme.get("stop"),
                                     "title": programme.find("title").text if programme.find("title") is not None else ""
                                 })
                         return epg
                     else:
                         logger.error(f"Failed to fetch EPG: Status {response.status}")
                         return {}
         except Exception as e:
             logger.error(f"Error fetching EPG: {e}")
             return {}

     async def fetch_logo(channel_name: str, tvg_id: str) -> str:
         """Fetch channel logo from Wikipedia or fallback to default source."""
         try:
             async with aiohttp.ClientSession() as session:
                 # Search Wikipedia for the channel
                 params = {
                     "action": "query",
                     "format": "json",
                     "list": "search",
                     "srsearch": f"{channel_name} television channel logo",
                     "srprop": "snippet",
                     "srlimit": 1
                 }
                 async with session.get(WIKIPEDIA_API_URL, params=params) as response:
                     if response.status == 200:
                         data = await response.json()
                         search_results = data.get("query", {}).get("search", [])
                         if search_results:
                             # Fetch the page for the first result
                             page_title = search_results[0]["title"]
                             params = {
                                 "action": "query",
                                 "format": "json",
                                 "titles": page_title,
                                 "prop": "images",
                                 "imlimit": 1
                             }
                             async with session.get(WIKIPEDIA_API_URL, params=params) as img_response:
                                 if img_response.status == 200:
                                     img_data = await img_response.json()
                                     pages = img_data.get("query", {}).get("pages", {})
                                     for page_id, page in pages.items():
                                         images = page.get("images", [])
                                         if images:
                                             img_title = images[0]["title"]
                                             # Get image URL
                                             params = {
                                                 "action": "query",
                                                 "format": "json",
                                                 "titles": img_title,
                                                 "prop": "imageinfo",
                                                 "iiprop": "url"
                                             }
                                             async with session.get(WIKIPEDIA_API_URL, params=params) as url_response:
                                                 if url_response.status == 200:
                                                     url_data = await url_response.json()
                                                     img_pages = url_data.get("query", {}).get("pages", {})
                                                     for img_page_id, img_page in img_pages.items():
                                                         img_info = img_page.get("imageinfo", [{}])[0]
                                                         return img_info.get("url", "")
         except Exception as e:
             logger.error(f"Error fetching logo for {channel_name}: {e}")
         
         # Fallback to default logo source
         return f"{LOGO_BASE_URL}{tvg_id}.png" if tvg_id else ""

     async def sync_playlists(playlist_urls: List[str]):
         """Sync and combine playlists, filter English channels, categorize, and add EPG/logos."""
         global combined_playlist, last_sync_time
         channels = []
         channel_counts = {}
         
         # Fetch all playlists
         tasks = [fetch_playlist(url) for url in playlist_urls]
         results = await asyncio.gather(*tasks, return_exceptions=True)
         
         for content in results:
             if isinstance(content, str):
                 channels.extend(parse_m3u(content))
         
         # Filter English channels and categorize
         english_channels = []
         for channel in channels:
             if await is_english_channel(channel["name"]):
                 category = await categorize_channel(channel["name"])
                 if category != "Other":  # Exclude movies/series
                     channel["group-title"] = category
                     english_channels.append(channel)
         
         # Handle duplicate channels
         final_channels = []
         for channel in english_channels:
             name = channel["name"]
             channel_counts[name] = channel_counts.get(name, 0) + 1
             if channel_counts[name] > 1:
                 channel["name"] = f"{name} {channel_counts[name]}"
             final_channels.append(channel)
         
         # Check live status and add logos/EPG
         epg_data = await fetch_epg()
         status_tasks = [check_channel_status(channel["url"]) for channel in final_channels]
         logo_tasks = [fetch_logo(channel["name"], channel["tvg-id"]) for channel in final_channels]
         statuses = await asyncio.gather(*status_tasks, return_exceptions=True)
         logos = await asyncio.gather(*logo_tasks, return_exceptions=True)
         
         for channel, status, logo in zip(final_channels, statuses, logos):
             channel["status"] = "Live" if status else "Offline"
             channel["tvg-logo"] = logo if isinstance(logo, str) else f"{LOGO_BASE_URL}{channel['tvg-id']}.png" if channel["tvg-id"] else ""
             channel["epg"] = epg_data.get(channel["tvg-id"], [])
         
         combined_playlist = final_channels
         last_sync_time = datetime.now()
         logger.info("Playlists synced successfully")

     def generate_m3u() -> str:
         """Generate M3U playlist from combined channels."""
         m3u = "#EXTM3U\n"
         for channel in combined_playlist:
             m3u += f'#EXTINF:-1 tvg-id="{channel["tvg-id"]}" tvg-name="{channel["tvg-name"]}" tvg-logo="{channel["tvg-logo"]}" group-title="{channel["group-title"]}",{channel["name"]} ({channel["status"]})\n'
             m3u += f"{channel['url']}\n"
         return m3u

     @app.route("/add_playlists", methods=["POST"])
     async def add_playlists():
         """API endpoint to add playlist URLs and trigger sync."""
         data = request.get_json()
         playlist_urls = data.get("urls", [])
         if not playlist_urls:
             return jsonify({"error": "No playlist URLs provided"}), 400
         save_playlist_urls(playlist_urls)
         await sync_playlists(playlist_urls)
         return jsonify({"message": "Playlists added and synced", "last_sync": str(last_sync_time)}), 200

     @app.route("/sync", methods=["POST"])
     async def sync():
         """API endpoint to manually trigger playlist sync."""
         playlist_urls = load_playlist_urls()
         if not playlist_urls:
             return jsonify({"error": "No playlist URLs stored. Please add playlists first."}), 400
         await sync_playlists(playlist_urls)
         return jsonify({"message": "Playlists synced", "last_sync": str(last_sync_time)}), 200

     @app.route("/playlist.m3u", methods=["GET"])
     def get_playlist():
         """API endpoint to get the combined M3U playlist."""
         if not combined_playlist:
             return jsonify({"error": "No playlist available. Please add playlists first."}), 400
         return generate_m3u(), 200, {"Content-Type": "text/plain"}

     # For Vercel, export the app as a serverless function
     if __name__ == "__main__":
         app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

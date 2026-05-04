import requests
import os
import logging
import time

class LastFmAPIClient:
    BASE_URL = "http://ws.audioscrobbler.com/2.0/"

    def __init__(self):
        self.api_key = os.getenv("LASTFM_API_KEY")
        if not self.api_key:
            raise ValueError("LASTFM_API_KEY must be set in environment variables.")

    def _make_request(self, params):
        """Helper to make request with retries/error handling."""
        params['api_key'] = self.api_key
        params['format'] = 'json'
        
        try:
            response = requests.get(self.BASE_URL, params=params, timeout=10)
            
            if response.status_code == 429:
                logging.warning("Rate limited (429). Waiting 5 seconds...")
                time.sleep(5)
                # Retry once
                response = requests.get(self.BASE_URL, params=params, timeout=10)

            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"Last.fm API Request Error: {e}")
            raise e

    def get_artist_info(self, artist_name, similar_limit=20, tags_limit=5):
        """
        Fetches similar artists AND top tags for the source artist.
        Returns a dict: {"similar": [...], "tags": [...]}
        """
        # 1. Get Similar Artists
        similar_params = {
            'method': 'artist.getsimilar',
            'artist': artist_name,
            'limit': similar_limit,
            'autocorrect': 1
        }
        
        # 2. Get Top Tags
        tags_params = {
            'method': 'artist.gettoptags',
            'artist': artist_name,
            'autocorrect': 1
        }
        
        try:
            # Fetch Similar
            similar_data = self._make_request(similar_params)
            similar_artists_list = []
            
            if 'similarartists' in similar_data and 'artist' in similar_data['similarartists']:
                rank = 1
                for item in similar_data['similarartists']['artist']:
                    # Some items might be empty or malformed
                    if 'name' in item:
                        similar_artists_list.append({
                            "name": item['name'],
                            "mbid": item.get('mbid', ''),
                            "match": item.get('match', '0'),
                            "rank": rank
                        })
                        rank += 1
            
            # Fetch Tags (Genres)
            tags_data = self._make_request(tags_params)
            tags_list = []
            
            if 'toptags' in tags_data and 'tag' in tags_data['toptags']:
                # The API returns all tags, we slice manually if limit parameter didn't work (it usually doesn't for tags on some endpoints)
                # Actually artist.gettoptags DOES NOT support limit officially in some docs, but let's just slice the list.
                all_tags = tags_data['toptags']['tag']
                # Sometimes it's a single dict if only 1 tag
                if isinstance(all_tags, dict):
                    all_tags = [all_tags]
                
                # Sort by count if available? Usually sorted by rank.
                for tag in all_tags[:tags_limit]:
                    if 'name' in tag:
                        tags_list.append(tag['name'])

            return {
                "similar_artists": similar_artists_list,
                "tags": tags_list
            }

        except Exception as e:
            logging.error(f"Error fetching info for {artist_name}: {e}")
            raise e

    def search_artist(self, artist_name):
        """
        Search for an artist to find canonical name.
        Returns a list of candidate names (up to 5).
        """
        params = {
            'method': 'artist.search',
            'artist': artist_name,
            'limit': 5
        }
        candidates = []
        try:
            data = self._make_request(params)
            if 'results' in data and 'artistmatches' in data['results'] and 'artist' in data['results']['artistmatches']:
                artists = data['results']['artistmatches']['artist']
                # artistmatches['artist'] can be a list or single dict or empty
                if isinstance(artists, list):
                    for item in artists:
                        if 'name' in item:
                            candidates.append(item['name'])
                elif isinstance(artists, dict):
                    if 'name' in artists:
                        candidates.append(artists['name'])
            return candidates
        except Exception as e:
            logging.error(f"Search failed for {artist_name}: {e}")
            return []

import httpx
from metadata.metadata_provider_base import MetadataProvider
from models.movie import Movie
from models.series import Series

class TMDB(MetadataProvider):
    def __init__(self, config, http_client: httpx.AsyncClient):
        super().__init__(config)
        self.http_client = http_client

    async def _fetch_data(self, url):
        try:
            response = await self.http_client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.RequestError as e:
            self.logger.error(f"Error requesting TMDB metadata: {e}")
            return None
        except httpx.HTTPStatusError as e:
            self.logger.error(f"TMDB request failed with status {e.response.status_code}")
            return None

    async def get_metadata(self, id, media_type):
        self.logger.info("Getting metadata for " + media_type + " with id " + id)

        full_id = id.split(":")
        self.logger.debug("Full id: " + str(full_id))
        
        data = None
        result = None
        
        if full_id[0].startswith("tt"):
            url = f"https://api.themoviedb.org/3/find/{full_id[0]}?api_key={self.config['tmdbApi']}&external_source=imdb_id&language=es-ES"
            data = await self._fetch_data(url)

            if not data:
                return None

            if media_type == "movie" and data.get("movie_results"):
                result = Movie(
                    id=data["movie_results"][0]["id"],
                    titles=[self.replace_weird_characters(data["movie_results"][0]["title"])],
                    year=data["movie_results"][0]["release_date"][:4],
                    languages='es-ES'
                )
            elif media_type == "series" and data.get("tv_results"):
                season_num = 1
                episode_num = 1
                if len(full_id) > 1 and full_id[1].isdigit():
                    season_num = int(full_id[1])
                if len(full_id) > 2 and full_id[2].isdigit():
                    episode_num = int(full_id[2])

                result = Series(
                    id=data["tv_results"][0]["id"],
                    titles=[self.replace_weird_characters(data["tv_results"][0]["name"])],
                    season=season_num,
                    episode=episode_num,
                    languages='es-ES'
                )

        elif full_id[0] == "tmdb" or full_id[0].isdigit():
            if full_id[0] == "tmdb":
                tmdb_id = full_id[1]
                offset = 1
            else:
                tmdb_id = full_id[0]
                offset = 0

            url = ""
            if media_type == "movie":
                url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={self.config['tmdbApi']}&language=es-ES"
            elif media_type == "series":
                url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={self.config['tmdbApi']}&language=es-ES"
            
            if url:
                data = await self._fetch_data(url)
                
                if media_type == "movie" and data:
                    result = Movie(
                        id=data["id"],
                        titles=[self.replace_weird_characters(data["title"])],
                        year=data.get("release_date", "")[:4],
                        languages='es-ES'
                    )
                elif media_type == "series" and data:
                    season_num = 1
                    episode_num = 1
                    
                    if len(full_id) > offset + 1 and full_id[offset + 1].isdigit():
                        season_num = int(full_id[offset + 1])
                    if len(full_id) > offset + 2 and full_id[offset + 2].isdigit():
                        episode_num = int(full_id[offset + 2])
                    
                    result = Series(
                        id=data["id"],
                        titles=[self.replace_weird_characters(data["name"])],
                        season=season_num,
                        episode=episode_num,
                        languages='es-ES'
                    )
        
        if result:
            self.logger.info("Got metadata for " + media_type + " with id " + id)
        else:
            self.logger.warning("Could not find metadata for " + media_type + " with id " + id)

        return result

from __future__ import annotations

import base64
import hashlib
import json
import uuid
import os
import re
import time
import secrets
import string
from pathlib import Path
from bs4 import BeautifulSoup
from collections import defaultdict
from datetime import datetime, timedelta
from urllib.parse import urlencode, quote, urlparse
from playwright.sync_api import sync_playwright
import http.cookiejar as cookiejar

from typing import Union, Dict

import click
import jsonpickle
import requests
import random
from langcodes import Language
from tldextract import tldextract
from click.core import ParameterSource

from vinetrimmer.objects import TextTrack, Title, Tracks, Track
from vinetrimmer.objects.tracks import MenuTrack
from vinetrimmer.services.BaseService import BaseService
from vinetrimmer.utils.Logger import Logger
from vinetrimmer.utils.widevine.device import BaseDevice, LocalDevice, RemoteDevice
from datetime import datetime, timezone

class Amazon(BaseService):
    """
    Service code for Amazon VOD (https://amazon.com) and Amazon Prime Video (https://primevideo.com).

    \b
    Authorization: Cookies
    Security: UHD@L1/SL3000 FHD@L3(ChromeCDM) FHD@L3, Maintains their own license server like Netflix, be cautious.

    \b
    Region is chosen automatically based on domain extension found in cookies.
    Prime Video specific code will be run if the ASIN is detected to be a prime video variant.
    Use 'Amazon Video ASIN Display' for Tampermonkey addon for ASIN
    https://greasyfork.org/en/scripts/381997-amazon-video-asin-display
    
    vt dl --list -z uk -q 1080 Amazon B09SLGYLK8 
    SDR:

    DASH = 15000Kbps, CVBR+CBR
    poetry run vt dl --keys -al te --proxy in-surf -q 2160p --vcodec h265 Amazon -b CVBR+CBR -mt DASH https://www.primevideo.com/detail/amzn1.dv.gti.ec11e6ee-b332-4b6c-92a0-da5300490ea5

    ISM = 15000Kbps, CVBR+CBR
    poetry run vt dl --keys -al te --proxy in-surf -q 2160p --vcodec h265 Amazon -b CVBR+CBR -mt SmoothStreaming https://www.primevideo.com/detail/amzn1.dv.gti.ec11e6ee-b332-4b6c-92a0-da5300490ea5

    ISM = 15000Kbps, CBR
    poetry run vt dl --keys -al te --proxy in-surf -q 2160p --vcodec h265 Amazon -b CBR -mt SmoothStreaming https://www.primevideo.com/detail/amzn1.dv.gti.ec11e6ee-b332-4b6c-92a0-da5300490ea5

    DASH = None, CBR
    poetry run vt dl --keys -al te --proxy in-surf -q 2160p --vcodec h265 Amazon -b CBR -mt DASH https://www.primevideo.com/detail/amzn1.dv.gti.ec11e6ee-b332-4b6c-92a0-da5300490ea5

    HDR10:

    DASH = 20000Kbps, CVBR+CBR
    poetry run vt dl --keys -al te --proxy in-surf -q 2160p -r hdr10 --vcodec h265 Amazon -b CVBR+CBR -mt DASH https://www.primevideo.com/detail/amzn1.dv.gti.ec11e6ee-b332-4b6c-92a0-da5300490ea5

    ISM = 20000Kbps, CVBR+CBR
    poetry run vt dl --keys -al te --proxy in-surf -q 2160p -r hdr10 --vcodec h265 Amazon -b CVBR+CBR -mt SmoothStreaming https://www.primevideo.com/detail/amzn1.dv.gti.ec11e6ee-b332-4b6c-92a0-da5300490ea5

    DASH = 15000Kbps, CBR
    poetry run vt dl --keys -al te --proxy in-surf -q 2160p -r hdr10 --vcodec h265 Amazon -b CBR -mt DASH https://www.primevideo.com/detail/amzn1.dv.gti.ec11e6ee-b332-4b6c-92a0-da5300490ea5

    ISM = 20000Kbps, CBR
    poetry run vt dl --keys -al te --proxy in-surf -q 2160p -r hdr10 --vcodec h265 Amazon -b CBR -mt SmoothStreaming https://www.primevideo.com/detail/amzn1.dv.gti.ec11e6ee-b332-4b6c-92a0-da5300490ea5
    """

    ALIASES = ["AMZN", "amazon"]
    TITLE_RE = [
        r"^(?:https?://(?:www\.)?(?P<domain>amazon\.(?P<region>com|co\.uk|de|co\.jp)|primevideo\.com)(?:/.+)?/)?(?P<id>[A-Z0-9]{10,}|amzn1\.dv\.gti\.[a-f0-9-]+)", r"^(?:https?://(?:www\.)?(?P<domain>amazon\.(?P<region>com|co\.uk|de|co\.jp)|primevideo\.com)(?:/[^?]*)?(?:\?gti=)?)(?P<id>[A-Z0-9]{10,}|amzn1\.dv\.gti\.[a-f0-9-]+)"]

    REGION_TLD_MAP = {
        "au": "com.au",
        "br": "com.br",
        "jp": "co.jp",
        "mx": "com.mx",
        "tr": "com.tr",
        "gb": "co.uk",
        "us": "com",
    }
    VIDEO_RANGE_MAP = {
        "SDR": "None",
        "HDR10": "Hdr10",
        "DV": "DolbyVision",
    }

    @staticmethod
    @click.command(name="Amazon", short_help="https://amazon.com, https://primevideo.com", help=__doc__)
    @click.argument("title", type=str, required=False)
    @click.option("-b", "--bitrate", default="CBR",
                  type=click.Choice(["CVBR", "CBR", "CVBR+CBR"], case_sensitive=False),
                  help="Video Bitrate Mode to download in. CVBR=Constrained Variable Bitrate, CBR=Constant Bitrate.")
    @click.option("-p", "--player", default="html5",
                  type=click.Choice(["html5", "xp"], case_sensitive=False),
                  help="Video playerType to download in. html5, xp.")
    @click.option("-c", "--cdn", default="Akamai", type=str,
                  help="CDN to download from, defaults to the CDN with the highest weight set by Amazon.") # Akamai, Cloudfront
    # UHD, HD, SD. UHD only returns HEVC, ever, even for <=HD only content
    @click.option("-vq", "--vquality", default="HD",
                  type=click.Choice(["SD", "HD", "UHD"], case_sensitive=False),
                  help="Manifest quality to request.")
    @click.option("-s", "--single", is_flag=True, default=False,
                  help="Force single episode/season instead of getting series ASIN.")
    @click.option("-mt", "--manifest_type", default="DASH,SmoothStreaming",
                  type=click.Choice(["DASH", "SmoothStreaming", "DASH,SmoothStreaming"], case_sensitive=False),
                  help="Define Streaming technology DASH', SmoothStreaming, DASH,SmoothStreaming.")
    @click.option("-am", "--amanifest", default="CVBR",
                  type=click.Choice(["CVBR", "CBR", "H265"], case_sensitive=False),
                  help="Manifest to use for audio. Defaults to H265 if the video manifest is missing 640k audio.")
    @click.option("-aq", "--aquality", default="SD",
                  type=click.Choice(["SD", "HD", "UHD"], case_sensitive=False),
                  help="Manifest quality to request for audio. Defaults to the same as --quality.")
    @click.option("-nr", "--no_true_region",is_flag=True, default=False,
                  help="Skip checking true current region.")
    @click.option("-cv", "--color-variant", default=None,
                  type=click.Choice(["COLOR", "BW"], case_sensitive=False),
                  help="Color variant to choose when both BW (black-and-white) and COLOR variants are available.")
    @click.pass_context
    def cli(ctx, **kwargs):
        return Amazon(ctx, **kwargs)

    def __init__(self, ctx, title, bitrate: str, player: str, cdn: str, vquality: str, single: bool,
                 amanifest: str, aquality: str, no_true_region: bool, manifest_type="DASH,SmoothStreaming", color_variant=None):
        m = self.parse_title(ctx, title)
        self.ctx_data = ctx.obj
        self.full_cfg = self.ctx_data.full_config
        self.bitrate = bitrate
        self.player = player
        self.bitrate_source = ctx.get_parameter_source("bitrate")
        self.cdn = cdn
        self.vquality = vquality
        self.vquality_source = ctx.get_parameter_source("vquality")
        self.single = single
        self.manifest_type_tech = [
            x.strip()
            for x in ctx.params.get("manifest_type", "DASH,SmoothStreaming").split(",")
        ]
        self.amanifest = amanifest
        self.aquality = aquality
        self.no_true_region = no_true_region
        if color_variant:
            self.requestd_color_variant = color_variant.upper()
        else:    
            self.requestd_color_variant = None
        
        super().__init__(ctx)

        if ctx.get_parameter_source("color_variant") != ParameterSource.DEFAULT:
            self.log.info(f" + Color variant set to {self.requestd_color_variant}")

        assert ctx.parent is not None

        self.vcodec = ctx.parent.params["vcodec"] or "H264"
        self.range = ctx.parent.params["range_"] or "SDR"
        self.chapters_only = ctx.parent.params["chapters_only"]
        self.atmos = ctx.parent.params["atmos"]
        self.quality = ctx.parent.params.get("quality") or 1080

        self.cdm = ctx.obj.cdm
        self.profile = ctx.obj.profile
        self.playready = ctx.obj.cdm.device.type == LocalDevice.Types.PLAYREADY

        self.region: Dict[str, str] = {}
        self.endpoints: Dict[str, str] = {}
        self.device: Dict[str, str] = {}

        self.pv = False
        self.rpv = False
        self.event = False
        self.device_token = None
        self.device_id: None
        self.customer_id = None
        self.client_id = "f22dbddb-ef2c-48c5-8876-bed0d47594fd"  # browser client id

        if self.vquality_source != ParameterSource.COMMANDLINE:
            if 0 < self.quality <= 576 and self.range == "SDR":
                self.log.info(" + Setting manifest quality to SD")
                self.vquality = "SD"

            if self.quality > 1080:
                self.log.info(" + Setting manifest quality to UHD to be able to get 2160p video track")
                self.vquality = "UHD"

        self.vquality = self.vquality or "HD"

        if self.vquality == "UHD":
            self.vcodec = "H265"

        if self.bitrate_source != ParameterSource.COMMANDLINE:
            if self.vcodec == "H265" and self.range == "SDR" and self.bitrate != "CVBR+CBR":
                self.bitrate = "CVBR+CBR"
                self.log.info(" + Changed bitrate mode to CVBR+CBR to be able to get H.265 SDR video track")

            if self.vquality == "UHD" and self.range != "SDR" and self.bitrate != "CBR":
                self.bitrate = "CVBR+CBR"
                self.log.info(f" + Changed bitrate mode to CBR to be able to get highest quality UHD {self.range} video track")

        self.orig_bitrate = self.bitrate

        self.configure()

    # Abstracted functions
    
    def _attach_mpd(self, tracks, mpd_url, mpd_type):
        for track in tracks:
            # store MPD info safely
            track.mpd_url = mpd_url
            track.mpd_type = mpd_type  # "HDR10", "DV", "SDR", "AUDIO", etc.
    
    def get_titles(self):
        if self.domain == "primevideo" and not self.pv:
            raise self.log.exit("Wrong titleID for primevideo cookies")
        if self.device_token:    
            title_id = self.title
            if not self.title.startswith("amzn"):
                params = {
                    'jic': '8|EgRzdm9k',
                }
                response = self.session.get(f"https://{self.region['base']}/gp/video/detail/{self.title}", params=params)
                soup = BeautifulSoup(response.text, "html.parser")
                for script in soup.find_all("script"):
                    content = script.string or script.text
                    if not content:
                        continue
                    if "pageTitleId" not in content:
                        continue
                    data = json.loads(content)
                    if "atf" in data["init"]["preparations"]["body"]:
                        state = data["init"]["preparations"]["body"]["atf"]["state"]
                        main_asin = state["pageTitleId"]   
                        title_id = main_asin
            params = {
                "clientName": self.device["app_name"],
                "contentType": "VOD",
                "deviceId": self.device_id,
                "deviceTypeID": self.device["device_type"],
                "dynamicFeatures": "DetailsAtf",
                "featureScheme": "tv-android-features-v11.1",
                "firmware": "fmw:33-app:3.0.451.1281",
                "format": "json",
                "isConsumptionOnlyMode": "false",
                "isGatedVamEnabled": "true",
                "isGeneratedRequest": "false",
                "itemId": title_id,
                "osLocale": "en_US",
                "overridePCON": "false",
                "priorityLevel": "1",
                "screenDensity": "HDPI",
                "screenWidth": "sw600dp",
                "softwareVersion": "451",
                "supportsConsentRedirection": "true",
                "supportsPKMZ": "false",
                "supportsPreorderModalMessaging": "true",
                "supportsScoreBug": "false",
                "supportsStreamSelectorModal": "true",
                "supportsVariantSwitching": "true",
                "swiftPriorityLevel": "background",
                "timeZoneId": "Asia/Calcutta",
                "uxLocale": "en_US",
                "version": "1"
            }

            headers = {
                "Authorization": f"Bearer {self.device_token}",
            }

            response = self.session.get(
                "https://ab8mt4dd97et.api.amazonvideo.com/cdp/switchblade/android/getDataByJvmTransform/v1/dv-android/detail/vod/v2.kt",
                params=params,
                headers=headers,
                timeout=30
            ).json()
            titles = []
            titles_ = []
            colur_variants_availble = []
            img_url = response["resource"]["detailPageHeader"]["decodedDatums"]["ImagesV4Output"]["coverImage"]["mediaCentralUrl"]
            if "CatalogV3Output" in response["resource"]["detailPageHeader"]["decodedDatums"]:
                title = response["resource"]["detailPageHeader"]["decodedDatums"]["CatalogV3Output"]["catalogMetadata"]["title"]
                ids = response["resource"]["detailPageHeader"]["decodedDatums"]["CatalogV3Output"]["catalogMetadata"]["id"]
                content_type = response["resource"]["detailPageHeader"]["decodedDatums"]["CatalogV3Output"]["catalogMetadata"]["entityType"]
                org_lang = response["resource"]["detailPageHeader"]["decodedDatums"]["CatalogV3Output"]["catalogMetadata"]["originalLanguages"][0]
                year = datetime.utcfromtimestamp(response["resource"]["detailPageHeader"]["decodedDatums"]["CatalogV3Output"]["catalogMetadata"]["originalReleaseDate"] / 1000).year
            else:
                title = response["resource"]["widgetList"]["tabs"][0]["tabWidgets"][0]["catalog"]["title"]
                ids = response["resource"]["widgetList"]["tabs"][0]["tabWidgets"][0]["catalog"]["id"]
                content_type = response["resource"]["widgetList"]["tabs"][0]["tabWidgets"][0]["catalog"]["entityType"]
                org_lang = response["resource"]["widgetList"]["tabs"][0]["tabWidgets"][0]["catalog"]["originalLanguages"][0]
                year = datetime.utcfromtimestamp(response["resource"]["widgetList"]["tabs"][0]["tabWidgets"][0]["catalog"]["originalReleaseDate"] / 1000).year
            if content_type == "Movie":
                titles.append(Title(
                    id_=ids,
                    type_=Title.Types.MOVIE,
                    name=title,
                    year=year,
                    # language is obtained afterward
                    original_lang=None,
                    source=self.ALIASES[0],
                    service_data=response
                ))
                playbackEnvelope_info = self.playbackEnvelope_data([ids])
                for title in titles:
                    for playbackInfo in playbackEnvelope_info:
                        if title.id == playbackInfo["titleID"]:
                            title.service_data.update({"playbackInfo": playbackInfo})
                            titles_.append(title)
                
            else:
                titles = []
                titles_ = []
                colur_variant_playback_metadata = None
                if self.requestd_color_variant:
                    requestd_color_variant = self.requestd_color_variant
                sow_title = response["resource"]["detailPageHeader"]["labeledDecodedDatums"]["catalog-v3-series"]["catalogMetadata"]["title"]
                for i in response["resource"]["detailPageHeader"]["decodedDatums"]["HierarchyV2Output"]["siblings"]:
                    season_num = i["sequence"]
                    season_id = i["gti"]
                    params["itemId"] = season_id
                    response = self.session.get(
                        "https://ab8mt4dd97et.api.amazonvideo.com/cdp/switchblade/android/getDataByJvmTransform/v1/dv-android/detail/vod/v2.kt",
                        params=params,
                        headers=headers
                    ).json()
                    for k in response["resource"]["widgetList"]:
                        if "tabs" in k:
                            for p in k["tabs"]:
                                if p["tabName"] == "Episodes":
                                    for j in p["tabWidgets"][0]["episodesDecoded"]:
                                        ep_id = j["id"]
                                        ep_num = j["sequenceNumber"]
                                        variant_details = []
                                        if "TitleActionsViewV15Output" in  j["decodedDatums"]:
                                            if "actionsViews" in  j["decodedDatums"]["TitleActionsViewV15Output"]:
                                                if "LIST_V2" in  j["decodedDatums"]["TitleActionsViewV15Output"]["actionsViews"]:
                                                    if "playbackGroup" in  j["decodedDatums"]["TitleActionsViewV15Output"]["actionsViews"]["LIST_V2"]:
                                                        for item in j["decodedDatums"]["TitleActionsViewV15Output"]["actionsViews"]["LIST_V2"]["playbackGroup"]["items"]:
                                                            if "itemReference" in item:
                                                                if "playbackExperienceMetadata" in  item["itemReference"]:
                                                                    colur_variant =  item["itemReference"]["playbackExperienceMetadata"]["videoColorVariant"]
                                                                    colur_variants_availble.append(colur_variant)
                                                                    if requestd_color_variant:
                                                                        if colur_variant == requestd_color_variant:
                                                                            colur_variant_playback_metadata =  item["itemReference"]["playbackExperienceMetadata"]
                                        content_type = j["decodedDatums"]["CatalogV3Output"]["catalogMetadata"]["entityType"]
                                        if content_type == "TVEpisode":
                                            ep_title = j["decodedDatums"]["CatalogV3Output"]["catalogMetadata"]["title"]
                                            titles.append(
                                                Title(
                                                    id_=ep_id,
                                                    type_=Title.Types.TV,
                                                    name=sow_title,
                                                    season=season_num,
                                                    episode=ep_num,
                                                    episode_name=ep_title,
                                                    original_lang=None,
                                                    year=year,
                                                    source=self.ALIASES[0],
                                                    service_data=response,
                                                )
                                            )
                                                                    
                                            # Get playback info for initial batch
                                            if not colur_variant_playback_metadata:
                                                playbackEnvelope_info = self.playbackEnvelope_data([ep_id])
                                            else: 
                                                value = colur_variant_playback_metadata["expiryTime"]
                                                if isinstance(value, str):
                                                    expiry = int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
                                                else:
                                                    expiry = int(value)
                                                colur_variant_playback_metadata["expiryTime"] = expiry  
                                                playbackEnvelope_info = [{"titleID": ep_id, "playbackExperienceMetadata": colur_variant_playback_metadata}]            
                                            for title in titles:
                                                for playbackInfo in playbackEnvelope_info:
                                                    if title.id == playbackInfo["titleID"]:
                                                        title.service_data.update({"playbackInfo": playbackInfo})
                                                        titles_.append(title)
                if colur_variants_availble:
                    unique_variants = sorted(set(colur_variants_availble))
                    self.log.info(f" + Detected {'/'.join(unique_variants)} variant Switching to {requestd_color_variant} variant.")
            if titles_ == []:
                device_cache_path = self.get_cache("device_tokens_{profile}_{hash}.json".format(
                    profile=self.profile,
                    hash=hashlib.md5(json.dumps(self.device).encode()).hexdigest()[0:6]
                ))
                if os.path.isfile(device_cache_path):
                    os.remove(device_cache_path)
                raise self.log.exit(" - The profile used does not have the rights to this title.")
            if titles_:
                # TODO: Needs playback permission on first title, title needs to be available
                original_lang = self.get_original_language(self.get_manifest(
                    next((x for x in titles_ if x.type == Title.Types.MOVIE or x.episode > 0), titles_[0]),
                    video_codec=self.vcodec,
                    bitrate_mode=self.bitrate,
                    quality=self.vquality,
                    ignore_errors=True
                ))
            if original_lang:
                for title in titles_:
                    title.original_lang = Language.get(original_lang)
            else:
                self.log.warning(" - Unable to obtain the title's original language, setting 'en' default...")
                for title in titles_:
                    title.original_lang = Language.get("en")

            filtered_titles = []
            season_episode_count = defaultdict(int)
            for title in titles_:
                key = (title.season, title.episode) 
                if season_episode_count[key] < 1:
                    filtered_titles.append(title)
                    season_episode_count[key] += 1

            titles = filtered_titles

            return titles
        else:
            colur_variants_availble = []
            params = {
                'jic': '8|EgRzdm9k',
            }
            response = self.session.get(f"https://{self.region['base']}/gp/video/detail/{self.title}", params=params)
            soup = BeautifulSoup(response.text, "html.parser")
            for script in soup.find_all("script"):
                content = script.string or script.text
                if not content:
                    continue
                if "pageTitleId" not in content:
                    continue
                try:
                    data = json.loads(content)
                except:
                    continue
                if data:
                    titles = []
                    titles_ = []
                    if "atf" in data["init"]["preparations"]["body"]:
                        state = data["init"]["preparations"]["body"]["atf"]["state"]
                        main_asin = state["pageTitleId"]
                        header = state["detail"]["headerDetail"][main_asin]
                        if header.get("entityType") == "TV Show":
                            title = header.get("parentTitle")
                        else:
                            title = header.get("title")
                        if header.get("entityType") == "Movie":
                            titles.append(Title(
                                id_=main_asin,
                                type_=Title.Types.MOVIE,
                                name=title,
                                year=header.get("releaseYear"),
                                # language is obtained afterward
                                original_lang=None,
                                source=self.ALIASES[0],
                                service_data=data
                            ))
                            playbackEnvelope_info = self.playbackEnvelope_data([main_asin])
                            for title in titles:
                                for playbackInfo in playbackEnvelope_info:
                                    if title.id == playbackInfo["titleID"]:
                                        title.service_data.update({"playbackInfo": playbackInfo})
                                        titles_.append(title)
                        else:
                            colur_variant_playback_metadata = None
                            requestd_color_variant = self.requestd_color_variant
                            seasons = state.get("seasons", {}).get(main_asin, [])
                            ep_data = []
                            for season in seasons:
                                headers = {
                                    'accept': 'application/json'
                                }
                                params = {
                                    'dvWebAppClientVersion': '1.0.124589.0',
                                }
                                response = self.session.get(f'https://{self.region["base"]}{season.get("seasonLink")}', params=params, headers=headers)
                                data = response.json()
                                main_asin = data["body"]["atf"]["state"]["pageTitleId"]
                                sow_title = data["body"]["atf"]["state"]["detail"]["headerDetail"][main_asin]["parentTitle"]
                                content_type = data["body"]["atf"]["state"]["detail"]["headerDetail"][main_asin]["entityType"]
                                ids = data["body"]["atf"]["state"]["detail"]["headerDetail"][main_asin]["catalogId"]
                                img_url = data["body"]["atf"]["state"]["detail"]["headerDetail"][main_asin]["images"]["covershot"]
                                year = data["body"]["atf"]["state"]["detail"]["headerDetail"][main_asin]["releaseYear"]
                                season_num = data["body"]["atf"]["state"]["detail"]["headerDetail"][main_asin]["seasonNumber"]
                                detail = data["body"]["btf"]["state"]["detail"]["detail"]
                                for title_id, info in detail.items():
                                    if info.get("titleType") == "episode":
                                        try:
                                            ep_var = data["body"]["btf"]["state"]["action"]["btf"][title_id]["primaryActions"][0]["payload"]["modal"]["sections"][0]["actions"]
                                            for item in ep_var:
                                                colur_variant =  item["payload"]["playback"]["videoColorVariant"]
                                                colur_variants_availble.append(colur_variant)
                                                if colur_variant == requestd_color_variant:
                                                    colur_variant_playback_metadata = item["payload"]["playback"]
                                        except Exception:
                                            pass
                                        titles.append(
                                            Title(
                                                id_=title_id,
                                                type_=Title.Types.TV,
                                                name=sow_title,
                                                season=season_num,
                                                episode=info.get("episodeNumber"),
                                                episode_name=info.get("title"),
                                                original_lang=None,
                                                source=self.ALIASES[0],
                                                service_data=response.json(),
                                            )
                                        )
                                                                    
                                        # Get playback info for initial batch
                                        if not colur_variant_playback_metadata:
                                            playbackEnvelope_info = self.playbackEnvelope_data([title_id])
                                        else: 
                                            value = colur_variant_playback_metadata["expiryTime"]
                                            if isinstance(value, str):
                                                expiry = int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
                                            else:
                                                expiry = int(value)
                                            colur_variant_playback_metadata["expiryTime"] = expiry  
                                            playbackEnvelope_info = [{"titleID": title_id, "playbackExperienceMetadata": colur_variant_playback_metadata}]            
                                        for title in titles:
                                            for playbackInfo in playbackEnvelope_info:
                                                if title.id == playbackInfo["titleID"]:
                                                    title.service_data.update({"playbackInfo": playbackInfo})
                                                    titles_.append(title)
                        if colur_variants_availble:
                            unique_variants = sorted(set(colur_variants_availble))
                            self.log.info(f" + Detected {'/'.join(unique_variants)} variant Switching to {requestd_color_variant} variant.")
                        if titles_ == []:
                            raise self.log.exit(" - The profile used does not have the rights to this title.")

                        if titles_:
                            # TODO: Needs playback permission on first title, title needs to be available
                            original_lang = self.get_original_language(self.get_manifest(
                                next((x for x in titles_ if x.type == Title.Types.MOVIE or x.episode > 0), titles_[0]),
                                video_codec=self.vcodec,
                                bitrate_mode=self.bitrate,
                                quality=self.vquality,
                                ignore_errors=True
                            ))
                            if original_lang:
                                for title in titles_:
                                    title.original_lang = Language.get(original_lang)
                            else:
                                self.log.warning(" - Unable to obtain the title's original language, setting 'en' default...")
                                for title in titles_:
                                    title.original_lang = Language.get("en")

                        filtered_titles = []
                        season_episode_count = defaultdict(int)
                        for title in titles_:
                            key = (title.season, title.episode) 
                            if season_episode_count[key] < 1:
                                filtered_titles.append(title)
                                season_episode_count[key] += 1

                        titles = filtered_titles

                    return titles_

    def get_tracks(self, title: Title) -> Tracks:
        """Modified get_tracks to support HYBRID mode."""
        if self.chapters_only:
            return []

        # Check if HYBRID mode is requested
        hybrid_mode = self.range and self.range.upper() in ("DVHDR", "HDRDV", "HYBRID")
        
        if hybrid_mode:
            # For HYBRID mode, we need both HDR10 and DV tracks
            self.log.info(" + HYBRID mode detected - getting both HDR10 and DV tracks")
            
            # First get HDR10 tracks
            tracks_hdr = self.get_best_quality(title)
            
            # Get HDR10 manifest
            manifest_hdr = self.get_manifest(
                title,
                video_codec=self.vcodec,
                bitrate_mode=self.bitrate,
                quality=self.vquality,
                hdr="HDR10",
                ignore_errors=False
            )
            
            if "rightsException" in manifest_hdr:
                self.log.error(" - The profile used does not have the rights to this title.")
                return
            
            # Get DV manifest for metadata extraction (lowest quality)
            manifest_dv = self.get_manifest(
                title,
                video_codec="H265",
                bitrate_mode=self.bitrate,
                quality=self.vquality,
                hdr="DV",
                ignore_errors=True
            )
            
            if not manifest_dv:
                self.log.warning(" - No DV manifest available for HYBRID mode, falling back to HDR10 only")
                self.range = "HDR10"
                return self.get_tracks(title)  # Recursive call with HDR10
            
            # Process HDR10 manifest
            chosen_manifest_hdr = self.choose_manifest(manifest_hdr, self.cdn)
            if not chosen_manifest_hdr:
                raise self.log.exit(f"No HDR10 manifests available")
            
            manifest_url_hdr = self.clean_mpd_url(chosen_manifest_hdr["url"], False)
            self.log.info(" + Downloading HDR10 Manifest")
            
            streamingProtocol_hdr = manifest_hdr["vodPlaybackUrls"]["result"]["playbackUrls"]["urlMetadata"]["streamingProtocol"]
            sessionHandoffToken_hdr = manifest_hdr["sessionization"]["sessionHandoffToken"]
            
            tracks = Tracks()
            
            if streamingProtocol_hdr == "DASH":
                tracks.add(Tracks([
                    x for x in iter(Tracks.from_mpd(
                        url=manifest_url_hdr,
                        session=self.session,
                        source=self.ALIASES[0],
                    ))
                ]))
                self._attach_mpd(tracks, manifest_url_hdr, "HDR10")
                for track in tracks:
                    track.extra = track.extra + (sessionHandoffToken_hdr,)
            elif streamingProtocol_hdr == "SmoothStreaming":
                tracks.add(Tracks([
                    x for x in iter(Tracks.from_ism(
                        url=manifest_url_hdr,
                        source=self.ALIASES[0],
                    ))
                ]))
                self._attach_mpd(tracks, manifest_url_hdr, "HDR10")
                for track in tracks:
                    track.extra = track.extra + (sessionHandoffToken_hdr,)
            
            # Mark HDR10 videos
            for video in tracks.videos:
                video.hdr10 = True
                video.dv = False
            
            # Process DV manifest (get lowest quality for metadata)
            chosen_manifest_dv = self.choose_manifest(manifest_dv, self.cdn)
            if chosen_manifest_dv:
                manifest_url_dv = self.clean_mpd_url(chosen_manifest_dv["url"], False)
                self.log.info(" + Downloading DV Manifest (for metadata)")
                
                streamingProtocol_dv = manifest_dv["vodPlaybackUrls"]["result"]["playbackUrls"]["urlMetadata"]["streamingProtocol"]
                sessionHandoffToken_dv = manifest_dv["sessionization"]["sessionHandoffToken"]
                
                if streamingProtocol_dv == "DASH":
                    dv_tracks = Tracks([
                        x for x in iter(Tracks.from_mpd(
                            url=manifest_url_dv,
                            session=self.session,
                            source=self.ALIASES[0],
                        ))
                    ])
                    self._attach_mpd(dv_tracks, manifest_url_dv, "DV")
                    for track in dv_tracks:
                        track.extra = track.extra + (sessionHandoffToken_dv,)
                elif streamingProtocol_dv == "SmoothStreaming":
                    dv_tracks = Tracks([
                        x for x in iter(Tracks.from_ism(
                            url=manifest_url_dv,
                            source=self.ALIASES[0],
                        ))
                    ])
                    self._attach_mpd(dv_tracks, manifest_url_dv, "DV")
                    for track in dv_tracks:
                        track.extra = track.extra + (sessionHandoffToken_dv,)
                
                # Mark DV videos and add lowest quality DV track
                for video in dv_tracks.videos:
                    video.dv = True
                    video.hdr10 = False
                
                # Sort DV tracks by bitrate and add the lowest one
                dv_tracks.videos = sorted(dv_tracks.videos, key=lambda x: float(x.bitrate or 0.0))
                if dv_tracks.videos:
                    tracks.add([dv_tracks.videos[0]], warn_only=True)
                    self.log.info(f" + Added DV track for metadata: {dv_tracks.videos[0].bitrate // 1000 if dv_tracks.videos[0].bitrate else '?'} kb/s")
        else:
            # Normal (non-HYBRID) mode - use existing logic
            tracks = self.get_best_quality(title)
            
            manifest = self.get_manifest(
                title,
                video_codec=self.vcodec,
                bitrate_mode=self.bitrate,
                quality=self.vquality,
                hdr=self.range,
                ignore_errors=False
            )
            
            if "rightsException" in manifest:
                self.log.error(" - The profile used does not have the rights to this title.")
                return
            
            chosen_manifest = self.choose_manifest(manifest, self.cdn)
            if not chosen_manifest:
                raise self.log.exit(f"No manifests available")
            
            manifest_url = self.clean_mpd_url(chosen_manifest["url"], False)
            if self.event:
                devicetype = self.device["device_type"]
                manifest_url = chosen_manifest["url"]
                manifest_url = f"{manifest_url}?amznDtid={devicetype}&encoding=segmentBase"
            
            self.log.info(f" + Downloading Manifest")
            
            streamingProtocol = manifest["vodPlaybackUrls"]["result"]["playbackUrls"]["urlMetadata"]["streamingProtocol"]
            sessionHandoffToken = manifest["sessionization"]["sessionHandoffToken"]
            
            if streamingProtocol == "DASH":
                tracks.add(Tracks([
                    x for x in iter(Tracks.from_mpd(
                        url=manifest_url,
                        session=self.session,
                        source=self.ALIASES[0],
                    ))
                ]))
                self._attach_mpd(tracks, manifest_url, "SDR")
                for track in tracks:
                    track.extra = track.extra + (sessionHandoffToken,)
            elif streamingProtocol == "SmoothStreaming":
                tracks.add(Tracks([
                    x for x in iter(Tracks.from_ism(
                        url=manifest_url,
                        source=self.ALIASES[0],
                    ))
                ]))
                self._attach_mpd(tracks, manifest_url, "SDR")
                for track in tracks:
                    track.extra = track.extra + (sessionHandoffToken,)
            else:
                raise self.log.exit(f"Unsupported manifest type: {streamingProtocol}")
            
            for video in tracks.videos:
                video.hdr10 = manifest["vodPlaybackUrls"]["result"]["playbackUrls"]["urlMetadata"]["dynamicRange"] == "Hdr10"
                video.dv = manifest["vodPlaybackUrls"]["result"]["playbackUrls"]["urlMetadata"]["dynamicRange"] == "DolbyVision"

        # Extract duration from the first video track and add to title
        if tracks.videos:
            # Get duration from MPD manifest by downloading it again to parse duration
            try:
                import requests
                from vinetrimmer.utils.xml import load_xml
                from vinetrimmer.objects.tracks import Track
                
                # Get the manifest URL (use HDR10 manifest for hybrid mode)
                manifest_url_for_duration = manifest_url_hdr if hybrid_mode else manifest_url
                mpd_response = self.session.get(manifest_url_for_duration)
                if mpd_response.ok:
                    root = load_xml(mpd_response.text)
                    
                    # Try to get duration from MPD
                    mpd_duration = root.get("mediaPresentationDuration")
                    if mpd_duration:
                        duration_seconds = Track.pt_to_sec(mpd_duration)
                        duration_minutes = int(duration_seconds // 60)
                        duration_hours = duration_minutes // 60
                        duration_minutes = duration_minutes % 60
                        remaining_seconds = int(duration_seconds % 60)
                        
                        # Format duration with yellow color including seconds
                        if duration_hours > 0:
                            duration_str = f"\033[93m{duration_hours}h {duration_minutes}m {remaining_seconds}s\033[0m"
                            duration_plain = f"{duration_hours}h {duration_minutes}m {remaining_seconds}s"
                        else:
                            duration_str = f"\033[93m{duration_minutes}m {remaining_seconds}s\033[0m"
                            duration_plain = f"{duration_minutes}m {remaining_seconds}s"
                        
                        self.log.info(f" + Duration: {duration_str}")
                        
                        # Store duration in title service_data for potential future use
                        title.service_data["duration"] = {
                            "seconds": duration_seconds,
                            "formatted": duration_plain  # Store plain text version
                        }
            except Exception as e:
                # If duration extraction fails, continue without it
                pass

        # Continue with audio/subtitle processing (same for both HYBRID and normal modes)
        need_separate_audio = ((self.aquality or self.vquality) != self.vquality
                               or self.amanifest == "CVBR" and (self.vcodec, self.bitrate) != ("H264", "CVBR")
                               or self.amanifest == "CBR" and (self.vcodec, self.bitrate) != ("H264", "CBR")
                               or self.amanifest == "H265" and self.vcodec != "H265"
                               or self.amanifest != "H265" and self.vcodec == "H265")

        if not need_separate_audio:
            audios = defaultdict(list)
            for audio in tracks.audios:
                audios[audio.language].append(audio)

            for lang in audios:
                if not any((x.bitrate or 0) >= 640000 for x in audios[lang]):
                    need_separate_audio = True
                    break

        # If we need separate audio manifests (or user requested --atmos),
        # try fetching higher-bitrate audio manifests (e.g. CVBR/H265) so
        # that when --atmos is used we can still obtain non-Atmos audio
        # tracks at 640kbps if available.
        if need_separate_audio or self.atmos:
            manifest_type = self.amanifest or "CVBR"
            self.log.info(f"Getting audio from {manifest_type} manifest for potential higher bitrate or better codec")
            audio_manifest = self.get_manifest(
                title=title,
                video_codec="H265" if manifest_type == "H265" else "H264",
                bitrate_mode="CVBR",
                quality=self.aquality or self.vquality,
                hdr=None,
                ignore_errors=True
            )
            if not audio_manifest:
                self.log.warning(f" - Unable to get {manifest_type} audio manifests, skipping")
            elif not (chosen_audio_manifest := self.choose_manifest(audio_manifest, self.cdn)):
                self.log.warning(f" - No {manifest_type} audio manifests available, skipping")
            else:
                audio_mpd_url = self.clean_mpd_url(chosen_audio_manifest["url"], optimise=False)
                self.log.debug(audio_mpd_url)
                if self.event:
                    devicetype = self.device["device_type"]
                    audio_mpd_url = chosen_audio_manifest["url"]
                    audio_mpd_url = f"{audio_mpd_url}?amznDtid={devicetype}&encoding=segmentBase"
                self.log.info(" + Downloading CVBR manifest")

                streamingProtocol = audio_manifest["vodPlaybackUrls"]["result"]["playbackUrls"]["urlMetadata"]["streamingProtocol"]
                sessionHandoffToken = audio_manifest["sessionization"]["sessionHandoffToken"]

                try:
                    if streamingProtocol == "DASH":
                        audio_mpd = Tracks([
                                x for x in iter(Tracks.from_mpd(
                                url=audio_mpd_url,
                                session=self.session,
                                source=self.ALIASES[0],
                            ))
                        ])
                        self._attach_mpd(audio_mpd, audio_mpd_url, "Audio")
                        for track in audio_mpd:
                            track.extra = track.extra + (sessionHandoffToken,)
                    elif streamingProtocol == "SmoothStreaming":
                        audio_mpd = Tracks([
                                x for x in iter(Tracks.from_ism(
                                url=audio_mpd_url,
                                source=self.ALIASES[0],
                            ))
                        ])
                        self._attach_mpd(audio_mpd, audio_mpd_url, "Audio")
                        for track in audio_mpd:
                            track.extra = track.extra + (sessionHandoffToken,)
                except KeyError:
                    self.log.warning(f" - Title has no {self.amanifest} stream, cannot get higher quality audio")
                else:
                    tracks.add(audio_mpd.audios, warn_only=True)

        # UHD audio handling remains the same
        need_uhd_audio = self.atmos

        if not self.amanifest and ((self.aquality == "UHD" and self.vquality != "UHD") or not self.aquality):
            audios = defaultdict(list)
            for audio in tracks.audios:
                audios[audio.language].append(audio)
            for lang in audios:
                if not any((x.bitrate or 0) >= 640000 for x in audios[lang]):
                    need_uhd_audio = True
                    break

        if need_uhd_audio and (self.config.get("device") or {}).get(self.profile, None):
            self.log.info("Getting audio from UHD manifest for potential higher bitrate or better codec")
            temp_device = self.device
            temp_device_token = self.device_token
            temp_device_id = self.device_id
            uhd_audio_manifest = None

            try:
                if self.cdm.device.type in [LocalDevice.Types.CHROME, LocalDevice.Types.PLAYREADY] and self.quality < 2160:
                    self.log.info(f" + Switching to device to get UHD manifest")
                    self.register_device()

                uhd_audio_manifest = self.get_manifest(
                    title=title,
                    video_codec="H265",
                    bitrate_mode="CVBR+CBR",
                    quality="UHD",
                    hdr="DV",
                    ignore_errors=True
                )
            except:
                pass

            self.device = temp_device
            self.device_token = temp_device_token
            self.device_id = temp_device_id

            if not uhd_audio_manifest:
                self.log.warning(f" - Unable to get UHD manifests, skipping")
            elif not (chosen_uhd_audio_manifest := self.choose_manifest(uhd_audio_manifest, self.cdn)):
                self.log.warning(f" - No UHD manifests available, skipping")
            else:
                uhd_audio_mpd_url = self.clean_mpd_url(chosen_uhd_audio_manifest["url"], optimise=False)
                self.log.debug(uhd_audio_mpd_url)
                if self.event:
                    devicetype = self.device["device_type"]
                    uhd_audio_mpd_url = chosen_uhd_audio_manifest["url"]
                    uhd_audio_mpd_url = f"{uhd_audio_mpd_url}?amznDtid={devicetype}&encoding=segmentBase"
                self.log.info(" + Downloading UHD manifest")

                streamingProtocol = uhd_audio_manifest["vodPlaybackUrls"]["result"]["playbackUrls"]["urlMetadata"]["streamingProtocol"]
                sessionHandoffToken = uhd_audio_manifest["sessionization"]["sessionHandoffToken"]

                try:
                    if streamingProtocol == "DASH":
                        uhd_audio_mpd = Tracks([
                                x for x in iter(Tracks.from_mpd(
                                url=uhd_audio_mpd_url,
                                session=self.session,
                                source=self.ALIASES[0],
                            ))
                        ])
                        self._attach_mpd(uhd_audio_mpd, uhd_audio_mpd_url, "SDR")
                        for track in uhd_audio_mpd:
                            track.extra = track.extra + (sessionHandoffToken,)
                    elif streamingProtocol == "SmoothStreaming":
                        uhd_audio_mpd = Tracks([
                                x for x in iter(Tracks.from_ism(
                                url=uhd_audio_mpd_url,
                                source=self.ALIASES[0],
                            ))
                        ])
                        self._attach_mpd(uhd_audio_mpd, uhd_audio_mpd_url, "SDR")
                        for track in uhd_audio_mpd:
                            track.extra = track.extra + (sessionHandoffToken,)
                except KeyError:
                    self.log.warning(f" - Title has no UHD stream, cannot get higher quality audio")
                else:
                    if any(x for x in uhd_audio_mpd.audios if x.atmos):
                        # Instead of replacing all audio tracks with the UHD manifest's
                        # tracks (which may only contain Atmos), merge Atmos tracks with
                        # existing audio tracks so we keep other high-bitrate (640kbps)
                        # audio tracks (added above) while still including Atmos.
                        atmos_tracks = [x for x in uhd_audio_mpd.audios if x.atmos]
                        # Start with existing audios and add Atmos tracks if missing
                        existing = list(tracks.audios)
                        for a in atmos_tracks:
                            # avoid exact object duplicates
                            if not any((ea.language == a.language and (ea.bitrate or 0) == (a.bitrate or 0) and getattr(ea, 'atmos', False) == getattr(a, 'atmos', False)) for ea in existing):
                                existing.append(a)
                        tracks.audios = existing

        # Audio metadata processing
        for audio in tracks.audios:
            if audio.descriptor == audio.descriptor.MPD:
                audio.descriptive = audio.extra[1].get("audioTrackSubtype") == "descriptive"
                audio_track_id = audio.extra[1].get("audioTrackId")
                if audio_track_id:
                    audio.language = Language.get(audio_track_id.split("_")[0])
                if audio.extra[1] is not None and "boosteddialog" in audio.extra[1].get("audioTrackSubtype", ""):
                    audio.bitrate = 1
            elif audio.descriptor == audio.descriptor.ISM:
                audio.descriptive = audio.extra[0].get("audioTrackSubtype") == "descriptive"
                audio_track_id = audio.extra[0].get("audioTrackId")
                if audio_track_id:
                    audio.language = Language.get(audio_track_id.split("_")[0])
                if audio.extra[1] is not None and "boosteddialog" in audio.extra[1].get("audioTrackSubtype", ""):
                    audio.bitrate = 1
        
        # Set is_original_lang property for audio tracks based on title's original language
        if title.original_lang:
            for audio in tracks.audios:
                # Compare both the full language code and base language
                audio_lang_base = str(audio.language).split("-")[0]
                title_lang_base = str(title.original_lang).split("-")[0]
                audio.is_original_lang = (audio.language == title.original_lang or audio_lang_base == title_lang_base)
        else:
            # If no original language detected, mark the first non-descriptive audio track as original
            first_non_descriptive = next((audio for audio in tracks.audios if not audio.descriptive), None)
            if first_non_descriptive:
                first_non_descriptive.is_original_lang = True
                # Set all other tracks as non-original
                for audio in tracks.audios:
                    if audio != first_non_descriptive:
                        audio.is_original_lang = False
                    
        # Remove duplicate audio tracks
        unique_audio_tracks = {}
        for audio in tracks.audios:
            key = (audio.language, audio.bitrate, audio.descriptive)
            if key not in unique_audio_tracks:
                unique_audio_tracks[key] = audio
        tracks.audios = list(unique_audio_tracks.values())

        # If user requested Atmos, prefer including a non-Atmos audio track
        # at >=640 kb/s per language (if available), while still including
        # Atmos tracks. This ensures we don't end up with only a low-bitrate
        # Atmos track and miss higher-bitrate non-Atmos alternatives.
        if self.atmos and tracks.audios:
            from collections import defaultdict as _dd
            grouped = _dd(list)
            for a in tracks.audios:
                grouped[a.language].append(a)

            selected = []
            for lang, group in grouped.items():
                # Check if this language has Atmos tracks available
                atmos_tracks = [x for x in group if getattr(x, 'atmos', False)]
                
                if atmos_tracks:
                    # If Atmos is available for this language, only include the best Atmos track
                    best_atmos = max(atmos_tracks, key=lambda x: getattr(x, 'bitrate', 0) or 0)
                    selected.append(best_atmos)
                else:
                    # If no Atmos available, include the best non-Atmos track (excluding descriptive)
                    non_atmos_high = [x for x in group if not getattr(x, 'atmos', False) and (getattr(x, 'bitrate', 0) or 0) >= 640000 and not getattr(x, 'descriptive', False)]
                    if non_atmos_high:
                        best_non_atmos = max(non_atmos_high, key=lambda x: getattr(x, 'bitrate', 0) or 0)
                        selected.append(best_non_atmos)
                    else:
                        non_atmos_any = [x for x in group if not getattr(x, 'atmos', False) and not getattr(x, 'descriptive', False)]
                        if non_atmos_any:
                            best_non_atmos = max(non_atmos_any, key=lambda x: getattr(x, 'bitrate', 0) or 0)
                            selected.append(best_non_atmos)

            # Don't replace tracks.audios here - let the language selection in dl.py handle it
            # Just mark the preferred tracks for later selection
            for audio in tracks.audios:
                audio._atmos_preferred = any(s.id == audio.id for s in selected)
        
        # Subtitle processing
        # Get subtitles from the appropriate manifest (HDR10 manifest for hybrid mode, main manifest otherwise)
        manifest_for_subs = manifest_hdr if hybrid_mode else manifest
        for sub in manifest_for_subs.get("timedTextUrls", {}).get("result", {}).get("subtitleUrls", []) + \
                   manifest_for_subs.get("timedTextUrls", {}).get("result", {}).get("forcedNarrativeUrls", []):
            tracks.add(TextTrack(
                id_=f"{sub['trackGroupId']}_{sub['languageCode']}_{sub['type']}_{sub['subtype']}",
                source=self.ALIASES[0],
                url=os.path.splitext(sub["url"])[0] + ".srt",
                codec="srt",
                language=sub["languageCode"],
                forced="ForcedNarrative" in sub["type"],
                sdh=sub["type"].lower() == "sdh"
            ), warn_only=True)
        
        for track in tracks:
            track.needs_proxy = False

        # Session management
        if self.vquality != "UHD" and not self.no_true_region:
            self.manage_session(tracks.videos[0])

        return tracks

    def get_chapters(self, title: Title) -> List[MenuTrack]:
        """Get chapters from Amazon's XRay Scenes API - DISABLED."""
        # Chapters disabled per user request
        return []

    def certificate(self, **_):
        return self.config["certificate"]
        
    def license(self, challenge: bytes, title: Title, track: Track, *_, **__) -> Union[bytes, str, dict, None]:
        if (
            isinstance(self.cdm, (RemoteDevice, LocalDevice))
            and challenge != self.cdm.service_certificate_challenge
        ):
            self.register_device(
                quality=track.quality or getattr(track, "height", None)
            )

        if self.playready:
            license_type_key = "playReadyLicense"
            license_endpoint = "license_pr"
            other_params = {}
        else:
            license_type_key = "widevineLicense"
            license_endpoint = "license_wv"
            other_params = {
                "includeHdcpTestKey": True,
            }

        # Build request payload
        request_json = {
            **other_params,
            "licenseChallenge": base64.b64encode(challenge).decode(),
            "playbackEnvelope": self.playbackInfo["playbackExperienceMetadata"]["playbackEnvelope"],
        }

        # Add device-specific parameters
        if self.device_token:
            # Android/device-based flow (for both Widevine and PlayReady with device)
            request_json.update({
                "capabilityDiscriminators": {
                    "discriminators": {
                        "hardware": {
                            "chipset": self.device["device_chipset"],
                            "manufacturer": self.device["manufacturer"],
                            "modelName": self.device["device_model"]
                        },
                        "software": {
                            "application": {
                                "name": self.device["app_name"],
                                "version": self.device["software_version"]
                            },
                            "client": {
                                "id": None
                            },
                            **(
                                {
                                    "firmware": {
                                        "version": str(
                                            self.device["firmware_version"]
                                        ),
                                    },
                                }
                                if self.device.get("firmware_version")
                                else {}
                            ),
                            "operatingSystem": {
                                "name": "Android",
                                "version": self.device["os_version"]
                            },
                            "player": {
                                "name": "Android Player",
                                "version": self.device["app_version"]
                            },
                            "renderer": {
                                "drmScheme": "WIDEVINE" if not self.playready else "PLAYREADY",
                                "name": "MCMD"
                            }
                        }
                    },
                    "version": 1
                },
                "deviceCapabilityFamily": "AndroidPlayer",
                "keyId": str(uuid.UUID(track.kid)).upper(),
                "packagingFormat": "SMOOTH_STREAMING"
                if track.descriptor == Track.Descriptor.ISM
                else "MPEG_DASH",
            })
            headers = {
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "Accept-Language": "en_US",
                "Authorization": f"Bearer {self.device_token}",
                "Connection": "Keep-Alive",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": self.device["user_agent"],
                "x-gasc-enabled": "true",
                "x-request-priority": "CRITICAL",
                "x-retry-count": "0"
            }
            params = {
                "deviceID": self.device_id,
                "deviceTypeID": self.device["device_type"],
                "firmware": self.device["firmware"],
                "format": "json",
                "osLocale": "en_US",
                "softwareVersion": self.device["software_version"],
                "titleId": title.id,
                "uxLocale": "en_US",
                "version": "1"
            }
        else:
            # Web-based flow - needs sessionHandoff from track.extra (Widevine only)
            # The sessionHandoffToken is stored in track.extra[2] during get_tracks()
            try:
                session_handoff = track.extra[2] if len(track.extra) > 2 else None
            except (IndexError, AttributeError):
                session_handoff = None
            
            if not session_handoff:
                self.log.exit("No sessionHandoff found in track data. Web licensing requires sessionHandoff.")
            
            request_json.update({
                "sessionHandoff": session_handoff,
                "deviceCapabilityFamily": "WebPlayer",
            })
            headers={
                "accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "connection": "Keep-Alive",
                "x-gasc-enabled": "true",
                "x-request-priority": "CRITICAL",
                "x-retry-count": "0",
                "nerid": self.generate_nerid(),
            }
            params={
                "deviceID": self.device_id,
                "deviceTypeID": self.device["device_type"],
                "gascEnabled": str(self.pv).lower(),
                "marketplaceID": self.region["marketplace_id"],
                "uxLocale": "en_EN",
                "firmware": "1",
                "titleId": title.id,
                "nerid": self.generate_nerid(),
            },

        try:
            res = self.session.post(
                url=self.endpoints[license_endpoint],
                headers=headers,
                params=params,
                json=request_json,
            )
            res.raise_for_status()
            response_data = res.json()
        except requests.exceptions.HTTPError as e:
            msg = "Failed to license"
            if e.response is not None:
                try:
                    res_json = e.response.json()
                    msg += f": {res_json}"
                except Exception:
                    msg += f": {e.response.text}"
            else:
                msg += f": {str(e)}"
            
            self.log.exit(msg)
        except Exception as e:
            self.log.exit(f"Failed to license: {str(e)}")

        return response_data[license_type_key]["license"]
        
        
    
    def configure(self) -> None:
        if len(self.title) > 10:
            self.pv = True

        self.log.info("Getting Account Region")
        self.region = self.get_region()
        if not self.region:
            raise self.log.exit(" - Failed to get Amazon Account region")
        print(self.region)
        self.GEOFENCE.append(self.region["code"])
        
        if self.no_true_region:
            self.log.info(f" + Region: {self.region['code']}")

        # endpoints must be prepared AFTER region data is retrieved
        self.endpoints = self.prepare_endpoints(self.config["endpoints"], self.region)

        self.session.headers.update({
            "Origin": f"https://{self.region['base']}",
            "Referer": f"https://{self.region['base']}/"
        })
        devices = self.config.get("device") or {}
        self.device = devices.get("default", {})
        if self.device and self.device["device_type"] not in set(self.config["dtid_dict"]):
            raise self.log.exit(f"{self.device['device_type']} Banned from Amazon Prime, Use another one to avoid Amazon Account Ban !!!")
        if (self.quality > 1080 or self.range != "SDR") and self.vcodec == "H265" and self.cdm.device.type == LocalDevice.Types.CHROME:
            self.log.info(f"Using device to get UHD manifests")
            self.register_device()
        elif not self.device or self.cdm.device.type == LocalDevice.Types.CHROME or self.vquality != "UHD":
            # falling back to browser-based device ID
            if not self.device:
                self.log.warning(
                    "No Device information was provided for %s, using browser device...",
                    self.config.get("device").get("default")
                )
            self.device_id = "c3714f0d-59c9-4eb7-8b96-903f0f8c3619" #hashlib.sha224(
                #("CustomerID" + self.session.headers["User-Agent"]).encode("utf-8")
            #).hexdigest()
            self.device = {"device_type": self.config["device_types"]["browser"]}
            res = self.session.get(
                url=self.endpoints["configuration"],
                params = {
                    "deviceTypeID": self.device["device_type"],
                    "deviceID": "Web",
                }
            )

            if not res.status_code == 200:
                raise self.log.exit(res.text)
            
            data = res.json()
            
            #PK added if
            if not self.no_true_region:
                self.log.info(f" + Current Region: {data['requestContext']['currentTerritory']}")     
                self.region["marketplace_id"] = data["requestContext"]["marketplaceID"]
            
        else:
            res = self.session.get(
                url=self.endpoints["configuration"],
                params = {
                    "deviceTypeID": self.device["device_type"],
                    "deviceID": "Tv",
                }
            )

            if not res.status_code == 200:
                raise self.log.exit(res.text)
            
            data = res.json()
            
            #PK added if
            if not self.no_true_region:
                self.log.info(f" + Current Region: {data['requestContext']['currentTerritory']}")
                self.region["marketplace_id"] = data["requestContext"]["marketplaceID"]

            self.register_device()

    def register_device(self) -> None:
        self.device = (self.config.get("device") or {}).get(self.profile, {})
        device_cache_path = self.get_cache("device_tokens_{profile}_{hash}.json".format(
            profile=self.profile,
            hash=hashlib.md5(json.dumps(self.device).encode()).hexdigest()[0:6]
        ))
        self.device_token = self.DeviceRegistration(
            device=self.device,
            full_cfg=self.full_cfg,
            config=self.config,
            endpoints=self.endpoints,
            log=self.log,
            cache_path=device_cache_path,
            session=self.session
        ).bearer
        if self.session.proxies:
            proxy = self.session.proxies['all']
            # If user passed 2-letter country (like US, IN)
            country = proxy.lower()
            if '@' in country:
                country = country.split('@')[1]
            if '.' in country:
                country = country.split('.')[0]
            elif '-' in country:
                country = country.split('-')[0]
            if 'in' in country:
                country = 'in'
            elif 'au' in country:
                country = 'au'
        self.device_id = self.device.get(f"device_serial")
        if not self.device_id:
            raise self.log.exit(f" - A device serial is required in the config, perhaps use: {os.urandom(8).hex()}")

    def get_region(self) -> dict:
        domain_region = self.get_domain_region()
        if not domain_region:
            return {}

        region = self.config["regions"].get(domain_region)
        if not region:
            raise self.log.exit(f" - There's no region configuration data for the region: {domain_region}")

        region["code"] = domain_region
        if self.pv:
            r = self.session.get("https://www.primevideo.com")
            res = r.text
            soup = BeautifulSoup(res, 'html.parser')
            scripts = soup.find_all('script')
            pv_url = None
            for script in scripts:
                if script.string:
                    if 'DVWebNode.loggingEndpoint' in script.string:
                        # Try both quote styles
                        match = re.search(
                            r'DVWebNode\.loggingEndpoint\s*=\s*["\']([^"\']+)["\']',
                            script.string
                        )

                        if match:
                            pv_url = match.group(1)
                            break
            if pv_url is None:
                raise self.log.exit(" - Failed to get PrimeVideo region")
            try:
                parsed = urlparse(pv_url)
                baseUrl = parsed.netloc
            except Exception as e:
                raise self.log.exit(f" - Failed to get PrimeVideo region: {e}")
            #pv_region = {"na": "atv-ps"}.get(pv_region, f"atv-ps-{pv_region}")
            region["base_manifest"] = baseUrl #f"{pv_region}.primevideo.com"
            region["base"] = "www.primevideo.com"

        return region

    def get_domain_region(self):
        """Get the region of the cookies from the domain."""
        tlds = [tldextract.extract(x.domain) for x in self.cookies if x.domain_specified]
        tld = next((x.suffix for x in tlds if x.domain.lower() in ("amazon", "primevideo")), None)
        self.domain = next((x.domain for x in tlds if x.domain.lower() in ("amazon", "primevideo")), None).lower()
        if tld:
            tld = tld.split(".")[-1]
        return {"com": "us", "uk": "gb"}.get(tld, tld)

    def prepare_endpoint(self, name: str, uri: str, region: dict) -> str:
        if name in ("browse", "configuration", "refreshplayback", "playback", "license_wv", "license_pr", "xray", "opensession", "updatesession", "closesession"):
            return f"https://{(region['base_manifest'])}{uri}"
        if name in ("ontv", "devicelink", "details", "getDetailWidgets", "metadata"):
            if self.pv:
                host = "www.primevideo.com"
            else:
                if name in ("metadata"):
                    host = f"{region['base']}/gp/video"
                else:
                    host = region["base"]
            return f"https://{host}{uri}"
        if name in ("codepair", "register", "token"):
            return f"https://{self.config['regions']['us']['base_api']}{uri}"
        raise ValueError(f"Unknown endpoint: {name}")

    def prepare_endpoints(self, endpoints: dict, region: dict) -> dict:
        return {k: self.prepare_endpoint(k, v, region) for k, v in endpoints.items()}

    def choose_manifest(self, manifest: dict, cdn=None):
        """Get manifest URL for the title based on CDN weight (or specified CDN)."""
        if cdn:
            cdn = cdn.lower()
            manifest = next((x for x in manifest["vodPlaybackUrls"]["result"]["playbackUrls"]["urlSets"] if x["cdn"].lower() == cdn), {})
            if not manifest:
                raise self.log.exit(f" - There isn't any DASH manifests available on the CDN \"{cdn}\" for this title")
        else:
            url_sets = manifest["vodPlaybackUrls"]["result"]["playbackUrls"].get("urlSets", [])
            manifest = random.choice(url_sets) if url_sets else {}

        return manifest
    
    def manage_session(self, track: Tracks):
        try:
            current_progress_time = round(random.uniform(0, 10), 6)
            time_ = 3 # Seconds

            # Open Session
            stream_update_time = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
            if self.device_token:
                params={
                    "deviceID": self.device_id,
                    "deviceTypeID": self.device["device_type"],
                    "firmware": self.device["firmware"],
                    "format": "json",
                    "osLocale": "en_US",
                    "softwareVersion": self.device["software_version"],
                    "uxLocale": "en_EN",
                    "version": "1",
                }
                headers={  
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "Accept-Language": "en_EN",
                    "Authorization": f"Bearer {self.device_token}",
                    "Connection": "Keep-Alive",
                    "Content-Type": "application/json; charset=utf-8",
                    "User-Agent": self.device["user_agent"],
                    "x-gasc-enabled": "true",
                    "x-request-priority": "CRITICAL"
                }
                json_start={         
                    "sessionHandoff": track.extra[2],
                    "playbackEnvelope": self.playbackEnvelope_update(self.playbackInfo)["playbackExperienceMetadata"]["playbackEnvelope"],
                    "streamInfo": {
                        "eventType": "START",
                        "vodProgressInfo": {
                            "currentProgressTime": f"PT{current_progress_time:.6f}S",
                            "timeFormat": "ISO8601DURATION"
                        },
                        "liveProgressInfo": None,
                        "streamIntent": None,
                        "streamExperience": None
                    }
                }
            else:        
                params={
                    "deviceID": self.device_id,
                    "deviceTypeID": self.device["device_type"],
                    "gascEnabled": str(self.pv).lower(),
                    "marketplaceID": self.region["marketplace_id"],
                    "uxLocale": "en_EN",
                    "firmware": "1",
                    "version": "1",
                    "nerid": self.generate_nerid()
                }
                headers={
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'Content-Type': 'application/json',
                    'Sec-Fetch-Dest': 'empty',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Site': 'same-site',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
                    'accept': 'application/json',
                    'sec-ch-ua': '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
                    'sec-ch-ua-mobile': '?0',
                    'sec-ch-ua-platform': '"Windows"',
                    'x-request-priority': 'CRITICAL',
                    'x-retry-count': '0',
                }
                json_start={
                    "sessionHandoff": track.extra[2],
                    "playbackEnvelope": self.playbackEnvelope_update(self.playbackInfo)["playbackExperienceMetadata"]["playbackEnvelope"],
                    "streamInfo": {
                        "eventType": "START",
                        "streamUpdateTime": current_progress_time,
                        "vodProgressInfo": {
                            "currentProgressTime": f"PT{current_progress_time:.6f}S",
                            "timeFormat": "ISO8601DURATION",
                        },
                    },
                    "userWatchSessionId": str(uuid.uuid4())
                }
            res = self.session.post(
                url=self.endpoints["opensession"],
                params=params,
                headers=headers,
                json=json_start
            )
            if res.status_code == 200:
                try:
                    data = res.json()
                    sessionToken = data["sessionToken"]
                except Exception as e:
                    raise self.log.exit(f"Unable to open session: {e}")
            else:
                raise self.log.exit(f"Unable to open session: {res.text}")
            
            # Update Session
            time.sleep(time_)
            stream_update_time = (datetime.fromisoformat(stream_update_time[:-1]) + timedelta(seconds=time_)).isoformat(timespec="milliseconds") + "Z"
            if self.device_token:
                json_update={     
                    "sessionToken": sessionToken,
                    "streamInfo": {
                        "eventType": "PAUSE",
                        "vodProgressInfo": {
                            "currentProgressTime": f"PT{current_progress_time + time_:.6f}S",
                            "timeFormat": "ISO8601DURATION"
                        },
                    "liveProgressInfo": None,
                    "streamIntent": None,
                    "streamExperience": None
                    }
                }
            else:
                json_update={
                    "sessionToken": sessionToken,
                    "streamInfo": {
                        "eventType": "PAUSE",
                        "streamUpdateTime": stream_update_time,
                        "vodProgressInfo": {
                            "currentProgressTime": f"PT{current_progress_time + time_:.6f}S",
                            "timeFormat": "ISO8601DURATION",
                        }
                    }
                }
            res = self.session.post(
                url=self.endpoints["updatesession"],
                params=params,
                headers=headers,
                json=json_update
            )
            if res.status_code == 200:
                try:
                    data = res.json()
                    sessionToken = data["sessionToken"]
                except Exception as e:
                    raise self.log.exit(f"Unable to update session: {e}")
            else:
                raise self.log.exit(f"Unable to update session: {res.text}")
            if self.device_token:
                json_close={
                    "sessionToken": sessionToken,
                    "streamInfo": {
                        "eventType": "STOP",
                        "vodProgressInfo": {
                            "currentProgressTime": f"PT{current_progress_time + time_:.6f}S",
                            "timeFormat": "ISO8601DURATION"
                        },
                    "liveProgressInfo": None,
                    "streamIntent": None,
                    "streamExperience": None
                    }
                }
            else:
                json_close={
                    "sessionToken": sessionToken,
                    "streamInfo": {
                        "eventType": "STOP",
                        "streamUpdateTime": stream_update_time,
                        "vodProgressInfo": {
                            "currentProgressTime": f"PT{current_progress_time + time_:.6f}S",
                            "timeFormat": "ISO8601DURATION",
                        }
                    }
                }
            # Close session
            res = self.session.post(
                url=self.endpoints["closesession"],
                params=params,
                headers=headers,
                json=json_close
            )
            if res.status_code == 200:
                self.log.info("Session completed successfully!")
                return None
            else:
                raise self.log.exit(f"Unable to close session: {res.text}")
        except Exception as e:
            raise self.log.exit(f"Unable to get session: {e}")

    def playbackEnvelope_data(self, titles):
        try:
            res = self.session.get(
                url=self.endpoints["metadata"],
                params={
                    "metadataToEnrich": json.dumps({"placement": "HOVER", "playback": "true", "preroll": "true", "trailer": "true", "watchlist": "true"}),
                    "titleIDsToEnrich": json.dumps(titles),
                    "currentUrl":  f"https://{self.region['base']}/"
                },
                headers={
                    "device-memory": "8",
                    "downlink": "10",
                    "dpr": "2",
                    "ect": "4g",
                    "rtt": "50",
                    "viewport-width": "671",
                    "x-amzn-client-ttl-seconds": "15",
                    "x-amzn-requestid": "".join(random.choices(string.ascii_uppercase + string.digits, k=20)).upper(),
                    "x-requested-with": "XMLHttpRequest"
                }
            )
            
            if res.status_code == 200:
                try:
                    data = res.json()
                    playbackEnvelope_info = []
                    enrichments = data["enrichments"]
                    
                    for titleid_, enrichment in list(enrichments.items()):
                        playbackActions = enrichment["playbackActions"]
                        if enrichment["entitlementCues"]['focusMessage'].get('message') == "Watch with a 30 day free Prime trial, auto renews at €4.99/month":
                            raise self.log.exit("Cookies Expired")
                        if playbackActions == []:
                            continue
                            #raise self.log.exit(" - The profile used does not have the rights to this title.")
                        for playbackAction in playbackActions:
                            if playbackAction.get("titleID") or playbackAction.get("legacyOfferASIN"):
                                title_id = titleid_ #playbackAction.get("titleID")
                                playbackExperienceMetadata = playbackAction.get("playbackExperienceMetadata")
                                if not title_id or not playbackExperienceMetadata:
                                    continue
                                    #raise self.log.exit("Unable to get playbackEnvelope informations")
                                playbackEnvelope_info.append({"titleID": title_id, "playbackExperienceMetadata": playbackExperienceMetadata})
                    return playbackEnvelope_info
                except Exception as e:             
                    raise self.log.exit(f"Unable to get playbackEnvelope: {e}")
            else:
                return []
                #raise self.log.exit(f"Unable to get playbackEnvelope: {res.text}")
        except Exception as e:
            return []
            #raise self.log.exit(f"Unable to get playbackEnvelope: {e}")
        
    def playbackEnvelope_update(self, playbackInfo):
        try:
            if not playbackInfo:
                self.log.exit("Unable to update playbackEnvelope")
            if (int(playbackInfo["playbackExperienceMetadata"]["expiryTime"]) / 1000) < time.time():
                self.log.warn("Updating playbackEnvelope")
                correlationId = playbackInfo["playbackExperienceMetadata"]["correlationId"]
                titleID = playbackInfo["titleID"]
                res = self.session.post(
                    url=self.endpoints["refreshplayback"],
                    params={
                        "deviceID": self.device_id,
                        "deviceTypeID": self.device["device_type"],
                        "gascEnabled": str(self.pv).lower(),
                        "marketplaceID": self.region["marketplace_id"],
                        "uxLocale": "en_EN",
                        "firmware": self.device["firmware"],
                        "version": "1",
                        "nerid": self.generate_nerid()
                    },
                    data=json.dumps({
                        "deviceId": self.device_id, 
                        "deviceTypeId": self.device["device_type"],
                        "identifiers": {titleID: correlationId},
                        "geoToken": "null",
                        "identityContext": "null"
                    })
                )
                if res.status_code == 200:
                    try:
                        data = res.json()
                        playbackExperience = data["response"][titleID]["playbackExperience"]
                        playbackExperience["expiryTime"] = int(playbackExperience["expiryTime"] * 1000)
                        return {"titleID": titleID, "playbackExperienceMetadata": playbackExperience}
                    except Exception as e:
                        raise self.log.exit(f"Unable to update playbackEnvelope: {e}")
                else:
                    raise self.log.exit(f"Unable to update playbackEnvelope: {res.text}")
            else:
                return playbackInfo
        except Exception as e:
            raise self.log.exit(f"Unable to update playbackEnvelope {e}")

    def get_manifest(
        self, title: Title, video_codec: str, bitrate_mode: str, quality: str, hdr=None,
            ignore_errors: bool = False
    ) -> dict:
        self.playbackInfo = self.playbackEnvelope_update(title.service_data.get("playbackInfo"))
        title.service_data["playbackInfo"] = self.playbackInfo
        if self.device_token:    
            data_dict = {
                "auditPingsRequest": {
                    **({
                        "device": {
                            "category": "Tv",
                            "platform": "Android"
                        }
                    })
                },
                "globalParameters": {
                    "capabilityDiscriminators": {
                        "discriminators": {
                            "hardware": {
                                "chipset": self.device["device_chipset"],
                                "manufacturer": self.device["firmware_version"].split("/")[0],
                                "modelName": self.device["device_model"]
                            },
                            "software": {
                                "application": {
                                    "name":  "ATV",
                                    "version": str(self.device["software_version"])
                                },
                                "client": {
                                    "id": None
                                },
                                "firmware": {
                                    "version": self.device["firmware_version"]
                                },
                                "operatingSystem": {
                                    "name": "Android",
                                    "version": "15"
                                },
                                "player": {
                                    "name": "Android Player",
                                    "version": self.device["app_version"]
                                },
                                "renderer": {
                                    "drmScheme": "PlayReady" if self.playready else "Widevine",
                                    "name": "MCMD"
                                }
                            }    
                        },
                        "version": 1
                    },
                    "deviceCapabilityFamily": "AndroidPlayer",
                    "playbackEnvelope": self.playbackInfo["playbackExperienceMetadata"]["playbackEnvelope"]
                },
                "playbackDataRequest": {},
                "timedTextUrlsRequest": {
                    "supportedTimedTextFormats": [
                        "TTMLv2",
                        "DFXP"
                    ]
                },
                "transitionTimecodesRequest": {},
                "trickplayUrlsRequest": {},
                "vodPlaybackUrlsRequest": {
                    "ads": {
                        "advertisingId": "",
                        "appBundle": "",
                        "appStoreUrl": "",
                        "gdpr": {
                            "consentMap": None,
                            "enabled": False
                        },
                        "optOutOfAdTracking": False
                    },
                    "device": {
                        "displayBasedVending": "supported",
                        "displayHeight": 3840,
                        "displayWidth": 2160,
                        "streamingTechnologies": {
                            "DASH": {
                                "edgeDeliveryAuthorizationSchemes": None,
                                "fragmentRepresentations": [
                                    "ByteOffsetRange",
                                    "SeparateFile"
                                ],
                                "manifestThinningToSupportedResolution": "Forbidden",
                                "segmentInfoType": "List",
                                "stitchType": "Native",
                                "timedTextRepresentations": [
                                    "BurnedIn",
                                    "NotInManifestNorStream",
                                    "SeparateStreamInManifest"
                                ],
                                "trickplayRepresentations": [
                                    "NotInManifestNorStream"
                                ],
                                "variableAspectRatio": "supported",
                                "vastTimelineType": "Absolute",
                                "bitrateAdaptations": ["CVBR", "CBR"] if bitrate_mode in ("CVBR+CBR", "CVBR,CBR") else [bitrate_mode],
                                "codecs": [video_codec],
                                "drmKeyScheme": "SingleKey",
                                "drmStrength": "L40",
                                "drmType": "PlayReady" if self.playready else "Widevine",
                                "dynamicRangeFormats": [self.VIDEO_RANGE_MAP.get(hdr, "None")],
                                "frameRates": [
                                    "Standard"
                                ]
                            },
                            "SmoothStreaming": {
                                "edgeDeliveryAuthorizationSchemes": None,
                                "fragmentRepresentations": [
                                    "ByteOffsetRange",
                                    "SeparateFile"
                                ],
                                "manifestThinningToSupportedResolution": "Forbidden",
                                "segmentInfoType": "List",
                                "stitchType": "Native",
                                "timedTextRepresentations": [
                                    "BurnedIn",
                                    "NotInManifestNorStream",
                                    "SeparateStreamInManifest"
                                ],
                                "trickplayRepresentations": [
                                    "NotInManifestNorStream"
                                ],
                                "variableAspectRatio": "supported",
                                "vastTimelineType": "Absolute",
                                "bitrateAdaptations": ["CVBR", "CBR"] if bitrate_mode in ("CVBR+CBR", "CVBR,CBR") else [bitrate_mode],
                                "codecs": [video_codec],
                                "drmKeyScheme": "SingleKey",
                                "drmStrength": "L40",
                                "drmType": "PlayReady" if self.playready else "Widevine",
                                "dynamicRangeFormats": [self.VIDEO_RANGE_MAP.get(hdr, "None")],
                                "frameRates": [
                                    "Standard"
                                ]
                            }
                        },
                        "acceptedCreativeApis": [],
                        "category": "TV",
                        "hdcpLevel": "2.2",
                        "maxVideoResolution": "2160p",
                        "operatingSystem": "Android13",
                        "platform": "Android",
                        "supportedStreamingTechnologies": self.manifest_type_tech
                    },
                    "playbackCustomizations": {
                        "videoColorVariant": self.requestd_color_variant
                    },
                    "playbackSettingsRequest": {
                        "deviceModel": self.device["device_model"],
                        "firmware": self.device["firmware"],
                        "responseFormatVersion": "1.0.0",
                        "heuristicProfile": "{\"STARTUP_TIME\":\"PRIORITY\",\"BUFFERING_RISK\":\"HIGH\",\"QUALITY\":\"HIGH\"}",
                        "playerType": "Android Player",
                        "softwareVersion": self.device["software_version"],
                        "titleId": title.id
                    }
                },
                "vodXrayMetadataRequest": {
                    "preferredLocale": "en_US",
                    "xrayDeviceClass": "normal",
                    "xrayPlaybackMode": "playback",
                    "xrayToken": "XRAY_ANDROID_TV_2023_V4"
                }
            }
        else:
            data_dict = {
                "globalParameters": {
                    "deviceCapabilityFamily": "WebPlayer",
                    "playbackEnvelope": self.playbackInfo["playbackExperienceMetadata"]["playbackEnvelope"],
                    "capabilityDiscriminators": {
                        "operatingSystem": {
                            "name": "Windows",
                            "version": "10.0"
                        },
                        "middleware": {
                            "name": "EdgeNext",
                            "version": "149.0.0.0"
                        },
                        "nativeApplication": {
                            "name": "EdgeNext",
                            "version": "149.0.0.0"
                        },
                        "hfrControlMode": "Legacy",
                        "displayResolution": {
                            "height": 2304,
                            "width": 4096
                        }
                    }
                },
                "auditPingsRequest": {},
                "playbackDataRequest": {},
                "timedTextUrlsRequest": {
                    "supportedTimedTextFormats": [
                        "TTMLv2",
                        "DFXP"
                    ]
                },
                "trickplayUrlsRequest": {},
                "transitionTimecodesRequest": {},
                "vodPlaybackUrlsRequest": {
                    "device": {
                        "hdcpLevel": "2.2" if quality == "UHD" else "1.4",
                        "maxVideoResolution": (
                            "1080p" if quality == "HD" else
                            "480p" if quality == "SD" else
                            "2160p"
                        ),
                        "supportedStreamingTechnologies": [
                            "DASH"
                        ],
                        "streamingTechnologies": {
                            "DASH": {
                                "bitrateAdaptations": ["CVBR", "CBR"] if bitrate_mode in ("CVBR+CBR", "CVBR,CBR") else [bitrate_mode],
                                "codecs": [video_codec],
                                "drmKeyScheme": "SingleKey",
                                "drmType": "PlayReady",
                                "dynamicRangeFormats": [self.VIDEO_RANGE_MAP.get(hdr, "None")],
                                "fragmentRepresentations": [
                                    "ByteOffsetRange",
                                    "SeparateFile"
                                ],
                                "edgeDeliveryAuthorizationSchemes": [
                                    "PVExchangeV1",
                                    "Transparent"
                                ],
                                "frameRates": [
                                    "Standard",
                                    "High"
                                ],
                                "stitchType": "MultiPeriod",
                                "segmentInfoType": "Base",
                                "timedTextRepresentations": [
                                    "NotInManifestNorStream",
                                    "SeparateStreamInManifest"
                                ],
                                "trickplayRepresentations": [
                                    "NotInManifestNorStream"
                                ],
                                "variableAspectRatio": "supported"
                            }
                        },
                        "displayWidth": 4096,
                        "displayHeight": 2304
                    },
                    "ads": {
                        "sitePageUrl": "",
                        "gdpr": {
                            "enabled": "false",
                            "consentMap": {},
                            "playerContractVersion": 1
                        }
                    },
                    "playbackCustomizations": {},
                    "playbackSettingsRequest": {
                        "firmware": "UNKNOWN",
                        "playerType": self.player,
                        "responseFormatVersion": "1.0.0",
                        "titleId": title.id
                    }
                },
                "vodXrayMetadataRequest": {
                    "xrayDeviceClass": "normal",
                    "xrayPlaybackMode": "playback",
                    "xrayToken": "XRAY_WEB_2023_V2"
                }
            }
        json_data = json.dumps(data_dict)
        params={
            "deviceID": self.device_id,
            "deviceTypeID": self.device["device_type"],
            "gascEnabled": str(self.pv).lower(),
            "marketplaceID": self.region["marketplace_id"] if not self.device_token else None,
            "uxLocale": "en_EN",
            "firmware": self.device["firmware"] if self.device_token else "UNKNOWN",
            "titleId": title.id,
            "nerid": self.generate_nerid() if not self.device_token else None,
        }
        headers = {
            "Accept": "application/json" if self.device_token else None,
            "Accept-Encoding": "gzip" if self.device_token else None,
            "Accept-Language": "en_IN" if self.device_token else None,
            "Authorization": f"Bearer {self.device_token}" if self.device_token else None,
            "Connection": "Keep-Alive",
            "Content-Type": "application/json; charset=utf-8" if self.device_token else None,
            "User-Agent": self.device["user_agent"] if self.device_token else None,
            "x-gasc-enabled": "true" if self.device_token else None,
            "x-request-priority": "CRITICAL" if self.device_token else None,
            "x-retry-count": "0" if self.device_token else None
        }
        res = self.session.post(
            url=self.endpoints["playback"],
            params=params,
            data=json_data,
            headers=headers,
        )
        # print(res.status_code)
        # print(json.dumps(res.json()["vodPlaybackUrls"], indent=4))
        # print('Tracks : \n')
        # print(self.endpoints["playback"])
        # print(json.dumps(params, indent=4))
        # print(json.dumps(data_dict, indent=4))
        # print(json.dumps(headers, indent=4))
        try:
            manifest = res.json()
        except json.JSONDecodeError:
            if ignore_errors:
                return {}

            raise self.log.exit(f" - Amazon reported an error when obtaining the Playback Manifest\n{res.text}")

        if "error" in manifest["vodPlaybackUrls"]:
            if ignore_errors:
                return {}
            message = manifest["vodPlaybackUrls"]["error"]["message"]
            raise self.log.exit(f" - Amazon reported an error when obtaining the Playback Manifest: {message}")

        # Commented out as we move the rights exception check elsewhere
        # if "rightsException" in manifest["returnedTitleRendition"]["selectedEntitlement"]:
        #     if ignore_errors:
        #         return {}
        #     raise self.log.exit(" - The profile used does not have the rights to this title.")

        # Below checks ignore NoRights errors

        if (
          manifest.get("errorsByResource", {}).get("PlaybackUrls") and
          manifest["errorsByResource"]["PlaybackUrls"].get("errorCode") != "PRS.NoRights.NotOwned"
        ):
            if ignore_errors:
                return {}
            error = manifest["errorsByResource"]["PlaybackUrls"]
            raise self.log.exit(f" - Amazon had an error with the Playback Urls: {error['message']} [{error['errorCode']}]")

        if (
          manifest.get("errorsByResource", {}).get("AudioVideoUrls") and
          manifest["errorsByResource"]["AudioVideoUrls"].get("errorCode") != "PRS.NoRights.NotOwned"
        ):
            if ignore_errors:
                return {}
            error = manifest["errorsByResource"]["AudioVideoUrls"]
            raise self.log.exit(f" - Amazon had an error with the A/V Urls: {error['message']} [{error['errorCode']}]")

        return manifest
        
    @staticmethod
    def get_original_language(manifest):
        """Get a title's original language from manifest data."""
        try:
            return next(
                x["language"].replace("_", "-")
                for x in manifest["catalogMetadata"]["playback"]["audioTracks"]
                if x["isOriginalLanguage"]
            )
        except (KeyError, StopIteration):
            pass

        if "defaultAudioTrackId" in manifest.get("playbackUrls", {}):
            try:
                return manifest["playbackUrls"]["defaultAudioTrackId"].split("_")[0]
            except IndexError:
                pass

        # Additional check for vodPlaybackUrls (vinetrimmer improvement)
        if "defaultAudioTrackId" in manifest.get("vodPlaybackUrls", {}).get("result", {}).get("playbackUrls", {}):
            try:
                return manifest["vodPlaybackUrls"]["result"]["playbackUrls"]["defaultAudioTrackId"].split("_")[0]
            except IndexError:
                pass

        try:
            return sorted(
                manifest["audioVideoUrls"]["audioTrackMetadata"],
                key=lambda x: x["index"]
            )[0]["languageCode"]
        except (KeyError, IndexError):
            pass

        return None
    
    @staticmethod
    def generate_nerid(e=0):
        """Generates Network Edge Request ID."""
        BASE64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    
        # Timestamp part (7 chars)
        timestamp = int(time.time() * 1000)
        ts_chars = []
        for _ in range(7):
            ts_chars.append(BASE64_CHARS[timestamp % 64])
            timestamp //= 64
            ts_part = ''.join(reversed(ts_chars))
    
        # Random part (15 chars)
        rand_part = ''.join(secrets.choice(BASE64_CHARS) for _ in range(15))
    
        # Suffix (2 digits, zero-padded)
        suffix = f"{e % 100:02d}"
    
        return ts_part + rand_part + suffix


    
    @staticmethod
    def clean_mpd_url(mpd_url, optimise=False):
        """Clean up an Amazon MPD manifest url."""
        if '@' in mpd_url:
            mpd_url = re.sub(r'/\d+@[^/]+', '', mpd_url, count=1)
        if optimise:
            return mpd_url.replace("~", "") + "?encoding=segmentBase"
        if match := re.match(r"(https?://.*/)d.?/.*~/(.*)", mpd_url):
            mpd_url = "".join(match.groups())
        else:
            try:
                mpd_url = "".join(
                    re.split(r"(?i)(/)", mpd_url)[:5] + re.split(r"(?i)(/)", mpd_url)[9:]
                )
            except IndexError:
                raise IndexError("Unable to parse MPD URL")

        return mpd_url
        
        
    def get_best_quality(self, title):
        """
        Choose the best quality manifest from CBR / CVBR
        """

        tracks = Tracks()
        bitrates = [self.orig_bitrate]

        if self.vcodec != "H265":
            bitrates = self.orig_bitrate.split('+')

        for bitrate in bitrates:
            manifest = self.get_manifest(
                title,
                video_codec=self.vcodec,
                bitrate_mode=bitrate,
                quality=self.vquality,
                hdr=self.range,
                ignore_errors=False
            )

            if not manifest:
                self.log.warning(f"Skipping {bitrate} manifest due to error")
                continue

            bitrate = manifest["vodPlaybackUrls"]["result"]["playbackUrls"]["urlMetadata"]["bitrateAdaptation"]
                
            # return three empty objects if a rightsException error exists to correlate to manifest, chosen_manifest, tracks
            #if "rightsException" in manifest["returnedTitleRendition"]["selectedEntitlement"]:
                #return None, None, None

            #self.customer_id = manifest["returnedTitleRendition"]["selectedEntitlement"]["grantedByCustomerId"]

            #default_url_set = manifest["playbackUrls"]["urlSets"][manifest["playbackUrls"]["defaultUrlSetId"]]
            #encoding_version = default_url_set["urls"]["manifest"]["encodingVersion"]
            #self.log.info(f" + Detected encodingVersion={encoding_version}")

            chosen_manifest = self.choose_manifest(manifest, self.cdn)

            if not chosen_manifest:
                self.log.warning(f"No {bitrate} DASH manifests available")
                continue

            mpd_url = self.clean_mpd_url(chosen_manifest["url"], optimise=True)
            self.log.debug(mpd_url)
            if self.event:
                devicetype = self.device["device_type"]
                mpd_url = chosen_manifest["url"]
                mpd_url = f"{mpd_url}?amznDtid={devicetype}&encoding=segmentBase"
            self.log.info(f" + Downloading {bitrate} MPD")
            print(f"{mpd_url}")
            streamingProtocol = manifest["vodPlaybackUrls"]["result"]["playbackUrls"]["urlMetadata"]["streamingProtocol"]
            sessionHandoffToken = manifest["sessionization"]["sessionHandoffToken"]

            if streamingProtocol == "DASH":
                tracks.add(Tracks.from_mpd(
                        url=mpd_url,
                        session=self.session,
                        source=self.ALIASES[0],
                ))
                for track in tracks:
                    track.extra = track.extra + (sessionHandoffToken,)
            elif streamingProtocol == "SmoothStreaming":
                tracks.add(Tracks.from_ism(
                        url=mpd_url,
                        source=self.ALIASES[0],
                    ))
                for track in tracks:
                    track.extra = track.extra + (sessionHandoffToken,)
            else:
                raise self.log.exit(f"Unsupported manifest type: {streamingProtocol}")

            for video in tracks.videos:
                video.note = bitrate
            
        if len(self.bitrate.split('+')) > 1:
            self.bitrate = "CVBR,CBR"
            self.log.info("Selected video manifest bitrate: %s", self.bitrate)

        return tracks

    # Service specific classes

    class DeviceRegistration:

        def __init__(self, device: dict, full_cfg, config, endpoints: dict, cache_path: Path, session: requests.Session, log: Logger):
            self.session = session
            self.device = device
            self.full_cfg = full_cfg
            self.config = config
            self.endpoints = endpoints
            self.cache_path = cache_path
            self.log = log
            self.device = {k: str(v) if not isinstance(v, str) else v for k, v in self.device.items()}

            self.bearer = None
            if os.path.isfile(self.cache_path):
                with open(self.cache_path, encoding="utf-8") as fd:
                    cache = jsonpickle.decode(fd.read())
                #self.device["device_serial"] = cache["device_serial"]
                if cache.get("expires_in", 0) > int(time.time()):
                    # not expired, lets use
                    self.log.info(" + Using cached device bearer")
                    self.bearer = cache["access_token"]
                else:
                    # expired, refresh
                    self.log.info("Cached device bearer expired, refreshing...")
                    refresh_success = False
                    if os.path.isfile(self.cache_path):
                        os.remove(self.cache_path)
                    # Try multiple refresh strategies
                    if cache.get("refresh_token"):
                        try:
                            # Strategy 1: Standard refresh with existing refresh token
                            refreshed_tokens = self.refresh(self.device, cache["refresh_token"])
                            refreshed_tokens["refresh_token"] = cache["refresh_token"]
                            # expires_in seems to be in minutes, create a unix timestamp and add the minutes in seconds
                            refreshed_tokens["expires_in"] = int(time.time()) + int(refreshed_tokens["expires_in"])
                            
                            # Preserve device info from cache
                            refreshed_tokens["device_serial"] = cache.get("device_serial", self.device.get("device_serial"))
                            
                            with open(self.cache_path, "w", encoding="utf-8") as fd:
                                fd.write(jsonpickle.encode(refreshed_tokens))
                            self.bearer = refreshed_tokens["access_token"]
                            refresh_success = True
                            self.log.info(" + Token refresh successful")
                        except Exception as e:
                            self.log.warning(f" - Token refresh failed: {str(e)}")
                    
                    if not refresh_success:
                        # Strategy 2: Try to get new tokens with existing device registration
                        self.log.info(" + Attempting to get new tokens with existing device")
                        try:
                            new_tokens = self.register(self.device)
                            # Preserve device serial and other important info
                            new_tokens["device_serial"] = cache.get("device_serial", self.device.get("device_serial"))
                            new_tokens["expires_in"] = int(time.time()) + int(new_tokens.get("expires_in", 3600))
                            
                            with open(self.cache_path, "w", encoding="utf-8") as fd:
                                fd.write(jsonpickle.encode(new_tokens))
                            self.bearer = new_tokens["access_token"]
                            refresh_success = True
                            self.log.info(" + Device re-registration successful")
                        except Exception as register_error:
                            self.log.warning(f" - Device re-registration failed: {str(register_error)}")
            else:
                self.log.info(" + Registering new device bearer")
                self.bearer = self.register(self.device)

        def register(self, device: dict) -> dict:
            """
            Register device to the account
            :param device: Device data to register
            :return: Device bearer tokens
            """
            import requests  # Import here to avoid circular imports
            
            try:
                # OnTV csrf
                self.log.debug(" + Getting CSRF token...")
                csrf_token = self.get_csrf_token()
                # print(csrf_token)
                # Code pair 
                self.log.debug(" + Getting code pair...")
                code_pair = self.get_code_pair(device)

                # Device link
                self.log.debug(" + Linking device...")
                
                # Create a session without the automatic raise_for_status hook for this request
                temp_session = requests.Session()
                temp_session.headers.update(self.session.headers)
                temp_session.cookies.update(self.session.cookies)
                temp_session.proxies.update(self.session.proxies)
                temp_session.verify = self.session.verify
                
                response = temp_session.post(
                    url=self.endpoints["devicelink"],
                    headers={
                        "Accept": "*/*",
                        "Accept-Language": "en-US,en;q=0.9,es-US;q=0.8,es;q=0.7",  # needed?
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Referer": self.endpoints["ontv"]
                    },
                    params=urlencode({
                        # any reason it urlencodes here? requests can take a param dict...
                        "ref_": "atv_set_rd_reg",
                        "publicCode": code_pair["public_code"],  # public code pair
                        "token": csrf_token  # csrf token
                    }),
                    timeout=30
                )
                if response.status_code not in [200, 202]:
                    raise Exception(f"Device linking failed with HTTP {response.status_code}: {response.text}")

                # Register
                self.log.debug(" + Registering device...")
                
                # Create a session without the automatic raise_for_status hook for this request
                temp_session = requests.Session()
                temp_session.headers.update(self.session.headers)
                temp_session.cookies.update(self.session.cookies)
                temp_session.proxies.update(self.session.proxies)
                temp_session.verify = self.session.verify
                
                response = temp_session.post(
                    url=self.endpoints["register"],
                    headers={
                        "Content-Type": "application/json",
                        "Accept-Language": "en-US"
                    },
                    json={
                        "auth_data": {
                            "code_pair": code_pair
                        },
                        "registration_data": device,
                        "requested_token_type": ["bearer"],
                        "requested_extensions": ["device_info", "customer_info"]
                    },
                    timeout=30
                )
                
                # Handle specific error cases
                if response.status_code == 401:
                    raise Exception("Unauthorized - cookies may be expired or invalid")
                elif response.status_code == 403:
                    raise Exception("Forbidden - account may be suspended or device banned")
                elif response.status_code not in [200, 201, 202]:
                    raise Exception(f"Registration failed with HTTP {response.status_code}: {response.text}")
                
                try:
                    response_data = response.json()
                except:
                    raise Exception(f"Invalid JSON response from registration endpoint: {response.text}")
                
                if "error" in response_data:
                    error_desc = response_data.get('error_description', 'Unknown error')
                    error_code = response_data.get('error', 'unknown')
                    raise Exception(f"Registration API error: {error_desc} [{error_code}]")
                
                if "response" not in response_data or "success" not in response_data["response"]:
                    raise Exception(f"Unexpected registration response structure: {response_data}")
                
                bearer = response_data["response"]["success"]["tokens"]["bearer"]
                bearer["expires_in"] = int(time.time()) + int(bearer["expires_in"])

                # Cache bearer
                if os.path.exists(self.cache_path):
                    os.remove(self.cache_path)
                os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
                with open(self.cache_path, "w", encoding="utf-8") as fd:
                    fd.write(jsonpickle.encode(bearer))

                return bearer["access_token"]
                
            except Exception as e:
                if "exit" in str(e.__class__.__name__).lower():
                    raise  # Re-raise log.exit exceptions
                raise Exception(f"Device registration failed: {str(e)}")

        def refresh(self, device: dict, refresh_token: str) -> dict:
            """
            Refresh access token using refresh token
            :param device: Device data
            :param refresh_token: Refresh token to use
            :return: New token data
            """
            if not refresh_token:
                raise Exception("No refresh token provided")
                
            try:
                response = self.session.post(
                    url=self.endpoints["token"],
                    json={
                        "app_name":  self.device["app_name"],
                        "app_version":  self.device["app_version"],
                        "source_token_type": "refresh_token",
                        "source_token": refresh_token,
                        "requested_token_type": "access_token",
                    },
                    timeout=30  # Add timeout to prevent hanging
                )
                
                # Check for HTTP errors
                if response.status_code == 400:
                    raise Exception("Bad Request - refresh token may be expired or invalid")
                elif response.status_code == 401:
                    raise Exception("Unauthorized - refresh token is invalid")
                elif response.status_code == 403:
                    raise Exception("Forbidden - device may be banned or suspended")
                elif not response.ok:
                    raise Exception(f"HTTP {response.status_code}: {response.reason}")
                
                response_data = response.json()
                
            except requests.exceptions.Timeout:
                raise Exception("Token refresh request timed out")
            except requests.exceptions.ConnectionError:
                raise Exception("Connection error during token refresh")
            except requests.exceptions.RequestException as e:
                raise Exception(f"Token refresh request failed: {str(e)}")
            except Exception as e:
                if "HTTP" in str(e) or "Bad Request" in str(e) or "Unauthorized" in str(e):
                    raise  # Re-raise our custom exceptions
                raise Exception(f"Token refresh request failed: {str(e)}")
            
            # Check for API-level errors
            if "error" in response_data:
                error_desc = response_data.get('error_description', 'Unknown error')
                error_code = response_data.get('error', 'unknown')
                raise Exception(f"Token refresh failed: {error_desc} [{error_code}]")
            
            # Validate response structure
            if not response_data.get("access_token"):
                raise Exception("No access token in refresh response")
                
            if response_data.get("token_type", "").lower() != "bearer":
                raise Exception(f"Unexpected token type: {response_data.get('token_type', 'none')}")
            
            return response_data

        def get_csrf_token(self) -> str:
            """
            On the amazon website, you need a token that is in the html page,
            this token is used to register the device
            :return: OnTV Page's CSRF Token
            """
            res = self.session.get(self.endpoints["ontv"])
            response = res.text
            if 'input type="hidden" name="appAction" value="SIGNIN"' in response:
                raise self.log.exit(
                    "Cookies are signed out, cannot get ontv CSRF token. "
                    f"Expecting profile to have cookies for: {self.endpoints['ontv']}"
                )
            for match in re.finditer(r"<script type=\"text/template\">(.+)</script>", response):
                prop = json.loads(match.group(1))
                prop = prop.get("props", {}).get("codeEntry", {}).get("token")
                if prop:
                    return prop
            if self.session.proxies:
                proxy = self.session.proxies['all']
                # If user passed 2-letter country (like US, IN)
                country = proxy.lower()
                if '@' in country:
                    country = country.split('@')[1]
                if '.' in country:
                    country = country.split('.')[0]
                elif '-' in country:
                    country = country.split('-')[0]
                if 'in' in country:
                    country = 'in'
                elif 'au' in country:
                    country = 'au'
                if country:
                    creds = self.full_cfg.credentials.get(f"amazon_{country}")
                    if creds:
                        EMAIL, PASSWORD = creds.split(":", 1)
                        SIGNIN_URL = self.config["signout_url"][country]
                        ontv_URL = self.config["ontv_url"][country]
                def handle_response(response):
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        print(f"HTTP Redirect {response.status} → {location}")

                def save_netscape_cookies(cookies, filename):
                    if os.path.exists(filename):
                        os.remove(filename)
                    with open(filename, "w", encoding="utf-8") as f:
                        f.write("# Netscape HTTP Cookie File\n")
                        f.write("# This file was generated by Playwright\n\n")

                        for cookie in cookies:
                            domain = cookie["domain"]
                            include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
                            path = cookie["path"]
                            secure = "TRUE" if cookie.get("secure", False) else "FALSE"
                            expiry = str(int(cookie.get("expires", time.time() + 3600)))
                            name = cookie["name"]
                            value = cookie["value"]

                            line = "\t".join([
                                domain,
                                include_subdomains,
                                path,
                                secure,
                                expiry,
                                name,
                                value
                            ])
                            f.write(line + "\n")

                    print(f"✅ Cookies saved in Netscape format → {filename}")

                with sync_playwright() as p:
                    if 'au' in country:
                        browser = p.chromium.launch(
                            headless=False,        # Visible like Chrome
                            channel="chrome",       # Use real installed Google Chrome
                            proxy={
                                "server": "https://au938.nordvpn.com:89",   # NO credentials here
                                "username": "FyXix8kHTho33pXCoJT8d1MH",
                                "password": "66TaVivaQV8Pzc8HvxV1z6ZT",
                            }
                        )
                    if 'in' in country:
                        browser = p.chromium.launch(
                            headless=False,        # Visible like Chrome
                            channel="chrome",       # Use real installed Google Chrome
                            #proxy={
                            #    "server": "https://in178.nordvpn.com:89",   # NO credentials here
                            #    "username": "FyXix8kHTho33pXCoJT8d1MH",
                            #    "password": "66TaVivaQV8Pzc8HvxV1z6ZT",
                            #}
                        )

                    context = browser.new_context(
                        viewport=None,         # Use real window size
                        user_agent=None        # Use default Chrome UA
                    )
                    page = context.new_page()
                    
                    # 🔥 Attach listener BEFORE any navigation
                    page.on("response", handle_response)

                    # Open signin page
                    page.goto(SIGNIN_URL)
                    
                    button = page.get_by_role("button", name="Weiter shoppen")
                    if button.count() > 0:
                        button.first.click()

                    page.fill("#ap_email", EMAIL)
                    page.click("#continue")
                    time.sleep(5)
                    

                    # Fill password
                    page.fill("#ap_password", PASSWORD)
                    time.sleep(5)
                    page.click("#signInSubmit")
                    
                    skip_locator = page.locator("#ap-account-fixup-phone-skip-link")
                    if skip_locator.count() > 0:
                        skip_locator.click()
                    # Wait for navigation to complete
                    page.wait_for_load_state("networkidle")
                    page.goto(ontv_URL)
                    page.wait_for_load_state("networkidle")
                    print("✅ Logged in successfully!")
                    # Extract cookies
                    cookies = context.cookies()
                    # cookie_file = f"C:\Users\CloudAdmin\Downloads\downloads\vinetrimmer-main\vinetrimmer\cookies\Amazon\{country}_2160.txt"
                    # save_netscape_cookies(cookies, cookie_file)
                    # print("🍪 Cookies saved to cookies.txt")
                    browser.close()
                # Clear existing session cookies
                self.session.cookies.clear()

                # Inject Playwright cookies into requests session
                for cookie in cookies:
                    self.session.cookies.set(
                        name=cookie["name"],
                        value=cookie["value"],
                        domain=cookie.get("domain"),
                        path=cookie.get("path", "/")
                    )
                res = self.session.get(self.endpoints["ontv"])
                response = res.text
                if 'input type="hidden" name="appAction" value="SIGNIN"' in response:
                    raise self.log.exit(
                        "Cookies are signed out, cannot get ontv CSRF token. "
                        f"Expecting profile to have cookies for: {self.endpoints['ontv']}"
                    )
                for match in re.finditer(r"<script type=\"text/template\">(.+)</script>", response):
                    prop = json.loads(match.group(1))
                    prop = prop.get("props", {}).get("codeEntry", {}).get("token")
                    if prop:
                        return prop
            raise self.log.exit("Unable to get ontv CSRF token")


        def get_code_pair(self, device: dict) -> dict:
            """
            Getting code pairs based on the device that you are using
            :return: public and private code pairs
            """
            # print(device)
            res = self.session.post(
                url=self.endpoints["codepair"],
                headers={
                    "Content-Type": "application/json",
                    "Accept-Language": "en-US"
                },
                json={"code_data": device}
            ).json()
            if "error" in res:
                raise self.log.exit(f"Unable to get code pair: {res['error_description']} [{res['error']}]")
            return res

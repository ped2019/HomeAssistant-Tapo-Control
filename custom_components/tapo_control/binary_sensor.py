from typing import Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.components.ffmpeg import DATA_FFMPEG
from homeassistant.const import STATE_UNAVAILABLE, STATE_ON, STATE_OFF
from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util.enum import try_parse_enum

from .const import (
    BRAND,
    DOMAIN,
    LOGGER,
    ENABLE_SOUND_DETECTION,
    SOUND_DETECTION_PEAK,
    SOUND_DETECTION_DURATION,
    SOUND_DETECTION_RESET,
)
from .utils import build_device_info, getStreamSource
from .tapo.entities import TapoBinarySensorEntity

import haffmpeg.sensor as ffmpeg_sensor


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    LOGGER.debug("Setting up Tapo binary sensors.")
    entry = hass.data[DOMAIN][config_entry.entry_id]
    events = entry["events"]
    name = entry["name"]
    camData = entry["camData"]

    hass.data[DOMAIN][config_entry.entry_id]["eventsListener"] = EventsListener(
        async_add_entities, hass, config_entry
    )

    binarySensors = []

    # Known binary sensor event UIDs to create upfront
    known_binary_sensor_uids = [
        "person_detection",
        "pet_detection",
        "vehicle_detection",
        "line_detector_crossed",
    ]

    for uid in known_binary_sensor_uids:
        event = events.get_uid(uid)
        binarySensors.append(TapoMotionSensor(uid, event, name, camData))

    # Add sound detection sensor if enabled
    if config_entry.data.get(ENABLE_SOUND_DETECTION):
        LOGGER.debug("Adding TapoNoiseBinarySensor...")
        binarySensors.append(TapoNoiseBinarySensor(entry, hass, config_entry))

    if binarySensors:
        async_add_entities(binarySensors)

    return True


class TapoNoiseBinarySensor(TapoBinarySensorEntity):
    def __init__(self, entry: dict, hass: HomeAssistant, config_entry: ConfigEntry):
        LOGGER.debug("TapoNoiseBinarySensor - init")
        super().__init__(
            "Noise",
            entry,
            hass,
            config_entry,
            None,
            BinarySensorDeviceClass.SOUND,
        )

        self._hass = hass
        self._config_entry = config_entry
        self._is_noise_sensor = True
        self._ffmpeg = hass.data[DATA_FFMPEG]
        self._sound_detection_peak = config_entry.data.get(SOUND_DETECTION_PEAK)
        self._sound_detection_duration = config_entry.data.get(SOUND_DETECTION_DURATION)
        self._sound_detection_reset = config_entry.data.get(SOUND_DETECTION_RESET)
        self.latestCamData = entry["camData"]

        manager = get_ffmpeg_manager(hass)
        self._noiseSensor = ffmpeg_sensor.SensorNoise(
            manager.binary, self._noiseCallback
        )
        self._noiseSensor.set_options(
            time_duration=int(self._sound_detection_duration),
            time_reset=int(self._sound_detection_reset),
            peak=int(self._sound_detection_peak),
        )

        self._attr_state = STATE_UNAVAILABLE

    async def startNoiseDetection(self):
        LOGGER.debug("startNoiseDetection")
        self._hass.data[DOMAIN][self._config_entry.entry_id][
            "noiseSensorStarted"
        ] = True
        await self._noiseSensor.open_sensor(
            input_source=getStreamSource(self._config_entry, False),
            extra_cmd="-nostats",
        )

    @callback
    def _noiseCallback(self, noiseDetected):
        LOGGER.debug("_noiseCallback: %s", noiseDetected)
        if not self.latestCamData or self.latestCamData.get("privacy_mode") == "on":
            self._attr_state = STATE_UNAVAILABLE
        else:
            self._attr_state = STATE_ON if noiseDetected else STATE_OFF
        self.async_write_ha_state()

    def updateTapo(self, camData):
        self.latestCamData = camData
        if not camData or camData.get("privacy_mode") == "on":
            self._attr_state = STATE_UNAVAILABLE


class EventsListener:
    def __init__(self, async_add_entities, hass: HomeAssistant, config_entry: ConfigEntry):
        LOGGER.debug("EventsListener init")
        self.metaData = hass.data[DOMAIN][config_entry.entry_id]
        self.async_add_entities = async_add_entities


class TapoMotionSensor(BinarySensorEntity):
    def __init__(self, uid: str, events, name: str, camData: dict):
        LOGGER.debug("TapoMotionSensor - init - UID: %s", uid)
        self.uid = uid
        self._name = name
        self._attributes = camData["basic_info"]

        self._attr_unique_id = uid
        self._attr_name = f"{name} {event.name}" if event else f"{name} {uid.replace('_', ' ').title()}"
        self._attr_device_class = try_parse_enum(BinarySensorDeviceClass, event.device_class) if event else BinarySensorDeviceClass.MOTION
        self._attr_entity_category = event.entity_category if event else None
        self._attr_entity_registry_enabled_default = event.entity_enabled if event else True
        self._attr_is_on = event.value if event else False
        self._attr_enabled = event.entity_enabled if event else True
        self._event = event

        super().__init__()

    @property
    def is_on(self) -> bool:
        return self._event.value if self._event else False

    @property
    def name(self) -> str:
        return self._attr_name

    @property
    def device_class(self) -> Optional[str]:
        if (event := self.events.get_uid(self._attr_unique_id)) is not None:
            return event.device_class
        return self._attr_device_class

    @property
    def unique_id(self) -> str:
        return self.uid

    @property
    def entity_registry_enabled_default(self) -> bool:
        if (event := self.events.get_uid(self._attr_unique_id)) is not None:
            return event.entity_enabled
        return self._attr_entity_registry_enabled_default

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self._attributes)

    @property
    def model(self):
        return self._attributes.get("device_model")

    @property
    def brand(self):
        return BRAND

    async def async_added_to_hass(self):
        if self._event:
            self.async_on_remove(self._event.events.async_add_listener(self.async_write_ha_state))

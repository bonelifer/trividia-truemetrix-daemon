"""Optional MQTT publishing of synced readings, alongside local SQLite recording.

There's no Home Assistant MQTT discovery support (auto-creating entities)
-- this publishes raw JSON only, same as etekcity-scale-daemon.
"""

from __future__ import annotations

import json
import logging
import ssl
from contextlib import asynccontextmanager

import aiomqtt

from .config import MqttConfig

_LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def mqtt_connection(mqtt_config: MqttConfig):
    """Yield a connected MQTT client, or None if disabled or unreachable.

    A broker connection failure is logged and treated as non-fatal: local
    sync to the database is the daemon's primary job and must not be
    blocked by an MQTT outage.

    Args:
        mqtt_config: Parsed [mqtt] configuration.

    Yields:
        A connected ``aiomqtt.Client``, or None if MQTT is disabled or the
        broker could not be reached.
    """
    if not mqtt_config.enabled:
        yield None
        return

    tls_context = ssl.create_default_context() if mqtt_config.use_tls else None
    try:
        async with aiomqtt.Client(
            hostname=mqtt_config.host,
            port=mqtt_config.port,
            username=mqtt_config.username or None,
            password=mqtt_config.password or None,
            tls_context=tls_context,
        ) as client:
            _LOGGER.info("Connected to MQTT broker %s:%s", mqtt_config.host, mqtt_config.port)
            yield client
    except aiomqtt.MqttError as exc:
        _LOGGER.warning(
            "Could not connect to MQTT broker %s:%s (%s) -- continuing without "
            "MQTT publishing",
            mqtt_config.host,
            mqtt_config.port,
            exc,
        )
        yield None


async def publish_reading(
    client: aiomqtt.Client, mqtt_config: MqttConfig, device_id: str, model: str, reading
) -> None:
    """Publish one newly-synced reading to MQTT as a JSON payload.

    Failures are logged, not raised -- a broker hiccup shouldn't be allowed
    to propagate into the sync loop.

    Args:
        client: A connected MQTT client.
        mqtt_config: Supplies the topic prefix, QoS, and retain flag.
        device_id: The meter's device_id, used as the topic's last segment.
        model: Full model name, included in the payload.
        reading: A trividia_truemetrix_hid.Reading.
    """
    topic = f"{mqtt_config.topic_prefix}/{device_id}/state"
    payload = {
        "device_id": device_id,
        "model": model,
        "device_time": reading.device_time.isoformat(),
        "value_mg_dl": reading.value_mg_dl,
        "out_of_range": reading.out_of_range,
    }
    try:
        await client.publish(
            topic, json.dumps(payload), qos=mqtt_config.qos, retain=mqtt_config.retain
        )
    except aiomqtt.MqttError as exc:
        _LOGGER.warning("MQTT publish failed for %s: %s", device_id, exc)

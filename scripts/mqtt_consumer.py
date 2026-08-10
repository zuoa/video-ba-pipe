#!/usr/bin/env python3
"""Subscribe to alerts using the MQTT settings saved from the web page."""

import json
import os
import signal
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import paho.mqtt.client as mqtt

from app.core.mqtt_config import get_mqtt_config


config = get_mqtt_config()


def on_connect(client, _userdata, _flags, reason_code, *args):
    if getattr(reason_code, "value", reason_code) != 0:
        raise RuntimeError(f"MQTT connection rejected: {reason_code}")
    topic = f"{config.topic_prefix.strip().strip('/')}/#"
    client.subscribe(topic, qos=1)
    print(f"Subscribed to {topic}")


def on_message(_client, _userdata, message):
    payload = json.loads(message.payload.decode("utf-8"))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main():
    kwargs = {
        "client_id": f"video-ba-example-consumer-{os.getpid()}",
        "protocol": mqtt.MQTTv311,
    }
    if hasattr(mqtt, "CallbackAPIVersion"):
        kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
    client = mqtt.Client(**kwargs)
    if config.username:
        client.username_pw_set(config.username, config.password)
    client.on_connect = on_connect
    client.on_message = on_message
    signal.signal(signal.SIGTERM, lambda *_: client.disconnect())
    signal.signal(signal.SIGINT, lambda *_: client.disconnect())
    client.connect(config.host, config.port, config.keepalive_seconds)
    client.loop_forever()


if __name__ == "__main__":
    main()

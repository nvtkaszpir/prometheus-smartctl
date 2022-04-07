#!/usr/bin/env python3
"""

Read smartctl data and expose it as prometheus metrics with smartprom_ prefix
Notice this script must be run as root to work or in privileged container.

Env vars:

LOGFORMAT (default '%(asctime)s:%(name)s:%(levelname)s:%(message)s') - set logging format
LOGLEVEL (default INFO) - increase log verbosity level
PORT (default 9902) - listen on given HTTP port
SCAN_EVERY_SECONDS (default 20) - how frequently collect metrics
SLEEP_SECONDS (default 0.1) - how long to sleep in the wait loop for next iteration, should be below  SCAN_EVERY_SECONDS and above 0.1 to avoid useless resource usage

"""

import logging
import subprocess
import time
import json
import os
from typing import List
from prometheus_client import start_http_server, Gauge

logger = logging.getLogger(__name__)
logging_level = os.environ.get("LOGLEVEL", "INFO").upper()
try:
    logging_level = int(logging_level)
except ValueError:
    pass

try:
    logging._checkLevel(logging_level)
except ValueError as err:
    logging_level = "INFO"
    logger.warning("Invalid LOGLEVEL set: {} , falling back to INFO".format(err))


logger.setLevel(level=logging_level)
ch = logging.StreamHandler()
formatter = logging.Formatter(
    os.environ.get("LOGFORMAT", "%(asctime)s:%(name)s:%(levelname)s:%(message)s")
)
ch.setFormatter(formatter)
logger.addHandler(ch)


def run(args: List[str]):
    """
    runs the smartctl command on the system
    """
    out = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    stdout, stderr = out.communicate()

    if out.returncode != 0:
        if stderr:
            logger.error(stderr.decode("utf-8"))
        raise Exception(
            'Command "{cmd}" returned code {code}:'.format(
                cmd=" ".join(args), code=out.returncode
            )
        )
    return stdout.decode("utf-8")


def get_drives():
    """
    returns a dictionary of devices and its types
    """
    disks = {}
    results = run(["smartctl", "--scan-open", "--json=c"])
    devices = json.loads(results).get("devices", {})
    for device in devices:
        if "open_error" in device:
            message = "Skipping device name={name}, type={type}, protocol={protocol}, open_error={open_error}".format(
                **device
            )
            logger.warning(message)
        else:
            disks[device["name"]] = device["type"]

    message = "Devices and its types: {}".format(disks)
    logger.debug(message)

    if not len(disks):
        logger.warning(
            "No devices added, check permissions - this script must be run as 'root' or if in container then as 'privileged'."
        )

    return disks


DRIVES = get_drives()
HEADER = "ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE"
METRICS = {}
LABELS = ["drive"]


def smart_sat(dev: str) -> List[str]:
    """
    Runs the smartctl command on a "sat" device - ATA/SATA disks
    and processes its attributes

    """
    # TODO: use json output
    results = run(["smartctl", "-A", "-d", "sat", dev])
    attributes = {}
    got_header = False
    for result in results.split("\n"):
        if not result:
            continue

        if result == HEADER:
            got_header = True
            continue

        if got_header:
            tokens = result.split()
            if len(tokens) > 3:
                raw = None
                try:
                    raw = int(tokens[9])
                except:
                    pass

                attributes[tokens[1]] = (int(tokens[0]), int(tokens[3]))
                if raw is not None:
                    attributes[f"{tokens[1]}_raw"] = (int(tokens[0]), raw)
    return attributes


def smart_nvme(dev: str) -> List[str]:
    """
    Runs the smartctl command on a "nvme" device
    and processes its attributes
    """
    results = run(["smartctl", "-A", "-d", "nvme", "--json=c", dev])
    attributes = {}

    health_info = json.loads(results)["nvme_smart_health_information_log"]
    for k, v in health_info.items():
        if k == "temperature_sensors":
            for i, value in enumerate(v, start=1):
                attributes["temperature_sensor{i}".format(i=i)] = value
            continue
        attributes[k] = v

    return attributes


def smart_scsi(dev: str) -> List[str]:
    """
    Runs the smartctl command on a "scsi" device
    and processes its attributes
    """
    results = run(["smartctl", "-A", "-d", "scsi", "--json=c", dev])
    attributes = {}
    data = json.loads(results)
    for key, value in data.items():
        if type(value) == dict:
            for _label, _value in value.items():
                if type(_value) == int:
                    attributes[f"{key}_{_label}"] = _value
        elif type(value) == int:
            attributes[key] = value
    return attributes


def collect():
    """
    Collect all drive metrics and save them as Gauge type
    """
    global METRICS

    for drive, typ in DRIVES.items():
        try:
            if typ == "sat":
                attrs = smart_sat(drive)
            elif typ == "nvme":
                attrs = smart_nvme(drive)
            elif typ == "scsi":
                attrs = smart_scsi(drive)
            else:
                logger.warning(
                    "Unsupported device type: name={name}, type={type}".format(
                        name=drive, type=typ
                    )
                )
                continue

            for key, values in attrs.items():
                # Create metric if does not exist
                if key not in METRICS:
                    name = (
                        key.replace("-", "_")
                        .replace(" ", "_")
                        .replace(".", "")
                        .replace("/", "_")
                        .lower()
                    )
                    desc = key.replace("_", " ")
                    if typ == "sat":
                        num = hex(values[0])
                    else:
                        num = hex(values)
                    skey = f"smartprom_{name}"
                    message = f"Adding new gauge {skey} ({num})"
                    logger.info(message)
                    METRICS[key] = Gauge(skey, f"({num}) {desc}", LABELS)

                # Update metric
                if typ == "sat":
                    METRICS[key].labels(drive.replace("/dev/", "")).set(values[1])
                else:
                    METRICS[key].labels(drive.replace("/dev/", "")).set(values)

        except Exception as e:
            logger.error("Exception:", e)
            pass


def main():
    """
    starts a server at port 9902 and exposes the metrics
    """
    start_http_server(int(os.environ.get("PORT", 9902)))
    collect()

    start_time = time.time()
    while True:
        elapsed_time = time.time() - start_time
        if elapsed_time > float(os.environ.get("SCAN_EVERY_SECONDS", 20)):
            start_time = time.time()
            collect()
        time.sleep(float(os.environ.get("SLEEP_SECONDS", 0.1)))


if __name__ == "__main__":
    main()

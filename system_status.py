#!/bin/env python3

import subprocess
import json
import time
import socket
import os
from threading import Timer

SOCKET = "/var/run/system_status.sock"

prev_idle: int = 0
prev_total: int = 0

def sensors():
   result = {}
   report = json.loads(subprocess.check_output(["/bin/env", "sensors", "-j"]))

   result["temp"] = []

   for driver in [v for k, v in report.items() if "coretemp" in k]:
      for package in [v for k, v in driver.items() if "Package" in k]:
         result["temp"].append(package)

   return result

def processor_load():
    global prev_idle
    global prev_total

    with open("/proc/stat", "r") as stat:
        cpu = stat.readline()

    cpu = [int(x) for x in filter(lambda x: x != "", cpu.split(" ")[1:])]

    total = sum(cpu) - (cpu[8] + cpu[9])
    idle = cpu[3] + cpu[4]

    total_diff = total - prev_total
    idle_diff = idle - prev_idle

    level = (total_diff - idle_diff) / (total_diff)

    prev_idle = idle
    prev_total = total

    return level


def memory_load():
    with open("/proc/meminfo", "r") as stat:
        mem_total = int(list(filter(lambda x: x != "", stat.readline().replace("  ", " ").split(" ")))[1])
        stat.readline()
        mem_available = int(list(filter(lambda x: x != "", stat.readline().replace("  ", " ").split(" ")))[1])
    return {"total": mem_total, "available": mem_available}


class RaidStatus:
    def __init__(self, name, type_, state, copy_percent, sync_percent, mismatch_count, devices):
        self.name = name
        self.type = type_
        self.state = state
        self.copy_percent = copy_percent
        self.sync_percent = sync_percent
        self.mismatch_count = mismatch_count
        self.devices = devices


def raid_status():
    result = []
    report = json.loads(subprocess.check_output(["/bin/env", "lvs", "-a", "-o",
                                                 "vg_name,name,copy_percent,sync_percent,devices,lv_health_status,"
                                                 "raid_mismatch_count,raid_sync_action,raid_write_behind,"
                                                 "raid_min_recovery_rate,raid_max_recovery_rate",
                                                 "--report-format=json"]))

    for entry in report["report"][0]["lv"]:
        if entry["copy_percent"] != "":
            name = entry["lv_name"]
            path = "/dev/mapper/{}-{}".format(entry["vg_name"], entry["lv_name"])
            copy_percent = entry["copy_percent"]
            sync_percent = entry["sync_percent"]
            mismatch_count = entry["raid_mismatch_count"]
            action = entry["raid_sync_action"]
            images = ["[" + x[0:x.find("(")] + "]" for x in entry["devices"].split(",")]
            devices = []
            status = {}
            for entry2 in report["report"][0]["lv"]:
                if entry2["lv_name"] in images:
                    devices = devices + [x[0:x.find("(")] for x in entry2["devices"].split(",")]

            for device in devices:
                smart_report = json.loads(subprocess.check_output(["/bin/env", "smartctl", "-jH", device]))
                status[smart_report["device"]["name"]] = ("OK" if smart_report["smart_status"]["passed"] else "FAIL")

            dm_report = subprocess.check_output(["/bin/env", "dmsetup", "table", path]).decode("utf-8").split(" ")
            type_ = dm_report[3]

            result.append(RaidStatus(name, type_, action, copy_percent, sync_percent, mismatch_count, status))
    return result


def listen_socket():
    try:
        os.unlink(SOCKET)
    except OSError:
        if os.path.exists(SOCKET):
            raise

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET)

    while True:
        server.listen(33233)
        conn, _ = server.accept()
        conn.setblocking(False)

        command = ""
        while True:
            try:
                command = command + conn.recv(1024).decode("utf-8")
                if "\n" in command: break
                print(command)
            except BlockingIOError:
                time.sleep(1)

        print("Command: " + command.strip())
        if command.strip() == "get_status":
            response = {"raid": [x.__dict__ for x in raid_status()], "cpu_load": processor_load(),
                        "memory_load": memory_load(), "sensors": sensors()}

            conn.send((json.dumps(response) + "\n").encode("utf-8"))
        conn.close()

with open(os.path.expanduser("~/.system_status"), "r") as conf_file:
    conf = json.loads(" ".join(conf_file.readlines()))
t = Timer(1000, processor_load)
listen_socket()

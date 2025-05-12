# 4월 이후로 해당 코드 사용하지 않음. alert_file.sh 로 전원 절체 진행중

import json
import os
import re
import subprocess
from datetime import datetime, timedelta

LOG_FILE = "/var/log/pcmk_alert_file.log"

# IP 주소가 0~255 범위 초과하지 않도록 정규식 설정
IP_REGEX = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}): Fencing Operation reboot of ((?:25[0-5]|2[0-4]\d|1\d\d|\d{1,2})\.(?:25[0-5]|2[0-4]\d|1\d\d|\d{1,2})\.(?:25[0-5]|2[0-4]\d|1\d\d|\d{1,2})\.(?:25[0-5]|2[0-4]\d|1\d\d|\d{1,2})) .*? Error"

def get_cluster_dc():
    try:
        dc_info = subprocess.check_output("pcs status | grep 'Current DC'", shell=True, text=True).strip()
        match = re.search(r"Current DC: (\d+\.\d+\.\d+\.\d+)", dc_info)
        return match.group(1) if match else None
    except subprocess.CalledProcessError:
        return None

def check_and_confirm_stonith():
    cluster_dc = get_cluster_dc()
    local_ip = os.popen("hostname -i | awk '{print $1}'").read().strip()

    if cluster_dc != local_ip:
        return json.dumps({"code": 500, "message": f"Skipping execution: This host ({local_ip}) is not the cluster DC ({cluster_dc})"}, indent=4)

    if not os.path.exists(LOG_FILE):
        return json.dumps({"code": 500, "message": "Log file not found"}, indent=4)

    now = datetime.now()
    error_ips = set()

    with open(LOG_FILE, "r") as f:
        for line in f:
            match = re.search(IP_REGEX, line)
            if match:
                log_time = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                ip_address = match.group(2)

                if now - log_time <= timedelta(minutes=2):
                    error_ips.add(ip_address)

    if not error_ips:
        return json.dumps({"code": 500, "message": "No recent errors found"}, indent=4)

    for ip in error_ips:
        hostname = os.popen(f"grep -w {ip} /etc/hosts | awk '{{print $2}}'").read().strip()
        subprocess.run(f"pcs stonith confirm {ip} --force", shell=True, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(f"pcs stonith disable fence-{hostname}", shell=True, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return json.dumps({"code": 200, "message": f"Confirmed stonith for: {', '.join(error_ips)}"}, indent=4)

print(check_and_confirm_stonith())